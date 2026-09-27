"""Sealed, deterministic, leakage-safe splits (Requirement 004 sections 10-12).

Partitioning is by *individual* (both sides of an animal stay together) into
TRAIN / VALIDATION / SEALED TEST. Inside VALIDATION and TEST:

* some individuals are held out as UNKNOWN: none of their images enter the
  gallery, all become open-set queries. Individuals that cannot supply a
  session-disjoint known query (only one leakage group on every side) are
  always unknown, then more are drawn until ``unknown_fraction`` is reached;
* for the remaining KNOWN individuals, each Side-ID's leakage groups (session,
  burst, near-duplicate, shared source frame) are ordered chronologically where
  timestamps exist; the latest groups become known queries and the earlier
  groups the gallery, so a query never shares a session with its references.

The output is a JSON spec whose ``split_sha256`` seals its content.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from math import floor
from typing import Any, Final

from toem_reid.dedup import LeakageError, leakage_groups
from toem_reid.hashing import sha256_canonical
from toem_reid.manifest import ManifestRecord

SCHEMA: Final = "toem-reid-split/1"
DIAGNOSTIC_LABEL: Final = "NON-REPRESENTATIVE / DIAGNOSTIC ONLY"
SESSION_LABEL: Final = "SESSION-DISJOINT"
EVAL_ROLES: Final = frozenset({"gallery", "query_known", "query_unknown"})


class SplitError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SplitParameters:
    seed: int
    train_fraction: float
    validation_fraction: float
    unknown_fraction: float
    query_fraction: float = 0.5
    burst_seconds: float = 0.0

    def validate(self) -> None:
        test = 1.0 - self.train_fraction - self.validation_fraction
        if self.train_fraction < 0 or self.validation_fraction <= 0 or test <= 0:
            raise SplitError(
                "fractions must satisfy train >= 0, validation > 0, train + validation < 1"
            )
        if not 0 <= self.unknown_fraction < 1:
            raise SplitError("unknown_fraction must be in [0, 1)")
        if not 0 < self.query_fraction < 1:
            raise SplitError("query_fraction must be in (0, 1)")


def _rank(seed: int, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def _individual(record: ManifestRecord) -> str:
    return f"{record.dataset_id}:{record.individual_label}"


def _partition_individuals(individuals: Iterable[str], params: SplitParameters) -> dict[str, str]:
    ordered = sorted(set(individuals), key=lambda key: _rank(params.seed, key))
    n = len(ordered)
    n_train = floor(n * params.train_fraction)
    n_val = floor(n * params.validation_fraction)
    out: dict[str, str] = {}
    for index, key in enumerate(ordered):
        out[key] = (
            "train" if index < n_train else "validation" if index < n_train + n_val else "test"
        )
    return out


def _choose_unknown(
    partition_of: Mapping[str, str], eligible: Callable[[str], bool], params: SplitParameters
) -> set[str]:
    unknown: set[str] = set()
    for partition in ("validation", "test"):
        members = sorted(
            (k for k, p in partition_of.items() if p == partition),
            key=lambda key: _rank(params.seed + 1, key),
        )
        target = round(len(members) * params.unknown_fraction)
        forced = [k for k in members if not eligible(k)]
        unknown.update(forced)
        for key in members:
            if len(unknown.intersection(members)) >= target:
                break
            unknown.add(key)
    return unknown


def _row(
    record: ManifestRecord, partition: str, role: str, group: str, reason: str = ""
) -> dict[str, str]:
    return {
        "image_id": record.image_id,
        "individual": _individual(record),
        "side_id": record.side_id,
        "partition": partition,
        "role": role,
        "group": group,
        "reason": reason,
    }


def _seal(spec: dict[str, Any]) -> dict[str, Any]:
    spec["split_sha256"] = sha256_canonical({k: v for k, v in spec.items() if k != "split_sha256"})
    return spec


def _summary(assignments: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    roles = Counter(f"{row['partition']}/{row['role']}" for row in assignments)
    side_ids: dict[str, set[str]] = defaultdict(set)
    individuals: dict[str, set[str]] = defaultdict(set)
    for row in assignments:
        side_ids[f"{row['partition']}/{row['role']}"].add(row["side_id"])
        individuals[f"{row['partition']}/{row['role']}"].add(row["individual"])
    return {
        "images": dict(sorted(roles.items())),
        "side_ids": {k: len(v) for k, v in sorted(side_ids.items())},
        "individuals": {k: len(v) for k, v in sorted(individuals.items())},
    }


def _near_hash(pairs: Iterable[tuple[str, str]]) -> str:
    return sha256_canonical(sorted(sorted(pair) for pair in pairs))


def _group_order_key(seed: int, group: str, members: Sequence[ManifestRecord]) -> tuple[int, str]:
    stamps = sorted(r.capture_timestamp_if_known for r in members if r.capture_timestamp_if_known)
    return (0, stamps[0]) if stamps else (1, _rank(seed, group))


def build_split(
    records: Sequence[ManifestRecord],
    near_duplicates: Sequence[tuple[str, str]],
    params: SplitParameters,
    *,
    manifest_sha256: str,
    excluded: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    params.validate()
    excluded = dict(excluded or {})
    active = [r for r in records if r.image_id not in excluded]
    groups = leakage_groups(active, near_duplicates, params.burst_seconds)
    members: dict[str, dict[str, list[ManifestRecord]]] = defaultdict(lambda: defaultdict(list))
    for r in active:
        members[r.side_id][groups[r.image_id]].append(r)
    sides_of: dict[str, set[str]] = defaultdict(set)
    for r in active:
        sides_of[_individual(r)].add(r.side_id)

    partition_of = _partition_individuals(sides_of, params)
    unknown = _choose_unknown(
        partition_of,
        lambda key: any(len(members[side]) >= 2 for side in sides_of[key]),
        params,
    )

    role_of: dict[str, str] = {}
    for side_id, side_groups in members.items():
        individual = side_id.rsplit(":", 1)[0]
        partition = partition_of[individual]
        ordered = sorted(
            side_groups, key=lambda g: _group_order_key(params.seed, g, side_groups[g])
        )
        n_query = max(1, floor(len(ordered) * params.query_fraction)) if len(ordered) >= 2 else 0
        for index, group in enumerate(ordered):
            if partition == "train":
                role = "train"
            elif individual in unknown:
                role = "query_unknown"
            else:
                role = "query_known" if index >= len(ordered) - n_query else "gallery"
            for r in side_groups[group]:
                role_of[r.image_id] = role

    assignments = []
    for r in sorted(records, key=lambda x: x.image_id):
        if r.image_id in excluded:
            assignments.append(_row(r, "excluded", "excluded", "", excluded[r.image_id]))
        else:
            assignments.append(
                _row(r, partition_of[_individual(r)], role_of[r.image_id], groups[r.image_id])
            )
    spec: dict[str, Any] = {
        "schema": SCHEMA,
        "protocol": "session-disjoint",
        "representative": True,
        "label": SESSION_LABEL,
        "manifest_sha256": manifest_sha256,
        "parameters": asdict(params),
        "near_duplicates_sha256": _near_hash(near_duplicates),
        "summary": _summary(assignments),
        "assignments": assignments,
    }
    return _seal(spec)


def build_diagnostic_split(
    records: Sequence[ManifestRecord], params: SplitParameters, *, manifest_sha256: str
) -> dict[str, Any]:
    """Random per-image gallery/query split. Leaky by design; never representative."""
    params.validate()
    by_side: dict[str, list[ManifestRecord]] = defaultdict(list)
    sides_of: dict[str, set[str]] = defaultdict(set)
    for r in records:
        by_side[r.side_id].append(r)
        sides_of[_individual(r)].add(r.side_id)
    partition_of = _partition_individuals(sides_of, params)
    unknown = _choose_unknown(
        partition_of, lambda key: any(len(by_side[s]) >= 2 for s in sides_of[key]), params
    )
    role_of: dict[str, str] = {}
    for side_id, side_records in by_side.items():
        individual = side_id.rsplit(":", 1)[0]
        partition = partition_of[individual]
        ordered = sorted(side_records, key=lambda r: _rank(params.seed, r.image_id))
        n_query = max(1, floor(len(ordered) * params.query_fraction)) if len(ordered) >= 2 else 0
        for index, r in enumerate(ordered):
            if partition == "train":
                role_of[r.image_id] = "train"
            elif individual in unknown:
                role_of[r.image_id] = "query_unknown"
            else:
                role_of[r.image_id] = "query_known" if index < n_query else "gallery"
    assignments = [
        _row(r, partition_of[_individual(r)], role_of[r.image_id], f"i:{r.image_id}")
        for r in sorted(records, key=lambda x: x.image_id)
    ]
    spec: dict[str, Any] = {
        "schema": SCHEMA,
        "protocol": "random-image-diagnostic",
        "representative": False,
        "label": DIAGNOSTIC_LABEL,
        "manifest_sha256": manifest_sha256,
        "parameters": asdict(params),
        "near_duplicates_sha256": _near_hash([]),
        "summary": _summary(assignments),
        "assignments": assignments,
    }
    return _seal(spec)


def verify_split(
    spec: Mapping[str, Any],
    records: Sequence[ManifestRecord],
    near_duplicates: Sequence[tuple[str, str]],
) -> list[str]:
    """Independent re-check of every leakage and structure rule; returns all violations."""
    errors: list[str] = []
    expected = sha256_canonical({k: v for k, v in spec.items() if k != "split_sha256"})
    if spec.get("split_sha256") != expected:
        errors.append("split_sha256 does not match the split content")
    rows: list[Mapping[str, str]] = spec["assignments"]
    by_id = {r.image_id: r for r in records}
    seen = Counter(row["image_id"] for row in rows)
    errors.extend(f"image {i} not assigned" for i in sorted(set(by_id) - set(seen)))
    errors.extend(f"assigned image {i} not in manifest" for i in sorted(set(seen) - set(by_id)))
    errors.extend(f"image {i} assigned {n} times" for i, n in sorted(seen.items()) if n > 1)
    active = [row for row in rows if row["role"] != "excluded" and row["image_id"] in by_id]

    partitions: dict[str, set[str]] = defaultdict(set)
    roles_by_individual: dict[str, set[str]] = defaultdict(set)
    for row in active:
        partitions[row["individual"]].add(row["partition"])
        roles_by_individual[row["individual"]].add(row["role"])
        if (row["partition"] == "train") != (row["role"] == "train") or (
            row["partition"] != "train" and row["role"] not in EVAL_ROLES
        ):
            errors.append(f"image {row['image_id']} has role {row['role']} in {row['partition']}")
    errors.extend(
        f"individual {k} spans partitions {sorted(v)}"
        for k, v in sorted(partitions.items())
        if len(v) > 1
    )
    for individual, roles in sorted(roles_by_individual.items()):
        if "query_unknown" in roles and roles & {"gallery", "query_known"}:
            errors.append(f"unknown individual {individual} has gallery or known-query images")

    gallery_sides = {
        (row["partition"], row["side_id"]) for row in active if row["role"] == "gallery"
    }
    for key in sorted(
        {(row["partition"], row["side_id"]) for row in active if row["role"] == "query_known"}
    ):
        if key not in gallery_sides:
            errors.append(f"known query Side-ID {key[1]} has no gallery in {key[0]}")

    if spec.get("representative"):
        session_roles: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in active:
            if row["role"] in ("gallery", "query_known"):
                session_roles[(row["side_id"], by_id[row["image_id"]].capture_session_id)].add(
                    row["role"]
                )
        errors.extend(
            f"session leakage: {side} session {session} is both gallery and query"
            for (side, session), roles in sorted(session_roles.items())
            if len(roles) > 1
        )
        burst = float(spec["parameters"].get("burst_seconds", 0.0))
        try:
            groups = leakage_groups(
                [by_id[row["image_id"]] for row in active], near_duplicates, burst
            )
        except LeakageError as error:
            errors.append(str(error))
        else:
            group_roles: dict[str, set[str]] = defaultdict(set)
            for row in active:
                group_roles[groups[row["image_id"]]].add(row["role"])
            errors.extend(
                f"leakage group {g} (session/burst/near-duplicate/source frame) "
                f"spans roles {sorted(r)}"
                for g, r in sorted(group_roles.items())
                if len(r) > 1
            )
    return errors
