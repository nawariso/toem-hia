package application

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/nawariso/toem-hia/services/api/internal/domain"
	"github.com/nawariso/toem-hia/services/api/internal/domain/encounter"
	"github.com/nawariso/toem-hia/services/api/internal/domain/hia"
	"github.com/nawariso/toem-hia/services/api/internal/domain/location"
	"github.com/nawariso/toem-hia/services/api/internal/domain/park"
)

var (
	// ErrUserNotBootstrapped is returned when a verified identity has no
	// internal user yet (POST /v1/auth/bootstrap has not been called).
	ErrUserNotBootstrapped = errors.New("user must be bootstrapped first")
	// ErrInvalidParkFilter is returned for a malformed parkId filter.
	ErrInvalidParkFilter = errors.New("parkId must be a UUID")
)

// Optional distinguishes an absent JSON field (Set=false) from an explicit
// null (Set=true, Value=nil) in partial updates.
type Optional[T any] struct {
	Set   bool
	Value *T
}

// ---------------------------------------------------------------- parks ---

type ParkService struct{ repo ParkRepository }

func NewParkService(repo ParkRepository) *ParkService { return &ParkService{repo: repo} }

func (s *ParkService) ListParks(ctx context.Context) ([]park.Park, error) {
	return s.repo.ListActiveParks(ctx)
}

func (s *ParkService) GetPark(ctx context.Context, id string) (park.Park, error) {
	if !isUUID(id) {
		return park.Park{}, park.ErrNotFound
	}
	return s.repo.FindActivePark(ctx, id)
}

func (s *ParkService) ListZones(ctx context.Context, parkID string) ([]park.Zone, error) {
	if _, err := s.GetPark(ctx, parkID); err != nil {
		return nil, err
	}
	return s.repo.ListActiveZones(ctx, parkID)
}

// ----------------------------------------------------------------- hias ---

// HiaView is a hia plus the public code of its merge target, so responses
// never need the target's internal ID.
type HiaView struct {
	Hia                  hia.Hia
	MergedIntoPublicCode *string
}

type HiaService struct{ repo HiaRepository }

func NewHiaService(repo HiaRepository) *HiaService { return &HiaService{repo: repo} }

func (s *HiaService) List(ctx context.Context, homeParkID *string) ([]HiaView, error) {
	if homeParkID != nil && !isUUID(*homeParkID) {
		return nil, ErrInvalidParkFilter
	}
	hias, err := s.repo.List(ctx, homeParkID)
	if err != nil {
		return nil, err
	}
	return s.views(ctx, hias)
}

func (s *HiaService) Get(ctx context.Context, publicCode string) (HiaView, error) {
	if hia.ValidatePublicCode(publicCode) != nil {
		return HiaView{}, hia.ErrNotFound
	}
	h, err := s.repo.FindByPublicCode(ctx, publicCode)
	if err != nil {
		return HiaView{}, err
	}
	views, err := s.views(ctx, []hia.Hia{h})
	if err != nil {
		return HiaView{}, err
	}
	return views[0], nil
}

func (s *HiaService) views(ctx context.Context, hias []hia.Hia) ([]HiaView, error) {
	targets := []string{}
	for _, h := range hias {
		if h.MergedIntoHiaID != nil {
			targets = append(targets, *h.MergedIntoHiaID)
		}
	}
	codes := map[string]string{}
	if len(targets) > 0 {
		var err error
		if codes, err = s.repo.PublicCodes(ctx, targets); err != nil {
			return nil, err
		}
	}
	views := make([]HiaView, 0, len(hias))
	for _, h := range hias {
		v := HiaView{Hia: h}
		if h.MergedIntoHiaID != nil {
			if code, ok := codes[*h.MergedIntoHiaID]; ok {
				v.MergedIntoPublicCode = &code
			}
		}
		views = append(views, v)
	}
	return views, nil
}

// ----------------------------------------------------------- encounters ---

// EncounterView is the owner-facing read model: the encounter plus the
// coarse public location (park and zone). It never carries coordinates.
type EncounterView struct {
	Encounter encounter.Encounter
	Park      *park.Park
	Zone      *park.Zone
}

// CreateEncounterInput is everything a client may supply on creation.
// There is no observer, status, or hia field by construction.
type CreateEncounterInput struct {
	Details  encounter.Details
	Location *location.Private
}

// EncounterPatch is a partial update of a DRAFT encounter.
type EncounterPatch struct {
	CapturedAt *time.Time
	ParkID     Optional[string]
	ZoneID     Optional[string]
	Behavior   Optional[string]
	Notes      Optional[string]
	Location   LocationChange
}

