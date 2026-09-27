from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import make_pattern_image
from PIL import Image

from toem_reid.experiment import ExperimentConfig
from toem_reid.hashing import sha256_file
from toem_reid.images import apply_preprocessing, load_image, prepare
from toem_reid.matchers import SiftMatcher, build_matcher, resolve_model_cache
from toem_reid.models import ModelArtifactError, acquire, parse_pin
from toem_reid.quality import QualityThresholds, assess


def _config(**overrides: Any) -> ExperimentConfig:
    data: dict[str, Any] = {
        "config_id": "a",
        "algorithm_family": "A",
        "model_identifier": "opencv-sift",
        "model_version": "opencv-sift",
        "preprocessing": "original",
        "max_side": 256,
        "training_config": None,
        "seed": 0,
        "parameters": {"ratio": 0.8, "max_features": 500},
    }
    data.update(overrides)
    return ExperimentConfig(**data)


def _arrays(tmp_path: Path) -> dict[str, np.ndarray]:
    out = {}
    for name, (identity, image) in {"a1": (1, 1), "a2": (1, 2), "b1": (2, 3)}.items():
        path = make_pattern_image(tmp_path / f"{name}.png", identity, image, size=256)
        out[name] = prepare(load_image(path), max_side=256)
    return out


def _colour_image(width: int = 300, height: int = 200) -> Image.Image:
    rng = np.random.default_rng(7)
    pixels = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
    pixels[:, :, 0] = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
    return Image.fromarray(pixels, mode="RGB")


def test_sift_scores_same_pattern_above_different_pattern(tmp_path: Path) -> None:
    arrays = _arrays(tmp_path)
    matcher = SiftMatcher(ratio=0.8, max_features=500, ransac_threshold=5.0)
    features = {k: matcher.extract(v) for k, v in arrays.items()}
    sim = matcher.similarity([features["a2"]], [features["a1"], features["b1"]])
    assert sim.shape == (1, 2)
    assert sim[0, 0] > sim[0, 1]


def test_sift_similarity_is_deterministic(tmp_path: Path) -> None:
    arrays = _arrays(tmp_path)
    matcher = SiftMatcher(ratio=0.8, max_features=500, ransac_threshold=5.0)
    f = [matcher.extract(v) for v in arrays.values()]
    assert np.array_equal(matcher.similarity(f, f), matcher.similarity(f, f))


def test_sift_handles_featureless_images() -> None:
    matcher = SiftMatcher(ratio=0.8, max_features=500, ransac_threshold=5.0)
    blank = matcher.extract(np.zeros((64, 64), dtype=np.uint8))
    assert matcher.similarity([blank], [blank]).tolist() == [[0.0]]


def test_sift_receives_grayscale_limited_to_max_side() -> None:
    matcher = build_matcher(_config(max_side=128))
    assert matcher.input_mode == "grayscale"
    prepared = matcher.prepare_input(_colour_image(300, 200))
    assert prepared.ndim == 2
    assert prepared.dtype == np.uint8
    assert max(prepared.shape) == 128
    with pytest.raises(ValueError, match="grayscale"):
        matcher.extract(np.zeros((32, 32, 3), dtype=np.uint8))
    assert matcher.provenance()["input"] == "grayscale"


def test_build_matcher_refuses_unknown_models_and_families() -> None:
    with pytest.raises(ValueError, match="no matcher"):
        build_matcher(_config(model_identifier="orb-magic"))
    with pytest.raises(ValueError, match="optional 'embedding' dependency group"):
        build_matcher(_config(algorithm_family="B", parameters={"_force_missing": True}))


def test_pretrained_families_need_a_model_cache_outside_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TOEM_REID_MODEL_CACHE", raising=False)
    with pytest.raises(ValueError, match="--model-cache"):
        resolve_model_cache(None)
    monkeypatch.setenv("TOEM_REID_MODEL_CACHE", str(tmp_path))
    assert resolve_model_cache(None) == tmp_path
    assert resolve_model_cache(tmp_path / "x") == tmp_path / "x"


