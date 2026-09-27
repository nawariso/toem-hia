# Lumpini / TOEM HIA Tier B phone-photo collection protocol (draft; no collection started)

Status: STOP — PERMISSION REVIEW REQUIRED. Complete `permission-licence-checklist.md` before research collection or ingestion. Observe. Don’t Disturb: never chase, feed, touch or lure a monitor lizard for data.

## Identity and sample design

The experimental identity is `Side-ID = dataset_id:individual_label:left|right`; an individual Hia is not a Side-ID. Preserve a separate, access-controlled individual-to-side mapping where both sides are known. Do not infer cross-side identity. Target at least 20 known Side-IDs (30+ preferred), at least 3 usable images per known Side-ID, two genuinely separate capture sessions per Side-ID where possible, and at least 20 unseen Side-IDs. Do not claim a decision boundary has been reached without a measured, leakage-safe split; unknown individuals must not enter the gallery.

Recruit realistic phone-camera observations, especially lateral head views, across natural distance, lateral angle, lighting/shade/direct sun, head size, mild blur, backgrounds and partial obstructions. Include different phones when they arise naturally, but do not encode phone identity as a matching feature. Do not curate only easy, close-up images; log exclusions and their reasons.

## Encounter/session rule

A capture session is one independent encounter with the same animal, not a burst, camera pause or a few seconds between frames. Assign a new session ID only for a documented new encounter after the animal left and later re-entered observation, or an independently observed event at a sufficiently separated time with evidence that it is not the same continuous observation. A change of observer sequence alone is not enough. If independence is uncertain, keep the images in the same session/leakage group. Burst photos, extracted frames and near duplicates remain grouped. Record a non-identifying encounter/session key and time if known; never manufacture a timestamp. Record the reason for treating two sessions as separate in a restricted collection log outside the matching manifest.

## Ground truth (mandatory)

Follow `tier-b-ground-truth.md` for the full state machine, reviewer ledger and evidence admission rule; only VERIFIED rows enter the sealed evidence CSV.

The labels CSV accepted by `toem-reid ingest` has only `relative_path,individual_label,side,capture_session_id,capture_timestamp_if_known,notes,attr_head_bbox,attr_angle,attr_lighting,attr_obstruction` (additional non-identity `attr_*` allowed). Verification status/authority stays in the access-controlled ledger; ingest only its VERIFIED subset, after permission review. Do not commit raw restricted labels or a reference mapping without explicit permission.

## Manual lateral-head annotation

Follow `tier-b-head-annotations.md` for the coordinate convention, field vocabulary, review procedure and CSV example. No automatic detector is built in 004B.

## Privacy, safety and permissions

No GPS, park micro-location, user/photographer ID, device ID, filename conventions or EXIF camera serial as matching features. EXIF orientation only is used in image loading; strip sensitive metadata from any permitted shared derivatives. Operational location, permissions and contact records, if necessary, remain outside the Re-ID evidence pipeline. Verify site rules, image ownership, contributor permission and publication/redistribution restrictions before collecting; if ambiguous: STOP — PERMISSION REVIEW REQUIRED. Keep raw photos and weights outside Git.

## Freeze discipline

Sequence after approved provenance and VERIFIED labels: ingest → dedup/leakage report → deterministic sealed split by individual → validation/calibration → reproducibility → freeze configuration → one sealed TEST → Gates A–E. Train/development and validation are separate from TEST. No TEST inspection while choosing algorithm, threshold, crop or quality policy; all decision thresholds remain those frozen in `docs/requirements/004-reid-technical-spike.md`. Never reuse a consumed sealed test. Until adequate mobile-like Tier B evidence exists, final conclusion is INSUFFICIENT DATA.
