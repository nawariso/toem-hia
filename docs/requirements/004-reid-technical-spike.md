# Requirement 004 — Re-ID Technical Spike

Status: **PROPOSED — PIPELINE BUILT, NO REAL-DATA EVIDENCE YET**

| Gate | Status |
| --- | --- |
| Research pipeline (manifest, leakage-safe sealed splits, metrics, open-set calibration, gates) | IMPLEMENTED — synthetic-fixture tests only |
| Tier A controlled monitor-lizard dataset | **NOT AVAILABLE** |
| Tier B mobile-like monitor-lizard dataset | **NOT AVAILABLE** |
| Proxy dataset (another species, pipeline validation only) | APPROVED IN PRINCIPLE — not yet downloaded |
| Family A (classical local features) | Implemented; not run on real data |
| Family B (general pretrained embedding) | Adapter implemented; weights not downloaded; not run |
| Family C (fine-tuning) | Not implemented — only after a review checkpoint |
| Current decision | **INSUFFICIENT DATA** — no labelled monitor-lizard imagery exists |
| DEVICE VALIDATION (Requirement 003) | NOT RUN — not a blocker for this spike |

Baseline: canonical `main` `9d1d78f1c6cd74aa62aed806112b58f1e0b0a448` (Requirement 003 accepted).

The spike produces research evidence only. It never creates an authoritative Hia assignment, and nothing in `research/reid/` is wired into the Go API or the mobile app.

## Data status (section 28 stop condition)

No trustworthy, individually labelled monitor-lizard images exist in the repository or in the project workspace, and no public labelled *Varanus* Re-ID dataset was found. The stop condition "trustworthy identity labels cannot be obtained" / "mobile-like data is insufficient for a decision" therefore applies today. The pipeline was built so the spike can resume as soon as real data exists, but **no feasibility claim is made**.

Approved direction (user decision, 2026-09-27):

- A licence-checked public Re-ID dataset from another species may be used for pipeline validation only. The leading candidate is SeaTurtleID2022 (lateral-head left/right photographs; multi-year). Every result from it carries the label `PROXY — PIPELINE VALIDATION ONLY` and is mechanically excluded from Gates C, D and E (`provenance.proxy = true` can never be tier B).
- The data-independent pipeline was built first; the spike stops here for review.

Needed to reach a responsible decision (section 8, Tier B minimums): ≥20 known Side-IDs (preferably 30+), ≥3 usable images per known Side-ID, ≥2 capture sessions per Side-ID where possible, and ≥20 unseen Side-IDs, with trustworthy labels and documented permission.

## What was built

`research/reid/` — isolated Python 3.13 package (`uv`, locked), CPU-only by default. See `research/reid/README.md` for the procedure.

| Area | Implementation | Requirement section |
| --- | --- | --- |
| Provenance | Per-dataset JSON: tier, species, source, licence, verification date, proxy flag. Proxy datasets can never be tier B. | 5.1, 8, 9 |
| Manifest | Immutable CSV with exactly the section 9 fields, plus optional `attr_*` stratification columns. Location/user/device columns are refused. Content hash covers every row. | 9, 21 |
| Ingest | Labels CSV + images → manifest, dHash sidecar, inventory report; exact duplicates dropped (conflicting labels refused); images copied to a content-addressed store outside Git; only the EXIF orientation tag is applied, GPS is never read. | 5.1, 21 |
| Leakage groups | Union of session, burst (timestamp window), near-duplicate (dHash Hamming) and shared source frame; conflicting labels across a near-duplicate pair are refused. | 10 |
| Sealed split | Deterministic, by individual (both sides together) into TRAIN / VALIDATION / TEST; unknown individuals held out of the gallery; known queries come from later sessions than their gallery; `split_sha256` seals the file; verification re-checks every rule. A random per-image split exists only as `NON-REPRESENTATIVE / DIAGNOSTIC ONLY` and cannot be used for the sealed test. | 10–12 |
| Matchers | Family A: OpenCV SIFT + ratio test + RANSAC inlier count. Family B: timm backbone with L2-normalised global features (optional `embedding` dependency group). | 5.3 |
| Metrics | Top-1/3/5, CMC, MRR; open-set FAR, known acceptance, false rejection, Top-K after threshold; Wilson 95 % intervals; stratification (side, quality, head pixels, every `attr_*`), small subgroups flagged but never hidden. Deterministic ranking; ties broken by Side-ID. | 13 |
| Calibration | UNKNOWN threshold calibrated on VALIDATION unknowns only (target FAR 5 %); TEST reuses it unchanged. | 11, 14-D |
| Sealed test | Append-only log registered *before* any test metric is computed; changing the config or calibration after the test has been examined is refused until a new split/protocol is created. | 11 |
| Budget | ≤3 configs per family, ≤1 fine-tuned model family, 2 preprocessing strategies; enforced by `toem-reid budget` and in CI. | 6 |
| Experiment record | Every section 20 field, plus evidence label, protocol label, latency, hardware, false-positive / false-negative examples (by image_id only), artifact hashes. | 16, 20, 27 |
| Quality signals | Research-only ACCEPT / RETRY PHOTO / INSUFFICIENT HEAD VIEW (head pixels, Laplacian sharpness, exposure) used for stratification. | 18 |
| Gates / decision | Gates A–E and the four decision classes. Proxy, Tier A, validation-partition, diagnostic-split or below-minimum Tier B evidence can only produce INSUFFICIENT DATA. | 14, 15 |
| Scaling | Flat cosine index mechanics at 50/100/250/500 Side-IDs on random vectors, labelled `SYNTHETIC VECTORS — SCALING MECHANICS ONLY; NOT ACCURACY EVIDENCE`. | 16 |

