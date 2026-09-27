package application_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"sync"
	"testing"

	"github.com/nawariso/toem-hia/services/api/internal/application"
	"github.com/nawariso/toem-hia/services/api/internal/domain"
	"github.com/nawariso/toem-hia/services/api/internal/domain/encounter"
	"github.com/nawariso/toem-hia/services/api/internal/domain/media"
	"github.com/nawariso/toem-hia/services/api/internal/infrastructure/media/localmedia"
	"github.com/nawariso/toem-hia/services/api/internal/testsupport"
)

// fakeMediaRepo mirrors the Postgres Attach contract: guard, dedupe by hash,
// count limit, insert. failAttach simulates a database failure after the
// bytes were published.
type fakeMediaRepo struct {
	mu         sync.Mutex
	encounters *fakeEncounters
	rows       map[string]media.Media
	failAttach error
}

func (f *fakeMediaRepo) Attach(ctx context.Context, m media.Media, maxPhotos int, guard func(encounter.Encounter) error) (media.Media, bool, error) {
	e, err := f.encounters.FindByID(ctx, m.EncounterID)
	if err != nil {
		return media.Media{}, false, err
	}
	if err = guard(e); err != nil {
		return media.Media{}, false, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failAttach != nil {
		return media.Media{}, false, f.failAttach
	}
	count := 0
	for _, row := range f.rows {
		if row.EncounterID == m.EncounterID {
			if row.SHA256 == m.SHA256 {
				return row, false, nil
			}
			count++
		}
	}
	if count >= maxPhotos {
		return media.Media{}, false, media.ErrTooManyPhotos
	}
	f.rows[m.ID] = m
	return m, true, nil
}

func (f *fakeMediaRepo) ListByEncounter(_ context.Context, encounterID string) ([]media.Media, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := []media.Media{}
	for _, row := range f.rows {
		if row.EncounterID == encounterID {
			out = append(out, row)
		}
	}
	return out, nil
}

func (f *fakeMediaRepo) FindByID(_ context.Context, id string) (media.Media, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	m, ok := f.rows[id]
	if !ok {
		return media.Media{}, media.ErrNotFound
	}
	return m, nil
}

type mediaFixture struct {
	*fixture
	svc   *application.MediaService
	repo  *fakeMediaRepo
	root  string
	draft string // a DRAFT encounter owned by "active"
}

func newMediaFixture(t *testing.T) *mediaFixture {
	t.Helper()
	f := newFixture(t)
	root := t.TempDir()
	store, err := localmedia.New(root)
	if err != nil {
		t.Fatal(err)
	}
	repo := &fakeMediaRepo{encounters: f.repo, rows: map[string]media.Media{}}
	// The fixture's user resolver is private to newFixture; rebuild the same
	// users so both services agree on identities.
	users := fakeUsers{}
	for _, e := range []struct {
		subject string
		status  string
	}{{"active", domain.UserStatusActive}, {"other", domain.UserStatusActive}, {"suspended", domain.UserStatusSuspended}, {"deleted", domain.UserStatusDeleted}} {
		users[e.subject] = domain.User{ID: e.subject + "-id", Status: e.status}
	}
	users["active"] = domain.User{ID: f.activeUserID, Status: domain.UserStatusActive}
	svc := application.NewMediaService(users, f.repo, repo, store)

	view, err := f.svc.Create(t.Context(), f.active, f.input())
	if err != nil {
		t.Fatal(err)
	}
	return &mediaFixture{fixture: f, svc: svc, repo: repo, root: root, draft: view.Encounter.ID}
}

// published lists every object file under the media root (not staging).
func (m *mediaFixture) published(t *testing.T) []string {
	t.Helper()
	var files []string
	err := filepath.WalkDir(filepath.Join(m.root, "photos"), func(path string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			files = append(files, path)
		}
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	return files
}

func (m *mediaFixture) staged(t *testing.T) int {
	t.Helper()
	entries, err := os.ReadDir(filepath.Join(m.root, ".staging"))
	if err != nil {
		t.Fatal(err)
	}
	return len(entries)
}

func TestOwnerCanUploadJPEGAndPNG(t *testing.T) {
	m := newMediaFixture(t)
	for _, tc := range []struct {
		contentType string
		data        []byte
	}{
		{media.ContentTypeJPEG, testsupport.JPEG(t, 64, 48)},
		{media.ContentTypePNG, testsupport.PNG(t, 20, 10)},
	} {
		got, err := m.svc.Upload(t.Context(), m.active, m.draft, tc.contentType, bytes.NewReader(tc.data))
		if err != nil {
			t.Fatalf("%s: %v", tc.contentType, err)
		}
		sum := sha256.Sum256(tc.data)
		if !got.Created || got.Media.EncounterID != m.draft || got.Media.ContentType != tc.contentType ||
			got.Media.ByteSize != int64(len(tc.data)) || got.Media.SHA256 != hex.EncodeToString(sum[:]) ||
			got.Media.Status != media.StatusReady || got.UserID != m.activeUserID {
			t.Fatalf("%s: unexpected upload result %+v", tc.contentType, got)
		}
		if !media.ValidStorageKey(got.Media.StorageKey) {
			t.Fatalf("storage key is not server-generated: %q", got.Media.StorageKey)
		}
		_, body, err := m.svc.Content(t.Context(), m.active, got.Media.ID)
		if err != nil {
			t.Fatal(err)
		}
		stored, _ := io.ReadAll(body)
		_ = body.Close()
		if !bytes.Equal(stored, tc.data) {
			t.Fatal("stored bytes differ from the upload: the original must be kept unmodified")
		}
	}
	if n := len(m.published(t)); n != 2 {
		t.Fatalf("want 2 published objects, got %d", n)
	}
	if m.staged(t) != 0 {
		t.Fatal("staging not empty after successful uploads")
	}
}

func TestInvalidUploadsAreRejectedAndLeaveNothingBehind(t *testing.T) {
	m := newMediaFixture(t)
	jpg := testsupport.JPEG(t, 32, 32)
	cases := []struct {
		name, contentType string
		body              []byte
		want              error
	}{
		{"not an image", media.ContentTypeJPEG, []byte("plain text pretending to be a photo"), media.ErrInvalidImage},
		{"empty", media.ContentTypeJPEG, nil, media.ErrInvalidImage},
		{"truncated jpeg", media.ContentTypeJPEG, jpg[:len(jpg)/2], media.ErrInvalidImage},
		{"png bytes declared jpeg", media.ContentTypeJPEG, testsupport.PNG(t, 8, 8), media.ErrTypeMismatch},
		{"jpeg bytes declared png", media.ContentTypePNG, jpg, media.ErrTypeMismatch},
		{"unsupported declared type", "image/gif", jpg, media.ErrUnsupportedType},
		{"missing declared type", "", jpg, media.ErrUnsupportedType},
		{"huge claimed dimensions", media.ContentTypePNG, testsupport.PNGClaiming(t, 60000, 60000), media.ErrDimensions},
		{"too many pixels", media.ContentTypePNG, testsupport.PNGClaiming(t, 10000, 10000), media.ErrDimensions},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := m.svc.Upload(t.Context(), m.active, m.draft, tc.contentType, bytes.NewReader(tc.body))
			if !errors.Is(err, tc.want) {
				t.Fatalf("want %v, got %v", tc.want, err)
			}
		})
	}
	if n := len(m.published(t)); n != 0 {
		t.Fatalf("rejected uploads left %d published objects", n)
	}
	if m.staged(t) != 0 {
		t.Fatal("rejected uploads left temporary files")
	}
	if len(m.repo.rows) != 0 {
		t.Fatal("rejected uploads created metadata")
	}
}

