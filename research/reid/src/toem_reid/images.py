"""Image loading and preprocessing for research matching.

Only the EXIF orientation tag is applied (so phone photos are upright); no other
EXIF field, and in particular no GPS field, is read (Requirement 004 section 21).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps

SUPPORTED_SUFFIXES: Final = frozenset({".jpg", ".jpeg", ".png"})
PREPROCESSING: Final = ("original", "head_crop")


def load_image(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        upright = ImageOps.exif_transpose(opened)
        return upright.convert("RGB")


def dhash(image: Image.Image, size: int = 8) -> int:
    """64-bit difference hash; robust to resizing/recompression, used for near-duplicates."""
    small = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = np.asarray(small, dtype=np.int16)
    bits = (pixels[:, 1:] > pixels[:, :-1]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def crop_to_bbox(image: Image.Image, bbox: str) -> Image.Image:
    """Crop to a manual ``x0,y0,x1,y1`` head box (pixel coordinates of the upright image)."""
    parts = bbox.split(",")
    try:
        x0, y0, x1, y1 = (int(p) for p in parts)
    except ValueError:
        raise ValueError(f"head_bbox must be four integers x0,y0,x1,y1, got {bbox!r}") from None
    width, height = image.size
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(f"head_bbox {bbox!r} is outside the {width}x{height} image")
    return image.crop((x0, y0, x1, y1))


def prepare(image: Image.Image, max_side: int) -> NDArray[np.uint8]:
    """Grayscale array with the longest side limited to ``max_side`` (never upscaled)."""
    gray = image.convert("L")
    longest = max(gray.size)
    if longest > max_side:
        scale = max_side / longest
        size = (max(1, round(gray.width * scale)), max(1, round(gray.height * scale)))
        gray = gray.resize(size, Image.Resampling.LANCZOS)
    return np.asarray(gray, dtype=np.uint8).copy()


def apply_preprocessing(
    image: Image.Image, preprocessing: str, head_bbox: str | None
) -> Image.Image:
    if preprocessing == "original":
        return image
    if preprocessing == "head_crop":
        if not head_bbox:
            raise ValueError("head_crop preprocessing requires an attr_head_bbox annotation")
        return crop_to_bbox(image, head_bbox)
    raise ValueError(f"preprocessing must be one of {PREPROCESSING}, got {preprocessing!r}")


def store_path(store: Path, dataset_id: str, sha256: str, suffix: str) -> Path:
    """Content-addressed location of a research copy (outside Git)."""
    return store / dataset_id / sha256[:2] / f"{sha256}{suffix}"
