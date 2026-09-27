"""Quality signals for research analysis only (Requirement 004 section 18).

Produces a recommendation (ACCEPT / RETRY PHOTO / INSUFFICIENT HEAD VIEW) used to
stratify results and inform future design. This is not a production quality
service and never gates product behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    min_head_pixels: int = 96
    min_sharpness: float = 20.0
    dark_mean: float = 35.0
    bright_mean: float = 220.0


@dataclass(frozen=True, slots=True)
class QualityReport:
    head_pixels: int
    sharpness: float
    mean_intensity: float
    reasons: list[str] = field(default_factory=list)

    @property
    def recommendation(self) -> str:
        if "head_too_small" in self.reasons:
            return "INSUFFICIENT HEAD VIEW"
        return "RETRY PHOTO" if self.reasons else "ACCEPT"


def assess(gray: NDArray[np.uint8], thresholds: QualityThresholds) -> QualityReport:
    """``gray`` is the region that will be matched (the head crop when one exists)."""
    head_pixels = int(min(gray.shape))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mean = float(gray.mean())
    reasons = []
    if head_pixels < thresholds.min_head_pixels:
        reasons.append("head_too_small")
    if sharpness < thresholds.min_sharpness:
        reasons.append("blur")
    if mean < thresholds.dark_mean:
        reasons.append("underexposed")
    if mean > thresholds.bright_mean:
        reasons.append("overexposed")
    return QualityReport(head_pixels, sharpness, mean, reasons)
