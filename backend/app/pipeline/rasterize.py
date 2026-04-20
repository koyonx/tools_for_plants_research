"""Rasterise polygon annotations into single-channel semantic masks.

Output convention (matches SegFormer / HuggingFace semantic-seg defaults):
- uint8 H-by-W array, value 0 = background, values 1..N = tissue classes
  (1-based index into `TISSUE_CLASS_KEYS` so the sentinel 0 is preserved)
- Overlapping polygons: the later annotation wins (last-write-wins).
  That matches how the annotation editor presents the layers visually.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from app.pipeline.classes import TISSUE_CLASS_KEYS

# 0 is reserved for "background / unlabelled".
CLASS_INDEX: dict[str, int] = {key: i + 1 for i, key in enumerate(TISSUE_CLASS_KEYS)}
INDEX_CLASS: dict[int, str] = {v: k for k, v in CLASS_INDEX.items()}


def rasterize_annotations(
    annotations: list[dict[str, Any]],
    image_height: int,
    image_width: int,
) -> np.ndarray:
    """Paint each polygon onto a semantic mask keyed by tissue class.

    Parameters
    ----------
    annotations
        Rows from `public.annotations` (need `class` + `polygon`).  Unknown
        class keys and malformed polygons are skipped silently — DB CHECK
        constraints catch them at write time, this guard is just defensive.
    image_height, image_width
        Target mask dimensions in pixels (come from `images.height_px` etc).
    """
    if image_height <= 0 or image_width <= 0:
        raise ValueError("image dimensions must be positive")

    mask = np.zeros((image_height, image_width), dtype=np.uint8)

    for ann in annotations:
        cls_key = ann.get("class")
        polygon = ann.get("polygon")
        idx = CLASS_INDEX.get(cls_key) if isinstance(cls_key, str) else None
        if idx is None:
            continue
        if not isinstance(polygon, list) or len(polygon) < 3:
            continue
        try:
            pts = np.asarray(polygon, dtype=np.int32)
        except (ValueError, TypeError):
            continue
        if pts.ndim != 2 or pts.shape[1] != 2:
            continue
        # Clip to image bounds; the upload path already clamps but legacy
        # rows (pre-PR-4-round-2) may still be out of range.
        pts[:, 0] = np.clip(pts[:, 0], 0, image_width - 1)
        pts[:, 1] = np.clip(pts[:, 1], 0, image_height - 1)
        # `color` must be a sequence for cv2's type stubs even on single-
        # channel masks; `(idx,)` is the canonical single-channel form.
        cv2.fillPoly(mask, [pts.reshape(-1, 1, 2)], color=(int(idx),))

    return mask


def classes_manifest() -> dict[str, Any]:
    """Metadata to ship alongside the mask (so trainers know the mapping)."""
    return {
        "background_index": 0,
        "classes": [
            {"index": idx, "key": key}
            for key, idx in sorted(CLASS_INDEX.items(), key=lambda kv: kv[1])
        ],
    }