type EncounterService struct {
	users CurrentUserResolver
	parks ParkRepository
	repo  EncounterRepository
	now   func() time.Time
}

func NewEncounterService(users CurrentUserResolver, parks ParkRepository, repo EncounterRepository) *EncounterService {
	return &EncounterService{users: users, parks: parks, repo: repo, now: time.Now}
}

// Create records a DRAFT encounter observed by the authenticated user.
func (s *EncounterService) Create(ctx context.Context, identity domain.ExternalIdentity, in CreateEncounterInput) (EncounterView, error) {
	user, err := s.writer(ctx, identity)
	if err != nil {
		return EncounterView{}, err
	}
	if in.Location != nil {
		if err = in.Location.Validate(); err != nil {
			return EncounterView{}, err
		}
	}
	e, err := encounter.NewDraft(user.ID, in.Details, s.now())
	if err != nil {
		return EncounterView{}, err
	}
	if err = s.checkPlace(ctx, e.ParkID, e.ZoneID); err != nil {
		return EncounterView{}, err
	}
	if err = s.repo.Create(ctx, e, in.Location); err != nil {
		return EncounterView{}, err
	}
	return s.view(ctx, e)
}

// Get returns the caller's own encounter. Another user's encounter is
// reported as not found so its existence is not disclosed.
func (s *EncounterService) Get(ctx context.Context, identity domain.ExternalIdentity, id string) (EncounterView, error) {
	user, err := s.reader(ctx, identity)
	if err != nil {
		return EncounterView{}, err
	}
	if !isUUID(id) {
		return EncounterView{}, encounter.ErrNotFound
	}
	e, err := s.repo.FindByID(ctx, id)
	if err != nil {
		return EncounterView{}, err
	}
	if !e.OwnedBy(user.ID) {
		return EncounterView{}, encounter.ErrNotFound
	}
	return s.view(ctx, e)
}

