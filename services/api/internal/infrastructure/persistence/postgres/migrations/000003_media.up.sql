-- Requirement 003: private photos attached to encounters. Idempotent so
-- `migrate up` can be re-run.
--
-- Only metadata lives here. The bytes live in the MediaStore under
-- storage_key, which is server-generated and never returned by the API.
-- There is no visibility column: every Requirement 003 photo is private to
-- the encounter's observer. Public media is a later requirement.
SELECT pg_advisory_xact_lock(2003003003);

CREATE TABLE IF NOT EXISTS encounter_media (
  id UUID PRIMARY KEY,
  encounter_id UUID NOT NULL REFERENCES encounters(id) ON DELETE RESTRICT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  content_type TEXT NOT NULL,
  byte_size BIGINT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  storage_key TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT encounter_media_kind_valid CHECK (kind IN ('PHOTO')),
  CONSTRAINT encounter_media_status_valid CHECK (status IN ('READY', 'REJECTED')),
  CONSTRAINT encounter_media_content_type_valid CHECK (content_type IN ('image/jpeg', 'image/png')),
  -- 15 MiB, matching media.MaxBytes.
  CONSTRAINT encounter_media_byte_size_valid CHECK (byte_size BETWEEN 1 AND 15728640),
  -- 12000 px per side and 50 MP in total, matching media.MaxEdge/MaxPixels.
  CONSTRAINT encounter_media_dimensions_valid CHECK (
    width BETWEEN 1 AND 12000 AND height BETWEEN 1 AND 12000
    AND width::bigint * height::bigint <= 50000000
  ),
  CONSTRAINT encounter_media_sha256_format CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT encounter_media_storage_key_format CHECK (
    storage_key ~ '^photos/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(jpg|png)$'
  ),
  CONSTRAINT encounter_media_storage_key_unique UNIQUE (storage_key),
  -- A retried upload of the same bytes to the same encounter is the same photo.
  CONSTRAINT encounter_media_encounter_sha256_unique UNIQUE (encounter_id, sha256)
);
CREATE INDEX IF NOT EXISTS encounter_media_encounter_created_idx ON encounter_media (encounter_id, created_at, id);

-- Encounters with photos cannot be deleted by accident (ON DELETE RESTRICT
-- above), and photo metadata is append-only: nothing in Requirement 003
-- updates a row, so identity and integrity fields are guarded explicitly.
CREATE OR REPLACE FUNCTION encounter_media_guard_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.id <> OLD.id OR NEW.encounter_id <> OLD.encounter_id OR NEW.sha256 <> OLD.sha256
     OR NEW.storage_key <> OLD.storage_key OR NEW.byte_size <> OLD.byte_size
     OR NEW.content_type <> OLD.content_type OR NEW.width <> OLD.width OR NEW.height <> OLD.height THEN
    RAISE EXCEPTION 'encounter media content is immutable' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER encounter_media_immutable
  BEFORE UPDATE ON encounter_media FOR EACH ROW EXECUTE FUNCTION encounter_media_guard_immutable();