func TestOversizedUploadIsRejectedWithoutTemporaryFiles(t *testing.T) {
	m := newMediaFixture(t)
	big := io.MultiReader(bytes.NewReader(testsupport.JPEG(t, 8, 8)), io.LimitReader(zeroReader{}, media.MaxBytes))
	if _, err := m.svc.Upload(t.Context(), m.active, m.draft, media.ContentTypeJPEG, big); !errors.Is(err, media.ErrTooLarge) {
		t.Fatalf("want ErrTooLarge, got %v", err)
	}
	if m.staged(t) != 0 || len(m.published(t)) != 0 {
		t.Fatal("oversized upload left files behind")
	}
}

type zeroReader struct{}

func (zeroReader) Read(p []byte) (int, error) {
	clear(p)
	return len(p), nil
}

// A database failure after the bytes were published must delete them, so no
// successful-looking photo exists without metadata.
func TestDatabaseFailureDoesNotOrphanPublishedMedia(t *testing.T) {
	m := newMediaFixture(t)
	m.repo.failAttach = errors.New("database unavailable")
	_, err := m.svc.Upload(t.Context(), m.active, m.draft, media.ContentTypeJPEG, bytes.NewReader(testsupport.JPEG(t, 16, 16)))
	if err == nil {
		t.Fatal("upload must fail when metadata cannot be recorded")
	}
	if n := len(m.published(t)); n != 0 {
		t.Fatalf("database failure orphaned %d published objects", n)
	}
	if m.staged(t) != 0 {
		t.Fatal("database failure left temporary files")
	}
}

