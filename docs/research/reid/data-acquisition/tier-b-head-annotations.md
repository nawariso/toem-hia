# Tier B manual head annotation specification

No automatic detector or cross-side resolver is in scope. Annotations are prepared on the upright image after EXIF-orientation correction; no other EXIF identity field is consulted. The approved ingest CSV has `side` and `attr_*` fields, not a free-form detector output.

| Field | Format | Validation / action |
| --- | --- | --- |
| `side` | `left` or `right` | Visible lateral side. Frontal/ambiguous views remain outside VERIFIED left/right evidence unless independently resolved. |
| `attr_head_bbox` | CSV-quoted string `"x0,y0,x1,y1"` | Four integer pixel coordinates on the upright image, 0 ≤ x0 < x1 ≤ width, 0 ≤ y0 < y1 ≤ height. x1/y1 exclusive. Required for head-crop configurations. |
| `attr_angle` | lateral / oblique / frontal | Keep a consistent coding guide; avoid cherry-picking only easy profiles. |
| `attr_lighting` | shade / direct-sun / mixed / low | Natural capture variability; not an identity feature. |
| `attr_obstruction` | none / partial / severe | Annotate severity, not the obstructing object/location. |
| `notes` | concise quality note | Motion blur, insufficient head pixels, exposure; no personal or location data. |

Draw a tight but adequate box covering the lateral head and distinguishing scale pattern. Avoid deliberately including identifiable signs, body markings outside the declared crop, background or animal surroundings. Save a review record of any rejected box and correction. Check dimensions against `width`,`height` from the manifest; review missing/invalid boxes before running head-crop configs. Preserve the original image for an original-frame comparison, and record whether a crop was manual. Crop first, then SIFT grayscale or the Family B model's RGB transform. Never re-annotate based on sealed TEST accuracy.

Example CSV (illustrative only, not an actual observation):

```text
relative_path,individual_label,side,capture_session_id,attr_head_bbox,attr_angle,attr_lighting,attr_obstruction,notes
example.jpg,example-verified-001,left,encounter-001,"10,20,110,120",lateral,shade,none,sharp
```

No example image is supplied or ingested. Permission and VERIFIED ground truth are prerequisites; see `permission-licence-checklist.md` and `tier-b-ground-truth.md`.
