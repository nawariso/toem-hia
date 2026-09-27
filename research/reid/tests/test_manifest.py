from __future__ import annotations

from pathlib import Path

import pytest
from conftest import record, valid_row, write_manifest_csv, write_provenance

from toem_reid.manifest import (
    FIELDS,
    ManifestError,
    load_manifest,
    manifest_sha256,
    write_manifest,
)
from toem_reid.provenance import DatasetProvenance, ProvenanceError, load_provenance


def test_field_list_is_the_requirement_minimum_in_order() -> None:
    assert FIELDS == (
        "dataset_id",
        "image_id",
        "source",
        "source_reference",
        "license_or_permission",
        "individual_label",
        "side",
        "capture_session_id",
        "capture_timestamp_if_known",
        "original_filename_hash",
        "file_sha256",
        "width",
        "height",
        "split",
        "notes",
    )


def test_valid_manifest_loads_and_derives_side_id(tmp_path: Path) -> None:
    path = write_manifest_csv(tmp_path / "m.csv", [valid_row()])
    records = load_manifest(path)
    assert len(records) == 1
    assert records[0].side_id == "synthetic:hia-1:left"
    assert records[0].width == 64


def test_left_and_right_of_the_same_individual_are_distinct_side_ids() -> None:
    left = record("a", side="left")
    right = record("b", side="right")
    assert left.side_id != right.side_id


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"side": "front"}, "side must be one of"),
        ({"side": ""}, "side must be one of"),
        ({"individual_label": ""}, "individual_label is required"),
        ({"license_or_permission": ""}, "license_or_permission is required"),
        ({"source": ""}, "source is required"),
        ({"file_sha256": "xyz"}, "file_sha256 must be 64 lowercase hex"),
        ({"original_filename_hash": "ABC"}, "original_filename_hash must be 64 lowercase hex"),
        ({"width": "0"}, "width must be a positive integer"),
        ({"height": "abc"}, "height must be a positive integer"),
        ({"split": "holdout"}, "split must be one of"),
        (
            {"capture_timestamp_if_known": "yesterday"},
            "capture_timestamp_if_known must be ISO 8601",
        ),
        ({"image_id": ""}, "image_id is required"),
        ({"dataset_id": "has:colon"}, "dataset_id must not contain ':'"),
        ({"individual_label": "a:b"}, "individual_label must not contain ':'"),
    ],
)
def test_invalid_field_values_are_rejected_with_row_number(
    tmp_path: Path, override: dict[str, str], message: str
) -> None:
    path = write_manifest_csv(tmp_path / "m.csv", [valid_row(**override)])
    with pytest.raises(ManifestError, match=message) as error:
        load_manifest(path)
    assert "row 2" in str(error.value)


def test_missing_required_column_is_rejected(tmp_path: Path) -> None:
    header = [f for f in FIELDS if f != "side"]
    row = {k: v for k, v in valid_row().items() if k != "side"}
    path = write_manifest_csv(tmp_path / "m.csv", [row], header=header)
    with pytest.raises(ManifestError, match="missing required column: side"):
        load_manifest(path)


def test_unknown_non_attribute_column_is_rejected(tmp_path: Path) -> None:
    header = [*FIELDS, "photographer"]
    path = write_manifest_csv(tmp_path / "m.csv", [valid_row(photographer="x")], header=header)
    with pytest.raises(ManifestError, match="unexpected column: photographer"):
        load_manifest(path)


@pytest.mark.parametrize(
    "column",
    ["gps_latitude", "attr_gps", "latitude", "attr_longitude", "attr_user_id", "device_id"],
)
def test_location_user_and_device_columns_are_refused(tmp_path: Path, column: str) -> None:
    header = [*FIELDS, column]
    path = write_manifest_csv(tmp_path / "m.csv", [valid_row(**{column: "1"})], header=header)
    with pytest.raises(ManifestError, match="must not be used as identity evidence"):
        load_manifest(path)


def test_attribute_columns_are_kept_for_stratification(tmp_path: Path) -> None:
    header = [*FIELDS, "attr_lighting"]
    path = write_manifest_csv(tmp_path / "m.csv", [valid_row(attr_lighting="shade")], header=header)
    assert load_manifest(path)[0].attributes == {"lighting": "shade"}


