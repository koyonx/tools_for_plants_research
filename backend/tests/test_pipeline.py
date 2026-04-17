"""Synthetic-image tests for the classical-CV pipeline.

Each test constructs a known ground-truth image in memory so CI runs
deterministically without needing real microscopy data.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.pipeline.measure import measure_from_mask
from app.pipeline.scale import detect_scale_bar
from app.pipeline.segment import leaf_mask


def _make_image_with_scalebar(bar_width_px: int) -> np.ndarray:
    """A mostly-white image with a thin black rectangle in the bottom-right."""
    image = np.full((900, 1200, 3), 240, dtype=np.uint8)  # near-white
    # Put the bar comfortably inside the ROI that detect_scale_bar searches
    y = 800
    x = 1000
    cv2.rectangle(image, (x, y), (x + bar_width_px, y + 4), (20, 20, 20), thickness=-1)
    return image


def test_detect_scale_bar_recovers_known_bar_length() -> None:
    # cv2.rectangle draws inclusively, so a bar from x to x+100 is 101 px wide.
    image = _make_image_with_scalebar(bar_width_px=100)
    result = detect_scale_bar(image, reference_um=101.0)
    assert result.bar_px_length == pytest.approx(101, abs=1)
    assert result.um_per_px == pytest.approx(1.0, rel=0.02)


def test_detect_scale_bar_rejects_invalid_reference() -> None:
    image = _make_image_with_scalebar(bar_width_px=60)
    with pytest.raises(ValueError):
        detect_scale_bar(image, reference_um=-1.0)


def test_detect_scale_bar_raises_when_no_bar() -> None:
    blank = np.full((900, 1200, 3), 240, dtype=np.uint8)
    with pytest.raises(ValueError):
        detect_scale_bar(blank, reference_um=100.0)


def test_measure_from_rectangular_mask() -> None:
    mask = np.zeros((200, 400), dtype=np.uint8)
    # 50 px tall by 300 px wide rectangle
    mask[80:130, 50:350] = 255
    um_per_px = 2.0
    result = measure_from_mask(mask, um_per_px)

    assert result.valid_columns == 300
    # Every column has thickness = 50 px = 100 µm
    assert result.leaf_mean_thickness_um == pytest.approx(100.0)
    assert result.leaf_min_thickness_um == pytest.approx(100.0)
    assert result.leaf_max_thickness_um == pytest.approx(100.0)
    # area = 50 * 300 * 4 = 60000 µm²
    assert result.leaf_area_um2 == pytest.approx(60000.0)


def test_measure_empty_mask() -> None:
    mask = np.zeros((50, 50), dtype=np.uint8)
    result = measure_from_mask(mask, 1.0)
    assert result.valid_columns == 0
    assert result.leaf_area_um2 == 0.0
    assert result.thickness_profile_um == []


def test_leaf_mask_picks_saturated_blob() -> None:
    # Grey background (low saturation) with a saturated blue ellipse
    image = np.full((300, 500, 3), 210, dtype=np.uint8)  # BGR grey
    cv2.ellipse(image, (250, 150), (160, 60), 0, 0, 360, (180, 60, 60), thickness=-1)

    mask = leaf_mask(image)
    nonzero = (mask > 0).sum()
    # The ellipse covers π·160·60 ≈ 30,159 px; after open/close we should
    # be well above 10k even with some shrinkage.
    assert nonzero > 10_000
    # And the mask should be confined to the central band
    ys, xs = np.where(mask > 0)
    assert 40 < ys.min() < 260
    assert 40 < xs.min() < 460
