from __future__ import annotations

import copy
from collections import defaultdict

import pytest
from conftest import record

from toem_reid.manifest import ManifestRecord
from toem_reid.splits import (
    DIAGNOSTIC_LABEL,
    SplitError,
    SplitParameters,
    build_diagnostic_split,
    build_split,
    verify_split,
)


def _dataset(
    individuals: int = 20, sessions: int = 3, images_per_session: int = 2, single_session: int = 4
) -> list[ManifestRecord]:
    records = []
    for i in range(individuals):
        count = 1 if i < single_session else sessions
        for side in ("left", "right"):
            for s in range(count):
                for k in range(images_per_session):
                    image_id = f"i{i:02d}-{side[0]}-s{s}-{k}"
                    records.append(
                        record(
                            image_id,
                            individual=f"hia-{i:02d}",
                            side=side,
                            session=f"day{s}",
                            timestamp=f"2026-01-{s + 1:02d}T10:00:0{k}",
                        )
                    )
    return records


PARAMS = SplitParameters(seed=7, train_fraction=0.4, validation_fraction=0.3, unknown_fraction=0.4)


def _by_role(spec: dict[str, object]) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in spec["assignments"]:  # type: ignore[attr-defined]
        out[row["role"]].append(row)
    return out


def test_split_is_deterministic_and_input_order_independent() -> None:
    records = _dataset()
    a = build_split(records, [], PARAMS, manifest_sha256="m" * 64)
    b = build_split(list(reversed(records)), [], PARAMS, manifest_sha256="m" * 64)
    assert a == b
    assert a["split_sha256"] == b["split_sha256"]


def test_different_seed_changes_the_split() -> None:
    records = _dataset()
    a = build_split(records, [], PARAMS, manifest_sha256="m" * 64)
    b = build_split(
        records,
        [],
        SplitParameters(seed=8, train_fraction=0.4, validation_fraction=0.3, unknown_fraction=0.4),
        manifest_sha256="m" * 64,
    )
    assert a["split_sha256"] != b["split_sha256"]


def test_every_image_is_assigned_exactly_once_and_spec_verifies() -> None:
    records = _dataset()
    spec = build_split(records, [], PARAMS, manifest_sha256="m" * 64)
    ids = [row["image_id"] for row in spec["assignments"]]
    assert sorted(ids) == sorted(r.image_id for r in records)
    assert verify_split(spec, records, []) == []
    assert spec["protocol"] == "session-disjoint"
    assert spec["representative"] is True


def test_individuals_never_span_partitions_and_sides_stay_together() -> None:
    records = _dataset()
    spec = build_split(records, [], PARAMS, manifest_sha256="m" * 64)
    partitions: dict[str, set[str]] = defaultdict(set)
    for row in spec["assignments"]:
        partitions[row["individual"]].add(row["partition"])
    assert all(len(p) == 1 for p in partitions.values())
    assert {p for ps in partitions.values() for p in ps} == {"train", "validation", "test"}


def test_known_queries_are_session_disjoint_from_their_gallery() -> None:
    records = _dataset()
    spec = build_split(records, [], PARAMS, manifest_sha256="m" * 64)
    sessions: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    lookup = {r.image_id: r for r in records}
    for row in spec["assignments"]:
        if row["role"] in ("gallery", "query_known"):
            rec = lookup[row["image_id"]]
            sessions[(row["partition"], rec.side_id, rec.capture_session_id)].add(row["role"])
    assert all(len(roles) == 1 for roles in sessions.values())
    assert _by_role(spec)["query_known"]


def test_unknown_individuals_have_no_gallery_images_and_prefer_single_session() -> None:
    records = _dataset()
    spec = build_split(records, [], PARAMS, manifest_sha256="m" * 64)
    roles = _by_role(spec)
    unknown = {row["individual"] for row in roles["query_unknown"]}
    gallery = {row["individual"] for row in roles["gallery"]}
    assert unknown
    assert not unknown & gallery
    single = {f"synthetic:hia-{i:02d}" for i in range(4)}
    eval_single = {
        row["individual"]
        for row in spec["assignments"]
        if row["partition"] != "train" and row["individual"] in single
    }
    assert eval_single <= unknown


def test_known_query_side_ids_always_have_gallery_images() -> None:
    spec = build_split(_dataset(), [], PARAMS, manifest_sha256="m" * 64)
    roles = _by_role(spec)
    for partition in ("validation", "test"):
        gallery = {r["side_id"] for r in roles["gallery"] if r["partition"] == partition}
        query = {r["side_id"] for r in roles["query_known"] if r["partition"] == partition}
        assert query <= gallery


