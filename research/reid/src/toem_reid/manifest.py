"""Immutable, machine-readable dataset manifest (Requirement 004 section 9).

One CSV row per image. The minimum fields are fixed; optional stratification
attributes use an ``attr_`` prefix. Columns that would carry location, user or
device identity are refused outright so they cannot become an identity shortcut
(sections 9 and 21).
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from toem_reid.hashing import sha256_canonical

FIELDS: Final = (
    "dataset_id",
    "image_id",
    "source",
    "source_reference",
    "license_or_permission",
    "individual_label",
    "side",
    "capture_session_id",
    "capture_timestamp_if_known",
    "original_filename_hash",
    "file_sha256",
    "width",
    "height",
    "split",
    "notes",
)
SIDES: Final = frozenset({"left", "right"})
SPLITS: Final = frozenset({"unassigned", "train", "validation", "test"})
ATTRIBUTE_PREFIX: Final = "attr_"
_FORBIDDEN_TOKENS: Final = ("gps", "latitude", "longitude", "user", "device", "owner")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_TEXT: Final = (
    "dataset_id",
    "image_id",
    "source",
    "source_reference",
    "license_or_permission",
    "individual_label",
    "capture_session_id",
)


class ManifestError(ValueError):
    """A manifest failed validation; the message lists every problem found."""


@dataclass(frozen=True, slots=True)
class ManifestRecord:
    dataset_id: str
    image_id: str
    source: str
    source_reference: str
    license_or_permission: str
    individual_label: str
    side: str
    capture_session_id: str
    capture_timestamp_if_known: str
    original_filename_hash: str
    file_sha256: str
    width: int
    height: int
    split: str
    notes: str
    attributes: Mapping[str, str] = field(default_factory=dict)

    @property
    def side_id(self) -> str:
        """Primary experimental identity unit: left and right views are separate identities."""
        return f"{self.dataset_id}:{self.individual_label}:{self.side}"

    @property
    def session_key(self) -> str:
        """Session grouping is scoped to one Side-ID's dataset so labels cannot collide."""
        return f"{self.dataset_id}:{self.capture_session_id}"

    def as_row(self) -> dict[str, str]:
        row = {name: str(getattr(self, name)) for name in FIELDS}
        for key in sorted(self.attributes):
            row[ATTRIBUTE_PREFIX + key] = self.attributes[key]
        return row


def _forbidden(column: str) -> bool:
    lowered = column.lower()
    return any(token in lowered for token in _FORBIDDEN_TOKENS)


def _positive_int(value: str) -> int | None:
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number > 0 else None


def _validate_row(row: Mapping[str, str], line: int, errors: list[str]) -> ManifestRecord | None:
    problems: list[str] = []
    for name in _REQUIRED_TEXT:
        if not row[name].strip():
            problems.append(f"{name} is required")
    for name in ("dataset_id", "individual_label", "side"):
        if ":" in row[name]:
            problems.append(f"{name} must not contain ':'")
    if row["side"] not in SIDES:
        problems.append(f"side must be one of {sorted(SIDES)}")
    for name in ("file_sha256", "original_filename_hash"):
        if not _HEX64.match(row[name]):
            problems.append(f"{name} must be 64 lowercase hex characters")
    width = _positive_int(row["width"])
    height = _positive_int(row["height"])
    if width is None:
        problems.append("width must be a positive integer")
    if height is None:
        problems.append("height must be a positive integer")
    if row["split"] not in SPLITS:
        problems.append(f"split must be one of {sorted(SPLITS)}")
    timestamp = row["capture_timestamp_if_known"].strip()
    if timestamp:
        try:
            datetime.fromisoformat(timestamp)
        except ValueError:
            problems.append("capture_timestamp_if_known must be ISO 8601 or empty")
    if problems:
        errors.extend(f"row {line}: {problem}" for problem in problems)
        return None
    assert width is not None
    assert height is not None
    attributes = {
        key[len(ATTRIBUTE_PREFIX) :]: value
        for key, value in row.items()
        if key.startswith(ATTRIBUTE_PREFIX)
    }
    return ManifestRecord(
        dataset_id=row["dataset_id"],
        image_id=row["image_id"],
        source=row["source"],
        source_reference=row["source_reference"],
        license_or_permission=row["license_or_permission"],
        individual_label=row["individual_label"],
        side=row["side"],
        capture_session_id=row["capture_session_id"],
        capture_timestamp_if_known=timestamp,
        original_filename_hash=row["original_filename_hash"],
        file_sha256=row["file_sha256"],
        width=width,
        height=height,
        split=row["split"],
        notes=row["notes"],
        attributes=attributes,
    )


def _check_header(header: list[str]) -> list[str]:
    errors = [f"missing required column: {name}" for name in FIELDS if name not in header]
    for column in header:
        if _forbidden(column):
            errors.append(
                f"column {column}: location, user and device data must not be used as "
                "identity evidence"
            )
        elif column not in FIELDS and not column.startswith(ATTRIBUTE_PREFIX):
            errors.append(f"unexpected column: {column}")
    return errors


def validate_records(records: Iterable[ManifestRecord]) -> list[str]:
    """Cross-row checks: unique image IDs and no exact-duplicate files."""
    errors: list[str] = []
    by_id: dict[str, int] = defaultdict(int)
    by_sha: dict[str, list[str]] = defaultdict(list)
    for item in records:
        by_id[item.image_id] += 1
        by_sha[item.file_sha256].append(item.image_id)
    errors.extend(
        f"duplicate image_id {image_id}" for image_id, n in sorted(by_id.items()) if n > 1
    )
    for sha, ids in sorted(by_sha.items()):
        if len(ids) > 1:
            errors.append(f"exact duplicate file {sha[:12]}: {', '.join(sorted(ids))}")
    return errors


def load_manifest(path: Path) -> list[ManifestRecord]:
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = list(reader.fieldnames or [])
    errors = _check_header(header)
    if errors:
        raise ManifestError(f"{path.name}: " + "; ".join(errors))
    records: list[ManifestRecord] = []
    for line, row in enumerate(reader, start=2):
        parsed = _validate_row(row, line, errors)
        if parsed is not None:
            records.append(parsed)
    if not errors and not records:
        errors.append("manifest has no records")
    if not errors:
        errors.extend(validate_records(records))
    if errors:
        raise ManifestError(f"{path.name}: " + "; ".join(errors))
    return sorted(records, key=lambda r: r.image_id)


def write_manifest(path: Path, records: Iterable[ManifestRecord]) -> None:
    """Write once: manifests are immutable, so an existing file is never overwritten."""
    ordered = sorted(records, key=lambda r: r.image_id)
    errors = validate_records(ordered)
    if errors:
        raise ManifestError("; ".join(errors))
    attribute_keys = sorted({key for r in ordered for key in r.attributes})
    header = [*FIELDS, *(ATTRIBUTE_PREFIX + key for key in attribute_keys)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        for item in ordered:
            writer.writerow({**dict.fromkeys(header, ""), **item.as_row()})


def manifest_sha256(records: Iterable[ManifestRecord]) -> str:
    """Content hash independent of file encoding, row order and line endings."""
    rows = [item.as_row() for item in sorted(records, key=lambda r: r.image_id)]
    return sha256_canonical(rows)