def test_duplicate_image_id_is_rejected(tmp_path: Path) -> None:
    rows = [valid_row(file_sha256="a" * 64), valid_row(file_sha256="b" * 64)]
    path = write_manifest_csv(tmp_path / "m.csv", rows)
    with pytest.raises(ManifestError, match="duplicate image_id img-1"):
        load_manifest(path)


def test_exact_duplicate_file_is_rejected(tmp_path: Path) -> None:
    rows = [valid_row(image_id="img-1"), valid_row(image_id="img-2")]
    path = write_manifest_csv(tmp_path / "m.csv", rows)
    with pytest.raises(ManifestError, match=r"exact duplicate file .* img-1, img-2"):
        load_manifest(path)


def test_all_errors_are_reported_together(tmp_path: Path) -> None:
    rows = [valid_row(side="up"), valid_row(image_id="img-2", file_sha256="b" * 64, width="-1")]
    path = write_manifest_csv(tmp_path / "m.csv", rows)
    with pytest.raises(ManifestError) as error:
        load_manifest(path)
    assert "row 2" in str(error.value)
    assert "row 3" in str(error.value)


def test_empty_manifest_is_rejected(tmp_path: Path) -> None:
    path = write_manifest_csv(tmp_path / "m.csv", [])
    with pytest.raises(ManifestError, match="no records"):
        load_manifest(path)


def test_write_then_load_round_trips_and_sorts_by_image_id(tmp_path: Path) -> None:
    records = [
        record("b", sha="b" * 64, attributes={"lighting": "sun"}),
        record("a", sha="a" * 64, attributes={"lighting": "shade"}),
    ]
    path = tmp_path / "out.csv"
    write_manifest(path, records)
    loaded = load_manifest(path)
    assert [r.image_id for r in loaded] == ["a", "b"]
    assert loaded[0].attributes == {"lighting": "shade"}
    assert path.read_bytes().count(b"\r\n") == 0


def test_manifest_hash_is_independent_of_row_order_and_line_endings(tmp_path: Path) -> None:
    a = record("a", sha="a" * 64)
    b = record("b", sha="b" * 64)
    assert manifest_sha256([a, b]) == manifest_sha256([b, a])
    path = tmp_path / "m.csv"
    write_manifest(path, [a, b])
    crlf = tmp_path / "crlf.csv"
    crlf.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert manifest_sha256(load_manifest(crlf)) == manifest_sha256([a, b])


def test_manifest_hash_changes_when_any_label_changes() -> None:
    assert manifest_sha256([record("a")]) != manifest_sha256([record("a", individual="hia-2")])


def test_write_manifest_refuses_to_overwrite_an_existing_manifest(tmp_path: Path) -> None:
    path = tmp_path / "m.csv"
    write_manifest(path, [record("a")])
    with pytest.raises(FileExistsError):
        write_manifest(path, [record("a")])


def test_provenance_loads_and_marks_proxy(tmp_path: Path) -> None:
    path = write_provenance(tmp_path / "p.json", proxy=True, tier="A")
    provenance = load_provenance(path)
    assert isinstance(provenance, DatasetProvenance)
    assert provenance.proxy
    assert provenance.evidence_label == "PROXY — PIPELINE VALIDATION ONLY"


def test_mobile_like_real_provenance_is_product_evidence(tmp_path: Path) -> None:
    provenance = load_provenance(write_provenance(tmp_path / "p.json"))
    assert provenance.evidence_label == "TIER B — MOBILE-LIKE REAL DATA"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"tier": "C"}, "tier must be A or B"),
        ({"license_or_permission": ""}, "license_or_permission is required"),
        ({"license_or_permission": "UNKNOWN"}, "license_or_permission is not verified"),
        (
            {"license_or_permission": "pending permission review"},
            "license_or_permission is not verified",
        ),
        ({"license_or_permission": "TBD"}, "license_or_permission is not verified"),
        ({"license_verified_on": ""}, "license_verified_on is required"),
        ({"license_verified_on": "YYYY-MM-DD"}, "license_verified_on must be ISO"),
        ({"proxy": "yes"}, "proxy must be a boolean"),
        ({"tier": "B", "proxy": True}, "a proxy dataset cannot be tier B"),
        ({"species": ""}, "species is required"),
    ],
)
def test_invalid_provenance_is_rejected(
    tmp_path: Path, override: dict[str, object], message: str
) -> None:
    with pytest.raises(ProvenanceError, match=message):
        load_provenance(write_provenance(tmp_path / "p.json", **override))
