# Requirement 003 — Camera & Local Media Foundation

Status: **IMPLEMENTED — PENDING INDEPENDENT REVIEW**

| Gate | Status |
| --- | --- |
| REQ-003 Camera & Local Media Foundation | IMPLEMENTED — PENDING INDEPENDENT REVIEW |
| Automated backend/mobile/contracts gates | see PR (local results recorded below) |
| DEVICE VALIDATION (real camera, device or simulator) | **NOT RUN** — mandatory before any public or user pilot |
| Supabase Auth / Email OTP / real provider JWT | DEFERRED TO INTEGRATION & PILOT HARDENING |
| Re-ID / identification | NOT STARTED |

Baseline: canonical `main` `b302b576440e0dce23807319a72212d35428b278` (TOEM HIA naming, Stage A).

## Scope

A user can open **Scan a Hia**, photograph a monitor lizard with the rear camera, preview/retake, and save it: the app creates a DRAFT encounter, uploads the photo privately, and submits the encounter. Guests can capture and preview but must create a Hia Passport to save; the capture survives the sign-in detour. The API stores photo bytes through a `MediaStore` port with a local filesystem adapter for development/test (ADR-010) and metadata in PostgreSQL.

Out of scope (not built): cloud/object storage, production media store, public media, video, Base64 transport, location permission or capture location, EXIF extraction, HiaDex, Explore, identification results, Re-ID, pgvector, Supabase/OTP work, background upload, recovery after OS process termination.

## Backend

| Area | Implementation |
| --- | --- |
| Domain | `internal/domain/media`: kind `PHOTO`, status `READY`/`REJECTED`, JPEG/PNG only, 15 MiB, 12000 px per side, 50 MP, 5 photos per encounter, server-generated storage key `photos/<uuid>.<ext>` |
| Port | `application.MediaStore` (Stage/Commit/Discard/Open/Delete) and `application.MediaRepository` (Attach under encounter lock, FindByID, ListByEncounter) |
| Adapter | `infrastructure/media/localmedia` — staging + same-volume rename, crash-staging cleanup at start, key shape re-validated before any path is built |
| Config | `MEDIA_MODE` required (`local`/`disabled`), no default; `MEDIA_LOCAL_ROOT` required for `local`; `production` + `local` is fatal |
| Schema | migration `000003_media`: `encounter_media` with check constraints, `UNIQUE(storage_key)`, `UNIQUE(encounter_id, sha256)`, immutability trigger, `ON DELETE RESTRICT` |
| API | `POST /v1/encounters/{id}/media` (raw body, `201` new / `200` same bytes), `GET /v1/encounters/{id}/media`, `GET /v1/media/{id}/content`; owner-only; `503 MEDIA_UNAVAILABLE` when disabled |

### Review-sensitive behaviour and where it is tested

| Behaviour | Mechanism | Test |
| --- | --- | --- |
| 15 MiB before unbounded memory | `Content-Length` pre-check, `http.MaxBytesReader`, `Stage` streams to disk reading at most limit+1 | `transport/http/media_test.go`, `localmedia/store_test.go` |
| Dimensions before full decode | `DecodeConfig` bound check, then full decode must match | `domain/media/media_test.go` (header-claimed 60000×1 and 56 MP in a tiny payload, empty, HTML, GIF, magic-only, truncated JPEG, PNG magic + junk) |
| Concurrent 5-photo limit | count under `SELECT … FOR UPDATE` on the encounter row | `TestConcurrentAttachNeverExceedsThePhotoLimit` (20 concurrent) |
| DRAFT vs submit race | Attach and Submit lock the same row; Attach re-checks DRAFT under the lock | `TestAttachAfterSubmitIsRefused`, `TestConcurrentAttachAndSubmitRespectDraftRule` (25 races) |
| Failed validation → no permanent file | staged file discarded by `defer` | `application/media_test.go` |
| Publish failure → no row | attach never runs | `TestPublishFailureRecordsNoMediaRow` |
| Row failure after publish → file removed | `Delete` of the just-published key | `application/media_test.go` |
| Duplicate → existing row, no orphan | `UNIQUE(encounter_id, sha256)` + delete of the new file | `TestRetriedUploadIsIdempotent`, postgres constraint test |
| Privacy | response allowlist, log field allowlist, `private, no-store`, `nosniff`, sandbox CSP | transport tests assert absence of key/path/EXIF fields |

## Mobile

| Area | Implementation |
| --- | --- |
| API client | `src/api/encounters.ts`: create DRAFT (only `capturedAt`), upload (`File.upload`, `BINARY_CONTENT`, exact `Content-Type`), submit, get; no `any`, no Base64, no path sent |
| Pending capture | `src/capture/pending-capture.ts`: one capture moved into `Paths.document/toem-hia/pending-capture/`; in-memory metadata (photo, capturedAt, encounterId, uploaded, saveRequested) |
| Save flow | `src/capture/save-flow.ts`: create → upload → submit → discard; each completed step is recorded so Retry resumes; single in-flight promise |
| Scan screen | `app/scan.tsx`: permission loading/denied/request, camera initializing/ready, capturing, capture failed, preview, saving, failed (+Retry), saved; one rear `CameraView` mounted only while focused and not previewing |
| Guest → auth → resume | Save as guest sets `saveRequested` and opens Passport; after sign-in (and Passport Setup if needed) the app returns to Scan and resumes |
| Home | **Scan a Hia** opens Scan (the `COMING SOON` placeholder is gone) |
| Logout | discards any pending capture |

Pending-capture rules: capture → move to pending storage; retake/cancel → delete; upload success → delete local copy; upload failure → keep for Retry; auth/navigation → keep; logout → delete. The camera cache URI is not used after the move.

Retry/idempotency: an existing `encounterId` is reused (no second encounter); after a successful upload the photo is never re-sent; a submit response counts as saved only when its status is exactly `SUBMITTED`. A submit that fails with `ENCOUNTER_NOT_EDITABLE` is confirmed with `GET` and treated as done only if the server reports exactly `SUBMITTED` (not merely non-DRAFT). Other statuses retain the pending capture for Retry without creating a second encounter or media row; `ENCOUNTER_NOT_FOUND` on upload (e.g. a different user signed in) clears the encounter so the next Retry starts a new DRAFT; the server also deduplicates the same bytes on the same encounter.

## Deviations

- Raw-body upload instead of multipart (provisionally accepted).
- `sha256` is part of the `EncounterMedia` client contract (it identifies a retried upload); the mobile UI does not use it.
- Pending-capture metadata is in memory; a file left by a terminated process is removed on the next capture, not recovered.
- The API has no update/delete endpoint for photos in this requirement.

## Technical debt

- No sweeper for files orphaned by a crash between commit and attach.
- Accepted for the local development adapter: the filesystem publishes the file before metadata is attached transactionally. Application cleanup handles normal attach/database failures, but an ambiguous database commit outcome could theoretically leave file and metadata inconsistent. Before production/shared object storage, design explicit reconciliation or object-state handling appropriate to the chosen storage provider; do not implement distributed transactions or reconciliation here.
- No `GET` media list/content use in the mobile UI yet (API and contract exist).
- Local adapter file permissions are POSIX-only; Windows ACLs are inherited.
- A DRAFT encounter created by a save that is never retried remains a DRAFT.

## Device validation

**DEVICE VALIDATION — NOT RUN.** All mobile tests mock Expo Camera and Expo FileSystem. The README camera smoke test must pass on a real device before any public or user pilot.
