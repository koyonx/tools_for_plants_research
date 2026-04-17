"""Geometric measurements from a leaf mask.

Outputs
-------
- area_um2:                total leaf cross-section area
- thickness_um (mean/min/max):
                           per-column (vertical span of mask) in physical units
- thickness_profile_um:    downsampled 1-D array along the horizontal axis
                           (for plotting on the frontend)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

MAX_PROFILE_POINTS = 512


@dataclass(frozen=True)
class MeasurementResult:
    leaf_area_um2: float
    leaf_mean_thickness_um: float
    leaf_median_thickness_um: float
    leaf_min_thickness_um: float
    leaf_max_thickness_um: float
    thickness_profile_um: list[float]
    thickness_profile_x_um: list[float]
    valid_columns: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def measure_from_mask(mask: np.ndarray, um_per_px: float) -> MeasurementResult:
    if mask.ndim != 2:
        raise ValueError("mask must be 2-D")
    if um_per_px <= 0:
        raise ValueError("um_per_px must be positive")

    bin_mask = (mask > 0).astype(np.uint8)
    h = bin_mask.shape[0]
    total_px = int(bin_mask.sum())
    if total_px == 0:
        return MeasurementResult(
            leaf_area_um2=0.0,
            leaf_mean_thickness_um=0.0,
            leaf_median_thickness_um=0.0,
            leaf_min_thickness_um=0.0,
            leaf_max_thickness_um=0.0,
            thickness_profile_um=[],
            thickness_profile_x_um=[],
            valid_columns=0,
        )

    # Per-column thickness = vertical extent of the mask in that column
    # (max y - min y + 1 where mask is set).  Using first/last nonzero row
    # rather than sum tolerates intra-leaf gaps (air spaces).
    col_has_any = bin_mask.any(axis=0)
    first = np.argmax(bin_mask, axis=0)
    # flip + argmax gives position from bottom; convert back to y index
    last = h - 1 - np.argmax(bin_mask[::-1], axis=0)
    thickness_px = np.where(col_has_any, last - first + 1, 0).astype(np.float32)
    valid = thickness_px > 0
    thickness_valid_um = thickness_px[valid] * um_per_px

    # Downsample profile for storage
    xs = np.where(valid)[0]
    if len(xs) > MAX_PROFILE_POINTS:
        idx = np.linspace(0, len(xs) - 1, MAX_PROFILE_POINTS).astype(np.int64)
        xs = xs[idx]
        thickness_valid_um = thickness_valid_um[idx]

    return MeasurementResult(
        leaf_area_um2=float(total_px) * (um_per_px**2),
        leaf_mean_thickness_um=float(thickness_valid_um.mean()),
        leaf_median_thickness_um=float(np.median(thickness_valid_um)),
        leaf_min_thickness_um=float(thickness_valid_um.min()),
        leaf_max_thickness_um=float(thickness_valid_um.max()),
        thickness_profile_um=thickness_valid_um.astype(float).tolist(),
        thickness_profile_x_um=(xs.astype(np.float64) * um_per_px).tolist(),
        valid_columns=int(valid.sum()),
    )
