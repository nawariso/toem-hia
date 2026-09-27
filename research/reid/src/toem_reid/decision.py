"""Evidence gates A-E and the four-way decision (Requirement 004 sections 14-15).

The mandatory targets come straight from the requirement. Two values the
requirement does not state numerically are explicit, recorded, *provisional*
parameters that the independent reviewer must confirm before any real
decision is issued:

* ``min_known_recall_after_threshold`` — what "useful known-query candidate
  recall" means for Gate D;
* the CONDITIONAL GO near-miss band (the requirement's only example is
  Top-5 = 76 %, FAR = 3 %).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Final

TOP5_TARGET: Final = 0.80
TOP1_PREFERRED: Final = 0.60
FAR_TARGET: Final = 0.05
FAR_STRONG: Final = 0.01


@dataclass(frozen=True, slots=True)
class TierBMinimums:
    known_side_ids: int = 20
    images_per_known_side_id: int = 3
    unseen_side_ids: int = 20


@dataclass(frozen=True, slots=True)
class ProvisionalPolicy:
    """PROVISIONAL — values not fixed by Requirement 004; reviewer to confirm."""

    min_known_recall_after_threshold: float = 0.50
    top5_near_miss_floor: float = 0.70
    far_near_miss_ceiling: float = 0.10


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


def _insufficient(reason: str, gates: list[Gate], policy: ProvisionalPolicy) -> Outcome:
    return Outcome("INSUFFICIENT DATA", gates, [reason], policy=asdict(policy))


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


def decide(
    result: Mapping[str, Any],
    minimums: TierBMinimums | None = None,
    policy: ProvisionalPolicy | None = None,
) -> Outcome:
    minimums = minimums or TierBMinimums()
    policy = policy or ProvisionalPolicy()
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
        return _insufficient(
            f"no mobile-like real Tier B evidence ({result.get('evidence_label', 'unlabelled')})",
            gates,
            policy,
        )
    if result.get("partition") != "test" or not result.get("representative"):
        return _insufficient(
            "decisions require the session-disjoint sealed TEST partition", gates, policy
        )
    shortfalls = _data_shortfalls(result.get("dataset", {}), minimums)
    if shortfalls:
        return _insufficient("Tier B below minimum: " + "; ".join(shortfalls), gates, policy)
    if gate_a.status == "FAIL" or gate_b.status == "FAIL":
        return _insufficient("evidence is not trustworthy until Gates A and B pass", gates, policy)

    top5, top1, far = float(result["top5"]), float(result["top1"]), float(result["far"])
    recall = float(result["known_top5_after_threshold"])

    def band(value: float) -> str:
        if value >= TOP5_TARGET:
            return "PASS"
        return "NEAR" if value >= policy.top5_near_miss_floor else "FAIL"

    gate_c = Gate(
        "C",
        "Candidate usefulness",
        band(top5),
        f"session-disjoint Top-5 {top5:.3f} (target >= {TOP5_TARGET})",
    )
    if recall < policy.min_known_recall_after_threshold:
        d_status = "FAIL"
    elif far <= FAR_TARGET:
        d_status = "PASS"
    elif far <= policy.far_near_miss_ceiling:
        d_status = "NEAR"
    else:
        d_status = "FAIL"
    gate_d = Gate(
        "D",
        "Unknown safety",
        d_status,
        f"FAR {far:.3f} (target <= {FAR_TARGET}); known Top-5 after threshold {recall:.3f} "
        f"(provisional minimum {policy.min_known_recall_after_threshold})",
    )
    diagnostic = result.get("diagnostic_top5")
    e_detail = f"session-disjoint Top-5 {top5:.3f}"
    if diagnostic is not None:
        e_detail += f" vs random-image diagnostic {float(diagnostic):.3f}"
    gate_e = Gate("E", "No leakage dependency", band(top5), e_detail)
    gates += [gate_c, gate_d, gate_e]

    notes = []
    if top1 < TOP1_PREFERRED:
        notes.append(f"Top-1 {top1:.3f} is below the preferred {TOP1_PREFERRED}")
    if far <= FAR_STRONG:
        notes.append(f"strong open-set result: FAR {far:.3f} <= {FAR_STRONG}")

    statuses = {g.gate: g.status for g in gates[2:]}
    if "FAIL" in statuses.values():
        failed = [g.gate for g in gates[2:] if g.status == "FAIL"]
        return Outcome(
            "NO-GO", gates, [f"gate(s) {', '.join(failed)} failed"], notes, policy=asdict(policy)
        )
    weaknesses = {
        "retrieval" if k in ("C", "E") else "unknown safety"
        for k, s in statuses.items()
        if s == "NEAR"
    }
    if not weaknesses:
        return Outcome(
            "GO", gates, ["all mandatory evidence gates passed"], notes, policy=asdict(policy)
        )
    if len(weaknesses) == 1:
        weakness = next(iter(weaknesses))
        action = (
            "collect more mobile-like sessions for the weakest Side-IDs and re-run the "
            "pre-registered configuration on a new sealed test protocol"
            if weakness == "retrieval"
            else "collect more unseen Side-IDs to re-calibrate the UNKNOWN threshold on validation "
            "and confirm on a new sealed test protocol"
        )
        return Outcome(
            "CONDITIONAL GO",
            gates,
            [f"one bounded weakness: {weakness}"],
            notes,
            next_action=action,
            policy=asdict(policy),
        )
    return Outcome(
        "NO-GO",
        gates,
        ["more than one gate is only a near miss; not a single bounded weakness"],
        notes,
        policy=asdict(policy),
    )
