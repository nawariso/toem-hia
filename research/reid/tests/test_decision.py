from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest

from toem_reid.decision import (
    FAR_CI_CAUTION_ACTION,
    DecisionPolicy,
    TierBMinimums,
    decide,
)


def _tier_b(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "known_side_ids": 30,
        "min_images_per_known_side_id": 3,
        "side_ids_with_two_sessions": 30,
        "unseen_side_ids": 25,
    }
    data.update(overrides)
    return data


def _result(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "evidence_label": "TIER B — MOBILE-LIKE REAL DATA",
        "counts_toward_product_gates": True,
        "partition": "test",
        "representative": True,
        "reproducible": True,
        "leakage_errors": [],
        "provenance_complete": True,
        "sealed": True,
        "top1": 0.65,
        "top5": 0.85,
        "far": 0.03,
        "far_ci95": [0.01, 0.08],
        "known_top5_after_threshold": 0.75,
        "diagnostic_top5": 0.87,
        "dataset": _tier_b(),
    }
    data.update(overrides)
    return data


def _gate(outcome: Any, name: str) -> Any:
    return next(g for g in outcome.gates if g.gate == name)


def test_policy_values_are_the_reviewed_values() -> None:
    policy = DecisionPolicy()
    assert policy.top5_target == 0.80
    assert policy.top1_preferred == 0.60
    assert policy.far_target == 0.05
    assert policy.far_strong == 0.01
    assert policy.min_known_recall_after_threshold == 0.70
    assert policy.top5_near_miss_floor == 0.70
    assert policy.far_near_miss_ceiling == 0.075
    assert policy.far_ci95_exclusion_ceiling == 0.10


def test_every_outcome_records_the_complete_policy_and_minimums() -> None:
    expected = {**asdict(DecisionPolicy()), "tier_b_minimums": asdict(TierBMinimums())}
    for result in (
        _result(),
        _result(top5=0.75),
        _result(top5=0.40),
        _result(counts_toward_product_gates=False),
        _result(partition="validation"),
    ):
        assert decide(result).policy == expected


def test_all_gates_passing_on_real_mobile_like_data_is_go() -> None:
    outcome = decide(_result())
    assert outcome.decision == "GO"
    assert all(g.status == "PASS" for g in outcome.gates)


@pytest.mark.parametrize(
    ("top5", "far", "recall"),
    [(0.80, 0.05, 0.70), (0.95, 0.0, 0.99)],
)
def test_go_boundaries_are_inclusive(top5: float, far: float, recall: float) -> None:
    outcome = decide(_result(top5=top5, far=far, known_top5_after_threshold=recall))
    assert outcome.decision == "GO"


def test_proxy_or_tier_a_evidence_can_never_be_go() -> None:
    outcome = decide(
        _result(
            evidence_label="PROXY — PIPELINE VALIDATION ONLY", counts_toward_product_gates=False
        )
    )
    assert outcome.decision == "INSUFFICIENT DATA"
    assert "no mobile-like real Tier B evidence" in outcome.reasons[0]


@pytest.mark.parametrize(
    "dataset",
    [
        _tier_b(known_side_ids=19),
        _tier_b(min_images_per_known_side_id=2),
        _tier_b(unseen_side_ids=19),
    ],
)
def test_tier_b_below_minimum_is_insufficient_data(dataset: dict[str, Any]) -> None:
    assert decide(_result(dataset=dataset)).decision == "INSUFFICIENT DATA"


def test_validation_partition_results_cannot_decide() -> None:
    assert decide(_result(partition="validation")).decision == "INSUFFICIENT DATA"


def test_reproducibility_or_integrity_failure_blocks_go() -> None:
    assert decide(_result(reproducible=False)).decision != "GO"
    assert decide(_result(leakage_errors=["session leakage"])).decision != "GO"
    assert decide(_result(sealed=False)).decision != "GO"