def test_later_sessions_become_queries_when_timestamps_exist() -> None:
    spec = build_split(_dataset(), [], PARAMS, manifest_sha256="m" * 64)
    for row in _by_role(spec)["query_known"]:
        assert "-s2-" in row["image_id"]


def test_near_duplicate_across_sessions_keeps_them_on_the_same_side() -> None:
    records = _dataset(single_session=0)
    pairs = [
        (r.image_id, r.image_id.replace("-s0-", "-s2-")) for r in records if "-s0-0" in r.image_id
    ]
    spec = build_split(records, pairs, PARAMS, manifest_sha256="m" * 64)
    assert verify_split(spec, records, pairs) == []
    role = {row["image_id"]: row["role"] for row in spec["assignments"]}
    for a, b in pairs:
        assert role[a] == role[b]


def test_excluded_images_are_recorded_and_not_evaluated() -> None:
    records = _dataset()
    spec = build_split(
        records, [], PARAMS, manifest_sha256="m" * 64, excluded={"i10-l-s0-0": "blurred"}
    )
    row = next(r for r in spec["assignments"] if r["image_id"] == "i10-l-s0-0")
    assert row["role"] == "excluded"
    assert row["reason"] == "blurred"


def test_invalid_fractions_are_refused() -> None:
    with pytest.raises(SplitError, match="fractions"):
        build_split(
            _dataset(),
            [],
            SplitParameters(
                seed=1, train_fraction=0.8, validation_fraction=0.3, unknown_fraction=0.4
            ),
            manifest_sha256="m" * 64,
        )
    with pytest.raises(SplitError, match="unknown_fraction"):
        build_split(
            _dataset(),
            [],
            SplitParameters(
                seed=1, train_fraction=0.4, validation_fraction=0.3, unknown_fraction=1.0
            ),
            manifest_sha256="m" * 64,
        )


def _mutate(spec: dict[str, object], image_id: str, **changes: str) -> dict[str, object]:
    broken = copy.deepcopy(spec)
    for row in broken["assignments"]:  # type: ignore[attr-defined]
        if row["image_id"] == image_id:
            row.update(changes)
    return broken


def test_verify_detects_session_leakage_between_gallery_and_query() -> None:
    records = _dataset()
    spec = build_split(records, [], PARAMS, manifest_sha256="m" * 64)
    query = _by_role(spec)["query_known"][0]
    sibling = query["image_id"][:-1] + ("1" if query["image_id"].endswith("0") else "0")
    broken = _mutate(spec, sibling, role="gallery")
    assert any("session leakage" in e for e in verify_split(broken, records, []))


def test_verify_detects_individual_spanning_partitions() -> None:
    records = _dataset()
    spec = build_split(records, [], PARAMS, manifest_sha256="m" * 64)
    row = _by_role(spec)["train"][0]
    broken = _mutate(spec, row["image_id"], partition="test", role="gallery")
    assert any("spans partitions" in e for e in verify_split(broken, records, []))


def test_verify_detects_near_duplicate_crossing_roles() -> None:
    records = _dataset()
    spec = build_split(records, [], PARAMS, manifest_sha256="m" * 64)
    q = _by_role(spec)["query_known"][0]["image_id"]
    g = next(
        r["image_id"]
        for r in _by_role(spec)["gallery"]
        if r["side_id"] == _by_role(spec)["query_known"][0]["side_id"]
    )
    assert any("near-duplicate" in e for e in verify_split(spec, records, [(q, g)]))


def test_verify_detects_unknown_individual_in_gallery_and_tampering() -> None:
    records = _dataset()
    spec = build_split(records, [], PARAMS, manifest_sha256="m" * 64)
    unknown = _by_role(spec)["query_unknown"][0]["image_id"]
    broken = _mutate(spec, unknown, role="gallery")
    errors = verify_split(broken, records, [])
    assert any("unknown individual" in e for e in errors)
    assert any("split_sha256 does not match" in e for e in errors)


def test_verify_detects_missing_or_extra_images() -> None:
    records = _dataset()
    spec = build_split(records, [], PARAMS, manifest_sha256="m" * 64)
    assert any("not assigned" in e for e in verify_split(spec, [*records, record("new")], []))


def test_diagnostic_split_is_labelled_non_representative() -> None:
    records = _dataset()
    spec = build_diagnostic_split(records, PARAMS, manifest_sha256="m" * 64)
    assert spec["protocol"] == "random-image-diagnostic"
    assert spec["representative"] is False
    assert spec["label"] == DIAGNOSTIC_LABEL == "NON-REPRESENTATIVE / DIAGNOSTIC ONLY"
    assert _by_role(spec)["query_known"]
    assert verify_split(spec, records, []) == []
