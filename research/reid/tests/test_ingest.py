from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from conftest import make_pattern_image, write_provenance
from PIL import Image

from toem_reid.images import (
    apply_preprocessing,
    crop_to_bbox,
    dhash,
    hamming,
    load_image,
    prepare,
)
from toem_reid.ingest import IngestError, ingest
from toem_reid.manifest import load_manifest


def _labels(path: Path, rows: list[str], header: str | None = None) -> Path:
    head = (
        header
        or "relative_path,individual_label,side,capture_session_id,capture_timestamp_if_known,notes"
    )
    path.write_text("\n".join([head, *rows]) + "\n", encoding="utf-8")
    return path


def test_dhash_survives_resize_and_separates_identities(tmp_path: Path) -> None:
    original = make_pattern_image(tmp_path / "a.png", identity_seed=1, image_seed=1, size=128)
    other = make_pattern_image(tmp_path / "b.png", identity_seed=2, image_seed=1, size=128)
    small = tmp_path / "a_small.png"
    load_image(original).resize((64, 64)).save(small)
    assert hamming(dhash(load_image(original)), dhash(load_image(small))) <= 4
    assert hamming(dhash(load_image(original)), dhash(load_image(other))) > 10


def test_load_image_applies_exif_orientation_only(tmp_path: Path) -> None:
    pixels = np.zeros((10, 20, 3), dtype=np.uint8)
    image = Image.fromarray(pixels)
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees on display
    path = tmp_path / "rotated.jpg"
    image.save(path, exif=exif)
    loaded = load_image(path)
    assert loaded.size == (10, 20)
    assert loaded.mode == "RGB"


def test_crop_to_bbox_and_rejects_invalid_boxes(tmp_path: Path) -> None:
    image = load_image(make_pattern_image(tmp_path / "a.png", 1, 1, size=64))
    assert crop_to_bbox(image, "8,8,40,24").size == (32, 16)
    for bad in ["", "1,2,3", "10,10,5,20", "0,0,65,10", "a,b,c,d", "-1,0,10,10"]:
        with pytest.raises(ValueError, match="head_bbox"):
            crop_to_bbox(image, bad)


def test_apply_preprocessing_strategies(tmp_path: Path) -> None:
    image = load_image(make_pattern_image(tmp_path / "a.png", 1, 1, size=64))
    assert apply_preprocessing(image, "original", None) is image
    assert apply_preprocessing(image, "head_crop", "0,0,32,16").size == (32, 16)
    with pytest.raises(ValueError, match="requires an attr_head_bbox"):
        apply_preprocessing(image, "head_crop", None)
    with pytest.raises(ValueError, match="preprocessing must be one of"):
        apply_preprocessing(image, "segmented", None)


def test_prepare_limits_the_longest_side_and_is_deterministic(tmp_path: Path) -> None:
    image = load_image(make_pattern_image(tmp_path / "a.png", 1, 1, size=128))
    first = prepare(image, max_side=64)
    assert first.shape == (64, 64)
    assert first.dtype == np.uint8
    assert np.array_equal(first, prepare(image, max_side=64))
    assert prepare(image, max_side=512).shape == (128, 128)


