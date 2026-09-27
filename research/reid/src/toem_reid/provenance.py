"""Dataset-level provenance and licence record (Requirement 004 sections 5.1, 8, 9).

A proxy dataset (another species, used only to validate the pipeline) can never
be tier B and never counts toward product-feasibility gates C, D or E.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROXY_LABEL = "PROXY — PIPELINE VALIDATION ONLY"
TIER_LABELS = {
    "A": "TIER A — CONTROLLED REAL DATA",
    "B": "TIER B — MOBILE-LIKE REAL DATA",
}


class ProvenanceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DatasetProvenance:
    dataset_id: str
    tier: str
    species: str
    source: str
    source_reference: str
    license_or_permission: str
    license_verified_on: str
    proxy: bool
    notes: str

    @property
    def evidence_label(self) -> str:
        return PROXY_LABEL if self.proxy else TIER_LABELS[self.tier]

    @property
    def counts_toward_product_gates(self) -> bool:
        return self.tier == "B" and not self.proxy


def _text(data: dict[str, Any], name: str, errors: list[str]) -> str:
    value = data.get(name, "")
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{name} is required")
        return ""
    return value


def load_provenance(path: Path) -> DatasetProvenance:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProvenanceError(f"{path.name}: provenance must be a JSON object")
    errors: list[str] = []
    texts = {
        name: _text(data, name, errors)
        for name in (
            "dataset_id",
            "species",
            "source",
            "source_reference",
            "license_or_permission",
            "license_verified_on",
        )
    }
    tier = data.get("tier")
    if tier not in TIER_LABELS:
        errors.append("tier must be A or B")
    proxy = data.get("proxy")
    if not isinstance(proxy, bool):
        errors.append("proxy must be a boolean")
    elif proxy and tier == "B":
        errors.append("a proxy dataset cannot be tier B")
    notes = data.get("notes", "")
    if errors:
        raise ProvenanceError(f"{path.name}: " + "; ".join(errors))
    assert isinstance(proxy, bool)
    assert isinstance(tier, str)
    return DatasetProvenance(tier=tier, proxy=proxy, notes=str(notes), **texts)
