from __future__ import annotations

from typing import Any

import pytest

from toem_reid.decision import TierBMinimums, decide


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
        "known_top5_after_threshold": 0.70,
        "diagnostic_top5": 0.87,
        "dataset": _tier_b(),
    }
    data.update(overrides)
    return data


def test_all_gates_passing_on_real_mobile_like_data_is_go() -> None:
    outcome = decide(_result())
    assert outcome.decision == "GO"
    assert all(g.status == "PASS" for g in outcome.gates)


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


def test_near_miss_on_one_gate_is_conditional_go() -> None:
    outcome = decide(_result(top5=0.76))
    assert outcome.decision == "CONDITIONAL GO"
    assert outcome.next_action


def test_near_miss_on_far_is_conditional_go() -> None:
    assert decide(_result(far=0.07)).decision == "CONDITIONAL GO"


def test_far_met_by_destroying_known_recall_fails_gate_d() -> None:
    outcome = decide(_result(known_top5_after_threshold=0.2))
    gate_d = next(g for g in outcome.gates if g.gate == "D")
    assert gate_d.status == "FAIL"
    assert outcome.decision != "GO"


def test_poor_retrieval_is_no_go() -> None:
    assert decide(_result(top5=0.40, top1=0.2)).decision == "NO-GO"


def test_high_false_accept_is_no_go() -> None:
    assert decide(_result(far=0.25)).decision == "NO-GO"


def test_collapse_without_same_session_images_fails_gate_e() -> None:
    outcome = decide(_result(diagnostic_top5=0.99, top5=0.81))
    assert outcome.decision == "GO"
    collapsed = decide(_result(diagnostic_top5=0.99, top5=0.55))
    gate_e = next(g for g in collapsed.gates if g.gate == "E")
    assert gate_e.status == "FAIL"
    assert collapsed.decision == "NO-GO"


def test_top1_preference_is_reported_but_not_mandatory() -> None:
    outcome = decide(_result(top1=0.5))
    assert outcome.decision == "GO"
    assert any("Top-1" in note for note in outcome.notes)


def test_minimums_are_the_requirement_values() -> None:
    m = TierBMinimums()
    assert (m.known_side_ids, m.images_per_known_side_id, m.unseen_side_ids) == (20, 3, 20)
