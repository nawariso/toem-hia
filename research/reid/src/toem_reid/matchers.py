"""Re-ID approach families behind one small interface.

* Family A — classical local features: OpenCV SIFT, Lowe ratio test, RANSAC
  homography; similarity = geometrically verified inlier count.
* Family B — general pretrained embedding (optional ``embedding`` dependency
  group): timm backbone, L2-normalised global features, cosine similarity.
* Family C — fine-tuning is deliberately not implemented yet; it is only
  attempted after a review checkpoint (Requirement 004 section 5.3).
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray


class Matcher(Protocol):
    def extract(self, gray: NDArray[np.uint8]) -> Any: ...

    def similarity(self, queries: Sequence[Any], gallery: Sequence[Any]) -> NDArray[np.float64]: ...


@dataclass(frozen=True, slots=True)
class SiftFeatures:
    points: NDArray[np.float32]
    descriptors: NDArray[np.float32] | None


class SiftMatcher:
    def __init__(self, ratio: float, max_features: int, ransac_threshold: float) -> None:
        self.ratio = ratio
        self.ransac_threshold = ransac_threshold
        self._sift = cv2.SIFT_create(nfeatures=max_features)  # type: ignore[attr-defined]
        self._matcher = cv2.BFMatcher(cv2.NORM_L2)

    def extract(self, gray: NDArray[np.uint8]) -> SiftFeatures:
        keypoints, descriptors = self._sift.detectAndCompute(gray, None)
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


class EmbeddingMatcher:  # pragma: no cover - needs the optional embedding group and weights
    def __init__(self, model_identifier: str, input_size: int, device: str) -> None:
        import timm  # type: ignore[import-not-found]
        import torch  # type: ignore[import-not-found]

        self._torch = torch
        self.device = device
        self.input_size = input_size
        self.model = timm.create_model(model_identifier, pretrained=True, num_classes=0)
        self.model.eval().to(device)
        config = timm.data.resolve_data_config({}, model=self.model)
        self.mean = np.array(config["mean"], dtype=np.float32).reshape(3, 1, 1)
        self.std = np.array(config["std"], dtype=np.float32).reshape(3, 1, 1)

    def extract(self, gray: NDArray[np.uint8]) -> NDArray[np.float32]:
        resized = cv2.resize(gray, (self.input_size, self.input_size), interpolation=cv2.INTER_AREA)
        rgb = np.repeat(resized[None, :, :], 3, axis=0).astype(np.float32) / 255.0
        tensor = self._torch.from_numpy((rgb - self.mean) / self.std)[None].to(self.device)
        with self._torch.inference_mode():
            vector: NDArray[np.float32] = self.model(tensor)[0].float().cpu().numpy()
        norm = float(np.linalg.norm(vector))
        return (vector / norm if norm else vector).astype(np.float32)

    def similarity(
        self, queries: Sequence[NDArray[np.float32]], gallery: Sequence[NDArray[np.float32]]
    ) -> NDArray[np.float64]:
        return np.asarray(np.stack(queries) @ np.stack(gallery).T, dtype=np.float64)


def build_matcher(family: str, model_identifier: str, parameters: dict[str, Any]) -> Matcher:
    if family == "A" and model_identifier == "opencv-sift":
        return SiftMatcher(
            ratio=float(parameters.get("ratio", 0.8)),
            max_features=int(parameters.get("max_features", 2000)),
            ransac_threshold=float(parameters.get("ransac_threshold", 5.0)),
        )
    if family == "B":
        if parameters.get("_force_missing") or importlib.util.find_spec("timm") is None:
            raise ValueError(
                "family B needs the optional 'embedding' dependency group: "
                "uv sync --group embedding"
            )
        return EmbeddingMatcher(  # pragma: no cover - optional dependency
            model_identifier,
            input_size=int(parameters.get("input_size", 224)),
            device=str(parameters.get("device", "cpu")),
        )
    raise ValueError(f"no matcher for family {family!r} model {model_identifier!r}")
