"""Build an immutable manifest, perceptual-hash sidecar and inventory report.

Input is a labels CSV (``relative_path,individual_label,side,capture_session_id``
plus optional ``capture_timestamp_if_known``, ``notes`` and ``attr_*`` columns)
and a directory of images outside Git. Identity labels come only from that CSV.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from toem_reid.hashing import sha256_bytes, sha256_file
from toem_reid.images import SUPPORTED_SUFFIXES, dhash, load_image, store_path
from toem_reid.manifest import (
    ATTRIBUTE_PREFIX,
    ManifestError,
    ManifestRecord,
    _forbidden,
    manifest_sha256,
    write_manifest,
)
from toem_reid.provenance import load_provenance

LABEL_REQUIRED = ("relative_path", "individual_label", "side", "capture_session_id")
LABEL_OPTIONAL = ("capture_timestamp_if_known", "notes")


class IngestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IngestResult:
    manifest_path: Path
    phash_path: Path
    report_path: Path
    manifest_sha256: str


def _safe_relative(relative: str) -> PurePosixPath | None:
    normalized = relative.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ":" in normalized or ".." in path.parts or not path.parts:
        return None
    return path


def _read_labels(labels: Path) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(labels.read_text(encoding="utf-8"), newline=""))
    header = list(reader.fieldnames or [])
    errors = [f"labels missing required column: {c}" for c in LABEL_REQUIRED if c not in header]
    for column in header:
        if _forbidden(column):
            errors.append(
                f"labels column {column}: location, user and device data must not be used as "
                "identity evidence"
            )
        elif column not in (*LABEL_REQUIRED, *LABEL_OPTIONAL) and not column.startswith(
            ATTRIBUTE_PREFIX
        ):
            errors.append(f"labels column {column} is not recognised")
    if errors:
        raise IngestError("; ".join(errors))
    return list(reader)


def ingest(
    labels: Path,
    image_root: Path,
    provenance_path: Path,
    *,
    out_dir: Path,
    store: Path | None = None,
) -> IngestResult:
    provenance = load_provenance(provenance_path)
    rows = _read_labels(labels)
    root = image_root.resolve()
    errors: list[str] = []
    candidates: list[tuple[str, dict[str, str], Path, str]] = []
    for line, row in enumerate(rows, start=2):
        relative = _safe_relative(row["relative_path"])
        if relative is None:
            errors.append(f"row {line}: {row['relative_path']!r} is outside the image root")
            continue
        path = root.joinpath(*relative.parts)
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            errors.append(f"row {line}: unsupported image type {path.suffix!r}")
            continue
        if not path.is_file():
            errors.append(f"row {line}: file not found: {relative}")
            continue
        candidates.append((str(relative), row, path, sha256_file(path)))
    if errors:
        raise IngestError("; ".join(errors))

    by_sha: dict[str, list[tuple[str, dict[str, str], Path, str]]] = defaultdict(list)
    for item in candidates:
        by_sha[item[3]].append(item)
    kept: list[tuple[str, dict[str, str], Path, str]] = []
    dropped: list[dict[str, str]] = []
    identity_fields = ("individual_label", "side", "capture_session_id")
    for sha in sorted(by_sha):
        group = sorted(by_sha[sha], key=lambda item: item[0])
        labels_seen = {tuple(item[1][f] for f in identity_fields) for item in group}
        if len(labels_seen) > 1:
            names = ", ".join(item[0] for item in group)
            errors.append(f"conflicting labels for identical file {sha[:12]}: {names}")
            continue
        kept.append(group[0])
        dropped.extend({"kept": group[0][0], "dropped": other[0]} for other in group[1:])
    if errors:
        raise IngestError("; ".join(errors))

    records: list[ManifestRecord] = []
    hashes: dict[str, str] = {}
    for relative_name, row, path, sha in kept:
        image = load_image(path)
        image_id = f"{provenance.dataset_id}-{sha[:16]}"
        attributes = {
            key[len(ATTRIBUTE_PREFIX) :]: value
            for key, value in row.items()
            if key.startswith(ATTRIBUTE_PREFIX) and value
        }
        records.append(
            ManifestRecord(
                dataset_id=provenance.dataset_id,
                image_id=image_id,
                source=provenance.source,
                source_reference=provenance.source_reference,
                license_or_permission=provenance.license_or_permission,
                individual_label=row["individual_label"],
                side=row["side"],
                capture_session_id=row["capture_session_id"],
                capture_timestamp_if_known=row.get("capture_timestamp_if_known", "") or "",
                original_filename_hash=sha256_bytes(relative_name.encode("utf-8")),
                file_sha256=sha,
                width=image.width,
                height=image.height,
                split="unassigned",
                notes=row.get("notes", "") or "",
                attributes=attributes,
            )
        )
        hashes[image_id] = f"{dhash(image):016x}"
        if store is not None:
            target = store_path(store, provenance.dataset_id, sha, path.suffix.lower())
            if target.exists():
                if sha256_file(target) != sha:
                    raise IngestError(f"store collision with different content at {target}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / f"{provenance.dataset_id}.manifest.csv"
    try:
        write_manifest(manifest_path, records)
    except ManifestError as error:
        raise IngestError(str(error)) from error
    digest = manifest_sha256(records)

    phash_path = out_dir / f"{provenance.dataset_id}.phash.json"
    _write_once(phash_path, {"algorithm": "dhash-64", "manifest_sha256": digest, "hashes": hashes})
    report_path = out_dir / f"{provenance.dataset_id}.inventory.json"
    _write_once(report_path, _inventory(records, provenance.evidence_label, digest, dropped))
    return IngestResult(manifest_path, phash_path, report_path, digest)


def _inventory(
    records: list[ManifestRecord], label: str, digest: str, dropped: list[dict[str, str]]
) -> dict[str, Any]:
    per_side_id = Counter(r.side_id for r in records)
    sessions: dict[str, set[str]] = defaultdict(set)
    for r in records:
        sessions[r.side_id].add(r.capture_session_id)
    session_counts = Counter(len(s) for s in sessions.values())
    return {
        "evidence_label": label,
        "manifest_sha256": digest,
        "image_count": len(records),
        "individual_count": len({(r.dataset_id, r.individual_label) for r in records}),
        "side_id_count": len(per_side_id),
        "side_counts": dict(sorted(Counter(r.side for r in records).items())),
        "images_per_side_id": dict(sorted(Counter(per_side_id.values()).items())),
        "sessions_per_side_id": dict(sorted(session_counts.items())),
        "side_ids_with_at_least_4_images": sum(1 for n in per_side_id.values() if n >= 4),
        "side_ids_with_at_least_2_sessions": sum(1 for s in sessions.values() if len(s) >= 2),
        "timestamps_known": sum(1 for r in records if r.capture_timestamp_if_known),
        "dropped_exact_duplicates": dropped,
    }


def _write_once(path: Path, data: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
