"""Run one experiment on one partition of a sealed split and write its record.

VALIDATION runs compute metrics and calibrate the UNKNOWN threshold (target FAR
from the config, default 5 %). TEST runs reuse that calibration unchanged and
are registered in the append-only sealed-test log *before* any test result is
computed (Requirement 004 section 11).
"""

from __future__ import annotations

import json
import platform
import statistics
import subprocess
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from toem_reid.experiment import config_sha256, load_config, register_sealed_test
from toem_reid.hashing import sha256_canonical, sha256_file
from toem_reid.images import apply_preprocessing, load_image, prepare
from toem_reid.manifest import ManifestRecord, load_manifest, manifest_sha256
from toem_reid.matchers import build_matcher
from toem_reid.metrics import (
    DEFAULT_KS,
    Ranking,
    calibrate_unknown_threshold,
    closed_set_metrics,
    open_set_metrics,
    rank_identities,
    stratify,
    true_ranks,
)
from toem_reid.provenance import load_provenance
from toem_reid.quality import QualityThresholds, assess
from toem_reid.splits import verify_split

RECORD_FIELDS: Final = (
    "experiment_id",
    "git_sha",
    "dataset_manifest_sha256",
    "split_version",
    "algorithm_family",
    "model_identifier",
    "model_version",
    "model_provenance",
    "preprocessing",
    "training_config",
    "seed",
    "gallery_size",
    "query_count",
    "unknown_query_count",
    "hardware",
    "started_at",
    "metrics",
    "artifact_hashes",
)
SCORE_SEMANTICS: Final = (
    "Scores are raw similarities used only for ranking and thresholding; "
    "similarity is not a probability and must never be shown to users."
)
SCALING_LABEL: Final = "SYNTHETIC VECTORS — SCALING MECHANICS ONLY; NOT ACCURACY EVIDENCE"
DEFAULT_TARGET_FAR: Final = 0.05
EXAMPLE_LIMIT: Final = 10


class EvaluationError(RuntimeError):
    pass


def current_git_sha(repo: Path) -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}-dirty" if dirty else sha


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "not-installed"


def hardware() -> dict[str, Any]:
    import os

    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "numpy": _version("numpy"),
        "opencv": _version("opencv-python-headless"),
        "torch": _version("torch"),
        "timm": _version("timm"),
    }


def _image_file(store: Path, record: ManifestRecord) -> Path:
    folder = store / record.dataset_id / record.file_sha256[:2]
    matches = sorted(folder.glob(f"{record.file_sha256}.*"))
    if not matches:
        raise EvaluationError(f"image {record.image_id} not found in the research store")
    if sha256_file(matches[0]) != record.file_sha256:
        raise EvaluationError(f"image {record.image_id} content does not match the manifest hash")
    return matches[0]


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _summary(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"median": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(values)
    return {
        "median": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))],
        "max": ordered[-1],
    }


def compare_metrics(a: Any, b: Any, tolerance: float, path: str = "") -> list[str]:
    """Structural comparison with numeric tolerance; used for Gate A (reproducibility)."""
    label = path or "<root>"
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        diffs = []
        for key in sorted(set(a) | set(b)):
            child = f"{path}.{key}" if path else str(key)
            if key not in a or key not in b:
                diffs.append(f"{child}: present in only one run")
            else:
                diffs.extend(compare_metrics(a[key], b[key], tolerance, child))
        return diffs
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{label}: length {len(a)} != {len(b)}"]
        return [
            d
            for i, (x, y) in enumerate(zip(a, b, strict=True))
            for d in compare_metrics(x, y, tolerance, f"{path}[{i}]")
        ]
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, str) or isinstance(b, str):
        return [] if a == b else [f"{label}: {a!r} != {b!r}"]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if np.isnan(a) and np.isnan(b):
            return []
        return [] if abs(float(a) - float(b)) <= tolerance else [f"{label}: {a} != {b}"]
    return [] if a == b else [f"{label}: {a!r} != {b!r}"]


