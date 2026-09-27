package localmedia_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/nawariso/toem-hia/services/api/internal/domain/media"
	"github.com/nawariso/toem-hia/services/api/internal/infrastructure/media/localmedia"
)

const key = "photos/0f8fad5b-d9cb-469f-a165-70867728950e.jpg"

func newStore(t *testing.T) (*localmedia.Store, string) {
	t.Helper()
	root := t.TempDir()
	s, err := localmedia.New(root)
	if err != nil {
		t.Fatal(err)
	}
	return s, root
}

func stagingEntries(t *testing.T, root string) []os.DirEntry {
	t.Helper()
	entries, err := os.ReadDir(filepath.Join(root, ".staging"))
	if err != nil {
		t.Fatal(err)
	}
	return entries
}

func TestStageHashesAndCommitPublishesAtomically(t *testing.T) {
	s, root := newStore(t)
	data := []byte("some photo bytes")
	staged, err := s.Stage(t.Context(), bytes.NewReader(data), 1024)
	if err != nil {
		t.Fatal(err)
	}
	want := sha256.Sum256(data)
	if staged.Size() != int64(len(data)) || staged.SHA256() != hex.EncodeToString(want[:]) {
		t.Fatalf("size/hash wrong: %d %s", staged.Size(), staged.SHA256())
	}
	if err = staged.Commit(t.Context(), key); err != nil {
		t.Fatal(err)
	}
	if err = staged.Discard(); err != nil {
		t.Fatalf("Discard after Commit must be a no-op: %v", err)
	}
	r, err := s.Open(t.Context(), key)
	if err != nil {
		t.Fatal(err)
	}
	got, _ := io.ReadAll(r)
	_ = r.Close()
	if !bytes.Equal(got, data) {
		t.Fatal("published bytes differ from the upload")
	}
	if n := len(stagingEntries(t, root)); n != 0 {
		t.Fatalf("staging not empty after commit: %d entries", n)
	}
	// Publishing never overwrites an existing object.
	again, err := s.Stage(t.Context(), bytes.NewReader(data), 1024)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = again.Discard() }()
	if err = again.Commit(t.Context(), key); err == nil {
		t.Fatal("Commit must refuse to replace an existing object")
	}
}

func TestOversizedUploadLeavesNoTemporaryFile(t *testing.T) {
	s, root := newStore(t)
	_, err := s.Stage(t.Context(), bytes.NewReader(make([]byte, 101)), 100)
	if !errors.Is(err, media.ErrTooLarge) {
		t.Fatalf("want ErrTooLarge, got %v", err)
	}
	if n := len(stagingEntries(t, root)); n != 0 {
		t.Fatalf("temporary file left behind: %d entries", n)
	}
	// Exactly at the limit is accepted.
	staged, err := s.Stage(t.Context(), bytes.NewReader(make([]byte, 100)), 100)
	if err != nil {
		t.Fatal(err)
	}
	_ = staged.Discard()
}

type failingReader struct{ n int }

func (f *failingReader) Read(p []byte) (int, error) {
	if f.n > 0 {
		f.n--
		return copy(p, "partial"), nil
	}
	return 0, errors.New("connection reset")
}

func TestFailedOrCancelledUploadLeavesNoTemporaryFile(t *testing.T) {
	s, root := newStore(t)
	if _, err := s.Stage(t.Context(), &failingReader{n: 3}, 1024); err == nil {
		t.Fatal("a failing body must fail staging")
	}
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	if _, err := s.Stage(ctx, strings.NewReader("data"), 1024); !errors.Is(err, context.Canceled) {
		t.Fatalf("want context.Canceled, got %v", err)
	}
	if n := len(stagingEntries(t, root)); n != 0 {
		t.Fatalf("temporary file left behind: %d entries", n)
	}
}

func TestDiscardRemovesStagedFile(t *testing.T) {
	s, root := newStore(t)
	staged, err := s.Stage(t.Context(), strings.NewReader("data"), 1024)
	if err != nil {
		t.Fatal(err)
	}
	if n := len(stagingEntries(t, root)); n != 1 {
		t.Fatalf("want 1 staged file, got %d", n)
	}
	if err = staged.Discard(); err != nil {
		t.Fatal(err)
	}
	if n := len(stagingEntries(t, root)); n != 0 {
		t.Fatalf("staged file not removed: %d", n)
	}
}

func TestNewClearsStagingLeftByACrash(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, ".staging"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, ".staging", "upload-1.tmp"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := localmedia.New(root); err != nil {
		t.Fatal(err)
	}
	if n := len(stagingEntries(t, root)); n != 0 {
		t.Fatalf("stale staging not cleared: %d", n)
	}
}

func TestKeysOutsideTheServerShapeAreRefused(t *testing.T) {
	s, root := newStore(t)
	outside := filepath.Join(filepath.Dir(root), "outside.jpg")
	for _, bad := range []string{"../outside.jpg", "photos/../../outside.jpg", outside, "photos/x.jpg", ""} {
		if _, err := s.Open(t.Context(), bad); !errors.Is(err, localmedia.ErrInvalidKey) {
			t.Fatalf("Open(%q): want ErrInvalidKey, got %v", bad, err)
		}
		if err := s.Delete(t.Context(), bad); !errors.Is(err, localmedia.ErrInvalidKey) {
			t.Fatalf("Delete(%q): want ErrInvalidKey, got %v", bad, err)
		}
		staged, err := s.Stage(t.Context(), strings.NewReader("x"), 10)
		if err != nil {
			t.Fatal(err)
		}
		if err = staged.Commit(t.Context(), bad); !errors.Is(err, localmedia.ErrInvalidKey) {
			t.Fatalf("Commit(%q): want ErrInvalidKey, got %v", bad, err)
		}
		_ = staged.Discard()
	}
}

func TestDeleteIsIdempotent(t *testing.T) {
	s, _ := newStore(t)
	if err := s.Delete(t.Context(), key); err != nil {
		t.Fatalf("deleting a missing object must succeed: %v", err)
	}
}

func TestRootMustBeAbsolute(t *testing.T) {
	if _, err := localmedia.New("relative/media"); err == nil {
		t.Fatal("a relative root must be refused")
	}
}
