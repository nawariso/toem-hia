from __future__ import annotations

import pytest
from conftest import record

from toem_reid.dedup import LeakageError, leakage_groups, near_duplicate_pairs


def test_near_duplicate_pairs_use_hamming_threshold_and_are_sorted() -> None:
    hashes = {"a": 0b0000, "b": 0b0011, "c": 0xFFFF_FFFF}
    assert near_duplicate_pairs(hashes, max_distance=2) == [("a", "b")]
    assert near_duplicate_pairs(hashes, max_distance=1) == []


def test_same_side_id_and_session_form_one_group() -> None:
    records = [record("a", session="s1"), record("b", session="s1"), record("c", session="s2")]
    groups = leakage_groups(records, near_duplicates=[])
    assert groups["a"] == groups["b"]
    assert groups["a"] != groups["c"]


def test_same_session_label_on_different_side_ids_is_not_merged() -> None:
    records = [record("a", individual="hia-1"), record("b", individual="hia-2")]
    groups = leakage_groups(records, near_duplicates=[])
    assert groups["a"] != groups["b"]


def test_near_duplicate_across_sessions_merges_the_sessions() -> None:
    records = [record("a", session="s1"), record("b", session="s2"), record("c", session="s2")]
    groups = leakage_groups(records, near_duplicates=[("a", "b")])
    assert groups["a"] == groups["b"] == groups["c"]


def test_burst_within_window_merges_differently_labelled_sessions() -> None:
    records = [
        record("a", session="s1", timestamp="2026-01-01T10:00:00"),
        record("b", session="s2", timestamp="2026-01-01T10:00:03"),
        record("c", session="s3", timestamp="2026-01-01T12:00:00"),
    ]
    groups = leakage_groups(records, near_duplicates=[], burst_seconds=10)
    assert groups["a"] == groups["b"]
    assert groups["a"] != groups["c"]


def test_source_frame_attribute_groups_derived_images() -> None:
    records = [
        record("a", session="s1", attributes={"source_frame": "f1"}),
        record("b", session="s2", attributes={"source_frame": "f1"}),
    ]
    groups = leakage_groups(records, near_duplicates=[])
    assert groups["a"] == groups["b"]


def test_near_duplicate_between_different_individuals_is_a_label_conflict() -> None:
    records = [record("a", individual="hia-1"), record("b", individual="hia-2")]
    with pytest.raises(LeakageError, match="label conflict: a and b"):
        leakage_groups(records, near_duplicates=[("a", "b")])


def test_near_duplicate_between_left_and_right_is_a_label_conflict() -> None:
    records = [record("a", side="left"), record("b", side="right")]
    with pytest.raises(LeakageError, match="label conflict"):
        leakage_groups(records, near_duplicates=[("a", "b")])


def test_group_ids_are_deterministic_and_input_order_independent() -> None:
    records = [record("b", session="s2"), record("a", session="s1")]
    assert leakage_groups(records, []) == leakage_groups(list(reversed(records)), [])
    assert leakage_groups(records, [])["a"] == "g:a"
