# Tier B ground-truth and identity verification protocol

This document governs evidence labels, not production Identification or a moderator UI. A contributor suggestion cannot become a VERIFIED identity without documented corroboration.

## States and admission

- UNVERIFIED: identity not proposed or unsupported. Keep outside the experiment labels CSV.
- CANDIDATE: a suggested Side-ID awaiting independent corroboration. Keep outside the sealed evidence dataset.
- VERIFIED: sufficient stable head-scale evidence, distinct encounter references and independent expert/reviewer confirmation have been recorded. Only these rows are eligible for experiment labels.

Confirm left and right independently. Do not match across sides or merge individuals using this spike. `individual_label` may link left/right for split isolation only when backed by the verification ledger; otherwise do not assert a cross-side mapping. Keep the mapping outside public Git if access terms require it. Two reviewers who merely copied a contributor's assertion are not independent confirmations.

## Restricted verification ledger (never an identity shortcut)

One record per candidate image/Side-ID: `image_id` (or pre-ingest source reference), `proposed_individual_label`, `side`, `status`, `verified_by` (role or non-identifying key only), `verification_method`, `verification_date`, `reference_evidence` (at least two distinct image references or a documented known re-encounter), `encounter/session references`, `reviewer_disagreements`, `resolution`, `permission_reference`. Preserve an audit trail when status changes; do not erase a rejected candidate. Do not put reviewer contact details, GPS, photographer, device ID or camera serial in the research manifest or matching input.

Verifier procedure: compare stable lateral head-scale patterns on multiple reference images, rule out near-duplicate/burst references and environmental shortcuts, check that claimed distinct encounters are truly separate, document the method and identifiers of the references, and request an independent reviewer. If the available references cannot support identity or reviewers disagree, remain CANDIDATE; never force a label to meet a sample threshold. Permission must already be cleared before research ingestion.

## Separation and freeze

Export only VERIFIED and permission-cleared rows to the accepted `toem-reid ingest` labels CSV. Images from the same session or burst never count as independent repeats. Resolve or quarantine conflicting exact/near-duplicate labels before splitting. Freeze labels and a hash of the authorised source ledger before generating a split; correct a later identity error only in a new protocol/split, never silently alter a consumed sealed test. A trusted image label alone is not sufficient for Tier B: provenance, collection conditions, session disjointness, sample minimums and untouched sealed TEST are also required.

Current status: no verification ledger or VERIFIED target-species images exist; no dataset admitted.
