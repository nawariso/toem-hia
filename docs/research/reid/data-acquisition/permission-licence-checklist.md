# Research image permission and licence review — fail-closed checklist

Complete for each source BEFORE collection for research, download (unless the published terms unambiguously cover the intended use), or ingestion. Record the actual document, version, reviewer and date. A public park or publicly downloadable file is not automatically unrestricted research evidence.

| Question | Record / evidence | State |
| --- | --- | --- |
| Site/park photography and research collection rules checked? | source and date | UNREVIEWED |
| Separate research/collection permit required? If yes, obtained? | permit reference, holder, scope | UNREVIEWED |
| Dataset and photo owner(s) identified? | names or organisation, no unnecessary personal data in manifests | UNREVIEWED |
| Contributor/third-party photos licensed for this research? | grant and scope | UNREVIEWED |
| Intended non-commercial research vs commercial/product use distinguished? | exact terms or written clarification | UNREVIEWED |
| Derivative crops, embeddings, feature caches allowed? | term reference | UNREVIEWED |
| May aggregate results be published? May example images be shown? | term reference | UNREVIEWED |
| Redistribution of raw data or manifest metadata allowed? | term reference | UNREVIEWED |
| Retention/deletion and access restrictions known? | policy / agreement | UNREVIEWED |
| Identity labels expert-verified, and sessions sufficiently separated? | verification protocol, encounter log | UNREVIEWED |

Decision: STOP — PERMISSION REVIEW REQUIRED until all applicable questions have documented answers. `license_or_permission` must hold an actual permission reference, never "unknown" or a guess. Keep restricted agreements and photos outside Git. If terms forbid publishing metadata, do not commit a manifest that reveals it.

SeaTurtleID2022 special case: Kaggle's owner listing labels the licence "Other (specified in description)", not CC0. Its description permits ML/computer-vision algorithm development, testing and analysis, but retains copyright, prohibits commercial photo use and commercial use of algorithms trained on it, limits image reproduction, and requires advance permission for studies focused on marine-turtle biology/ecology/conservation.[2] Research-only proxy mechanics may fit the permitted technical purpose; the project's potential product context merits explicit owner clarification before downloading or running the proxy experiment. No dataset downloaded.

## Sources

[2] https://www.kaggle.com/api/v1/datasets/view/wildlifedatasets/seaturtleid2022 — SeaTurtleID2022 official dataset metadata and use requirements
