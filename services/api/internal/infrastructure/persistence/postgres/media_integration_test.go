package postgres_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/nawariso/toem-hia/services/api/internal/application"
	"github.com/nawariso/toem-hia/services/api/internal/domain"
	"github.com/nawariso/toem-hia/services/api/internal/domain/encounter"
	"github.com/nawariso/toem-hia/services/api/internal/domain/media"
	persistence "github.com/nawariso/toem-hia/services/api/internal/infrastructure/persistence/postgres"
	"github.com/nawariso/toem-hia/services/api/internal/infrastructure/persistence/postgres/migrations"
	"github.com/nawariso/toem-hia/services/api/internal/testsupport"
)

func photoFor(encounterID string, seed string) media.Media {
	sum := sha256.Sum256([]byte(seed))
	return media.NewPhoto(encounterID,
		media.Info{ContentType: media.ContentTypeJPEG, Width: 40, Height: 30},
		int64(len(seed)+1), hex.EncodeToString(sum[:]), time.Now())
}

func draftGuard(e encounter.Encounter) error {
	if e.Status != encounter.StatusDraft {
		return encounter.ErrNotEditable
	}
	return nil
}

// Requirement 003: 000001 -> 000002 -> 000003 up -> down -> up; Requirement
// 001 identity and Requirement 002 wildlife data survive every step.
func TestMediaMigrationUpgradesAndRollsBackWithoutTouchingWildlife(t *testing.T) {
	pool := testsupport.EmptyDatabase(t)
	ctx := t.Context()
	if err := migrations.UpTo(ctx, pool, 1); err != nil {
		t.Fatal(err)
	}
	identity := persistence.NewRepository(pool)
	u, err := identity.Bootstrap(ctx, domain.ExternalIdentity{Provider: "LOCAL_DEV", Subject: "developer-001"})
	if err != nil {
		t.Fatal(err)
	}
	if err = migrations.UpTo(ctx, pool, 2); err != nil {
		t.Fatal(err)
	}
	if tableExists(t, pool, "encounter_media") {
		t.Fatal("encounter_media exists at version 2")
	}
	e, err := encounter.NewDraft(u.ID, encounter.Details{CapturedAt: time.Now().Add(-time.Hour)}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	encounters := persistence.NewEncounterRepository(pool)
	if err = encounters.Create(ctx, e, nil); err != nil {
		t.Fatal(err)
	}
	check := func(step string, wantMedia bool) {
		t.Helper()
		if tableExists(t, pool, "encounter_media") != wantMedia {
			t.Fatalf("%s: encounter_media exists=%t", step, !wantMedia)
		}
		if _, err := encounters.FindByID(ctx, e.ID); err != nil {
			t.Fatalf("%s: encounter data lost: %v", step, err)
		}
		again, err := identity.Bootstrap(ctx, domain.ExternalIdentity{Provider: "LOCAL_DEV", Subject: "developer-001"})
		if err != nil || again.ID != u.ID {
			t.Fatalf("%s: identity data lost: %v %s != %s", step, err, again.ID, u.ID)
		}
	}
	for _, step := range []struct {
		name string
		run  func() error
		want bool
	}{
		{"up", func() error { return migrations.Up(ctx, pool) }, true},
		{"up again", func() error { return migrations.Up(ctx, pool) }, true},
		{"down to 2", func() error { return migrations.DownTo(ctx, pool, 2) }, false},
		{"re-up", func() error { return migrations.Up(ctx, pool) }, true},
	} {
		if err := step.run(); err != nil {
			t.Fatalf("%s: %v", step.name, err)
		}
		check(step.name, step.want)
	}
}

func TestMediaAttachListAndDatabaseConstraints(t *testing.T) {
	w := newWorld(t)
	ctx := t.Context()
	repo := persistence.NewMediaRepository(w.pool)
	e := newDraft(t, w, false)
	if err := w.encounters.Create(ctx, e, nil); err != nil {
		t.Fatal(err)
	}
	p := photoFor(e.ID, "one")
	stored, created, err := repo.Attach(ctx, p, media.MaxPhotosPerEncounter, draftGuard)
	if err != nil || !created || stored.ID != p.ID {
		t.Fatalf("attach: %v %t %+v", err, created, stored)
	}
	again, created, err := repo.Attach(ctx, photoFor(e.ID, "one"), media.MaxPhotosPerEncounter, draftGuard)
	if err != nil || created || again.ID != p.ID {
		t.Fatalf("same bytes must return the existing row: %v %t %+v", err, created, again)
	}
	found, err := repo.FindByID(ctx, p.ID)
	if err != nil || found.StorageKey != p.StorageKey || found.SHA256 != p.SHA256 || found.Width != 40 {
		t.Fatalf("round trip: %v %+v", err, found)
	}
	if _, err = repo.FindByID(ctx, uuid.NewString()); !errors.Is(err, media.ErrNotFound) {
		t.Fatalf("missing media: %v", err)
	}
	items, err := repo.ListByEncounter(ctx, e.ID)
	if err != nil || len(items) != 1 {
		t.Fatalf("list: %v %d", err, len(items))
	}
	if _, _, err = repo.Attach(ctx, photoFor(uuid.NewString(), "x"), 5, draftGuard); !errors.Is(err, encounter.ErrNotFound) {
		t.Fatalf("attach to a missing encounter: %v", err)
	}

	bad := []struct {
		name, sql string
	}{
		{"kind", `UPDATE encounter_media SET kind='VIDEO' WHERE id=$1`},
		{"status", `UPDATE encounter_media SET status='PUBLIC' WHERE id=$1`},
		{"immutable sha", `UPDATE encounter_media SET sha256=repeat('b',64) WHERE id=$1`},
		{"immutable key", `UPDATE encounter_media SET storage_key='photos/00000000-0000-0000-0000-000000000000.jpg' WHERE id=$1`},
		{"immutable size", `UPDATE encounter_media SET byte_size=2 WHERE id=$1`},
	}
	for _, b := range bad {
		if _, err = w.pool.Exec(ctx, b.sql, p.ID); err == nil {
			t.Fatalf("database accepted invalid %s", b.name)
		}
	}
	insert := `INSERT INTO encounter_media (id, encounter_id, kind, status, content_type, byte_size, width, height, sha256, storage_key)
		VALUES ($1, $2, 'PHOTO', 'READY', $3, $4, $5, $6, $7, $8)`
	sha := func(i int) string { s := sha256.Sum256([]byte("c" + strconv.Itoa(i))); return hex.EncodeToString(s[:]) }
	key := func(id, ext string) string { return "photos/" + id + "." + ext }
	rejects := []struct {
		name        string
		contentType string
		size        int64
		w, h        int
		sha, key    string
	}{
		{"gif", "image/gif", 10, 1, 1, sha(1), ""},
		{"zero size", "image/jpeg", 0, 1, 1, sha(2), ""},
		{"over 15 MiB", "image/jpeg", media.MaxBytes + 1, 1, 1, sha(3), ""},
		{"edge", "image/jpeg", 10, 12001, 1, sha(4), ""},
		{"pixels", "image/jpeg", 10, 10000, 10000, sha(5), ""},
		{"sha format", "image/jpeg", 10, 1, 1, "NOT-A-HASH", ""},
		{"traversal key", "image/jpeg", 10, 1, 1, sha(6), "../../etc/passwd"},
		{"absolute key", "image/jpeg", 10, 1, 1, sha(7), "/srv/media/x.jpg"},
		{"duplicate key", "image/jpeg", 10, 1, 1, sha(8), p.StorageKey},
		{"duplicate hash", "image/jpeg", 10, 1, 1, p.SHA256, ""},
	}
	for _, r := range rejects {
		id := uuid.NewString()
		k := r.key
		if k == "" {
			k = key(id, "jpg")
		}
		if _, err = w.pool.Exec(ctx, insert, id, e.ID, r.contentType, r.size, r.w, r.h, r.sha, k); err == nil {
			t.Fatalf("database accepted invalid row: %s", r.name)
		}
	}
	if _, err = w.pool.Exec(ctx, `DELETE FROM encounters WHERE id=$1`, e.ID); err == nil {
		t.Fatal("an encounter with photos must not be deletable")
	}
}

func TestConcurrentAttachNeverExceedsThePhotoLimit(t *testing.T) {
	w := newWorld(t)
	repo := persistence.NewMediaRepository(w.pool)
	e := newDraft(t, w, false)
	if err := w.encounters.Create(t.Context(), e, nil); err != nil {
		t.Fatal(err)
	}
	const n = 20
	errs := make([]error, n)
	var wg sync.WaitGroup
	for i := range n {
		wg.Go(func() {
			_, _, errs[i] = repo.Attach(context.Background(), photoFor(e.ID, "p"+strconv.Itoa(i)), media.MaxPhotosPerEncounter, draftGuard)
		})
	}
	wg.Wait()
	ok, limited := 0, 0
	for _, err := range errs {
		switch {
		case err == nil:
			ok++
		case errors.Is(err, media.ErrTooManyPhotos):
			limited++
		default:
			t.Fatalf("unexpected error: %v", err)
		}
	}
	if ok != media.MaxPhotosPerEncounter || limited != n-media.MaxPhotosPerEncounter {
		t.Fatalf("want %d attached and %d refused, got %d and %d", media.MaxPhotosPerEncounter, n-media.MaxPhotosPerEncounter, ok, limited)
	}
}

// Attach and Submit lock the same encounter row, so a photo can never land on
// an encounter that has already left DRAFT.
func TestAttachAfterSubmitIsRefused(t *testing.T) {
	w := newWorld(t)
	ctx := t.Context()
	repo := persistence.NewMediaRepository(w.pool)
	e := newDraft(t, w, false)
	if err := w.encounters.Create(ctx, e, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := w.pool.Exec(ctx, `UPDATE encounters SET status='SUBMITTED', submitted_at=now() WHERE id=$1`, e.ID); err != nil {
		t.Fatal(err)
	}
	if _, _, err := repo.Attach(ctx, photoFor(e.ID, "late"), media.MaxPhotosPerEncounter, draftGuard); !errors.Is(err, encounter.ErrNotEditable) {
		t.Fatalf("attach to a SUBMITTED encounter: %v", err)
	}
	var count int
	if err := w.pool.QueryRow(ctx, `SELECT count(*) FROM encounter_media`).Scan(&count); err != nil || count != 0 {
		t.Fatalf("refused attach wrote a row: %v %d", err, count)
	}
}

// Attach and Submit racing on the same encounter: whichever takes the row
// lock first wins. A photo is either attached while DRAFT (and the submit that
// follows sees it) or refused; it is never attached after the submit.
func TestConcurrentAttachAndSubmitRespectDraftRule(t *testing.T) {
	w := newWorld(t)
	ctx := t.Context()
	repo := persistence.NewMediaRepository(w.pool)
	for i := range 25 {
		e := newDraft(t, w, false)
		if err := w.encounters.Create(ctx, e, nil); err != nil {
			t.Fatal(err)
		}
		var attachErr, submitErr error
		var submittedAt time.Time
		start := make(chan struct{})
		var wg sync.WaitGroup
		wg.Go(func() {
			<-start
			_, _, attachErr = repo.Attach(context.Background(), photoFor(e.ID, "race"+strconv.Itoa(i)), media.MaxPhotosPerEncounter, draftGuard)
		})
		wg.Go(func() {
			<-start
			var updated encounter.Encounter
			updated, submitErr = w.encounters.Update(context.Background(), e.ID, func(x *encounter.Encounter) (application.LocationChange, error) {
				_, err := x.Submit(time.Now())
				return application.LocationChange{}, err
			})
			if updated.SubmittedAt != nil {
				submittedAt = *updated.SubmittedAt
			}
		})
		close(start)
		wg.Wait()
		if submitErr != nil || submittedAt.IsZero() {
			t.Fatalf("submit: %v", submitErr)
		}
		var count int
		if err := w.pool.QueryRow(ctx, `SELECT count(*) FROM encounter_media WHERE encounter_id=$1`, e.ID).Scan(&count); err != nil {
			t.Fatal(err)
		}
		switch {
		case attachErr == nil && count == 1:
		case errors.Is(attachErr, encounter.ErrNotEditable) && count == 0:
		default:
			t.Fatalf("iteration %d: attach=%v rows=%d", i, attachErr, count)
		}
	}
}
