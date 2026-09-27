"""Re-ID approach families behind one small interface.

Each matcher owns its *input preparation*, because the families need different
inputs (review of 3259571):

* Family A — classical local features: OpenCV SIFT on a **grayscale** array
  (longest side limited to ``max_side``), Lowe ratio test, RANSAC homography;
  similarity = geometrically verified inlier count.
* Family B — general pretrained embedding (optional ``embedding`` dependency
  group): the **RGB** head crop/image goes through the model's canonical
  evaluation transform, built by timm from the ``pretrained_cfg`` inside the
  pinned, SHA-256-verified ``config.json`` (input size, interpolation,
  resize/centre-crop via ``crop_pct``/``crop_mode``, mean/std). timm resizes the
  shorter side and centre-crops, so aspect ratio is not distorted. Weights are
  loaded strictly from the verified local artifact; nothing is downloaded here.
* Family C — fine-tuning is deliberately not implemented; it is only attempted
  after a review checkpoint (Requirement 004 section 5.3).

The evaluation loop applies the head crop *before* ``prepare_input``.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from toem_reid.experiment import ExperimentConfig
from toem_reid.images import prepare
from toem_reid.models import (
    ModelArtifactError,
    ResolvedArtifact,
    acquire,
    parse_pin,
    read_model_config,
)

MODEL_CACHE_ENV = "TOEM_REID_MODEL_CACHE"


class Matcher(Protocol):
    input_mode: str

    def prepare_input(self, image: Image.Image) -> Any: ...

    def extract(self, prepared: Any) -> Any: ...

    def similarity(self, queries: Sequence[Any], gallery: Sequence[Any]) -> NDArray[np.float64]: ...

    def provenance(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SiftFeatures:
    points: NDArray[np.float32]
    descriptors: NDArray[np.float32] | None


class SiftMatcher:
    input_mode = "grayscale"

    def __init__(
        self, ratio: float, max_features: int, ransac_threshold: float, max_side: int = 768
    ) -> None:
        self.ratio = ratio
        self.max_features = max_features
        self.ransac_threshold = ransac_threshold
        self.max_side = max_side
        self._sift = cv2.SIFT_create(nfeatures=max_features)  # type: ignore[attr-defined]
        self._matcher = cv2.BFMatcher(cv2.NORM_L2)

    def prepare_input(self, image: Image.Image) -> NDArray[np.uint8]:
        return prepare(image, self.max_side)

    def extract(self, prepared: NDArray[np.uint8]) -> SiftFeatures:
        if prepared.ndim != 2 or prepared.dtype != np.uint8:
            raise ValueError("SIFT expects a 2-D uint8 grayscale array")
        keypoints, descriptors = self._sift.detectAndCompute(prepared, None)
        points = np.array([kp.pt for kp in keypoints], dtype=np.float32).reshape(-1, 2)
        return SiftFeatures(points, descriptors)

    def _pair(self, query: SiftFeatures, reference: SiftFeatures) -> float:
        if query.descriptors is None or reference.descriptors is None:
            return 0.0
        if len(query.descriptors) < 2 or len(reference.descriptors) < 2:
            return 0.0
        good = [
            pair[0]
            for pair in self._matcher.knnMatch(query.descriptors, reference.descriptors, k=2)
            if len(pair) == 2 and pair[0].distance < self.ratio * pair[1].distance
        ]
        if len(good) < 4:
            return 0.0
        src = query.points[[m.queryIdx for m in good]].reshape(-1, 1, 2)
        dst = reference.points[[m.trainIdx for m in good]].reshape(-1, 1, 2)
        cv2.setRNGSeed(0)
        _, mask = cv2.findHomography(src, dst, cv2.RANSAC, self.ransac_threshold)
        return float(mask.sum()) if mask is not None else 0.0

    def similarity(
        self, queries: Sequence[SiftFeatures], gallery: Sequence[SiftFeatures]
    ) -> NDArray[np.float64]:
        out = np.zeros((len(queries), len(gallery)), dtype=np.float64)
        for i, q in enumerate(queries):
            for j, g in enumerate(gallery):
                out[i, j] = self._pair(q, g)
        return out

    def provenance(self) -> dict[str, Any]:
        return {
            "input": self.input_mode,
            "max_side": self.max_side,
            "library": "opencv-python-headless",
            "library_version": cv2.__version__,
            "ratio": self.ratio,
            "max_features": self.max_features,
            "ransac_threshold": self.ransac_threshold,
        }


class _LoadReport(logging.Handler):
    """Collects timm's missing/unexpected-key reports so a partial load can fail loudly."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.problems: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "Missing keys" in message or "Unexpected keys" in message:
            self.problems.append(message)