def test_ingest_builds_manifest_phash_store_and_report(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    make_pattern_image(root / "x" / "1.png", 1, 1)
    make_pattern_image(root / "x" / "2.png", 1, 2)
    make_pattern_image(root / "y.png", 2, 3)
    labels = _labels(
        tmp_path / "labels.csv",
        [
            "x/1.png,hia-1,left,s1,2026-01-01T10:00:00,",
            "x/2.png,hia-1,left,s2,,",
            "y.png,hia-2,right,s1,,",
        ],
    )
    provenance = write_provenance(tmp_path / "p.json")
    out = tmp_path / "out"
    result = ingest(labels, root, provenance, out_dir=out, store=tmp_path / "store")

    records = load_manifest(result.manifest_path)
    assert len(records) == 3
    first = next(r for r in records if r.capture_session_id == "s1" and r.side == "left")
    assert first.image_id == f"synthetic-{first.file_sha256[:16]}"
    assert first.capture_timestamp_if_known == "2026-01-01T10:00:00"
    assert (
        tmp_path / "store" / "synthetic" / first.file_sha256[:2] / f"{first.file_sha256}.png"
    ).is_file()
    assert first.original_filename_hash != first.file_sha256

    phash = json.loads(result.phash_path.read_text(encoding="utf-8"))
    assert set(phash["hashes"]) == {r.image_id for r in records}
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["image_count"] == 3
    assert report["side_id_count"] == 2
    assert report["evidence_label"] == "TIER B — MOBILE-LIKE REAL DATA"
    assert report["manifest_sha256"] == result.manifest_sha256


def test_ingest_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    make_pattern_image(root / "1.png", 1, 1)
    make_pattern_image(root / "2.png", 2, 2)
    labels = _labels(tmp_path / "l.csv", ["2.png,hia-2,left,s1,,", "1.png,hia-1,left,s1,,"])
    provenance = write_provenance(tmp_path / "p.json")
    a = ingest(labels, root, provenance, out_dir=tmp_path / "a")
    b = ingest(labels, root, provenance, out_dir=tmp_path / "b")
    assert a.manifest_path.read_bytes() == b.manifest_path.read_bytes()
    assert a.manifest_sha256 == b.manifest_sha256


def test_ingest_drops_exact_duplicates_with_agreeing_labels(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    make_pattern_image(root / "1.png", 1, 1)
    (root / "copy.png").write_bytes((root / "1.png").read_bytes())
    labels = _labels(tmp_path / "l.csv", ["1.png,hia-1,left,s1,,", "copy.png,hia-1,left,s1,,"])
    result = ingest(labels, root, write_provenance(tmp_path / "p.json"), out_dir=tmp_path / "o")
    assert len(load_manifest(result.manifest_path)) == 1
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["dropped_exact_duplicates"] == [{"kept": "1.png", "dropped": "copy.png"}]


def test_ingest_refuses_exact_duplicates_with_conflicting_labels(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    make_pattern_image(root / "1.png", 1, 1)
    (root / "copy.png").write_bytes((root / "1.png").read_bytes())
    labels = _labels(tmp_path / "l.csv", ["1.png,hia-1,left,s1,,", "copy.png,hia-2,left,s1,,"])
    with pytest.raises(IngestError, match="conflicting labels for identical file"):
        ingest(labels, root, write_provenance(tmp_path / "p.json"), out_dir=tmp_path / "o")


@pytest.mark.parametrize("relative", ["../outside.png", "/abs.png", "C:/abs.png"])
def test_ingest_refuses_paths_outside_the_image_root(tmp_path: Path, relative: str) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    labels = _labels(tmp_path / "l.csv", [f"{relative},hia-1,left,s1,,"])
    with pytest.raises(IngestError, match="outside the image root"):
        ingest(labels, root, write_provenance(tmp_path / "p.json"), out_dir=tmp_path / "o")


def test_ingest_refuses_unsupported_or_missing_files(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    (root / "a.gif").write_bytes(b"GIF89a")
    labels = _labels(tmp_path / "l.csv", ["a.gif,hia-1,left,s1,,", "missing.png,hia-1,left,s1,,"])
    with pytest.raises(IngestError) as error:
        ingest(labels, root, write_provenance(tmp_path / "p.json"), out_dir=tmp_path / "o")
    assert "unsupported image type" in str(error.value)
    assert "file not found" in str(error.value)


def test_ingest_refuses_location_columns_in_labels(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    make_pattern_image(root / "1.png", 1, 1)
    labels = _labels(
        tmp_path / "l.csv",
        ["1.png,hia-1,left,s1,13.73"],
        header="relative_path,individual_label,side,capture_session_id,gps_lat",
    )
    with pytest.raises(IngestError, match="must not be used as identity evidence"):
        ingest(labels, root, write_provenance(tmp_path / "p.json"), out_dir=tmp_path / "o")


def test_ingest_keeps_attribute_columns(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    make_pattern_image(root / "1.png", 1, 1)
    labels = _labels(
        tmp_path / "l.csv",
        ["1.png,hia-1,left,s1,shade,4,4,40,40"],
        header="relative_path,individual_label,side,capture_session_id,attr_lighting,attr_head_bbox",
    )
    labels.write_text(
        "relative_path,individual_label,side,capture_session_id,attr_lighting,attr_head_bbox\n"
        '1.png,hia-1,left,s1,shade,"4,4,40,40"\n',
        encoding="utf-8",
    )
    result = ingest(labels, root, write_provenance(tmp_path / "p.json"), out_dir=tmp_path / "o")
    assert load_manifest(result.manifest_path)[0].attributes == {
        "lighting": "shade",
        "head_bbox": "4,4,40,40",
    }
