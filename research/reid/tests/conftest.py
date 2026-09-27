"""Synthetic fixtures for pipeline-correctness tests.

These images exist only to exercise the pipeline. They are never evidence that
Re-ID works (Requirement 004 section 7).
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from toem_reid.manifest import FIELDS, ManifestRecord


def make_pattern_image(path: Path, identity_seed: int, image_seed: int, size: int = 64) -> Path:
    """Write a PNG whose coarse pattern depends on the identity and fine noise on the image."""
    base = np.random.default_rng(identity_seed).integers(0, 256, (8, 8), dtype=np.uint8)
    coarse = np.kron(base, np.ones((size // 8, size // 8), dtype=np.uint8)).astype(np.int16)
    noise = np.random.default_rng(10_000 + image_seed).integers(-20, 21, coarse.shape)
    pixels = np.clip(coarse + noise, 0, 255).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels, mode="L").save(path, format="PNG")
    return path


def record(
    image_id: str,
    *,
    individual: str = "hia-1",
    side: str = "left",
    session: str = "s1",
    sha: str | None = None,
    dataset_id: str = "synthetic",
    split: str = "unassigned",
    timestamp: str = "",
    attributes: dict[str, str] | None = None,
) -> ManifestRecord:
    digest = sha if sha is not None else (image_id.encode().hex() * 64)[:64]
    return ManifestRecord(
        dataset_id=dataset_id,
        image_id=image_id,
        source="synthetic-fixture",
        source_reference="tests",
        license_or_permission="test-fixture",
        individual_label=individual,
        side=side,
        capture_session_id=session,
        capture_timestamp_if_known=timestamp,
        original_filename_hash="0" * 64,
        file_sha256=digest,
        width=64,
        height=64,
        split=split,
        notes="",
        attributes=attributes or {},
    )


def write_manifest_csv(
    path: Path, rows: list[dict[str, str]], header: list[str] | None = None
) -> Path:
    columns = header if header is not None else list(FIELDS)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def valid_row(**overrides: str) -> dict[str, str]:
    row = {
        "dataset_id": "synthetic",
        "image_id": "img-1",
        "source": "synthetic-fixture",
        "source_reference": "tests",
        "license_or_permission": "test-fixture",
        "individual_label": "hia-1",
        "side": "left",
        "capture_session_id": "s1",
        "capture_timestamp_if_known": "",
        "original_filename_hash": "0" * 64,
        "file_sha256": "a" * 64,
        "width": "64",
        "height": "64",
        "split": "unassigned",
        "notes": "",
    }
    row.update(overrides)
    return row


def write_provenance(path: Path, **overrides: object) -> Path:
    data: dict[str, object] = {
        "dataset_id": "synthetic",
        "tier": "B",
        "species": "synthetic",
        "source": "synthetic-fixture",
        "source_reference": "tests",
        "license_or_permission": "test-fixture",
        "license_verified_on": "2026-09-27",
        "proxy": False,
        "notes": "",
    }
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def pattern_image() -> Callable[[Path, int, int], Path]:
    return make_pattern_image
