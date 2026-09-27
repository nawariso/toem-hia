"""Retrieval, open-set and stratified metrics (Requirement 004 section 13).

Scores are *similarities* (higher = more alike). They are evidence for ranking
and thresholding only and are never interpreted as probabilities (section 26).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

DEFAULT_KS = (1, 3, 5)
Z95 = 1.959963984540054


@dataclass(frozen=True, slots=True)
class Ranking:
    """Per-query identity ranking; ``order[q]`` indexes ``side_ids`` best-first."""

    side_ids: list[str]
    scores: NDArray[np.float64]
    order: NDArray[np.int64]

    def best_scores(self) -> NDArray[np.float64]:
        if not self.side_ids:
            return np.full(self.scores.shape[0], -np.inf)
        return np.asarray(
            self.scores[np.arange(self.scores.shape[0]), self.order[:, 0]], dtype=np.float64
        )

    def top(self, query: int, k: int) -> list[tuple[str, float]]:
        return [(self.side_ids[i], float(self.scores[query, i])) for i in self.order[query, :k]]


def rank_identities(
    similarity: NDArray[np.floating[Any]], gallery_side_ids: Sequence[str]
) -> Ranking:
    """Aggregate image-level similarity to Side-ID level by max, then rank.

    Ties are broken by Side-ID string so the ranking never depends on gallery order.
    """
    matrix = np.asarray(similarity, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != len(gallery_side_ids):
        raise ValueError("similarity columns must match the gallery images")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("similarity values must be finite")
    side_ids = sorted(set(gallery_side_ids))
    column = {s: i for i, s in enumerate(side_ids)}
    scores = np.full((matrix.shape[0], len(side_ids)), -np.inf)
    for j, side in enumerate(gallery_side_ids):
        c = column[side]
        scores[:, c] = np.maximum(scores[:, c], matrix[:, j])
    # lexsort: last key is primary. Negate scores for descending; index ascending breaks ties.
    tie = np.broadcast_to(np.arange(len(side_ids)), scores.shape)
    order = np.lexsort((tie, -scores), axis=1).astype(np.int64)
    return Ranking(side_ids, scores, order)


def wilson_interval(successes: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + Z95**2 / n
    centre = (p + Z95**2 / (2 * n)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95**2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _rate(count: int, n: int) -> dict[str, Any]:
    return {
        "count": count,
        "n": n,
        "rate": count / n if n else float("nan"),
        "ci95": list(wilson_interval(count, n)),
    }


def true_ranks(ranking: Ranking, truth: Sequence[str]) -> list[int]:
    ranks = []
    position = {s: i for i, s in enumerate(ranking.side_ids)}
    for q, side in enumerate(truth):
        if side not in position:
            raise ValueError(f"query truth {side} is not in the gallery")
        ranks.append(int(np.nonzero(ranking.order[q] == position[side])[0][0]) + 1)
    return ranks


def closed_set_metrics(
    ranking: Ranking, truth: Sequence[str], ks: Sequence[int] = DEFAULT_KS
) -> dict[str, Any]:
    ranks = true_ranks(ranking, truth)
    n = len(ranks)
    gallery = len(ranking.side_ids)
    cmc = [
        sum(1 for r in ranks if r <= k) / n if n else float("nan") for k in range(1, gallery + 1)
    ]
    return {
        "n": n,
        "gallery_side_ids": gallery,
        "top_k": {str(k): _rate(sum(1 for r in ranks if r <= k), n) for k in ks},
        "mrr": sum(1 / r for r in ranks) / n if n else float("nan"),
        "cmc": cmc,
        "ranks": ranks,
    }


def calibrate_unknown_threshold(
    unknown_best: NDArray[np.floating[Any]], target_far: float
) -> float:
    """Smallest threshold whose false-accept rate on these (validation) unknowns is <= target.

    A query is accepted when its best score is >= threshold. Ties are resolved
    conservatively: the returned value is strictly above the highest score that
    would push the rate over target.
    """
    scores = np.sort(np.asarray(unknown_best, dtype=np.float64))[::-1]
    if scores.size == 0:
        raise ValueError("threshold calibration needs at least one unknown query")
    if not 0 <= target_far < 1:
        raise ValueError("target_far must be in [0, 1)")
    allowed = math.floor(target_far * scores.size)
    return float(np.nextafter(scores[allowed], np.inf))


def open_set_metrics(
    known: Ranking,
    known_truth: Sequence[str],
    unknown: Ranking,
    threshold: float,
    ks: Sequence[int] = DEFAULT_KS,
) -> dict[str, Any]:
    ranks = true_ranks(known, known_truth)
    known_best = known.best_scores()
    unknown_best = unknown.best_scores()
    accepted = known_best >= threshold
    n_known = len(ranks)
    n_unknown = int(unknown_best.size)
    false_accepts = int(np.sum(unknown_best >= threshold))
    return {
        "threshold": threshold,
        "known_acceptance": _rate(int(np.sum(accepted)), n_known),
        "false_rejection": _rate(int(np.sum(~accepted)), n_known),
        "known_top_k_after_threshold": {
            str(k): _rate(
                sum(1 for a, r in zip(accepted, ranks, strict=True) if a and r <= k), n_known
            )
            for k in ks
        },
        "unknown_false_accept": _rate(false_accepts, n_unknown),
        "decisions": {
            "known": ["CANDIDATES" if a else "UNKNOWN" for a in accepted],
            "unknown": ["CANDIDATES" if s >= threshold else "UNKNOWN" for s in unknown_best],
        },
    }


def stratify(
    ranks: Sequence[int], values: Sequence[str], k: int, min_n: int = 10
) -> dict[str, dict[str, Any]]:
    """Top-k rate per subgroup; subgroups below ``min_n`` are flagged, never hidden."""
    buckets: dict[str, list[int]] = defaultdict(list)
    for rank, value in zip(ranks, values, strict=True):
        buckets[value or "unrecorded"].append(rank)
    table: dict[str, dict[str, Any]] = {}
    for value in sorted(buckets):
        group = buckets[value]
        hits = sum(1 for r in group if r <= k)
        table[value] = {
            "n": len(group),
            "top_k_rate": hits / len(group),
            "ci95": list(wilson_interval(hits, len(group))),
            "reportable": len(group) >= min_n,
        }
    return table
