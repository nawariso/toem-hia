# Requirement 004 — Re-ID Technical Spike

Status: **OPEN — RESEARCH PIPELINE ACCEPTED / 004B EVIDENCE READINESS ACCEPTED / RE-ID FEASIBILITY INSUFFICIENT DATA**. Completion blocker: **DATA ACQUISITION**.

The research pipeline is **ACCEPTED** after PR #4's normal merge commit `89130438f60c8258da5c78f81b79a595aec65ab3` and green push CI (run `36297138908`: backend, mobile and research-reid SUCCESS). See `docs/research/reid/004-pipeline-acceptance.md`. The feasibility question stays open; Requirement 004 as a whole is **not** ACCEPTED.

| Gate | Status |
| --- | --- |
| Research pipeline (manifest, leakage-safe sealed splits, metrics, open-set calibration, gates) | **ACCEPTED** — synthetic-fixture tests only; no feasibility conclusion |
| Requirement 004B evidence acquisition and validation readiness | **ACCEPTED** — PR #5 merge `15d12d447df0193e63f80840782199b3e5e634ee`; data acquisition BLOCKED, no dataset or experiment |
| Tier A controlled monitor-lizard dataset | **NOT AVAILABLE** |
| Tier B mobile-like monitor-lizard dataset | **NOT AVAILABLE** |
| Proxy dataset (another species, pipeline validation only) | SeaTurtleID2022 custom non-commercial terms reviewed; use in project context requires permission clarification before download. No data/experiment. |
| Family A (classical local features) | Implemented; not run on real data |
| Family B (general pretrained embedding) | RGB + model-canonical preprocessing; revision + SHA-256 pinned; not run on any dataset |
| Family C (fine-tuning) | Not implemented — only after a review checkpoint |
| Current decision | **INSUFFICIENT DATA** — no labelled monitor-lizard imagery exists |
| DEVICE VALIDATION (Requirement 003) | NOT RUN — not a blocker for this spike |

Baseline: canonical `main` `9d1d78f1c6cd74aa62aed806112b58f1e0b0a448` (Requirement 003 accepted).

The spike produces research evidence only. It never creates an authoritative Hia assignment, and nothing in `research/reid/` is wired into the Go API or the mobile app.

## Data status (section 28 stop condition)

No trustworthy, individually labelled monitor-lizard images exist in the repository or in the project workspace, and no public labelled *Varanus* Re-ID dataset was found. The stop condition "trustworthy identity labels cannot be obtained" / "mobile-like data is insufficient for a decision" therefore applies today. The pipeline was built so the spike can resume as soon as real data exists, but **no feasibility claim is made**.

## Real-data acquisition plan

### Priority A — same-species research data (request access)

Contact the corresponding author of *Non-Invasive Individual Re-Identification of Water Monitors (Varanus salvator) Using Deep Learning*. The publication reports 15,469 initial images, 510 Side-IDs, and a 3,311-image open-set evaluation set covering 161 Side-IDs, with expert-verified identity labels and left/right handled as separate Side-IDs. It offers no public download; its Data Availability statement directs inquiries to the corresponding author.

Request research access and permission for whatever the authors are allowed to share:

- images, or an approved research subset
- Side-ID labels, and the individual mapping if available
- left/right side
- capture session / date or encounter grouping
- head crops or bounding boxes, if available
- permission / licence terms, and restrictions on redistribution or publication

If access is granted, classify the data by its actual conditions. It can provide strong **same-species controlled evidence (Tier A)**. It is **not** automatically Tier B: the published capture used DSLR/telephoto field photography, not TOEM HIA phone capture.

### Priority B — TOEM HIA mobile-like Tier B collection

Establish a Lumpini / mobile collection in any case:

- ≥20 known Side-IDs (preferably 30+), ≥3 usable images per known Side-ID
- ≥2 genuinely separate capture sessions per Side-ID where possible
- ≥20 unseen Side-IDs
- trustworthy identity ground truth; explicit permission and collection provenance
- realistic phone distance, angle, lighting and obstruction

No GO / NO-GO decision is made without adequate Tier B evidence.

### Proxy validation (approved, deferred)

