"""Leakage grouping: exact/near duplicates, bursts, derived frames and sessions.

Images linked by any of these relations must end up on the same side of every
split boundary (Requirement 004 section 10). Links are only ever made within a
single Side-ID; a near duplicate or shared source frame across two Side-IDs is a
label conflict and stops the pipeline instead of being silently merged.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from itertools import combinations

from toem_reid.images import hamming
from toem_reid.manifest import ManifestRecord


class LeakageError(ValueError):
    pass


class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Smallest ID becomes the root so group IDs never depend on input order.
            low, high = sorted((ra, rb))
            self.parent[high] = low


def near_duplicate_pairs(hashes: Mapping[str, int], max_distance: int) -> list[tuple[str, str]]:
    ids = sorted(hashes)
    return [
        (a, b) for a, b in combinations(ids, 2) if hamming(hashes[a], hashes[b]) <= max_distance
    ]


def _timestamp(value: str) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def leakage_groups(
    records: Sequence[ManifestRecord],
    near_duplicates: Iterable[tuple[str, str]],
    burst_seconds: float = 0.0,
) -> dict[str, str]:
    """Map image_id -> deterministic group ID (``g:<smallest image_id in group>``)."""
    by_id = {r.image_id: r for r in records}
    uf = _UnionFind(sorted(by_id))
    conflicts: list[str] = []

    def link(a: str, b: str, reason: str) -> None:
        if by_id[a].side_id != by_id[b].side_id:
            conflicts.append(
                f"label conflict: {a} and {b} are {reason} but labelled "
                f"{by_id[a].side_id} and {by_id[b].side_id}"
            )
            return
        uf.union(a, b)

    by_session: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_frame: dict[str, list[str]] = defaultdict(list)
    by_side: dict[str, list[ManifestRecord]] = defaultdict(list)
    for r in sorted(records, key=lambda x: x.image_id):
        by_session[(r.side_id, r.capture_session_id)].append(r.image_id)
        frame = r.attributes.get("source_frame")
        if frame:
            by_frame[f"{r.dataset_id}:{frame}"].append(r.image_id)
        by_side[r.side_id].append(r)
    for ids in by_session.values():
        for other in ids[1:]:
            uf.union(ids[0], other)
    for ids in by_frame.values():
        for other in ids[1:]:
            link(ids[0], other, "derived from the same source frame")
    for a, b in near_duplicates:
        if a in by_id and b in by_id:
            link(a, b, "near duplicates")
    if burst_seconds > 0:
        for side_records in by_side.values():
            timed = [
                (t, r.image_id)
                for r in side_records
                if (t := _timestamp(r.capture_timestamp_if_known))
            ]
            for (t1, a), (t2, b) in combinations(timed, 2):
                if (t1.tzinfo is None) == (t2.tzinfo is None) and abs(
                    (t2 - t1).total_seconds()
                ) <= burst_seconds:
                    uf.union(a, b)
    if conflicts:
        raise LeakageError("; ".join(sorted(conflicts)))
    return {image_id: f"g:{uf.find(image_id)}" for image_id in sorted(by_id)}