def test_head_crop_happens_before_family_input_preparation() -> None:
    seen: list[tuple[int, int]] = []

    class Spy:
        input_mode = "rgb"

        def prepare_input(self, image: Image.Image) -> Image.Image:
            seen.append(image.size)
            return image

    image = _colour_image(300, 200)
    cropped = apply_preprocessing(image, "head_crop", "10,20,110,120")
    Spy().prepare_input(cropped)
    assert seen == [(100, 100)]


def test_quality_flags_small_blurred_and_exposure(tmp_path: Path) -> None:
    thresholds = QualityThresholds(
        min_head_pixels=96, min_sharpness=20.0, dark_mean=35.0, bright_mean=220.0
    )
    sharp = prepare(load_image(make_pattern_image(tmp_path / "s.png", 1, 1, size=256)), 256)
    assert assess(sharp, thresholds).recommendation == "ACCEPT"
    tiny = sharp[:40, :40]
    assert assess(tiny, thresholds).recommendation == "INSUFFICIENT HEAD VIEW"
    flat = np.full((256, 256), 128, dtype=np.uint8)
    report = assess(flat, thresholds)
    assert report.recommendation == "RETRY PHOTO"
    assert "blur" in report.reasons
    dark = np.clip(sharp.astype(np.int16) // 8, 0, 255).astype(np.uint8)
    assert "underexposed" in assess(dark, thresholds).reasons


# --- Family B: exercised with a tiny random-init timm model built locally. ---------------
# No pretrained weights are downloaded; these tests check preprocessing and pinning
# mechanics only and say nothing about Re-ID quality.

REVISION = "0123456789abcdef0123456789abcdef01234567"
PRETRAINED_CFG = {
    "input_size": [3, 64, 64],
    "interpolation": "bicubic",
    "crop_pct": 0.875,
    "crop_mode": "center",
    "mean": [0.1, 0.2, 0.3],
    "std": [0.4, 0.5, 0.6],
}


def _import_embedding(name: str) -> Any:
    """Skip locally without the embedding group; fail in CI (TOEM_REID_REQUIRE_EMBEDDING=1)."""
    if os.environ.get("TOEM_REID_REQUIRE_EMBEDDING") == "1":
        return importlib.import_module(name)
    return pytest.importorskip(name)


def _local_artifact(tmp_path: Path) -> tuple[dict[str, Any], Any]:
    timm = _import_embedding("timm")
    torch = _import_embedding("torch")
    safetensors_torch = _import_embedding("safetensors.torch")
    torch.manual_seed(0)
    model = timm.create_model("test_vit", pretrained=False, num_classes=0, img_size=64)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "config.json").write_text(
        json.dumps(
            {
                "architecture": "test_vit",
                "num_classes": 0,
                "model_args": {"img_size": 64},
                "pretrained_cfg": PRETRAINED_CFG,
            }
        ),
        encoding="utf-8",
    )
    safetensors_torch.save_file(
        {k: v.contiguous() for k, v in model.state_dict().items()},
        str(upstream / "model.safetensors"),
    )
    pin = {
        "source": "huggingface",
        "repo_id": "local/test-vit",
        "revision": REVISION,
        "config_file": "config.json",
        "weights_file": "model.safetensors",
        "files": {n: sha256_file(upstream / n) for n in ("config.json", "model.safetensors")},
        "license": "test-fixture",
        "licence_scope": "tests only",
    }

    def downloader(
        *, repo_id: str, filename: str, revision: str, cache_dir: str, local_files_only: bool
    ) -> str:
        assert revision == REVISION
        return str(upstream / filename)

    return pin, downloader


def _embedding_matcher(tmp_path: Path) -> Any:
    from toem_reid.matchers import EmbeddingMatcher

    pin, downloader = _local_artifact(tmp_path)
    artifact = acquire(
        parse_pin(pin), tmp_path / "cache", allow_download=False, downloader=downloader
    )
    return EmbeddingMatcher(artifact)


