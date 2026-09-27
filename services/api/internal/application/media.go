package application

import (
	"context"
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/nawariso/toem-hia/services/api/internal/domain"
	"github.com/nawariso/toem-hia/services/api/internal/domain/encounter"
	"github.com/nawariso/toem-hia/services/api/internal/domain/media"
)

// MediaUpload is the result of an upload: the stored photo, the owner (for
// logging), and whether this request created it (false for an idempotent
// retry of the same bytes).
type MediaUpload struct {
	Media   media.Media
	UserID  string
	Created bool
}

// MediaService attaches private photos to the caller's own DRAFT encounters
// and serves them back to the owner only. Every photo is PRIVATE; there is no
// public media path in Requirement 003.
type MediaService struct {
	users      CurrentUserResolver
	encounters EncounterRepository
	repo       MediaRepository
	store      MediaStore
	now        func() time.Time
}

func NewMediaService(users CurrentUserResolver, encounters EncounterRepository, repo MediaRepository, store MediaStore) *MediaService {
	return &MediaService{users: users, encounters: encounters, repo: repo, store: store, now: time.Now}
}

// Upload validates the photo and attaches it to the caller's DRAFT
// encounter. The order is deliberate:
//
//  1. authorise (ACTIVE owner, DRAFT) before reading the body;
//  2. stage the bytes to a temporary object, enforcing the size limit and
//     computing size and SHA-256 server-side;
//  3. validate the staged bytes (sniffed format, declared type, dimensions,
//     full decode) — the client's type, filename, or path prove nothing;
//  4. publish the bytes under a server-generated key;
//  5. record metadata in the same transaction that re-checks ownership and
//     DRAFT. If recording fails the published bytes are deleted, so a
//     database failure never leaves a successful-looking photo behind.
func (s *MediaService) Upload(ctx context.Context, identity domain.ExternalIdentity, encounterID, contentType string, body io.Reader) (MediaUpload, error) {
	user, err := writerUser(ctx, s.users, identity)
	if err != nil {
		return MediaUpload{}, err
	}
	guard := func(e encounter.Encounter) error {
		if !e.OwnedBy(user.ID) {
			return encounter.ErrNotFound
		}
		if e.Status != encounter.StatusDraft {
			return encounter.ErrNotEditable
		}
		return nil
	}
	if !isUUID(encounterID) {
		return MediaUpload{}, encounter.ErrNotFound
	}
	e, err := s.encounters.FindByID(ctx, encounterID)
	if err != nil {
		return MediaUpload{}, err
	}
	if err = guard(e); err != nil {
		return MediaUpload{}, err
	}
	declared, err := media.DeclaredType(contentType)
	if err != nil {
		return MediaUpload{}, err
	}

	staged, err := s.store.Stage(ctx, body, media.MaxBytes)
	if err != nil {
		return MediaUpload{}, err
	}
	defer func() { _ = staged.Discard() }()
	if staged.Size() == 0 {
		return MediaUpload{}, media.ErrInvalidImage
	}
	info, err := media.Inspect(staged.Open)
	if err != nil {
		return MediaUpload{}, err
	}
	if info.ContentType != declared {
		return MediaUpload{}, media.ErrTypeMismatch
	}

	photo := media.NewPhoto(encounterID, info, staged.Size(), staged.SHA256(), s.now())
	if err = staged.Commit(ctx, photo.StorageKey); err != nil {
		return MediaUpload{}, fmt.Errorf("publish media: %w", err)
	}
	stored, created, err := s.repo.Attach(ctx, photo, media.MaxPhotosPerEncounter, guard)
	if err != nil || !created {
		// Not recorded (failure) or a duplicate of an existing photo: the
		// bytes just published are not referenced by any metadata.
		if cleanupErr := s.store.Delete(context.WithoutCancel(ctx), photo.StorageKey); cleanupErr != nil && err == nil {
			err = fmt.Errorf("discard duplicate media: %w", cleanupErr)
		}
	}
	if err != nil {
		return MediaUpload{}, err
	}
	return MediaUpload{Media: stored, UserID: user.ID, Created: created}, nil
}

// List returns the photos of the caller's own encounter. A SUSPENDED user can
// still read their own data.
func (s *MediaService) List(ctx context.Context, identity domain.ExternalIdentity, encounterID string) ([]media.Media, error) {
	if _, err := s.ownedEncounter(ctx, identity, encounterID); err != nil {
		return nil, err
	}
	return s.repo.ListByEncounter(ctx, encounterID)
}

// Content opens the bytes of one of the caller's own photos. Another user's
// photo is reported as not found so its existence is not disclosed.
func (s *MediaService) Content(ctx context.Context, identity domain.ExternalIdentity, mediaID string) (media.Media, io.ReadCloser, error) {
	user, err := readerUser(ctx, s.users, identity)
	if err != nil {
		return media.Media{}, nil, err
	}
	if !isUUID(mediaID) {
		return media.Media{}, nil, media.ErrNotFound
	}
	m, err := s.repo.FindByID(ctx, mediaID)
	if err != nil {
		return media.Media{}, nil, err
	}
	e, err := s.encounters.FindByID(ctx, m.EncounterID)
	if errors.Is(err, encounter.ErrNotFound) || (err == nil && !e.OwnedBy(user.ID)) {
		return media.Media{}, nil, media.ErrNotFound
	}
	if err != nil {
		return media.Media{}, nil, err
	}
	if m.Status != media.StatusReady {
		return media.Media{}, nil, media.ErrNotFound
	}
	r, err := s.store.Open(ctx, m.StorageKey)
	if err != nil {
		return media.Media{}, nil, fmt.Errorf("open media: %w", err)
	}
	return m, r, nil
}

func (s *MediaService) ownedEncounter(ctx context.Context, identity domain.ExternalIdentity, encounterID string) (encounter.Encounter, error) {
	user, err := readerUser(ctx, s.users, identity)
	if err != nil {
		return encounter.Encounter{}, err
	}
	if !isUUID(encounterID) {
		return encounter.Encounter{}, encounter.ErrNotFound
	}
	e, err := s.encounters.FindByID(ctx, encounterID)
	if err != nil {
		return encounter.Encounter{}, err
	}
	if !e.OwnedBy(user.ID) {
		return encounter.Encounter{}, encounter.ErrNotFound
	}
	return e, nil
}
