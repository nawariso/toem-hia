from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from toem_reid.hashing import sha256_file
from toem_reid.models import (
    ModelArtifactError,
    ModelPin,
    acquire,
    parse_pin,
    read_model_config,
)

REVISION = "3ea58ff6c6195bc748bb86c111ff40c32bdddcba"


def _source(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "upstream"
    source.mkdir()
    (source / "config.json").write_text(
        json.dumps(
            {
                "architecture": "test_vit",
                "num_classes": 0,
                "pretrained_cfg": {
                    "input_size": [3, 160, 160],
                    "interpolation": "bicubic",
                    "crop_pct": 0.9,
                    "crop_mode": "center",
                    "mean": [0.5, 0.5, 0.5],
                    "std": [0.5, 0.5, 0.5],
                },
            }
        ),
        encoding="utf-8",
    )
    (source / "model.safetensors").write_bytes(b"weights-v1")
    return {
        "config.json": source / "config.json",
        "model.safetensors": source / "model.safetensors",
    }


def _pin_data(sources: dict[str, Path], **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source": "huggingface",
        "repo_id": "example/model",
        "revision": REVISION,
        "config_file": "config.json",
        "weights_file": "model.safetensors",
        "files": {name: sha256_file(path) for name, path in sources.items()},
        "license": "apache-2.0",
        "licence_scope": "research comparison",
    }
    data.update(overrides)
    return data


class FakeHub:
    """Stands in for huggingface_hub.hf_hub_download; records every request."""

    def __init__(self, files: dict[str, Path]) -> None:
        self.files = files
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        repo_id: str,
        filename: str,
        revision: str,
        cache_dir: str,
        local_files_only: bool,
    ) -> str:
        self.calls.append(
            {
                "repo_id": repo_id,
                "filename": filename,
                "revision": revision,
                "local_files_only": local_files_only,
            }
        )
        target = Path(cache_dir) / revision / filename
        if not target.exists():
            if local_files_only:
                raise FileNotFoundError(filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.files[filename], target)
        return os.fspath(target)


def test_pin_requires_an_immutable_commit_revision_and_file_hashes(tmp_path: Path) -> None:
    files = _source(tmp_path)
    assert isinstance(parse_pin(_pin_data(files)), ModelPin)
    cases: list[tuple[dict[str, Any], str]] = [
        ({"revision": "main"}, "40-hex commit"),
        ({"revision": "v1.0"}, "40-hex commit"),
        ({"files": {}}, "files"),
        ({"files": {"config.json": "abc"}}, "sha256"),
        ({"weights_file": "other.bin"}, "weights_file"),
        ({"config_file": "missing.json"}, "config_file"),
        ({"license": ""}, "license"),
        ({"source": "somewhere"}, "source"),
    ]
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            parse_pin(_pin_data(files, **override))


def test_fetch_downloads_the_pinned_revision_and_verifies_bytes(tmp_path: Path) -> None:
    files = _source(tmp_path)
    hub = FakeHub(files)
    pin = parse_pin(_pin_data(files))
    resolved = acquire(pin, tmp_path / "cache", allow_download=True, downloader=hub)
    assert {call["revision"] for call in hub.calls} == {REVISION}
    assert all(call["local_files_only"] is False for call in hub.calls)
    assert resolved.revision == REVISION
    assert resolved.files["model.safetensors"]["sha256"] == sha256_file(files["model.safetensors"])
    assert resolved.files["model.safetensors"]["bytes"] == len(b"weights-v1")
    assert resolved.weights_path.read_bytes() == b"weights-v1"


def test_evaluation_acquisition_never_downloads(tmp_path: Path) -> None:
    files = _source(tmp_path)
    hub = FakeHub(files)
    pin = parse_pin(_pin_data(files))
    with pytest.raises(ModelArtifactError, match="toem-reid fetch-model"):
        acquire(pin, tmp_path / "cache", allow_download=False, downloader=hub)
    acquire(pin, tmp_path / "cache", allow_download=True, downloader=hub)
    hub.calls.clear()
    acquire(pin, tmp_path / "cache", allow_download=False, downloader=hub)
    assert all(call["local_files_only"] is True for call in hub.calls)


def test_changed_upstream_bytes_fail_instead_of_being_used(tmp_path: Path) -> None:
    files = _source(tmp_path)
    pin = parse_pin(_pin_data(files))
    files["model.safetensors"].write_bytes(b"weights-v2")
    with pytest.raises(ModelArtifactError, match=r"SHA-256 mismatch for model\.safetensors"):
        acquire(pin, tmp_path / "cache", allow_download=True, downloader=FakeHub(files))


def test_tampered_cache_fails_verification(tmp_path: Path) -> None:
    files = _source(tmp_path)
    hub = FakeHub(files)
    pin = parse_pin(_pin_data(files))
    resolved = acquire(pin, tmp_path / "cache", allow_download=True, downloader=hub)
    resolved.weights_path.write_bytes(b"tampered")
    with pytest.raises(ModelArtifactError, match="SHA-256 mismatch"):
        acquire(pin, tmp_path / "cache", allow_download=False, downloader=hub)


def test_pretrained_cfg_is_read_from_the_verified_config(tmp_path: Path) -> None:
    files = _source(tmp_path)
    resolved = acquire(
        parse_pin(_pin_data(files)),
        tmp_path / "cache",
        allow_download=True,
        downloader=FakeHub(files),
    )
    architecture, cfg, model_args = read_model_config(resolved.config_path)
    assert architecture == "test_vit"
    assert cfg["input_size"] == [3, 160, 160]
    assert cfg["crop_pct"] == 0.9
    assert model_args == {}


def test_record_contains_revision_and_artifact_hashes(tmp_path: Path) -> None:
    files = _source(tmp_path)
    resolved = acquire(
        parse_pin(_pin_data(files)),
        tmp_path / "cache",
        allow_download=True,
        downloader=FakeHub(files),
    )
    record = resolved.record()
    assert record["repo_id"] == "example/model"
    assert record["revision"] == REVISION
    assert record["verified"] is True
    assert set(record["files"]) == {"config.json", "model.safetensors"}
    assert record["weights_sha256"] == sha256_file(files["model.safetensors"])
    assert "cache" not in json.dumps(record)


def test_real_research_configs_pin_revisions_and_hashes() -> None:
    from toem_reid.experiment import load_config

    configs = Path(__file__).resolve().parents[1] / "configs"
    for path in sorted(configs.glob("b-*.json")):
        config = load_config(path)
        assert config.model_artifact is not None
        pin = parse_pin(config.model_artifact)
        assert len(pin.revision) == 40
        assert pin.weights_file in pin.files