def test_family_b_receives_rgb_and_uses_the_pinned_model_preprocessing(tmp_path: Path) -> None:
    matcher = _embedding_matcher(tmp_path)
    assert matcher.input_mode == "rgb"
    config = matcher.data_config
    assert config["input_size"] == [3, 64, 64]
    assert config["interpolation"] == "bicubic"
    assert config["crop_pct"] == 0.875
    assert config["crop_mode"] == "center"
    assert config["mean"] == PRETRAINED_CFG["mean"]
    assert config["std"] == PRETRAINED_CFG["std"]
    tensor = matcher.prepare_input(_colour_image(300, 200))
    assert tuple(tensor.shape) == (3, 64, 64)
    with pytest.raises(ValueError, match="RGB"):
        matcher.prepare_input(_colour_image().convert("L"))


def test_family_b_preserves_aspect_ratio_by_resize_then_centre_crop(tmp_path: Path) -> None:
    matcher = _embedding_matcher(tmp_path)
    text = repr(matcher.transform)
    assert "Resize(size=73" in text  # shorter side -> 64 / 0.875, aspect preserved
    assert "CenterCrop(size=[64, 64])" in text
    assert "bicubic" in text
    assert "Normalize" in text


def test_family_b_colour_reaches_the_model(tmp_path: Path) -> None:
    matcher = _embedding_matcher(tmp_path)
    tensor = matcher.prepare_input(_colour_image())
    channels = tensor.reshape(3, -1)
    assert not bool((channels[0] == channels[1]).all())


def test_family_b_preprocessing_and_embedding_are_deterministic(tmp_path: Path) -> None:
    matcher = _embedding_matcher(tmp_path)
    image = _colour_image()
    a = matcher.prepare_input(image)
    b = matcher.prepare_input(image)
    assert bool((a == b).all())
    va, vb = matcher.extract(a), matcher.extract(b)
    assert np.array_equal(va, vb)
    assert abs(float(np.linalg.norm(va)) - 1.0) < 1e-5
    sim = matcher.similarity([va], [vb])
    assert sim.shape == (1, 1)


def test_family_b_provenance_records_revision_hash_and_transform(tmp_path: Path) -> None:
    matcher = _embedding_matcher(tmp_path)
    record = matcher.provenance()
    assert record["input"] == "rgb"
    assert record["architecture"] == "test_vit"
    assert record["artifact"]["revision"] == REVISION
    assert len(record["artifact"]["weights_sha256"]) == 64
    assert "CenterCrop" in record["transform"]


def test_family_b_refuses_weights_that_do_not_load_exactly(tmp_path: Path) -> None:
    from toem_reid.matchers import create_pinned_model

    timm = _import_embedding("timm")
    safetensors_torch = _import_embedding("safetensors.torch")
    donor = timm.create_model("test_vit", pretrained=False, num_classes=0, img_size=64)
    state = {k: v.contiguous() for k, v in donor.state_dict().items()}
    state.pop(next(iter(state)))
    weights = tmp_path / "partial.safetensors"
    safetensors_torch.save_file(state, str(weights))
    with pytest.raises(ModelArtifactError, match="did not load exactly"):
        create_pinned_model("test_vit", dict(PRETRAINED_CFG), {"img_size": 64}, weights)


def test_family_b_build_matcher_refuses_unverified_or_uncached_weights(tmp_path: Path) -> None:
    _import_embedding("timm")
    pin, _ = _local_artifact(tmp_path)
    config = _config(
        algorithm_family="B",
        model_identifier="hf-hub:local/test-vit",
        parameters={"device": "cpu"},
        model_artifact=pin,
    )
    with pytest.raises(ModelArtifactError, match="fetch-model"):
        build_matcher(config, model_cache=tmp_path / "empty-cache")
