# TOEM HIA Re-ID research spike (Requirement 004)

Research code only. Nothing here is part of the TOEM HIA product API. It never assigns an authoritative Hia identity, and raw similarity scores are **not probabilities** and must never be shown to users.

Requirement record and current status: `docs/requirements/004-reid-technical-spike.md`.

## Rules that the tooling enforces

- Raw and derived imagery, model weights and feature caches stay **outside Git**. Keep them under a directory such as `%USERPROFILE%\toem-reid-data` (set `TOEM_REID_DATA`, and `TOEM_REID_MODEL_CACHE` for pretrained models); `.gitignore` is only a second line of defence.
- Pretrained models (families B/C) are pinned in the config's `model_artifact`: an immutable 40-hex upstream revision plus the SHA-256 of `config.json` and the weights file. `toem-reid fetch-model` is the only command that downloads. Evaluation loads strictly from the verified local cache, re-hashes every file, and fails on any mismatch or on weights that do not load exactly into the backbone. The revision and weight hash are written into every experiment record.
- Each family prepares its own input after the head crop: family A gets grayscale for SIFT; family B gets the RGB crop through the pinned model's canonical evaluation transform (input size, interpolation, resize/centre-crop, mean/std from its `config.json`). A grayscale embedding experiment would have to be a separately declared configuration.
- Every dataset has a provenance file. A dataset from another species must set `"proxy": true`. Its results are labelled `PROXY — PIPELINE VALIDATION ONLY` and can never pass Gates C, D or E.
- Location, user and device columns are refused in labels and manifests; GPS/EXIF (except orientation) is never read.
- Splits are session-disjoint and sealed. A split file is never overwritten. The UNKNOWN threshold is calibrated on VALIDATION only; the TEST partition reuses it unchanged and is registered in an append-only log before any test metric is computed.
- Budget (section 6): at most 3 configurations per family, at most one fine-tuned model family. `toem-reid budget configs/*.json` checks it (also in CI).
- Synthetic images are used only in tests. They are never evidence that Re-ID works.

## Setup

Python 3.13 via `uv` (pinned in `.python-version`, locked in `uv.lock`):

```bash
cd research/reid
uv sync --locked                    # core + dev tools, CPU only
uv sync --locked --group embedding  # optional: timm + CPU-only torch for family B
```

Quality gates (same as CI job `research-reid`):

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest
uv run toem-reid budget configs/*.json
```

These gates need no dataset and no pretrained weights. Without the `embedding` group, the family B tests are skipped locally. CI installs the group, runs offline (`HF_HUB_OFFLINE=1`), and fails instead of skipping (`TOEM_REID_REQUIRE_EMBEDDING=1`).

Pretrained model artifacts (families B/C, before their first evaluation):

```bash
uv run toem-reid fetch-model --config configs/b-dinov2-small-head-crop.json \
  --model-cache "$TOEM_REID_MODEL_CACHE"
```

The command prints the verified revision, file hashes and the canonical preprocessing. `evaluate` / `reproduce` take the same `--model-cache` (or the environment variable) and never download.

## Procedure

1. Provenance file (`provenance.json`, stored next to the dataset outside Git):

   ```json
   {
     "dataset_id": "lumpini-tier-b-v1",
     "tier": "B",
     "species": "Varanus salvator",
     "source": "TOEM HIA field collection",
     "source_reference": "collection protocol / permission reference",
     "license_or_permission": "written permission reference",
     "license_verified_on": "YYYY-MM-DD",
     "proxy": false,
     "notes": ""
   }
   ```

2. Labels CSV (one row per image; paths relative to the image root):

   ```text
   relative_path,individual_label,side,capture_session_id,capture_timestamp_if_known,notes,attr_head_bbox,attr_lighting,attr_angle,attr_obstruction
   day1/IMG_0001.jpg,hia-017,left,2026-10-01-am,2026-10-01T08:12:00,,"812,440,1400,980",sun,frontal-lateral,none
   ```

   `side` is `left` or `right`; each side is its own Side-ID (section 4). `attr_head_bbox` (`x0,y0,x1,y1` on the upright image) is required for `head_crop` configs. Other `attr_*` columns are used only for stratification. Quote values that contain commas.

3. Ingest (writes the manifest, dHash sidecar and inventory; copies images to the content-addressed store):

   ```bash
   uv run toem-reid ingest --labels labels.csv --images <image-root> \
     --provenance provenance.json --out manifests --store "$TOEM_REID_DATA/store"
   ```

4. Sealed split (session-disjoint; pass `--burst-seconds` when timestamps exist):

   ```bash
   uv run toem-reid split --manifest manifests/<id>.manifest.csv --phash manifests/<id>.phash.json \
     --seed 20261001 --train 0.0 --validation 0.5 --unknown 0.3 --burst-seconds 10 \
     --out splits/<id>-v1.json
   ```

   Optional diagnostic split (for Gate E comparison; never used for the sealed test): add `--diagnostic`.

5. Validation (model selection and UNKNOWN threshold calibration):

   ```bash
   uv run toem-reid evaluate --partition validation --manifest ... --provenance ... \
     --split splits/<id>-v1.json --config configs/a-sift-original.json \
     --store "$TOEM_REID_DATA/store" --results results --phash manifests/<id>.phash.json
   ```

6. Reproducibility (Gate A): `uv run toem-reid reproduce ... --record results/<validation-record>.json`.

7. Sealed test (once per configuration and protocol):

   ```bash
   uv run toem-reid evaluate --partition test ...   # same arguments as step 5
   ```

8. Decision: `uv run toem-reid decide --test-record results/<test-record>.json --diagnostic-record results/<diagnostic-record>.json --reproducible --out reports/decision.json`. The complete decision policy (GO: Top-5 ≥ 0.80, FAR ≤ 0.05, known Top-5 after threshold ≥ 0.70; single near miss: Top-5 ≥ 0.70 or FAR ≤ 0.075; downgrade if the FAR 95 % upper bound exceeds 0.10) is written into the decision file.

9. Scaling mechanics (synthetic vectors only): `uv run toem-reid scale --out reports/scaling-synthetic.json`.

Commit manifests, split specifications, configs, experiment records, reports and decisions. Never commit images, crops, features or weights.
