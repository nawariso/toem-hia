from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import make_pattern_image, write_provenance

from toem_reid.evaluate import (
    RECORD_FIELDS,
    EvaluationError,
    compare_metrics,
    run_experiment,
    scaling_benchmark,
)
from toem_reid.experiment import SealError
from toem_reid.ingest import ingest
from toem_reid.manifest import load_manifest
from toem_reid.splits import SplitParameters, build_diagnostic_split, build_split, verify_split


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("eval")
    raw = root / "raw"
    rows = [
        "relative_path,individual_label,side,capture_session_id,capture_timestamp_if_known,attr_lighting"
    ]
    for i in range(8):
        for side_index, side in enumerate(("left", "right")):
            for s in range(3):
                name = f"h{i:02d}{side[0]}{s}.png"
                make_pattern_image(
                    raw / name,
                    identity_seed=100 * i + side_index,
                    image_seed=1000 * i + 10 * side_index + s,
                    size=128,
                )
                lighting = "sun" if s % 2 else "shade"
                rows.append(
                    f"{name},hia-{i:02d},{side},day{s},2026-01-0{s + 1}T09:00:00,{lighting}"
                )
    labels = root / "labels.csv"
    labels.write_text("\n".join(rows) + "\n", encoding="utf-8")
    provenance = write_provenance(root / "provenance.json", tier="A", proxy=True)
    result = ingest(labels, raw, provenance, out_dir=root / "manifests", store=root / "store")
    records = load_manifest(result.manifest_path)
    params = SplitParameters(
        seed=3, train_fraction=0.0, validation_fraction=0.5, unknown_fraction=0.34
    )
    split = build_split(records, [], params, manifest_sha256=result.manifest_sha256)
    assert verify_split(split, records, []) == []
    split_path = root / "split.json"
    split_path.write_text(json.dumps(split), encoding="utf-8")
    diag_path = root / "diag.json"
    diag_path.write_text(
        json.dumps(build_diagnostic_split(records, params, manifest_sha256=result.manifest_sha256)),
        encoding="utf-8",
    )
    config = root / "a.json"
    config.write_text(
        json.dumps(
            {
                "config_id": "a-sift-original",
                "algorithm_family": "A",
                "model_identifier": "opencv-sift",
                "model_version": "opencv-sift",
                "preprocessing": "original",
                "max_side": 128,
                "training_config": None,
                "seed": 0,
                "parameters": {"ratio": 0.8, "max_features": 150},
            }
        ),
        encoding="utf-8",
    )
    return {
        "root": root,
        "manifest": result.manifest_path,
        "phash": result.phash_path,
        "provenance": provenance,
        "store": root / "store",
        "split": split_path,
        "diag": diag_path,
        "config": config,
    }


def _run(ws: dict[str, Path], partition: str, results: str, **extra: Any) -> dict[str, Any]:
    return run_experiment(
        manifest_path=ws["manifest"],
        provenance_path=ws["provenance"],
        split_path=extra.pop("split", ws["split"]),
        config_path=ws["config"],
        store=ws["store"],
        results_dir=ws["root"] / results,
        partition=partition,
        seal_log=ws["root"] / results / "sealed.jsonl",
        git_sha="0" * 40,
        **extra,
    )


def test_validation_run_writes_a_complete_record_and_calibration(
    workspace: dict[str, Path],
) -> None:
    record = _run(workspace, "validation", "r1")
    assert set(RECORD_FIELDS) <= set(record)
    assert record["split_version"] == json.loads(workspace["split"].read_text())["split_sha256"]
    assert record["evidence_label"] == "PROXY — PIPELINE VALIDATION ONLY"
    assert record["partition"] == "validation"
    assert record["gallery_size"] > 0
    assert record["query_count"] > 0
    assert record["unknown_query_count"] > 0
    metrics = record["metrics"]
    assert set(metrics["closed_set"]["top_k"]) == {"1", "3", "5"}
    assert metrics["open_set"]["unknown_false_accept"]["rate"] <= 0.05 + 1e-9
    assert "latency" in record
    assert record["latency"]["extract_ms_per_image"]["median"] >= 0
    assert "side" in metrics["stratified"]
    assert "lighting" in metrics["stratified"]
    record_path = Path(record["artifacts"]["record"])
    calibration_path = Path(record["artifacts"]["calibration"])
    assert record_path.is_file()
    assert calibration_path.is_file()
    assert record["artifact_hashes"]["calibration"]
    assert "similarity is not a probability" in record["score_semantics"]
    assert record["model_provenance"]["input"] == "grayscale"
    assert "model_weights" not in record["artifact_hashes"]


