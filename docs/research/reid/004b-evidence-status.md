# Requirement 004B — evidence acquisition and validation readiness

Status: ACCEPTED FOR EVIDENCE READINESS / BLOCKED — DATA ACQUISITION. Requirement 004 research pipeline: ACCEPTED (see `004-pipeline-acceptance.md`). Overall Requirement 004 Re-ID feasibility: INSUFFICIENT DATA — OPEN; Requirement 004 as a whole is NOT ACCEPTED.

Independent review accepted PR #5 at `c7c84b09f35d8c1995fb8946c7cd7f8d3c249115`. Its normal merge commit is `15d12d447df0193e63f80840782199b3e5e634ee` (second parent is the reviewed head); push CI run `36302090627` passed backend, mobile and research-reid. This acceptance covers acquisition and validation readiness, not evidence collection or a feasibility result.

| Track | Evidence class | Availability / action | Counts toward final Gates C/D/E? |
| --- | --- | --- | --- |
| A: Kasetsart water-monitor study | Tier A, same species but DSLR/telephoto capture | Access-request draft prepared (`data-acquisition/kasetsart-access-request.md`); NOT SENT; permission, labels and data NOT RECEIVED | No |
| B: TOEM HIA Lumpini phone observations | Tier B, mobile-like | Collection and verification protocol drafted; site/collection permissions NOT REVIEWED; no images/VERIFIED labels available | Only after minimums, integrity and one sealed TEST |
| C: SeaTurtleID2022 | PROXY — PIPELINE VALIDATION ONLY | Owner's Kaggle terms checked; custom non-commercial licence boundary requires clarification for this project's intended use. No download/experiment. Separate proxy branch reserved for evidence after clearance. | Never |

Proxy listing: "Other (specified in description)" rather than CC0. Its owner description permits ML/imaging/computer-vision testing and analysis, but imposes reproduction, commercial-use and marine-turtle-biology restrictions.[2] This is not a blanket open licence. Do not download or use in a product-linked study until the use boundary has been reviewed or written permission obtained. The dataset has not been downloaded.

The draft Tier A provenance template is deliberately non-ingestible. The research loader now rejects unknown/pending licence markers and invalid verification dates; this is an entry guard, not a substitute for permission review.

No target-species SEALED TEST consumed, no Family C, no production Identification/Re-ID integration. DEVICE VALIDATION remains NOT RUN. Only a reproducible pipeline exists.

## Sources

[2] https://www.kaggle.com/api/v1/datasets/view/wildlifedatasets/seaturtleid2022 — SeaTurtleID2022 official dataset metadata and use requirements
