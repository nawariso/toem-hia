package application

import (
	"context"
	"errors"
	"io"

	"github.com/nawariso/toem-hia/services/api/internal/domain"
	"github.com/nawariso/toem-hia/services/api/internal/domain/encounter"
	"github.com/nawariso/toem-hia/services/api/internal/domain/hia"
	"github.com/nawariso/toem-hia/services/api/internal/domain/location"
	"github.com/nawariso/toem-hia/services/api/internal/domain/media"
	"github.com/nawariso/toem-hia/services/api/internal/domain/park"
)

var ErrNotFound = errors.New("user not found")

type IdentityVerifier interface {
	Verify(context.Context, string) (domain.ExternalIdentity, error)
}
type UserRepository interface {
	Bootstrap(context.Context, domain.ExternalIdentity) (domain.User, error)
	FindByIdentity(context.Context, domain.ExternalIdentity) (domain.User, error)
	UpdateProfile(context.Context, domain.ExternalIdentity, domain.ProfilePatch) (domain.User, error)
}
type Readiness interface{ Ping(context.Context) error }
type Users interface {
	Bootstrap(context.Context, domain.ExternalIdentity) (domain.User, error)
	Current(context.Context, domain.ExternalIdentity) (domain.User, error)
	UpdateProfile(context.Context, domain.ExternalIdentity, domain.ProfilePatch) (domain.User, error)
}

// CurrentUserResolver is the identity/user boundary the wildlife modules use
// to turn a verified external identity into the internal user. Wildlife code
// never reads auth_identities itself.
type CurrentUserResolver interface {
	Current(context.Context, domain.ExternalIdentity) (domain.User, error)
}

// Parks, Hias, and Encounters are the use cases the HTTP transport depends on.
type Parks interface {
	ListParks(context.Context) ([]park.Park, error)
	GetPark(ctx context.Context, id string) (park.Park, error)
	ListZones(ctx context.Context, parkID string) ([]park.Zone, error)
}
type Hias interface {
	List(ctx context.Context, homeParkID *string) ([]HiaView, error)
	Get(ctx context.Context, publicCode string) (HiaView, error)
}
type Encounters interface {
	Create(context.Context, domain.ExternalIdentity, CreateEncounterInput) (EncounterView, error)
	Get(ctx context.Context, identity domain.ExternalIdentity, id string) (EncounterView, error)
	ListMine(context.Context, domain.ExternalIdentity) ([]EncounterView, error)
	Update(ctx context.Context, identity domain.ExternalIdentity, id string, patch EncounterPatch) (EncounterView, error)
	Submit(ctx context.Context, identity domain.ExternalIdentity, id string) (EncounterView, bool, error)
}

// ParkRepository owns parks and zones.
type ParkRepository interface {
	CreatePark(context.Context, park.Park) error
	CreateZone(context.Context, park.Zone) error
	ListActiveParks(context.Context) ([]park.Park, error)
	FindActivePark(ctx context.Context, id string) (park.Park, error)
	ListActiveZones(ctx context.Context, parkID string) ([]park.Zone, error)
	// FindParks and FindZones resolve references regardless of status and
	// omit unknown IDs from the result.
	FindParks(ctx context.Context, ids []string) (map[string]park.Park, error)
	FindZones(ctx context.Context, ids []string) (map[string]park.Zone, error)
}

// HiaRepository owns hias. Create allocates the public code; there is no
// public create/update API in Requirement 002.
type HiaRepository interface {
	Create(context.Context, hia.Hia) (hia.Hia, error)
	List(ctx context.Context, homeParkID *string) ([]hia.Hia, error)
	FindByPublicCode(ctx context.Context, code string) (hia.Hia, error)
	// PublicCodes maps internal hia IDs to public codes, for rendering
	// merge targets without exposing internal IDs.
	PublicCodes(ctx context.Context, ids []string) (map[string]string, error)
}

// LocationChange describes what an encounter edit does to the private
// location: leave it (Set=false), replace it (Value!=nil), or remove it.
type LocationChange struct {
	Set   bool
	Value *location.Private
}

// EncounterRepository owns encounters and their private locations.
type EncounterRepository interface {
	Create(ctx context.Context, e encounter.Encounter, loc *location.Private) error
	FindByID(ctx context.Context, id string) (encounter.Encounter, error)
	ListByObserver(ctx context.Context, observerUserID string) ([]encounter.Encounter, error)
	// Update locks the encounter row, runs mutate on it, and persists the
	// result and the location change in one transaction. mutate's error
	// aborts the transaction.
	Update(ctx context.Context, id string, mutate func(*encounter.Encounter) (LocationChange, error)) (encounter.Encounter, error)
}

// EncounterMedia is the Requirement 003 private photo use case the HTTP
// transport depends on.
type EncounterMedia interface {
	Upload(ctx context.Context, identity domain.ExternalIdentity, encounterID, contentType string, body io.Reader) (MediaUpload, error)
	List(ctx context.Context, identity domain.ExternalIdentity, encounterID string) ([]media.Media, error)
	Content(ctx context.Context, identity domain.ExternalIdentity, mediaID string) (media.Media, io.ReadCloser, error)
}

// MediaRepository owns media metadata (Media module table only).
type MediaRepository interface {
	// Attach locks the encounter row, runs guard on it, and records m in the
	// same transaction, so an encounter cannot leave DRAFT between the check
	// and the insert. If the encounter already has a photo with the same
	// SHA-256, that photo is returned with created=false and m is not stored
	// (a retried upload is idempotent). More than max photos per encounter is
	// refused with media.ErrTooManyPhotos.
	Attach(ctx context.Context, m media.Media, max int, guard func(encounter.Encounter) error) (stored media.Media, created bool, err error)
	ListByEncounter(ctx context.Context, encounterID string) ([]media.Media, error)
	FindByID(ctx context.Context, id string) (media.Media, error)
}

// MediaStore keeps media bytes under server-generated keys. It is the
// replaceable edge: the local filesystem adapter serves development and
// tests, and a future object-storage adapter implements the same contract.
// Application code never sees a filesystem path or bucket.
type MediaStore interface {
	// Stage copies at most limit bytes from r into a private temporary
	// object while computing its size and SHA-256. More than limit bytes is
	// media.ErrTooLarge and nothing is kept.
	Stage(ctx context.Context, r io.Reader, limit int64) (StagedMedia, error)
	// Open reads a published object. Keys must be media.ValidStorageKey.
	Open(ctx context.Context, key string) (io.ReadCloser, error)
	// Delete removes a published object; a missing object is not an error.
	Delete(ctx context.Context, key string) error
}

// StagedMedia is an upload that has been received but not yet published.
type StagedMedia interface {
	Size() int64
	SHA256() string
	// Open reads the staged bytes, for validation before publishing.
	Open() (io.ReadCloser, error)
	// Commit atomically publishes the staged bytes under key.
	Commit(ctx context.Context, key string) error
	// Discard removes the staged bytes. It is safe after Commit (no-op).
	Discard() error
}