Pre-declared configurations (`research/reid/configs/`, inside the budget):

| Config | Family | Preprocessing | Model |
| --- | --- | --- | --- |
| `a-sift-original` | A | original frame | OpenCV SIFT |
| `a-sift-head-crop` | A | head crop | OpenCV SIFT |
| `b-megadescriptor-t224-head-crop` | B | head crop | `BVRA/MegaDescriptor-T-224` (animal Re-ID; **CC BY-NC 4.0**) |
| `b-dinov2-small-head-crop` | B | head crop | `timm/vit_small_patch14_dinov2.lvd142m` (Apache-2.0) |

Hub revisions were recorded when the configs were written. No weights have been downloaded. The CC BY-NC licence of MegaDescriptor restricts it to non-commercial research use; it must not ship in the product without a licence review.

## Provisional parameters (reviewer to confirm)

Requirement 004 does not state these numerically. They live in `toem_reid.decision.ProvisionalPolicy` and are written into every decision record:

| Parameter | Provisional value | Why it is needed |
| --- | --- | --- |
| Minimum known-query Top-5 recall after the UNKNOWN threshold | 0.50 | Gate D: "retaining useful known-query candidate recall" |
| CONDITIONAL GO band, retrieval | Top-5 in [0.70, 0.80) | Section 15 gives only one example (Top-5 = 76 %) |
| CONDITIONAL GO band, open-set | FAR in (0.05, 0.10] | Same |

Gate E's operational rule is that the session-disjoint sealed TEST must reach the Gate C target on its own. The random-image diagnostic is reported next to it so that any collapse is visible.

## Verification

- `research/reid`: `uv run pytest` — 143 tests pass (synthetic fixtures only; branch coverage 95 %), `ruff format --check`, `ruff check` and `mypy --strict` clean; `toem-reid budget configs/*.json` passes.
- CI: new `research-reid` job in `.github/workflows/quality.yml` runs the same gates. It downloads no data or weights.
- Synthetic images are used only to test the pipeline. They are never evidence that Re-ID works (section 7).

## Not built (section 25 non-goals respected)

No production Re-ID API, model serving, on-device inference, Encounter → Hia assignment, Identification tables, verification workflow or UI, public confidence scores, vector database / pgvector, cloud GPU, MLOps or cross-side identity inference. Raw similarity is documented as "not a probability" in every experiment record (section 26).

## Next steps (require user review)

1. Confirm or adjust the provisional parameters above.
2. Proxy run (optional): download SeaTurtleID2022 after re-checking its licence, write its provenance file with `proxy: true`, and run families A/B to validate the pipeline end to end. Labelled PROXY; does not count toward any gate.
3. Real data: establish a labelled monitor-lizard collection meeting the Tier A / Tier B minimums (labelling protocol, permission, session recording). Until then the only valid decision is **INSUFFICIENT DATA**.