// A publish (file system) failure must not record any metadata and must not
// leave temporary files behind.
func TestPublishFailureRecordsNoMediaRow(t *testing.T) {
	m := newMediaFixture(t)
	svc := application.NewMediaService(fakeUsers{"active": {ID: m.activeUserID, Status: domain.UserStatusActive}},
		m.fixture.repo, m.repo, failingCommitStore{mustStore(t, m.root)})
	_, err := svc.Upload(t.Context(), m.active, m.draft, media.ContentTypeJPEG, bytes.NewReader(testsupport.JPEG(t, 16, 16)))
	if err == nil {
		t.Fatal("upload must fail when the bytes cannot be published")
	}
	if items, _ := m.repo.ListByEncounter(t.Context(), m.draft); len(items) != 0 {
		t.Fatalf("publish failure recorded %d media rows", len(items))
	}
	if len(m.published(t)) != 0 || m.staged(t) != 0 {
		t.Fatal("publish failure left files behind")
	}
}

// failingCommitStore stages normally but refuses to publish.
type failingCommitStore struct{ *localmedia.Store }

func (s failingCommitStore) Stage(ctx context.Context, r io.Reader, limit int64) (application.StagedMedia, error) {
	staged, err := s.Store.Stage(ctx, r, limit)
	if err != nil {
		return nil, err
	}
	return failingCommit{staged}, nil
}

type failingCommit struct{ application.StagedMedia }

func (failingCommit) Commit(context.Context, string) error { return errors.New("disk full") }

func TestRetriedUploadIsIdempotent(t *testing.T) {
	m := newMediaFixture(t)
	data := testsupport.JPEG(t, 16, 16)
	first, err := m.svc.Upload(t.Context(), m.active, m.draft, media.ContentTypeJPEG, bytes.NewReader(data))
	if err != nil {
		t.Fatal(err)
	}
	again, err := m.svc.Upload(t.Context(), m.active, m.draft, media.ContentTypeJPEG, bytes.NewReader(data))
	if err != nil {
		t.Fatal(err)
	}
	if again.Created || again.Media.ID != first.Media.ID {
		t.Fatalf("retry must return the existing photo, got %+v", again)
	}
	if n := len(m.published(t)); n != 1 {
		t.Fatalf("retry must not keep a second copy, got %d objects", n)
	}
}

func TestPhotoLimitPerEncounter(t *testing.T) {
	m := newMediaFixture(t)
	for i := range media.MaxPhotosPerEncounter {
		if _, err := m.svc.Upload(t.Context(), m.active, m.draft, media.ContentTypePNG, bytes.NewReader(testsupport.PNG(t, i+1, 1))); err != nil {
			t.Fatal(err)
		}
	}
	_, err := m.svc.Upload(t.Context(), m.active, m.draft, media.ContentTypePNG, bytes.NewReader(testsupport.PNG(t, 99, 1)))
	if !errors.Is(err, media.ErrTooManyPhotos) {
		t.Fatalf("want ErrTooManyPhotos, got %v", err)
	}
	if n := len(m.published(t)); n != media.MaxPhotosPerEncounter {
		t.Fatalf("refused upload left an object: %d", n)
	}
}