// ListMine returns the caller's encounters, most recently captured first.
func (s *EncounterService) ListMine(ctx context.Context, identity domain.ExternalIdentity) ([]EncounterView, error) {
	user, err := s.reader(ctx, identity)
	if err != nil {
		return nil, err
	}
	list, err := s.repo.ListByObserver(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	return s.views(ctx, list)
}

// Update edits the caller's own DRAFT encounter.
func (s *EncounterService) Update(ctx context.Context, identity domain.ExternalIdentity, id string, patch EncounterPatch) (EncounterView, error) {
	user, err := s.writer(ctx, identity)
	if err != nil {
		return EncounterView{}, err
	}
	if !isUUID(id) {
		return EncounterView{}, encounter.ErrNotFound
	}
	if patch.Location.Set && patch.Location.Value != nil {
		if err = patch.Location.Value.Validate(); err != nil {
			return EncounterView{}, err
		}
	}
	updated, err := s.repo.Update(ctx, id, func(e *encounter.Encounter) (LocationChange, error) {
		if !e.OwnedBy(user.ID) {
			return LocationChange{}, encounter.ErrNotFound
		}
		d := mergeDetails(*e, patch)
		if err := e.Edit(d, s.now()); err != nil {
			return LocationChange{}, err
		}
		if err := s.checkPlace(ctx, e.ParkID, e.ZoneID); err != nil {
			return LocationChange{}, err
		}
		return patch.Location, nil
	})
	if err != nil {
		return EncounterView{}, err
	}
	return s.view(ctx, updated)
}

// Submit moves the caller's own encounter DRAFT -> SUBMITTED. Re-submitting a
// SUBMITTED encounter succeeds without changing it.
func (s *EncounterService) Submit(ctx context.Context, identity domain.ExternalIdentity, id string) (EncounterView, bool, error) {
	user, err := s.writer(ctx, identity)
	if err != nil {
		return EncounterView{}, false, err
	}
	if !isUUID(id) {
		return EncounterView{}, false, encounter.ErrNotFound
	}
	changed := false
	updated, err := s.repo.Update(ctx, id, func(e *encounter.Encounter) (LocationChange, error) {
		if !e.OwnedBy(user.ID) {
			return LocationChange{}, encounter.ErrNotFound
		}
		var err error
		changed, err = e.Submit(s.now())
		return LocationChange{}, err
	})
	if err != nil {
		return EncounterView{}, false, err
	}
	view, err := s.view(ctx, updated)
	return view, changed, err
}

// reader resolves the internal user through the identity boundary.
func (s *EncounterService) reader(ctx context.Context, identity domain.ExternalIdentity) (domain.User, error) {
	return readerUser(ctx, s.users, identity)
}

// writer additionally requires User.status = ACTIVE for protected writes.
func (s *EncounterService) writer(ctx context.Context, identity domain.ExternalIdentity) (domain.User, error) {
	return writerUser(ctx, s.users, identity)
}

// readerUser resolves the internal user through the identity boundary.
func readerUser(ctx context.Context, users CurrentUserResolver, identity domain.ExternalIdentity) (domain.User, error) {
	if err := domain.ValidateIdentity(identity); err != nil {
		return domain.User{}, err
	}
	user, err := users.Current(ctx, identity)
	if errors.Is(err, ErrNotFound) {
		return domain.User{}, ErrUserNotBootstrapped
	}
	return user, err
}

// writerUser additionally requires User.status = ACTIVE.
func writerUser(ctx context.Context, users CurrentUserResolver, identity domain.ExternalIdentity) (domain.User, error) {
	user, err := readerUser(ctx, users, identity)
	if err != nil {
		return domain.User{}, err
	}
	if !user.CanWrite() {
		return domain.User{}, domain.ErrUserNotActive
	}
	return user, nil
}

// checkPlace verifies that referenced parks/zones exist and that the zone
// belongs to the park. The composite foreign key enforces the same rule.
func (s *EncounterService) checkPlace(ctx context.Context, parkID, zoneID *string) error {
	if parkID == nil {
		return nil
	}
	parks, err := s.parks.FindParks(ctx, []string{*parkID})
	if err != nil {
		return err
	}
	if _, ok := parks[*parkID]; !ok {
		return encounter.ErrParkNotFound
	}
	if zoneID == nil {
		return nil
	}
	zones, err := s.parks.FindZones(ctx, []string{*zoneID})
	if err != nil {
		return err
	}
	zone, ok := zones[*zoneID]
	if !ok {
		return encounter.ErrZoneNotFound
	}
	if zone.ParkID != *parkID {
		return encounter.ErrZoneNotInPark
	}
	return nil
}

func (s *EncounterService) view(ctx context.Context, e encounter.Encounter) (EncounterView, error) {
	views, err := s.views(ctx, []encounter.Encounter{e})
	if err != nil {
		return EncounterView{}, err
	}
	return views[0], nil
}

func (s *EncounterService) views(ctx context.Context, list []encounter.Encounter) ([]EncounterView, error) {
	parkIDs, zoneIDs := []string{}, []string{}
	for _, e := range list {
		if e.ParkID != nil {
			parkIDs = append(parkIDs, *e.ParkID)
		}
		if e.ZoneID != nil {
			zoneIDs = append(zoneIDs, *e.ZoneID)
		}
	}
	parks, zones := map[string]park.Park{}, map[string]park.Zone{}
	var err error
	if len(parkIDs) > 0 {
		if parks, err = s.parks.FindParks(ctx, parkIDs); err != nil {
			return nil, err
		}
	}
	if len(zoneIDs) > 0 {
		if zones, err = s.parks.FindZones(ctx, zoneIDs); err != nil {
			return nil, err
		}
	}
	views := make([]EncounterView, 0, len(list))
	for _, e := range list {
		v := EncounterView{Encounter: e}
		if e.ParkID != nil {
			if p, ok := parks[*e.ParkID]; ok {
				v.Park = &p
			}
		}
		if e.ZoneID != nil {
			if z, ok := zones[*e.ZoneID]; ok {
				v.Zone = &z
			}
		}
		views = append(views, v)
	}
	return views, nil
}

func mergeDetails(e encounter.Encounter, p EncounterPatch) encounter.Details {
	d := encounter.Details{CapturedAt: e.CapturedAt, ParkID: e.ParkID, ZoneID: e.ZoneID, Behavior: e.Behavior, Notes: e.Notes}
	if p.CapturedAt != nil {
		d.CapturedAt = *p.CapturedAt
	}
	if p.ParkID.Set {
		d.ParkID = p.ParkID.Value
	}
	if p.ZoneID.Set {
		d.ZoneID = p.ZoneID.Value
	}
	if p.Behavior.Set {
		d.Behavior = p.Behavior.Value
	}
	if p.Notes.Set {
		d.Notes = p.Notes.Value
	}
	return d
}

func isUUID(s string) bool {
	_, err := uuid.Parse(s)
	return err == nil && len(s) == 36
}
