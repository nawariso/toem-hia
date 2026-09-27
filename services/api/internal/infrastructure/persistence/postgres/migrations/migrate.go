package migrations

import (
	"context"
	_ "embed"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed 000001_identity.up.sql
var identityUp string

//go:embed 000001_identity.down.sql
var identityDown string

//go:embed 000002_wildlife.up.sql
var wildlifeUp string

//go:embed 000002_wildlife.down.sql
var wildlifeDown string

//go:embed 000003_media.up.sql
var mediaUp string

//go:embed 000003_media.down.sql
var mediaDown string

type migration struct {
	version  int
	name     string
	up, down string
}

// ordered lists migrations oldest first. Every up script is idempotent, so
// re-running Up is safe; every down script only reverts its own objects.
var ordered = []migration{
	{version: 1, name: "identity", up: identityUp, down: identityDown},
	{version: 2, name: "wildlife", up: wildlifeUp, down: wildlifeDown},
	{version: 3, name: "media", up: mediaUp, down: mediaDown},
}

// Latest is the newest migration version.
func Latest() int { return ordered[len(ordered)-1].version }

// Up applies every migration in order.
func Up(ctx context.Context, pool *pgxpool.Pool) error { return UpTo(ctx, pool, Latest()) }

// Down reverts every migration, newest first.
func Down(ctx context.Context, pool *pgxpool.Pool) error { return DownTo(ctx, pool, 0) }

// UpTo applies migrations 1..version in order.
func UpTo(ctx context.Context, pool *pgxpool.Pool, version int) error {
	if err := checkVersion(version); err != nil {
		return err
	}
	for _, m := range ordered {
		if m.version > version {
			break
		}
		if _, err := pool.Exec(ctx, m.up); err != nil {
			return fmt.Errorf("migration %06d_%s up: %w", m.version, m.name, err)
		}
	}
	return nil
}

// DownTo reverts every migration newer than version, newest first. DownTo(2)
// reverts only the media migration; DownTo(1) also reverts wildlife and keeps
// the identity schema and data.
func DownTo(ctx context.Context, pool *pgxpool.Pool, version int) error {
	if err := checkVersion(version); err != nil {
		return err
	}
	for i := len(ordered) - 1; i >= 0; i-- {
		m := ordered[i]
		if m.version <= version {
			break
		}
		if _, err := pool.Exec(ctx, m.down); err != nil {
			return fmt.Errorf("migration %06d_%s down: %w", m.version, m.name, err)
		}
	}
	return nil
}

func checkVersion(version int) error {
	if version < 0 || version > Latest() {
		return fmt.Errorf("migration version must be between 0 and %d", Latest())
	}
	return nil
}
