// Package localmedia is the development/test MediaStore: private photos on
// this machine's filesystem. It is selected only by MEDIA_MODE=local, which
// configuration refuses outside APP_ENV=development|test.
package localmedia

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/nawariso/toem-hia/services/api/internal/application"
	"github.com/nawariso/toem-hia/services/api/internal/domain/media"
)

// ErrInvalidKey is returned for any key that is not server-generated.
var ErrInvalidKey = errors.New("invalid media storage key")

// Store keeps published objects at <root>/<key> and staged uploads in
// <root>/.staging. Staging lives under the same root so publishing is a
// same-volume rename, which is atomic where the platform supports it.
type Store struct {
	root    string
	staging string
}

var _ application.MediaStore = (*Store)(nil)

// New prepares root (owner-only permissions) and removes staged uploads left
// behind by a previous crash.
func New(root string) (*Store, error) {
	if root == "" || !filepath.IsAbs(root) {
		return nil, errors.New("local media root must be an absolute path")
	}
	s := &Store{root: filepath.Clean(root), staging: filepath.Join(filepath.Clean(root), ".staging")}
	for _, dir := range []string{s.root, s.staging, filepath.Join(s.root, "photos")} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return nil, fmt.Errorf("prepare local media root: %w", err)
		}
	}
	entries, err := os.ReadDir(s.staging)
	if err != nil {
		return nil, fmt.Errorf("prepare local media root: %w", err)
	}
	for _, entry := range entries {
		_ = os.Remove(filepath.Join(s.staging, entry.Name()))
	}
	return s, nil
}

// Stage streams r into a temporary file, hashing as it writes. It reads at
// most limit+1 bytes, so an oversized body is detected without buffering it.
func (s *Store) Stage(ctx context.Context, r io.Reader, limit int64) (application.StagedMedia, error) {
	f, err := os.CreateTemp(s.staging, "upload-*.tmp")
	if err != nil {
		return nil, fmt.Errorf("stage media: %w", err)
	}
	staged := &staged{store: s, path: f.Name()}
	hash := sha256.New()
	n, copyErr := io.Copy(io.MultiWriter(f, hash), io.LimitReader(ctxReader{ctx, r}, limit+1))
	closeErr := f.Close()
	switch {
	case copyErr != nil:
		_ = staged.Discard()
		return nil, copyErr
	case closeErr != nil:
		_ = staged.Discard()
		return nil, fmt.Errorf("stage media: %w", closeErr)
	case n > limit:
		_ = staged.Discard()
		return nil, media.ErrTooLarge
	}
	staged.size, staged.sum = n, hex.EncodeToString(hash.Sum(nil))
	return staged, nil
}

func (s *Store) Open(_ context.Context, key string) (io.ReadCloser, error) {
	path, err := s.path(key)
	if err != nil {
		return nil, err
	}
	return os.Open(path)
}

func (s *Store) Delete(_ context.Context, key string) error {
	path, err := s.path(key)
	if err != nil {
		return err
	}
	if err = os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

// path maps a server-generated key to a file under root. Keys are checked
// against the exact generated shape, so no key can name a path outside root.
func (s *Store) path(key string) (string, error) {
	if !media.ValidStorageKey(key) {
		return "", ErrInvalidKey
	}
	return filepath.Join(s.root, filepath.FromSlash(key)), nil
}

type staged struct {
	store     *Store
	path      string
	size      int64
	sum       string
	committed bool
}

func (t *staged) Size() int64                  { return t.size }
func (t *staged) SHA256() string               { return t.sum }
func (t *staged) Open() (io.ReadCloser, error) { return os.Open(t.path) }

// Commit publishes by renaming within the same directory tree. It refuses to
// replace an existing object.
func (t *staged) Commit(_ context.Context, key string) error {
	target, err := t.store.path(key)
	if err != nil {
		return err
	}
	if _, err = os.Lstat(target); err == nil {
		return fmt.Errorf("media object already exists")
	}
	if err = os.Rename(t.path, target); err != nil {
		return err
	}
	t.committed = true
	return nil
}

func (t *staged) Discard() error {
	if t.committed {
		return nil
	}
	if err := os.Remove(t.path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

// ctxReader stops a long upload promptly when the request is cancelled.
type ctxReader struct {
	ctx context.Context
	r   io.Reader
}

func (c ctxReader) Read(p []byte) (int, error) {
	if err := c.ctx.Err(); err != nil {
		return 0, err
	}
	return c.r.Read(p)
}