@pytest.mark.parametrize("top5", [0.70, 0.76, 0.7999])
def test_top5_near_miss_alone_is_conditional_go(top5: float) -> None:
    outcome = decide(_result(top5=top5))
    assert outcome.decision == "CONDITIONAL GO"
    assert _gate(outcome, "C").status == "NEAR"
    assert outcome.next_action


def test_top5_below_near_miss_floor_is_no_go() -> None:
    outcome = decide(_result(top5=0.6999, top1=0.5))
    assert _gate(outcome, "C").status == "FAIL"
    assert outcome.decision == "NO-GO"


@pytest.mark.parametrize("far", [0.0501, 0.07, 0.075])
def test_far_near_miss_alone_is_conditional_go(far: float) -> None:
    outcome = decide(_result(far=far))
    assert _gate(outcome, "D").status == "NEAR"
    assert outcome.decision == "CONDITIONAL GO"


def test_far_above_near_miss_ceiling_fails_gate_d() -> None:
    outcome = decide(_result(far=0.0751))
    assert _gate(outcome, "D").status == "FAIL"
    assert outcome.decision == "NO-GO"


def test_two_simultaneous_near_misses_are_not_conditional_go() -> None:
    outcome = decide(_result(top5=0.76, far=0.07))
    assert outcome.decision == "NO-GO"
    assert "not a single bounded weakness" in outcome.reasons[0]


@pytest.mark.parametrize("recall", [0.2, 0.6999])
def test_known_recall_below_minimum_is_a_gate_d_failure_not_a_near_miss(recall: float) -> None:
    outcome = decide(_result(known_top5_after_threshold=recall))
    assert _gate(outcome, "D").status == "FAIL"
    assert outcome.decision == "NO-GO"


def test_far_interval_that_cannot_exclude_ten_percent_downgrades_go() -> None:
    outcome = decide(_result(far=0.03, far_ci95=[0.01, 0.1001]))
    assert outcome.decision == "CONDITIONAL GO"
    assert outcome.next_action == FAR_CI_CAUTION_ACTION
    assert (
        outcome.next_action == "collect additional unseen-individual evidence and re-evaluate "
        "under a new sealed protocol"
    )
    assert all(g.status == "PASS" for g in outcome.gates)


def test_far_interval_upper_bound_at_ten_percent_still_allows_go() -> None:
    assert decide(_result(far=0.03, far_ci95=[0.0, 0.10])).decision == "GO"


def test_missing_far_interval_cannot_support_go() -> None:
    result = _result()
    del result["far_ci95"]
    outcome = decide(result)
    assert outcome.decision == "CONDITIONAL GO"
    assert outcome.next_action == FAR_CI_CAUTION_ACTION


def test_far_interval_caution_is_noted_but_does_not_rewrite_other_outcomes() -> None:
    outcome = decide(_result(top5=0.76, far_ci95=[0.01, 0.2]))
    assert outcome.decision == "CONDITIONAL GO"
    assert outcome.next_action != FAR_CI_CAUTION_ACTION
    assert any("cannot exclude FAR > 0.1" in note for note in outcome.notes)


def test_poor_retrieval_is_no_go() -> None:
    assert decide(_result(top5=0.40, top1=0.2)).decision == "NO-GO"


def test_high_false_accept_is_no_go() -> None:
    assert decide(_result(far=0.25)).decision == "NO-GO"


def test_collapse_without_same_session_images_fails_gate_e() -> None:
    outcome = decide(_result(diagnostic_top5=0.99, top5=0.81))
    assert outcome.decision == "GO"
    collapsed = decide(_result(diagnostic_top5=0.99, top5=0.55))
    assert _gate(collapsed, "E").status == "FAIL"
    assert collapsed.decision == "NO-GO"


def test_top1_preference_is_reported_but_not_mandatory() -> None:
    outcome = decide(_result(top1=0.5))
    assert outcome.decision == "GO"
    assert any("Top-1" in note for note in outcome.notes)


def test_minimums_are_the_requirement_values() -> None:
    m = TierBMinimums()
    assert (m.known_side_ids, m.images_per_known_side_id, m.unseen_side_ids) == (20, 3, 20)
