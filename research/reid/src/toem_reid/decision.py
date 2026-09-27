"""Evidence gates A-E and the four-way decision (Requirement 004 sections 14-15).

The mandatory targets come from the requirement. The values it does not fix
numerically were settled by the independent review of commit 3259571 and are
recorded, complete, in every decision outcome (``Outcome.policy``):

* GO needs session-disjoint sealed Top-5 >= 0.80, unknown FAR <= 0.05 and known
  Top-5 recall after the UNKNOWN threshold >= 0.70, plus Gates A, B and E.
* CONDITIONAL GO allows exactly one bounded weakness: Top-5 in [0.70, 0.80) *or*
  FAR in (0.05, 0.075]. Two simultaneous weaknesses are NO-GO.
* Known recall after threshold below 0.70 is not a near miss: Gate D fails.
* Statistical caution: if every gate passes but the FAR 95 % interval cannot
  exclude FAR > 0.10 (or no interval is available) the outcome is downgraded to
  CONDITIONAL GO with ``FAR_CI_CAUTION_ACTION``.

Thresholds are never tuned on the sealed TEST partition; this module only
judges results that were produced with a validation-calibrated threshold.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Final

FAR_CI_CAUTION_ACTION: Final = (
    "collect additional unseen-individual evidence and re-evaluate under a new sealed protocol"
)


@dataclass(frozen=True, slots=True)
class TierBMinimums:
    known_side_ids: int = 20
    images_per_known_side_id: int = 3
    unseen_side_ids: int = 20


@dataclass(frozen=True, slots=True)
class DecisionPolicy:
    """Reviewed decision policy (independent review of 3259571)."""

    top5_target: float = 0.80
    top1_preferred: float = 0.60
    far_target: float = 0.05
    far_strong: float = 0.01
    min_known_recall_after_threshold: float = 0.70
    top5_near_miss_floor: float = 0.70
    far_near_miss_ceiling: float = 0.075
    far_ci95_exclusion_ceiling: float = 0.10


@dataclass(frozen=True, slots=True)
class Gate:
    gate: str
    name: str
    status: str  # PASS | NEAR | FAIL
    detail: str


@dataclass(frozen=True, slots=True)
class Outcome:
    decision: str
    gates: list[Gate]
    reasons: list[str]
    notes: list[str] = field(default_factory=list)
    next_action: str = ""
    policy: dict[str, Any] = field(default_factory=dict)


def policy_record(policy: DecisionPolicy, minimums: TierBMinimums) -> dict[str, Any]:
    return {**asdict(policy), "tier_b_minimums": asdict(minimums)}


def _data_shortfalls(dataset: Mapping[str, Any], minimums: TierBMinimums) -> list[str]:
    problems = []
    if dataset.get("known_side_ids", 0) < minimums.known_side_ids:
        problems.append(
            f"known Side-IDs {dataset.get('known_side_ids', 0)} < {minimums.known_side_ids}"
        )
    if dataset.get("min_images_per_known_side_id", 0) < minimums.images_per_known_side_id:
        problems.append(
            f"images per known Side-ID {dataset.get('min_images_per_known_side_id', 0)} < "
            f"{minimums.images_per_known_side_id}"
        )
    if dataset.get("unseen_side_ids", 0) < minimums.unseen_side_ids:
        problems.append(
            f"unseen Side-IDs {dataset.get('unseen_side_ids', 0)} < {minimums.unseen_side_ids}"
        )
    return problems


def _far_upper(result: Mapping[str, Any]) -> float | None:
    interval = result.get("far_ci95")
    if not isinstance(interval, Sequence) or len(interval) != 2:
        return None
    return float(interval[1])


def decide(
    result: Mapping[str, Any],
    minimums: TierBMinimums | None = None,
    policy: DecisionPolicy | None = None,
) -> Outcome:
    minimums = minimums or TierBMinimums()
    policy = policy or DecisionPolicy()
    recorded = policy_record(policy, minimums)

    def outcome(
        decision: str,
        gates: list[Gate],
        reasons: list[str],
        notes: list[str] | None = None,
        next_action: str = "",
    ) -> Outcome:
        return Outcome(decision, gates, reasons, notes or [], next_action, recorded)

    gate_a = Gate(
        "A",
        "Reproducibility",
        "PASS" if result.get("reproducible") else "FAIL",
        "repeat evaluation reproduced the metrics"
        if result.get("reproducible")
        else "not reproduced",
    )
    integrity_problems = list(result.get("leakage_errors", []))
    if not result.get("provenance_complete"):
        integrity_problems.append("provenance or licence incomplete")
    if not result.get("sealed"):
        integrity_problems.append("test set was not sealed before final evaluation")
    gate_b = Gate(
        "B",
        "Data integrity",
        "FAIL" if integrity_problems else "PASS",
        "; ".join(integrity_problems) or "no leakage detected; provenance recorded; test sealed",
    )
    gates = [gate_a, gate_b]

    if not result.get("counts_toward_product_gates"):
        label = result.get("evidence_label", "unlabelled")
        return outcome(
            "INSUFFICIENT DATA", gates, [f"no mobile-like real Tier B evidence ({label})"]
        )
    if result.get("partition") != "test" or not result.get("representative"):
        return outcome(
            "INSUFFICIENT DATA",
            gates,
            ["decisions require the session-disjoint sealed TEST partition"],
        )
    shortfalls = _data_shortfalls(result.get("dataset", {}), minimums)
    if shortfalls:
        return outcome(
            "INSUFFICIENT DATA", gates, ["Tier B below minimum: " + "; ".join(shortfalls)]
        )
    if gate_a.status == "FAIL" or gate_b.status == "FAIL":
        return outcome(
            "INSUFFICIENT DATA", gates, ["evidence is not trustworthy until Gates A and B pass"]
        )

    top5, top1, far = float(result["top5"]), float(result["top1"]), float(result["far"])
    recall = float(result["known_top5_after_threshold"])

    def band(value: float) -> str:
        if value >= policy.top5_target:
            return "PASS"
        return "NEAR" if value >= policy.top5_near_miss_floor else "FAIL"

    gate_c = Gate(
        "C",
        "Candidate usefulness",
        band(top5),
        f"session-disjoint Top-5 {top5:.3f} (target >= {policy.top5_target}; "
        f"near miss >= {policy.top5_near_miss_floor})",
    )
    if recall < policy.min_known_recall_after_threshold:
        d_status = "FAIL"
    elif far <= policy.far_target:
        d_status = "PASS"
    elif far <= policy.far_near_miss_ceiling:
        d_status = "NEAR"
    else:
        d_status = "FAIL"
    gate_d = Gate(
        "D",
        "Unknown safety",
        d_status,
        f"FAR {far:.3f} (target <= {policy.far_target}; near miss <= "
        f"{policy.far_near_miss_ceiling}); known Top-5 after threshold {recall:.3f} "
        f"(minimum {policy.min_known_recall_after_threshold}, never a near miss)",
    )
    diagnostic = result.get("diagnostic_top5")
    e_detail = f"session-disjoint Top-5 {top5:.3f}"
    if diagnostic is not None:
        e_detail += f" vs random-image diagnostic {float(diagnostic):.3f}"
    gate_e = Gate("E", "No leakage dependency", band(top5), e_detail)
    gates += [gate_c, gate_d, gate_e]

    notes = []
    if top1 < policy.top1_preferred:
        notes.append(f"Top-1 {top1:.3f} is below the preferred {policy.top1_preferred}")
    if far <= policy.far_strong:
        notes.append(f"strong open-set result: FAR {far:.3f} <= {policy.far_strong}")
    far_upper = _far_upper(result)
    far_ci_weak = far_upper is None or far_upper > policy.far_ci95_exclusion_ceiling
    if far_ci_weak:
        shown = "unavailable" if far_upper is None else f"{far_upper:.3f}"
        notes.append(
            f"FAR 95% CI upper bound {shown} cannot exclude FAR > "
            f"{policy.far_ci95_exclusion_ceiling}"
        )

    statuses = {g.gate: g.status for g in gates[2:]}
    if "FAIL" in statuses.values():
        failed = [g.gate for g in gates[2:] if g.status == "FAIL"]
        return outcome("NO-GO", gates, [f"gate(s) {', '.join(failed)} failed"], notes)
    weaknesses = {
        "retrieval" if k in ("C", "E") else "unknown safety"
        for k, s in statuses.items()
        if s == "NEAR"
    }
    if not weaknesses:
        if far_ci_weak:
            return outcome(
                "CONDITIONAL GO",
                gates,
                [
                    "point estimates support GO but the FAR 95% interval cannot exclude "
                    f"FAR > {policy.far_ci95_exclusion_ceiling}"
                ],
                notes,
                FAR_CI_CAUTION_ACTION,
            )
        return outcome("GO", gates, ["all mandatory evidence gates passed"], notes)
    if len(weaknesses) == 1:
        weakness = next(iter(weaknesses))
        action = (
            "collect more mobile-like sessions for the weakest Side-IDs and re-run the "
            "pre-registered configuration on a new sealed test protocol"
            if weakness == "retrieval"
            else FAR_CI_CAUTION_ACTION
        )
        return outcome(
            "CONDITIONAL GO", gates, [f"one bounded weakness: {weakness}"], notes, action
        )
    return outcome(
        "NO-GO",
        gates,
        ["more than one gate is only a near miss; not a single bounded weakness"],
        notes,
    )
