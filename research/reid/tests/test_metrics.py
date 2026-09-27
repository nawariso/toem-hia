from __future__ import annotations

import math

import numpy as np
import pytest

from toem_reid.metrics import (
    calibrate_unknown_threshold,
    closed_set_metrics,
    open_set_metrics,
    rank_identities,
    stratify,
    wilson_interval,
)


def test_rank_identities_aggregates_by_max_and_breaks_ties_by_side_id() -> None:
    similarity = np.array([[0.2, 0.9, 0.5, 0.9]])
    gallery = ["b", "b", "a", "c"]
    ranking = rank_identities(similarity, gallery)
    assert ranking.side_ids == ["a", "b", "c"]
    assert ranking.scores[0].tolist() == [0.5, 0.9, 0.9]
    assert [ranking.side_ids[i] for i in ranking.order[0]] == ["b", "c", "a"]
    assert ranking.top(0, 2) == [("b", 0.9), ("c", 0.9)]


def test_rank_identities_tie_break_is_independent_of_gallery_order() -> None:
    a = rank_identities(np.array([[0.5, 0.5]]), ["y", "x"])
    b = rank_identities(np.array([[0.5, 0.5]]), ["x", "y"])
    assert [a.side_ids[i] for i in a.order[0]] == [b.side_ids[i] for i in b.order[0]] == ["x", "y"]


def test_rank_identities_rejects_nan_and_shape_mismatch() -> None:
    with pytest.raises(ValueError, match="finite"):
        rank_identities(np.array([[np.nan]]), ["a"])
    with pytest.raises(ValueError, match="columns"):
        rank_identities(np.array([[0.1, 0.2]]), ["a"])


def test_closed_set_metrics_hand_computed() -> None:
    # true identity ranks: 1, 2, 4 (of 4 identities), and 1
    similarity = np.array(
        [
            [0.9, 0.1, 0.2, 0.3],
            [0.8, 0.9, 0.1, 0.0],
            [0.9, 0.8, 0.7, 0.1],
            [0.1, 0.2, 0.3, 0.95],
        ]
    )
    gallery = ["a", "b", "c", "d"]
    truth = ["a", "a", "d", "d"]
    m = closed_set_metrics(rank_identities(similarity, gallery), truth, ks=(1, 3, 5))
    assert m["n"] == 4
    assert m["top_k"]["1"]["rate"] == pytest.approx(0.5)
    assert m["top_k"]["3"]["rate"] == pytest.approx(0.75)
    assert m["top_k"]["5"]["rate"] == pytest.approx(1.0)
    assert m["mrr"] == pytest.approx((1 + 1 / 2 + 1 / 4 + 1) / 4)
    assert m["cmc"] == pytest.approx([0.5, 0.75, 0.75, 1.0])
    assert m["ranks"] == [1, 2, 4, 1]


def test_closed_set_requires_truth_in_gallery() -> None:
    with pytest.raises(ValueError, match="not in the gallery"):
        closed_set_metrics(rank_identities(np.array([[0.1]]), ["a"]), ["z"])


def test_wilson_interval_known_values() -> None:
    low, high = wilson_interval(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-4)
    assert high == pytest.approx(0.9433, abs=1e-4)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    low0, _ = wilson_interval(0, 20)
    assert low0 == 0.0


def test_threshold_calibration_meets_the_far_target_on_validation() -> None:
    unknown_best = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95])
    tau = calibrate_unknown_threshold(unknown_best, target_far=0.10)
    assert tau > 0.9
    assert tau <= 0.95
    assert float(np.mean(unknown_best >= tau)) <= 0.10
    tau_zero = calibrate_unknown_threshold(unknown_best, target_far=0.0)
    assert float(np.mean(unknown_best >= tau_zero)) == 0.0
    assert tau_zero > 0.95


def test_threshold_calibration_handles_ties_conservatively() -> None:
    unknown_best = np.array([0.5, 0.5, 0.5, 0.5])
    tau = calibrate_unknown_threshold(unknown_best, target_far=0.5)
    assert float(np.mean(unknown_best >= tau)) <= 0.5


def test_threshold_calibration_refuses_empty_or_bad_target() -> None:
    with pytest.raises(ValueError, match="unknown"):
        calibrate_unknown_threshold(np.array([]), target_far=0.05)
    with pytest.raises(ValueError, match="target_far"):
        calibrate_unknown_threshold(np.array([0.1]), target_far=1.5)


def test_open_set_metrics_hand_computed() -> None:
    gallery = ["a", "b", "c"]
    known_sim = np.array([[0.9, 0.1, 0.0], [0.4, 0.8, 0.1], [0.3, 0.2, 0.35]])
    known_truth = ["a", "a", "c"]
    unknown_sim = np.array([[0.95, 0.0, 0.0], [0.2, 0.3, 0.1]])
    m = open_set_metrics(
        rank_identities(known_sim, gallery),
        known_truth,
        rank_identities(unknown_sim, gallery),
        threshold=0.5,
        ks=(1, 3),
    )
    # known best scores 0.9, 0.8, 0.35 -> accepted 2 of 3
    assert m["known_acceptance"]["rate"] == pytest.approx(2 / 3)
    assert m["false_rejection"]["rate"] == pytest.approx(1 / 3)
    # accepted & correct at top-1: query 0 only; at top-3: queries 0 and 1
    assert m["known_top_k_after_threshold"]["1"]["rate"] == pytest.approx(1 / 3)
    assert m["known_top_k_after_threshold"]["3"]["rate"] == pytest.approx(2 / 3)
    assert m["unknown_false_accept"]["rate"] == pytest.approx(1 / 2)
    assert m["unknown_false_accept"]["count"] == 1
    assert m["threshold"] == 0.5
    assert "ci95" in m["unknown_false_accept"]


def test_open_set_decisions_never_force_a_match() -> None:
    m = open_set_metrics(
        rank_identities(np.array([[0.1]]), ["a"]),
        ["a"],
        rank_identities(np.array([[0.1]]), ["a"]),
        threshold=0.5,
    )
    assert m["decisions"]["known"] == ["UNKNOWN"]
    assert m["decisions"]["unknown"] == ["UNKNOWN"]


def test_stratify_reports_weak_subgroups_and_marks_small_ones() -> None:
    ranks = [1, 1, 6, 6, 1]
    values = ["sun", "sun", "shade", "shade", "rain"]
    table = stratify(ranks, values, k=5, min_n=2)
    assert table["sun"] == {
        "n": 2,
        "top_k_rate": 1.0,
        "ci95": pytest.approx(list(wilson_interval(2, 2))),
        "reportable": True,
    }
    assert table["shade"]["top_k_rate"] == 0.0
    assert table["rain"]["reportable"] is False
    assert math.isnan(table["rain"]["top_k_rate"]) is False
