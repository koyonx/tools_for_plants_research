"""Leaf-region segmentation via classical CV (HSV saturation + morphology).

A full SAM2 / deep-learning pipeline lands in PR #5; this module provides a
good-enough mask for the PR #3 thickness + area measurements.  Toluidine-blue
stained tissue carries far more saturation than the light-blue embedding
medium and the grey background, so an Otsu threshold on the S channel
separates them reliably on the sample images we have.
"""

from __future__ import annotations

import cv2
import numpy as np


def leaf_mask(image_bgr: np.ndarray) -> np.ndarray:
    """Return a uint8 binary mask (0 / 255) of the leaf tissue.

    The largest connected foreground component is kept; smaller debris and
    embedding-medium blobs are discarded.  If no foreground is detected the
    mask is all zeros.
    """
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr must be an HxWx3 BGR array")

    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    saturation = hsv[..., 1]
    # Otsu on saturation — leaf cells have clearly higher S than the
    # translucent embedding resin or the grey slide background.
    _, binary = cv2.threshold(
        saturation, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=2)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=3)

    num, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if num < 2:
        return np.zeros_like(binary)
    # skip label 0 (background)
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest_label = 1 + int(np.argmax(areas))
    mask = np.where(labels == largest_label, 255, 0).astype(np.uint8)
    return mask