func TestOnlyActiveOwnerCanUploadToADraft(t *testing.T) {
	m := newMediaFixture(t)
	data := testsupport.JPEG(t, 16, 16)
	upload := func(who domain.ExternalIdentity, encounterID string) error {
		_, err := m.svc.Upload(t.Context(), who, encounterID, media.ContentTypeJPEG, bytes.NewReader(data))
		return err
	}
	if err := upload(m.other, m.draft); !errors.Is(err, encounter.ErrNotFound) {
		t.Fatalf("another user must get not-found, got %v", err)
	}
	if err := upload(m.suspended, m.draft); !errors.Is(err, domain.ErrUserNotActive) {
		t.Fatalf("SUSPENDED user must not upload, got %v", err)
	}
	if err := upload(m.deleted, m.draft); !errors.Is(err, domain.ErrUserNotActive) {
		t.Fatalf("DELETED user must not upload, got %v", err)
	}
	if err := upload(m.notBootstrapped, m.draft); !errors.Is(err, application.ErrUserNotBootstrapped) {
		t.Fatalf("unknown user must be told to bootstrap, got %v", err)
	}
	for _, bad := range []string{"not-a-uuid", "../../etc/passwd", "00000000-0000-0000-0000-000000000000"} {
		if err := upload(m.active, bad); !errors.Is(err, encounter.ErrNotFound) {
			t.Fatalf("encounter %q: want not-found, got %v", bad, err)
		}
	}
	if _, _, err := m.fixture.svc.Submit(t.Context(), m.active, m.draft); err != nil {
		t.Fatal(err)
	}
	if err := upload(m.active, m.draft); !errors.Is(err, encounter.ErrNotEditable) {
		t.Fatalf("a SUBMITTED encounter must not receive media, got %v", err)
	}
	if len(m.published(t)) != 0 || m.staged(t) != 0 {
		t.Fatal("refused uploads left files behind")
	}
}

func TestContentAndListAreOwnerOnly(t *testing.T) {
	m := newMediaFixture(t)
	up, err := m.svc.Upload(t.Context(), m.active, m.draft, media.ContentTypeJPEG, bytes.NewReader(testsupport.JPEG(t, 16, 16)))
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = m.svc.Content(t.Context(), m.other, up.Media.ID); !errors.Is(err, media.ErrNotFound) {
		t.Fatalf("another user's photo must be not-found, got %v", err)
	}
	if _, err = m.svc.List(t.Context(), m.other, m.draft); !errors.Is(err, encounter.ErrNotFound) {
		t.Fatalf("another user's encounter media must be not-found, got %v", err)
	}
	for _, bad := range []string{"not-a-uuid", "00000000-0000-0000-0000-000000000000"} {
		if _, _, err = m.svc.Content(t.Context(), m.active, bad); !errors.Is(err, media.ErrNotFound) {
			t.Fatalf("media %q: want not-found, got %v", bad, err)
		}
	}
	items, err := m.svc.List(t.Context(), m.active, m.draft)
	if err != nil || len(items) != 1 || items[0].ID != up.Media.ID {
		t.Fatalf("owner list: %+v %v", items, err)
	}
}

// A SUSPENDED user keeps read access to their own data.
func TestSuspendedOwnerCanStillReadOwnMedia(t *testing.T) {
	m := newMediaFixture(t)
	up, err := m.svc.Upload(t.Context(), m.active, m.draft, media.ContentTypeJPEG, bytes.NewReader(testsupport.JPEG(t, 16, 16)))
	if err != nil {
		t.Fatal(err)
	}
	suspended := application.NewMediaService(fakeUsers{"active": {ID: m.activeUserID, Status: domain.UserStatusSuspended}},
		m.fixture.repo, m.repo, mustStore(t, m.root))
	if _, err = suspended.List(t.Context(), m.active, m.draft); err != nil {
		t.Fatal(err)
	}
	_, body, err := suspended.Content(t.Context(), m.active, up.Media.ID)
	if err != nil {
		t.Fatal(err)
	}
	_ = body.Close()
}

func mustStore(t *testing.T, root string) *localmedia.Store {
	t.Helper()
	s, err := localmedia.New(root)
	if err != nil {
		t.Fatal(err)
	}
	return s
}
