# Dataset quality / evidence-readiness report template

Dataset ID: NOT PROVIDED | Tier: NOT ASSESSED | Species: NOT PROVIDED | Assessment date: NOT RUN
Permission reference: NOT PROVIDED | Licence/redistribution/derived-result terms: NOT REVIEWED
Inputs (local paths and SHA-256, never private content): manifest —; provenance —; inventory —; dHash sidecar —; labels verification ledger —; split —.

No image dataset is available as of this template. Every numeric field below is **NOT MEASURED**, never zero. Do not treat this template as an evidence report.

| Check | Result | Source / required action |
| --- | --- | --- |
| Individual Hia count | NOT MEASURED | inventory `individual_count` (not Side-ID count) |
| Side-ID count; left/right distribution | NOT MEASURED | inventory `side_id_count`, `side_counts`; reconcile with manifest |
| Images per Side-ID (min, median, full distribution) | NOT MEASURED | inventory `images_per_side_id` and manifest |
| Sessions per Side-ID; genuinely separate encounters | NOT MEASURED | inventory `sessions_per_side_id`; audit restricted encounter log |
| Known Side-IDs available to sealed TEST gallery | NOT MEASURED | sealed split roles (`gallery` / `query_known`) |
| Unseen Side-IDs in TEST | NOT MEASURED | sealed split `query_unknown`; verify individuals absent from gallery |
| Image dimensions (min / distribution) | NOT MEASURED | manifest `width`, `height` |
| Head-box dimensions / valid/invalid/missing | NOT MEASURED | parse `attr_head_bbox`, enforce upright-image bounds |
| Quality categories: ACCEPT / RETRY PHOTO / INSUFFICIENT HEAD VIEW | NOT MEASURED | assess actual crops; report full distribution and excluded examples |
| Exact duplicates dropped | NOT MEASURED | inventory `dropped_exact_duplicates` |
| Near duplicates and cross-label conflicts | NOT MEASURED | dHash sidecar + near-duplicate groups; conflicts STOP |
| Burst groups / shared source frames | NOT MEASURED | leakage grouping and session timestamp audit |
| Missing labels; UNVERIFIED/CANDIDATE not promoted | NOT MEASURED | compare raw candidate/verification ledger to VERIFIED labels CSV; ingest alone cannot count missing labels |
| Missing session IDs / timestamps | NOT MEASURED | manifest + restricted encounter log; timestamp absence may be genuine but no fabricated date |
| Provenance and permission complete | NO | source/permission/allowed-derivatives/publication checks not yet supplied |
| Tier A controlled adequacy | NOT ASSESSED | same-species VERIFIED identities; session-disjoint retrieval evidence; not Tier B |
| Tier B minimums | NOT MET — no data available | ≥20 known Side-IDs (30+ preferred), ≥3 images each, ≥20 unseen Side-IDs, ≥2 separate sessions where possible |
| Sealed split integrity | NOT ASSESSED | `verify_split` and no test leakage |

### Existing safe tooling (no invented metrics)

Once permission is known and images have been ingested outside Git, `toem-reid ingest` emits `<dataset_id>.inventory.json` and `<dataset_id>.phash.json` next to the manifest. `toem-reid split` validates leakage and writes a sealed split. `toem-reid evaluate --partition validation` stratifies quality. These alone do not certify ground truth, consent, or that separate sessions represent separate encounters: human verification remains mandatory. Keep input data and unapproved records outside Git; publish only permitted aggregate reports.

Assessment: BLOCKED — DATA ACQUISITION. Final scientific conclusion: INSUFFICIENT DATA. Do not fill with synthetic or proxy accuracy to claim Tier B readiness.