The proxy run happens only after this pipeline PR has been reviewed and merged, on a **separate evidence branch**. Preferred proxy: SeaTurtleID2022. Before any download:

1. retrieve and review the dataset's actual licence / terms;
2. record the licence text or reference and the verification date in the provenance file;
3. confirm research-only use is permitted;
4. never redistribute the dataset;
5. set `"proxy": true`.

Every proxy result is mechanically labelled `PROXY — PIPELINE VALIDATION ONLY` and cannot satisfy Gates C, D or E. It validates only ingestion, provenance, Side-ID / orientation handling, session/time-aware splitting, validation calibration, unknown rejection mechanics, sealed-TEST registration, experiment records, reproducibility, Family A/B execution and latency/scaling reporting. Proxy metrics are never used to estimate TOEM HIA accuracy.

## What was built

`research/reid/` — isolated Python 3.13 package (`uv`, locked), CPU-only by default. See `research/reid/README.md` for the procedure.

| Area | Implementation | Requirement section |
| --- | --- | --- |
| Provenance | Per-dataset JSON: tier, species, source, licence, verification date, proxy flag. Proxy datasets can never be tier B. | 5.1, 8, 9 |
| Manifest | Immutable CSV with exactly the section 9 fields, plus optional `attr_*` stratification columns. Location/user/device columns are refused. Content hash covers every row. | 9, 21 |
| Ingest | Labels CSV + images → manifest, dHash sidecar, inventory report; exact duplicates dropped (conflicting labels refused); images copied to a content-addressed store outside Git; only the EXIF orientation tag is applied, GPS is never read. | 5.1, 21 |
| Leakage groups | Union of session, burst (timestamp window), near-duplicate (dHash Hamming) and shared source frame; conflicting labels across a near-duplicate pair are refused. | 10 |
| Sealed split | Deterministic, by individual (both sides together) into TRAIN / VALIDATION / TEST; unknown individuals held out of the gallery; known queries come from later sessions than their gallery; `split_sha256` seals the file; verification re-checks every rule. A random per-image split exists only as `NON-REPRESENTATIVE / DIAGNOSTIC ONLY` and cannot be used for the sealed test. | 10–12 |
| Matchers | Each family owns its input preparation; the head crop is always applied first. Family A: grayscale → OpenCV SIFT + ratio test + RANSAC inlier count. Family B: RGB crop → the pinned model's canonical timm evaluation transform (input size, interpolation, shorter-side resize + centre crop per `crop_pct`, mean/std, all read from the hash-verified `config.json`) → L2-normalised global features (optional CPU-only `embedding` dependency group). | 5.3 |
| Model pinning | `model_artifact` in every family B/C config: 40-hex upstream commit revision + SHA-256 of `config.json` and the weights file. `toem-reid fetch-model` is the only command that downloads, into a cache outside Git (`--model-cache` / `TOEM_REID_MODEL_CACHE`), at exactly that revision; every file is re-hashed on every load and any mismatch fails. Evaluation never downloads. Weights must load exactly into the backbone, or the run fails. The experiment record carries the revision and weight SHA-256 in `model_provenance` and `artifact_hashes`. | 20, Gate A |
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

Pinned model artifacts (Hugging Face; revision and hashes verified 2026-09-27):

| Config | Revision | Weights file | Weights SHA-256 | `config.json` SHA-256 | Licence boundary |
| --- | --- | --- | --- | --- | --- |
| `b-megadescriptor-t224-head-crop` | `3ea58ff6c6195bc748bb86c111ff40c32bdddcba` | `pytorch_model.bin` (204,267,588 B) | `62f53e63…218190c` | `27ef9cc2…e83b86c` | **CC BY-NC 4.0 — research-only comparison.** The embedded `pretrained_cfg.license` field says `mit`; the stricter model-card licence governs. Not a production model candidate without a future licence decision. |
| `b-dinov2-small-head-crop` | `4610ca143709d58a633b6397a74412c2c3842454` | `model.safetensors` (88,240,510 B) | `04d27f34…ee20081` | `b651fc1b…7d52a05` | General embedding baseline. Model card: Apache-2.0. The embedded `pretrained_cfg.license` field says `cc-by-nc-4.0`; both are recorded and must be resolved before any production use. |

