# SeaTurtleID2022 proxy licence review — no download authorised yet

Checked: 2026-09-27. Authoritative source: the dataset owner's Kaggle listing/API metadata, dataset `wildlifedatasets/seaturtleid2022`, version 4 (listing last updated 2025-04-16).[2] Licence selector: **Other (specified in description)**; do not describe it as CC0.

The owner's description states that ML, imaging-science and computer-vision algorithm development, testing and analysis are permitted, with owner-retained photo copyright. Reasonable photographic reproduction for relevant scientific publication is conditional on proper citation; other reproduction is restricted. It also prohibits commercial use of the photographs and commercial use of algorithms trained on the dataset, and requires prior owner permission for studies focused on marine-turtle biology/ecology/conservation.[2]

Intended 004B use would be a technical comparison of an untrained SIFT method and two separately pretrained models on turtle imagery to exercise the pipeline, not to develop a turtle biology study, train a product model or redistribute photographs. Nevertheless, TOEM HIA is a potential product: whether this specific product-linked research comparison is covered by the non-commercial boundary, and whether aggregate/derived outputs can be published, are not explicit enough to approve a download here. **STOP — DO NOT DOWNLOAD.** Seek an explicit owner clarification or a documented permission review before creating the separate `research/req-004b-proxy-seaturtle` experiment branch or obtaining any images. This is a cautious project decision, not a legal opinion.

If authorised later: store all raw data and weights outside Git; set `proxy=true`, `tier=A`, and the exact licence terms/reference/date in provenance; use only a separate proxy evidence branch and never allow proxy metrics to satisfy C/D/E. No proxy dataset has been downloaded and no proxy experiment has run.

## Sources

[2] https://www.kaggle.com/api/v1/datasets/view/wildlifedatasets/seaturtleid2022 — SeaTurtleID2022 official dataset metadata and use requirements