def _roles(spec: Mapping[str, Any], partition: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for row in spec["assignments"]:
        if row["partition"] == partition:
            out[row["role"]].append(row["image_id"])
    return out


def _examples(
    known_ids: Sequence[str],
    known_truth: Sequence[str],
    known: Ranking,
    ranks: Sequence[int],
    unknown_ids: Sequence[str],
    unknown: Ranking,
    threshold: float,
) -> dict[str, list[dict[str, Any]]]:
    """Representative failures, identified by image_id only (never file paths)."""
    unknown_best = unknown.best_scores()
    known_best = known.best_scores()
    false_pos_order = sorted(
        (q for q in range(len(unknown_ids)) if unknown_best[q] >= threshold),
        key=lambda q: (-float(unknown_best[q]), unknown_ids[q]),
    )
    false_neg_order = sorted(
        (q for q in range(len(known_ids)) if ranks[q] > 5 or known_best[q] < threshold),
        key=lambda q: (-ranks[q], known_ids[q]),
    )
    hardest_order = sorted(
        (q for q in range(len(known_ids)) if ranks[q] == 1),
        key=lambda q: (float(known_best[q]), known_ids[q]),
    )
    return {
        "false_positive_unknown": [
            {
                "image_id": unknown_ids[q],
                "top_candidates": unknown.top(q, 3),
                "best_score": float(unknown_best[q]),
            }
            for q in false_pos_order[:EXAMPLE_LIMIT]
        ],
        "false_negative_known": [
            {
                "image_id": known_ids[q],
                "true_side_id": known_truth[q],
                "true_rank": ranks[q],
                "rejected_as_unknown": bool(known_best[q] < threshold),
                "top_candidates": known.top(q, 3),
            }
            for q in false_neg_order[:EXAMPLE_LIMIT]
        ],
        "hardest_correct": [
            {
                "image_id": known_ids[q],
                "true_side_id": known_truth[q],
                "best_score": float(known_best[q]),
            }
            for q in hardest_order[:EXAMPLE_LIMIT]
        ],
    }


def _dataset_stats(
    records: Mapping[str, ManifestRecord], roles: Mapping[str, list[str]]
) -> dict[str, Any]:
    known_sides = {records[i].side_id for i in roles.get("gallery", [])}
    per_known: dict[str, int] = defaultdict(int)
    sessions: dict[str, set[str]] = defaultdict(set)
    for i in roles.get("gallery", []) + roles.get("query_known", []):
        per_known[records[i].side_id] += 1
        sessions[records[i].side_id].add(records[i].capture_session_id)
    return {
        "known_side_ids": len(known_sides),
        "min_images_per_known_side_id": min(per_known.values(), default=0),
        "side_ids_with_two_sessions": sum(1 for s in sessions.values() if len(s) >= 2),
        "unseen_side_ids": len({records[i].side_id for i in roles.get("query_unknown", [])}),
    }


def run_experiment(
    *,
    manifest_path: Path,
    provenance_path: Path,
    split_path: Path,
    config_path: Path,
    store: Path,
    results_dir: Path,
    partition: str,
    seal_log: Path,
    near_duplicates: Sequence[tuple[str, str]] = (),
    registered_configs: Sequence[str] = (),
    git_sha: str | None = None,
    model_cache: Path | None = None,
) -> dict[str, Any]:
    if partition not in ("validation", "test"):
        raise EvaluationError("partition must be validation or test")
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    provenance = load_provenance(provenance_path)
    records_list = load_manifest(manifest_path)
    manifest_digest = manifest_sha256(records_list)
    spec = json.loads(split_path.read_text(encoding="utf-8"))
    if spec.get("manifest_sha256") != manifest_digest:
        raise EvaluationError("split was built from a different manifest")
    problems = verify_split(spec, records_list, near_duplicates)
    if problems:
        raise EvaluationError("split failed verification: " + "; ".join(problems[:5]))
    representative = bool(spec.get("representative"))
    if partition == "test" and not representative:
        raise EvaluationError(
            "a diagnostic (non-representative) split cannot be used for the sealed test"
        )
    config = load_config(config_path)
    config_digest = config_sha256(config)
    split_digest = spec["split_sha256"]
    target_far = float(config.parameters.get("target_far", DEFAULT_TARGET_FAR))
    experiment_id = f"{config.config_id}-{partition}-{split_digest[:8]}-{config_digest[:8]}"
    results_dir.mkdir(parents=True, exist_ok=True)
    calibration_path = results_dir / f"calibration-{split_digest[:12]}-{config_digest[:12]}.json"

    calibration: dict[str, Any] | None = None
    if partition == "test":
        if not calibration_path.is_file():
            raise EvaluationError(
                "no validation calibration for this split and config; calibrate on validation first"
            )
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        register_sealed_test(
            seal_log,
            split_sha256=split_digest,
            config_sha256=config_digest,
            calibration_sha256=sha256_file(calibration_path),
            experiment_id=experiment_id,
            registered_configs=registered_configs,
        )

    records = {r.image_id: r for r in records_list}
    roles = _roles(spec, partition)
    gallery_ids, known_ids, unknown_ids = (
        roles["gallery"],
        roles["query_known"],
        roles["query_unknown"],
    )
    if not gallery_ids or not known_ids or not unknown_ids:
        raise EvaluationError(f"{partition} needs gallery, known-query and unknown-query images")

    matcher = build_matcher(config, model_cache)
    thresholds = QualityThresholds(**config.parameters.get("quality", {}))
    features: dict[str, Any] = {}
    quality: dict[str, str] = {}
    extract_ms: dict[str, float] = {}
    for image_id in sorted({*gallery_ids, *known_ids, *unknown_ids}):
        record = records[image_id]
        # Head crop (if configured) always happens before any family-specific transform.
        image = apply_preprocessing(
            load_image(_image_file(store, record)),
            config.preprocessing,
            record.attributes.get("head_bbox"),
        )
        # The quality signal is family-independent: a grayscale view of the same crop.
        quality[image_id] = assess(prepare(image, config.max_side), thresholds).recommendation
        start = time.perf_counter()
        features[image_id] = matcher.extract(matcher.prepare_input(image))
        extract_ms[image_id] = _elapsed_ms(start)

    gallery_features = [features[i] for i in gallery_ids]
    gallery_sides = [records[i].side_id for i in gallery_ids]
    search_ms: list[float] = []

    def search(ids: Sequence[str]) -> NDArray[np.float64]:
        rows = []
        for i in ids:
            start = time.perf_counter()
            row = matcher.similarity([features[i]], gallery_features)
            search_ms.append(_elapsed_ms(start))
            rows.append(row[0])
        return np.vstack(rows)

    known_rank = rank_identities(search(known_ids), gallery_sides)
    unknown_rank = rank_identities(search(unknown_ids), gallery_sides)
    known_truth = [records[i].side_id for i in known_ids]
    ranks = true_ranks(known_rank, known_truth)

    if partition == "validation":
        threshold = calibrate_unknown_threshold(unknown_rank.best_scores(), target_far)
        calibration = {
            "split_sha256": split_digest,
            "config_sha256": config_digest,
            "target_far": target_far,
            "threshold": threshold,
            "calibrated_on": "validation",
            "validation_unknown_queries": len(unknown_ids),
        }
        calibration_path.write_text(
            json.dumps(calibration, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    assert calibration is not None
    threshold = float(calibration["threshold"])

    stratified: dict[str, Any] = {
        "side": stratify(ranks, [records[i].side for i in known_ids], k=5),
        "quality": stratify(ranks, [quality[i] for i in known_ids], k=5),
        "head_pixels": stratify(
            ranks,
            [
                "<128" if min(records[i].width, records[i].height) < 128 else ">=128"
                for i in known_ids
            ]
            if config.preprocessing == "original"
            else ["head-crop" for _ in known_ids],
            k=5,
        ),
    }
    for key in sorted({k for i in known_ids for k in records[i].attributes if k != "head_bbox"}):
        stratified[key] = stratify(
            ranks, [records[i].attributes.get(key, "") for i in known_ids], k=5
        )

    metrics = {
        "closed_set": closed_set_metrics(known_rank, known_truth, DEFAULT_KS),
        "open_set": open_set_metrics(known_rank, known_truth, unknown_rank, threshold, DEFAULT_KS),
        "stratified": stratified,
        "unknown_quality": dict(sorted(_count(quality[i] for i in unknown_ids).items())),
    }
    record_path = results_dir / f"{experiment_id}.json"
    model_provenance = matcher.provenance()
    artifact = model_provenance.get("artifact")
    out: dict[str, Any] = {
        "experiment_id": experiment_id,
        "git_sha": git_sha if git_sha is not None else current_git_sha(manifest_path.parent),
        "dataset_manifest_sha256": manifest_digest,
        "split_version": split_digest,
        "protocol_label": spec["label"],
        "representative": representative,
        "partition": partition,
        "sealed_test": partition == "test",
        "evidence_label": provenance.evidence_label,
        "counts_toward_product_gates": provenance.counts_toward_product_gates,
        "algorithm_family": config.algorithm_family,
        "model_identifier": config.model_identifier,
        "model_version": config.model_version,
        "model_provenance": model_provenance,
        "preprocessing": config.preprocessing,
        "training_config": config.training_config,
        "seed": config.seed,
        "config_sha256": config_digest,
        "parameters": config.parameters,
        "gallery_size": len(gallery_ids),
        "gallery_side_ids": len(set(gallery_sides)),
        "query_count": len(known_ids),
        "unknown_query_count": len(unknown_ids),
        "dataset": _dataset_stats(records, roles),
        "hardware": hardware(),
        "started_at": started_at,
        "latency": {
            "extract_ms_per_image": _summary(list(extract_ms.values())),
            "search_ms_per_query": _summary(search_ms),
            "gallery_build_ms": sum(extract_ms[i] for i in gallery_ids),
        },
        "score_semantics": SCORE_SEMANTICS,
        "metrics": metrics,
        "examples": _examples(
            known_ids, known_truth, known_rank, ranks, unknown_ids, unknown_rank, threshold
        ),
    }
    out["artifact_hashes"] = {
        "config": config_digest,
        "split": split_digest,
        "manifest": manifest_digest,
        "calibration": sha256_file(calibration_path),
        "metrics": sha256_canonical(metrics),
    }
    if artifact is not None:
        out["artifact_hashes"]["model_weights"] = artifact["weights_sha256"]
        out["artifact_hashes"]["model_revision"] = artifact["revision"]
    out["artifacts"] = {"record": str(record_path), "calibration": str(calibration_path)}
    record_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def _count(values: Any) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return counts


def scaling_benchmark(
    dim: int, side_id_counts: Sequence[int], images_per_side_id: int, queries: int, seed: int
) -> dict[str, Any]:
    """Index/search mechanics for a flat cosine index using random unit vectors."""
    rng = np.random.default_rng(seed)
    rows = []
    for count in side_id_counts:
        n = count * images_per_side_id
        vectors = rng.standard_normal((n, dim)).astype(np.float32)
        start = time.perf_counter()
        index = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        build_ms = _elapsed_ms(start)
        sides = [f"s{i // images_per_side_id}" for i in range(n)]
        query = rng.standard_normal((queries, dim)).astype(np.float32)
        query /= np.linalg.norm(query, axis=1, keepdims=True)
        start = time.perf_counter()
        rank_identities(query @ index.T, sides)
        search_ms = _elapsed_ms(start)
        rows.append(
            {
                "side_ids": count,
                "gallery_vectors": n,
                "index_build_ms": build_ms,
                "search_ms_per_query": search_ms / queries,
                "index_bytes": int(index.nbytes),
            }
        )
    return {
        "label": SCALING_LABEL,
        "dim": dim,
        "images_per_side_id": images_per_side_id,
        "rows": rows,
    }
