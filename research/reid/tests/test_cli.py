from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_pattern_image, write_provenance

from toem_reid.cli import main


def test_cli_end_to_end_on_synthetic_proxy_is_insufficient_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = tmp_path / "raw"
    rows = ["relative_path,individual_label,side,capture_session_id"]
    for i in range(8):
        for s in range(3):
            name = f"h{i}s{s}.png"
            make_pattern_image(raw / name, identity_seed=i, image_seed=100 * i + s, size=128)
            rows.append(f"{name},hia-{i},left,day{s}")
    labels = tmp_path / "labels.csv"
    labels.write_text("\n".join(rows) + "\n", encoding="utf-8")
    provenance = write_provenance(tmp_path / "provenance.json", tier="A", proxy=True)
    out = tmp_path / "manifests"
    store = tmp_path / "store"
    assert (
        main(
            [
                "ingest",
                "--labels",
                str(labels),
                "--images",
                str(raw),
                "--provenance",
                str(provenance),
                "--out",
                str(out),
                "--store",
                str(store),
            ]
        )
        == 0
    )
    manifest = next(out.glob("*.manifest.csv"))
    phash = next(out.glob("*.phash.json"))
    split = tmp_path / "splits" / "s.json"
    # Same-identity synthetic images are pHash near-duplicates, so with pHash grouping every
    # Side-ID collapses to one leakage group and the split is refused, not silently all-unknown.
    merged = tmp_path / "splits" / "merged.json"
    base = [
        "split",
        "--manifest",
        str(manifest),
        "--seed",
        "1",
        "--validation",
        "0.5",
        "--unknown",
        "0.34",
    ]
    capsys.readouterr()
    assert main([*base, "--phash", str(phash), "--out", str(merged)]) == 1
    assert "has no gallery images" in capsys.readouterr().err
    assert not merged.exists()
    split_args = [*base, "--out", str(split)]
    assert main(split_args) == 0
    assert main(split_args) == 1  # sealed split is never overwritten
    phash_args: list[str] = []
    config = tmp_path / "a.json"
    config.write_text(
        json.dumps(
            {
                "config_id": "a",
                "algorithm_family": "A",
                "model_identifier": "opencv-sift",
                "model_version": "opencv-sift",
                "preprocessing": "original",
                "max_side": 128,
                "training_config": None,
                "seed": 0,
                "parameters": {"max_features": 150},
            }
        ),
        encoding="utf-8",
    )
    assert main(["budget", str(config)]) == 0
    results = tmp_path / "results"
    common = [
        "--manifest",
        str(manifest),
        "--provenance",
        str(provenance),
        "--split",
        str(split),
        "--config",
        str(config),
        "--store",
        str(store),
        "--results",
        str(results),
        *phash_args,
    ]
    assert main(["evaluate", *common, "--partition", "validation"]) == 0
    validation_record = next(results.glob("a-validation-*.json"))
    assert main(["reproduce", *common, "--record", str(validation_record)]) == 0
    assert main(["evaluate", *common, "--partition", "test"]) == 0
    test_record = next(results.glob("a-test-*.json"))
    decision = tmp_path / "decision.json"
    capsys.readouterr()
    assert (
        main(
            ["decide", "--test-record", str(test_record), "--reproducible", "--out", str(decision)]
        )
        == 0
    )
    payload = json.loads(decision.read_text(encoding="utf-8"))
    outcome = payload["outcome"]
    assert outcome["decision"] == "INSUFFICIENT DATA"
    assert "PROXY" in outcome["reasons"][0]
    assert outcome["policy"]["min_known_recall_after_threshold"] == 0.70
    assert outcome["policy"]["far_near_miss_ceiling"] == 0.075
    assert outcome["policy"]["far_ci95_exclusion_ceiling"] == 0.10
    assert outcome["policy"]["tier_b_minimums"]["unseen_side_ids"] == 20
    assert len(payload["input"]["far_ci95"]) == 2


def test_cli_scale_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "scale.json"
    assert (
        main(["scale", "--dim", "16", "--side-ids", "50", "--queries", "3", "--out", str(out)]) == 0
    )
    assert "SYNTHETIC" in json.loads(out.read_text(encoding="utf-8"))["label"]
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["budget", str(bad)]) == 2
    assert "error:" in capsys.readouterr().err


def test_long_error_lists_are_bounded() -> None:
    from toem_reid.cli import _bounded

    assert _bounded("a; b") == "a; b"
    long = "; ".join(f"e{i}" for i in range(25))
    assert _bounded(long).endswith("e9; ... (+15 more)")


def test_fetch_model_verifies_pinned_artifact_and_refuses_unpinned_configs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import toem_reid.cli as cli_module
    from toem_reid.hashing import sha256_file
    from toem_reid.models import acquire as real_acquire

    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "config.json").write_text(
        json.dumps(
            {
                "architecture": "test_vit",
                "pretrained_cfg": {"input_size": [3, 64, 64], "interpolation": "bicubic"},
            }
        ),
        encoding="utf-8",
    )
    (upstream / "model.safetensors").write_bytes(b"weights")
    revision = "1" * 40
    config = {
        "config_id": "b-local",
        "algorithm_family": "B",
        "model_identifier": "hf-hub:local/model",
        "model_version": f"local/model@{revision}",
        "preprocessing": "head_crop",
        "max_side": 256,
        "training_config": None,
        "seed": 0,
        "parameters": {},
        "model_artifact": {
            "source": "huggingface",
            "repo_id": "local/model",
            "revision": revision,
            "config_file": "config.json",
            "weights_file": "model.safetensors",
            "files": {n: sha256_file(upstream / n) for n in ("config.json", "model.safetensors")},
            "license": "test-fixture",
            "licence_scope": "tests only",
        },
    }
    config_path = tmp_path / "b-local.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    def offline_hub(
        *, repo_id: str, filename: str, revision: str, cache_dir: str, local_files_only: bool
    ) -> str:
        return str(upstream / filename)

    monkeypatch.setattr(
        cli_module,
        "acquire",
        lambda pin, cache, allow_download: real_acquire(
            pin, cache, allow_download=allow_download, downloader=offline_hub
        ),
    )
    cache = tmp_path / "cache"
    assert main(["fetch-model", "--config", str(config_path), "--model-cache", str(cache)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["artifact"]["revision"] == revision
    assert printed["artifact"]["verified"] is True
    assert printed["preprocessing"]["input_size"] == [3, 64, 64]

    sift = tmp_path / "a.json"
    sift.write_text(
        json.dumps(
            {**config, "algorithm_family": "A", "model_identifier": "opencv-sift"}
            | {"model_artifact": None}
        ),
        encoding="utf-8",
    )
    assert main(["fetch-model", "--config", str(sift), "--model-cache", str(cache)]) == 2
    assert "nothing to fetch" in capsys.readouterr().err
