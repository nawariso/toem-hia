-- Reverts Requirement 003 only. Encounters, locations, and the identity
-- schema are untouched. Media bytes in the MediaStore are NOT deleted by a
-- schema migration; remove them explicitly if the metadata is dropped.
DROP TRIGGER IF EXISTS encounter_media_immutable ON encounter_media;
DROP FUNCTION IF EXISTS encounter_media_guard_immutable();
DROP TABLE IF EXISTS encounter_media;
