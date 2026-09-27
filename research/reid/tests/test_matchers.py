from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import make_pattern_image

from toem_reid.images import load_image, prepare
from toem_reid.matchers import SiftMatcher, build_matcher
from toem_reid.quality import QualityThresholds, assess


def _arrays(tmp_path: Path) -> dict[str, np.ndarray]:
    out = {}
    for name, (identity, image) in {"a1": (1, 1), "a2": (1, 2), "b1": (2, 3)}.items():
        path = make_pattern_image(tmp_path / f"{name}.png", identity, image, size=256)
        out[name] = prepare(load_image(path), max_side=256)
    return out


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


def test_build_matcher_refuses_unknown_models_and_families() -> None:
    with pytest.raises(ValueError, match="no matcher"):
        build_matcher("A", "orb-magic", {})
    with pytest.raises(ValueError, match="optional 'embedding' dependency group"):
        build_matcher("B", "hf-hub:BVRA/MegaDescriptor-T-224", {"_force_missing": True})


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