Full hashes live in the config files. Canonical preprocessing, read from each verified `config.json`: MegaDescriptor-T-224: 224 × 224, bicubic, `crop_pct` 0.9 (resize the shorter side to 248, then centre crop), ImageNet mean/std. DINOv2-small: 518 × 518, bicubic, `crop_pct` 1.0, ImageNet mean/std. Neither transform distorts the aspect ratio. **No model selection decision is authorised until real data exists.** No weights are in Git.

## Decision policy (reviewed and final)

Set by the independent review of `3259571`. Implemented in `toem_reid.decision.DecisionPolicy` and written in full into every decision artifact (`outcome.policy`):

| Parameter | Value |
| --- | --- |
| Gate C — session-disjoint sealed TEST Top-5 | ≥ 0.80 |
| Gate D — unknown FAR | ≤ 0.05 |
| Gate D — `min_known_recall_after_threshold` (known Top-5 after the UNKNOWN threshold) | ≥ 0.70 |
| `top5_near_miss_floor` | 0.70 |
| `far_near_miss_ceiling` | 0.075 |
| `far_ci95_exclusion_ceiling` | 0.10 |

- **GO** requires Top-5 ≥ 0.80, FAR ≤ 0.05 and post-threshold known Top-5 recall ≥ 0.70, plus Gates A, B and E.
- **CONDITIONAL GO** allows exactly one bounded weakness: Top-5 in [0.70, 0.80), *or* FAR in (0.05, 0.075]. Two weaknesses together are NO-GO.
- Post-threshold known recall below 0.70 is not a near miss: Gate D fails.
- **Statistical caution:** if the point estimates would otherwise give GO, but the upper bound of the 95 % Wilson interval for FAR is above 0.10, the outcome is downgraded to CONDITIONAL GO. Next action: *collect additional unseen-individual evidence and re-evaluate under a new sealed protocol*. A decision input without the FAR interval can never be GO.
- Thresholds are never tuned on the sealed TEST.

Gate E's operational rule is that the session-disjoint sealed TEST must reach the Gate C target on its own. The random-image diagnostic is reported next to it so that any collapse is visible.

## Verification

- `research/reid` (verified 2026-09-27 on the remediation commit's tree):
  - with the `embedding` group, offline, skips treated as failures (the CI configuration): **183 passed**, branch coverage **96 %**;
  - base environment only (`uv sync --locked`): 176 passed, 7 skipped (the family B tests that need timm);
  - both environments: `ruff format --check`, `ruff check` and `mypy --strict` clean; `toem-reid budget configs/*.json` passes.
  - All tests use synthetic fixtures only.
- CI: `research-reid` job in `.github/workflows/quality.yml` runs the same gates. It installs the CPU-only `embedding` group so the family B preprocessing and pinning tests run against a tiny locally-built model. `HF_HUB_OFFLINE=1` guarantees no data or pretrained weights are downloaded, and `TOEM_REID_REQUIRE_EMBEDDING=1` turns those tests' skips into failures.
- Pinned-artifact check (local, outside CI, 2026-09-27): `toem-reid fetch-model` fetched both pinned revisions into a scratch cache outside the repository; all SHA-256 values matched, and both models loaded strictly and produced deterministic embeddings (768-d / 384-d) on a random RGB array. This checks mechanics only, not Re-ID quality. The cache was deleted afterwards.
- Synthetic images are used only to test the pipeline. They are never evidence that Re-ID works (section 7).

## Not built (section 25 non-goals respected)

No production Re-ID API, model serving, on-device inference, Encounter → Hia assignment, Identification tables, verification workflow or UI, public confidence scores, vector database / pgvector, cloud GPU, MLOps or cross-side identity inference. Raw similarity is documented as "not a probability" in every experiment record (section 26).

## Next steps (require user review)

1. Independent review of the pipeline PR (do not merge before acceptance).
2. After merge: proxy evidence on a separate branch, following the procedure above.
3. Request access to the *Varanus salvator* research dataset (Priority A) and start the Lumpini mobile collection (Priority B). Until adequate Tier B evidence exists, the only valid decision is **INSUFFICIENT DATA**.

Not started, by instruction: sealed real-data experiments, Family C, production Identification code.
