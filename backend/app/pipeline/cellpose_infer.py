"""Cellpose 3 wrapper for cell / guard-cell detection.

We import `cellpose` lazily inside `detect_cells` so the rest of the
backend (health, measurement pipeline, rasteriser, tests) can run on
CI without pulling in PyTorch + Cellpose (~1.5 GB).  The ML extras live
behind the `ml` optional-dependency group in pyproject.toml and are
installed by the runtime Docker image only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import cv2
import numpy as np

# `cyto3` is Cellpose 3's generalist cytoplasm model (~26 MB weights).
# Good first pass for plant-cell cross-sections; PR #5c (SegFormer) will
# specialise when we have enough annotated data.
DEFAULT_MODEL = "cyto3"
# Down-sampling target for inference — full-resolution 10x microscope
# images (~2048 px) are slow on CPU and rarely need full precision for
# cell-level detection.  Results are scaled back to original coords.
DEFAULT_MAX_SIDE_PX = 1024
MIN_CELL_AREA_DOWNSAMPLED_PX = 5


@dataclass(frozen=True)
class Cell:
    polygon: list[list[float]]
    centroid: list[float]
    area_px: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CellposeResult:
    model: str
    cell_count: int
    cells: list[Cell] = field(default_factory=list)
    downsample_factor: float = 1.0
    mean_area_px: float = 0.0
    median_area_px: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "cell_count": self.cell_count,
            "downsample_factor": self.downsample_factor,
            "mean_area_px": self.mean_area_px,
            "median_area_px": self.median_area_px,
            "cells": [c.to_dict() for c in self.cells],
        }


_model_cache: dict[str, Any] = {}


def _get_model(model_name: str) -> Any:
    """Return a cached Cellpose model, loading it on first use."""
    if model_name in _model_cache:
        return _model_cache[model_name]
    # Import lazily — the cellpose + torch stack is only present in the
    # runtime Docker image (see pyproject's `ml` extra).
    from cellpose import models as cp_models

    # The `pretrained_model=` kwarg is the Cellpose 3 idiom; `model_type=`
    # is kept around for compatibility but emits DeprecationWarnings.
    try:
        model = cp_models.CellposeModel(pretrained_model=model_name, gpu=False)
    except TypeError:
        model = cp_models.CellposeModel(model_type=model_name, gpu=False)
    _model_cache[model_name] = model
    return model


def _polygon_from_label(
    mask_region: np.ndarray, inv_factor: float
) -> tuple[list[list[float]], list[float], int] | None:
    """Turn a single-instance binary mask into (polygon, centroid, area_px).

    `inv_factor` maps down-sampled coordinates back into the original
    full-resolution pixel grid.  Returns None if the contour is too small
    to be a useful cell.
    """
    contours, _ = cv2.findContours(mask_region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area_ds = float(cv2.contourArea(contour))
    if area_ds < MIN_CELL_AREA_DOWNSAMPLED_PX:
        return None

    epsilon = 0.005 * cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, epsilon, True)
    polygon = [[float(p[0][0]) * inv_factor, float(p[0][1]) * inv_factor] for p in approx]

    moments = cv2.moments(contour)
    if moments["m00"] == 0:
        return None
    cx = (moments["m10"] / moments["m00"]) * inv_factor
    cy = (moments["m01"] / moments["m00"]) * inv_factor
    area_orig = int(area_ds * inv_factor * inv_factor)
    return polygon, [cx, cy], area_orig


def detect_cells(
    image_bgr: np.ndarray,
    *,
    max_side_px: int = DEFAULT_MAX_SIDE_PX,
    diameter: float | None = None,
    model_name: str = DEFAULT_MODEL,
) -> CellposeResult:
    """Run Cellpose over the image and return per-cell polygons + stats.

    Coordinates in the result are in the **original** image's pixel space;
    any internal down-sampling is reversed before return.
    """
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr must be an HxWx3 BGR array")

    h, w = image_bgr.shape[:2]
    longest = max(h, w)
    factor = max_side_px / longest if longest > max_side_px else 1.0
    if factor < 1.0:
        resized = cv2.resize(
            image_bgr,
            (int(w * factor), int(h * factor)),
            interpolation=cv2.INTER_AREA,
        )
    else:
        resized = image_bgr
    inv_factor = 1.0 / factor

    model = _get_model(model_name)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    # Cellpose 3 returns (masks, flows, styles); older calls also returned
    # diams as a 4th element — tolerate both.
    outputs = model.eval(rgb, diameter=diameter, channels=[0, 0])
    masks = outputs[0] if isinstance(outputs, tuple) else outputs
    if not isinstance(masks, np.ndarray):
        raise RuntimeError("cellpose did not return a mask array")

    cells: list[Cell] = []
    max_label = int(masks.max())
    for label in range(1, max_label + 1):
        region = (masks == label).astype(np.uint8) * 255
        extracted = _polygon_from_label(region, inv_factor)
        if extracted is None:
            continue
        polygon, centroid, area = extracted
        cells.append(Cell(polygon=polygon, centroid=centroid, area_px=area))

    areas = np.array([c.area_px for c in cells], dtype=np.float64) if cells else np.array([0.0])
    return CellposeResult(
        model=model_name,
        cell_count=len(cells),
        cells=cells,
        downsample_factor=float(factor),
        mean_area_px=float(areas.mean()) if cells else 0.0,
        median_area_px=float(np.median(areas)) if cells else 0.0,
    )
