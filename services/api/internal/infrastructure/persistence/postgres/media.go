package postgres

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/nawariso/toem-hia/services/api/internal/application"
	"github.com/nawariso/toem-hia/services/api/internal/domain/encounter"
	"github.com/nawariso/toem-hia/services/api/internal/domain/media"
)

// MediaRepository persists photo metadata (Media module table only). It
// reads the encounters row solely to lock it for the ownership/DRAFT guard.
type MediaRepository struct{ pool *pgxpool.Pool }

var _ application.MediaRepository = (*MediaRepository)(nil)

func NewMediaRepository(pool *pgxpool.Pool) *MediaRepository {
	return &MediaRepository{pool: pool}
}

const mediaColumns = `id::text, encounter_id::text, kind, status, content_type, byte_size, width, height,
	sha256, storage_key, created_at`

// Attach locks the encounter row (the same lock EncounterRepository.Update
// takes), so a concurrent submit cannot move the encounter out of DRAFT
// between the guard and the insert, and concurrent uploads to one encounter
// are counted one at a time.
func (r *MediaRepository) Attach(ctx context.Context, m media.Media, maxPhotos int, guard func(encounter.Encounter) error) (media.Media, bool, error) {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return media.Media{}, false, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	e, err := scanEncounter(tx.QueryRow(ctx, `SELECT `+encounterColumns+` FROM encounters WHERE id = $1 FOR UPDATE`, m.EncounterID))
	if errors.Is(err, pgx.ErrNoRows) {
		return media.Media{}, false, encounter.ErrNotFound
	}
	if err != nil {
		return media.Media{}, false, err
	}
	if err = guard(e); err != nil {
		return media.Media{}, false, err
	}
	existing, err := scanMedia(tx.QueryRow(ctx, `SELECT `+mediaColumns+` FROM encounter_media
		WHERE encounter_id = $1 AND sha256 = $2`, m.EncounterID, m.SHA256))
	if err == nil {
		return existing, false, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return media.Media{}, false, err
	}
	var count int
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM encounter_media WHERE encounter_id = $1`, m.EncounterID).Scan(&count); err != nil {
		return media.Media{}, false, err
	}
	if count >= maxPhotos {
		return media.Media{}, false, media.ErrTooManyPhotos
	}
	if _, err = tx.Exec(ctx, `INSERT INTO encounter_media
		(id, encounter_id, kind, status, content_type, byte_size, width, height, sha256, storage_key, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)`,
		m.ID, m.EncounterID, m.Kind, m.Status, m.ContentType, m.ByteSize, m.Width, m.Height, m.SHA256, m.StorageKey, m.CreatedAt); err != nil {
		return media.Media{}, false, mapMediaError(err)
	}
	if err = tx.Commit(ctx); err != nil {
		return media.Media{}, false, err
	}
	return m, true, nil
}

func (r *MediaRepository) ListByEncounter(ctx context.Context, encounterID string) ([]media.Media, error) {
	rows, err := r.pool.Query(ctx, `SELECT `+mediaColumns+` FROM encounter_media
		WHERE encounter_id = $1 ORDER BY created_at, id`, encounterID)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(row pgx.CollectableRow) (media.Media, error) { return scanMedia(row) })
}

func (r *MediaRepository) FindByID(ctx context.Context, id string) (media.Media, error) {
	m, err := scanMedia(r.pool.QueryRow(ctx, `SELECT `+mediaColumns+` FROM encounter_media WHERE id = $1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return media.Media{}, media.ErrNotFound
	}
	return m, err
}

func scanMedia(row pgx.Row) (media.Media, error) {
	var m media.Media
	err := row.Scan(&m.ID, &m.EncounterID, &m.Kind, &m.Status, &m.ContentType, &m.ByteSize, &m.Width, &m.Height,
		&m.SHA256, &m.StorageKey, &m.CreatedAt)
	return m, err
}

func mapMediaError(err error) error {
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		switch pgErr.ConstraintName {
		case "encounter_media_encounter_id_fkey":
			return encounter.ErrNotFound
		case "encounter_media_byte_size_valid":
			return media.ErrTooLarge
		case "encounter_media_dimensions_valid":
			return media.ErrDimensions
		}
	}
	return err
}