def test_pretrained_record_carries_model_revision_and_weight_hash(
    workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import toem_reid.evaluate as evaluate_module
    from toem_reid.matchers import SiftMatcher

    artifact = {"revision": "f" * 40, "weights_sha256": "e" * 64, "repo_id": "x/y"}

    class PinnedSift(SiftMatcher):
        def provenance(self) -> dict[str, Any]:
            return {**super().provenance(), "artifact": artifact}

    monkeypatch.setattr(
        evaluate_module,
        "build_matcher",
        lambda config, model_cache=None: PinnedSift(0.8, 500, 5.0, config.max_side),
    )
    record = _run(workspace, "validation", "r-pinned")
    assert record["model_provenance"]["artifact"] == artifact
    assert record["artifact_hashes"]["model_weights"] == "e" * 64
    assert record["artifact_hashes"]["model_revision"] == "f" * 40


def test_synthetic_pipeline_retrieves_known_patterns(workspace: dict[str, Path]) -> None:
    # Pipeline sanity only: synthetic fixtures are never evidence that Re-ID works.
    record = _run(workspace, "validation", "r2")
    assert record["metrics"]["closed_set"]["top_k"]["5"]["rate"] >= 0.8


def test_repeat_run_reproduces_metrics(workspace: dict[str, Path]) -> None:
    a = _run(workspace, "validation", "r3a")
    b = _run(workspace, "validation", "r3b")
    assert compare_metrics(a["metrics"], b["metrics"], tolerance=1e-9) == []


def test_compare_metrics_reports_differences() -> None:
    assert compare_metrics({"a": 1.0, "b": [1, 2]}, {"a": 1.0 + 1e-12, "b": [1, 2]}, 1e-9) == []
    diffs = compare_metrics({"a": 1.0, "b": {"c": 2}}, {"a": 1.5, "b": {"c": 2, "d": 1}}, 1e-9)
    assert any(d.startswith("a:") for d in diffs)
    assert any("b.d" in d for d in diffs)


def test_test_partition_requires_validation_calibration(workspace: dict[str, Path]) -> None:
    with pytest.raises(EvaluationError, match="calibrate on validation first"):
        _run(workspace, "test", "r4")


def test_test_partition_uses_the_validation_threshold_and_is_sealed(
    workspace: dict[str, Path],
) -> None:
    validation = _run(workspace, "validation", "r5")
    test = _run(workspace, "test", "r5")
    assert (
        test["metrics"]["open_set"]["threshold"] == validation["metrics"]["open_set"]["threshold"]
    )
    assert test["sealed_test"] is True
    log = (workspace["root"] / "r5" / "sealed.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1
    repeat = _run(workspace, "test", "r5")
    assert compare_metrics(test["metrics"], repeat["metrics"], 1e-9) == []


def test_changed_config_after_sealed_test_needs_a_new_protocol(
    workspace: dict[str, Path], tmp_path: Path
) -> None:
    _run(workspace, "validation", "r6")
    _run(workspace, "test", "r6")
    changed = json.loads(workspace["config"].read_text())
    changed["parameters"]["ratio"] = 0.7
    original = workspace["config"].read_text()
    workspace["config"].write_text(json.dumps(changed), encoding="utf-8")
    try:
        _run(workspace, "validation", "r6")
        with pytest.raises(SealError):
            _run(workspace, "test", "r6")
    finally:
        workspace["config"].write_text(original, encoding="utf-8")


def test_tampered_split_is_refused(workspace: dict[str, Path], tmp_path: Path) -> None:
    spec = json.loads(workspace["split"].read_text())
    spec["assignments"][0]["role"] = (
        "gallery" if spec["assignments"][0]["role"] != "gallery" else "query_known"
    )
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(EvaluationError, match="split failed verification"):
        _run(workspace, "validation", "r7", split=bad)


def test_diagnostic_split_is_labelled_and_cannot_be_used_for_test(
    workspace: dict[str, Path],
) -> None:
    record = _run(workspace, "validation", "r8", split=workspace["diag"])
    assert record["protocol_label"] == "NON-REPRESENTATIVE / DIAGNOSTIC ONLY"
    assert record["representative"] is False
    with pytest.raises(EvaluationError, match="diagnostic"):
        _run(workspace, "test", "r8", split=workspace["diag"])


def test_record_lists_false_positive_and_false_negative_examples_by_id_only(
    workspace: dict[str, Path],
) -> None:
    record = _run(workspace, "validation", "r9")
    examples = record["examples"]
    assert set(examples) == {"false_positive_unknown", "false_negative_known", "hardest_correct"}
    for items in examples.values():
        for item in items:
            assert "image_id" in item
            assert "path" not in json.dumps(item)


def test_scaling_benchmark_is_labelled_synthetic() -> None:
    result = scaling_benchmark(
        dim=32, side_id_counts=(50, 100), images_per_side_id=2, queries=5, seed=0
    )
    assert result["label"] == "SYNTHETIC VECTORS — SCALING MECHANICS ONLY; NOT ACCURACY EVIDENCE"
    assert [row["side_ids"] for row in result["rows"]] == [50, 100]
    assert all(row["search_ms_per_query"] >= 0 for row in result["rows"])
    assert result["rows"][1]["index_bytes"] == 2 * result["rows"][0]["index_bytes"]
