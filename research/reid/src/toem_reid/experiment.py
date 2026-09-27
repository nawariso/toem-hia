"""Experiment configuration, budget enforcement and sealed-test registration.

Budget (Requirement 004 section 6): at most three algorithm families, two
preprocessing strategies, three configurations per family before a review
checkpoint, and one fine-tuned model family.

Sealed test (section 11): the first time a split's TEST partition is evaluated,
the set of configurations allowed on it is fixed (either the pre-registered set
or just the first configuration). Each configuration may then only be re-run
with the same calibration. Anything else needs a new test protocol/version.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from toem_reid.hashing import sha256_canonical
from toem_reid.images import PREPROCESSING
from toem_reid.models import parse_pin

FAMILIES: Final = {
    "A": "classical local features",
    "B": "general pretrained visual embedding",
    "C": "metric-learning / Re-ID fine-tuning",
}
PRETRAINED_FAMILIES: Final = frozenset({"B", "C"})
MAX_CONFIGS_PER_FAMILY: Final = 3


class BudgetError(ValueError):
    pass


class SealError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    config_id: str
    algorithm_family: str
    model_identifier: str
    model_version: str
    preprocessing: str
    max_side: int
    training_config: dict[str, Any] | None
    seed: int
    parameters: dict[str, Any]
    model_artifact: dict[str, Any] | None = None

    def material(self) -> dict[str, Any]:
        """Everything that affects results; ``config_id`` is only a label."""
        data = asdict(self)
        del data["config_id"]
        return data


def config_sha256(config: ExperimentConfig) -> str:
    return sha256_canonical(config.material())


def load_config(path: Path) -> ExperimentConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for name in ("config_id", "model_identifier", "model_version"):
        if not isinstance(data.get(name), str) or not data[name].strip():
            errors.append(f"{name} is required")
    if data.get("algorithm_family") not in FAMILIES:
        errors.append(f"algorithm_family must be one of {sorted(FAMILIES)}")
    if data.get("preprocessing") not in PREPROCESSING:
        errors.append(f"preprocessing must be one of {list(PREPROCESSING)}")
    max_side = data.get("max_side")
    if not isinstance(max_side, int) or isinstance(max_side, bool) or max_side < 32:
        errors.append("max_side must be an integer >= 32")
    seed = data.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        errors.append("seed must be an integer")
    training = data.get("training_config")
    if data.get("algorithm_family") == "C" and not isinstance(training, dict):
        errors.append("family C requires training_config")
    if training is not None and not isinstance(training, dict):
        errors.append("training_config must be an object or null")
    parameters = data.get("parameters", {})
    if not isinstance(parameters, dict):
        errors.append("parameters must be an object")
    artifact = data.get("model_artifact")
    if data.get("algorithm_family") in PRETRAINED_FAMILIES and artifact is None:
        errors.append(
            "families B and C require a model_artifact pin (immutable revision + file SHA-256)"
        )
    if artifact is not None:
        if not isinstance(artifact, dict):
            errors.append("model_artifact must be an object")
        else:
            try:
                pin = parse_pin(artifact)
            except ValueError as error:
                errors.append(str(error))
            else:
                expected = f"hf-hub:{pin.repo_id}"
                if data.get("model_identifier") != expected:
                    errors.append(f"model_identifier must be {expected!r} to match model_artifact")
    if errors:
        raise ValueError(f"{path.name}: " + "; ".join(errors))
    return ExperimentConfig(
        config_id=data["config_id"],
        algorithm_family=data["algorithm_family"],
        model_identifier=data["model_identifier"],
        model_version=data["model_version"],
        preprocessing=data["preprocessing"],
        max_side=max_side,
        training_config=training,
        seed=seed,
        parameters=parameters,
        model_artifact=artifact,
    )


def check_budget(configs: Sequence[ExperimentConfig]) -> None:
    per_family: dict[str, set[str]] = defaultdict(set)
    fine_tuned: set[str] = set()
    for config in configs:
        per_family[config.algorithm_family].add(config_sha256(config))
        if config.algorithm_family == "C":
            fine_tuned.add(config.model_identifier)
    errors = [
        f"family {family} has {len(hashes)} configurations; the budget is "
        f"{MAX_CONFIGS_PER_FAMILY} before a review checkpoint"
        for family, hashes in sorted(per_family.items())
        if len(hashes) > MAX_CONFIGS_PER_FAMILY
    ]
    if len(fine_tuned) > 1:
        errors.append(f"at most one fine-tuned model family is allowed, found {sorted(fine_tuned)}")
    if errors:
        raise BudgetError("; ".join(errors) + ". Stop and propose a research extension.")


def _entries(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def register_sealed_test(
    log: Path,
    *,
    split_sha256: str,
    config_sha256: str,
    calibration_sha256: str,
    experiment_id: str,
    registered_configs: Iterable[str] | None = None,
) -> None:
    """Append-only guard; raises before any sealed-test result is produced."""
    history = [e for e in _entries(log) if e["split_sha256"] == split_sha256]
    if history:
        allowed = set(history[0]["registered_configs"])
        same_config = [e for e in history if e["config_sha256"] == config_sha256]
        if config_sha256 not in allowed or any(
            e["calibration_sha256"] != calibration_sha256 for e in same_config
        ):
            raise SealError(
                "the sealed test for this split has already been examined with a different "
                "configuration or calibration; create a new test protocol/version"
            )
        registered = sorted(allowed)
    else:
        registered = sorted(set(registered_configs or ()) | {config_sha256})
    entry = {
        "split_sha256": split_sha256,
        "config_sha256": config_sha256,
        "calibration_sha256": calibration_sha256,
        "experiment_id": experiment_id,
        "registered_configs": registered,
        "registered_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