def create_pinned_model(
    architecture: str,
    pretrained_cfg: dict[str, Any],
    model_args: dict[str, Any],
    weights_path: Path,
) -> Any:
    """Build a timm model from verified local weights via timm's canonical pretrained path.

    Using ``pretrained=True`` with a ``file`` source keeps the architecture's own
    checkpoint filter (e.g. old Swin key layouts) while guaranteeing no network
    access: the hub id is removed and the verified file is the only source.
    """
    timm = importlib.import_module("timm")
    cfg = {k: v for k, v in pretrained_cfg.items() if k not in {"hf_hub_id", "url", "source"}}
    cfg["file"] = os.fspath(weights_path)
    report = _LoadReport()
    builder_logger = logging.getLogger("timm.models._builder")
    builder_logger.addHandler(report)
    previous_level = builder_logger.level
    builder_logger.setLevel(logging.INFO)
    try:
        model = timm.create_model(
            architecture, pretrained=True, pretrained_cfg=cfg, num_classes=0, **model_args
        )
    except RuntimeError as error:
        raise ModelArtifactError(
            f"pinned weights did not load exactly into the backbone: {error}"
        ) from error
    finally:
        builder_logger.removeHandler(report)
        builder_logger.setLevel(previous_level)
    if report.problems:
        raise ModelArtifactError(
            "pinned weights did not load exactly into the backbone: " + " | ".join(report.problems)
        )
    return model


def _plain(value: Any) -> Any:
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    return value


class EmbeddingMatcher:
    input_mode = "rgb"

    def __init__(self, artifact: ResolvedArtifact, device: str = "cpu") -> None:
        timm_data = importlib.import_module("timm.data")
        torch = importlib.import_module("torch")
        self._torch = torch
        self.artifact = artifact
        self.device = device
        self.architecture, pretrained_cfg, model_args = read_model_config(artifact.config_path)
        self.model = create_pinned_model(
            self.architecture, pretrained_cfg, model_args, artifact.weights_path
        )
        self.model.eval().to(device)
        self.data_config: dict[str, Any] = {
            k: _plain(v)
            for k, v in timm_data.resolve_data_config({}, pretrained_cfg=pretrained_cfg).items()
        }
        self.transform = timm_data.create_transform(**self.data_config, is_training=False)

    def prepare_input(self, image: Image.Image) -> Any:
        if image.mode != "RGB":
            raise ValueError(f"family B expects an RGB image, got mode {image.mode!r}")
        return self.transform(image)

    def extract(self, prepared: Any) -> NDArray[np.float32]:
        with self._torch.inference_mode():
            vector: NDArray[np.float32] = (
                self.model(prepared[None].to(self.device))[0].float().cpu().numpy()
            )
        norm = float(np.linalg.norm(vector))
        return (vector / norm if norm else vector).astype(np.float32)

    def similarity(
        self, queries: Sequence[NDArray[np.float32]], gallery: Sequence[NDArray[np.float32]]
    ) -> NDArray[np.float64]:
        return np.asarray(np.stack(queries) @ np.stack(gallery).T, dtype=np.float64)

    def provenance(self) -> dict[str, Any]:
        return {
            "input": self.input_mode,
            "architecture": self.architecture,
            "data_config": self.data_config,
            "transform": repr(self.transform),
            "timm_version": importlib.import_module("timm").__version__,
            "torch_version": str(self._torch.__version__),
            "device": self.device,
            "artifact": self.artifact.record(),
        }


def resolve_model_cache(model_cache: Path | None) -> Path:
    if model_cache is not None:
        return model_cache
    env = os.environ.get(MODEL_CACHE_ENV)
    if env:
        return Path(env)
    raise ValueError(
        f"pretrained families need a model cache outside Git: pass --model-cache or set "
        f"{MODEL_CACHE_ENV}"
    )


def build_matcher(config: ExperimentConfig, model_cache: Path | None = None) -> Matcher:
    family, parameters = config.algorithm_family, config.parameters
    if family == "A" and config.model_identifier == "opencv-sift":
        return SiftMatcher(
            ratio=float(parameters.get("ratio", 0.8)),
            max_features=int(parameters.get("max_features", 2000)),
            ransac_threshold=float(parameters.get("ransac_threshold", 5.0)),
            max_side=config.max_side,
        )
    if family == "B":
        if parameters.get("_force_missing") or importlib.util.find_spec("timm") is None:
            raise ValueError(
                "family B needs the optional 'embedding' dependency group: "
                "uv sync --group embedding"
            )
        if config.model_artifact is None:  # pragma: no cover - load_config refuses this
            raise ValueError("family B requires a model_artifact pin")
        artifact = acquire(
            parse_pin(config.model_artifact),
            resolve_model_cache(model_cache),
            allow_download=False,
        )
        return EmbeddingMatcher(artifact, device=str(parameters.get("device", "cpu")))
    raise ValueError(f"no matcher for family {family!r} model {config.model_identifier!r}")


def pretrained_cfg_summary(config_path: Path) -> dict[str, Any]:
    """Human-readable view of a verified config.json (used by ``fetch-model``)."""
    architecture, cfg, model_args = read_model_config(config_path)
    keys = ("input_size", "interpolation", "crop_pct", "crop_mode", "mean", "std")
    return {"architecture": architecture, "model_args": model_args, **{k: cfg.get(k) for k in keys}}
