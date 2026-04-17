"""Scale-bar detection for plant microscopy images.

The user supplies the known length of the embedded scale bar (e.g. 100 µm);
we locate the widest dark horizontal segment in the bottom-right corner and
compute µm/px from its pixel width.  OCR'ing the label is deferred to a
later PR — the value is rarely ambiguous per microscope setup, and asking
once keeps the first-cut pipeline robust.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class ScaleResult:
    um_per_px: float
    bar_px_length: int
    bbox_xywh: tuple[int, int, int, int]  # absolute coords in the input image


def detect_scale_bar(
    image_bgr: np.ndarray,
    reference_um: float,
    roi_fraction: tuple[float, float] = (0.70, 0.85),
) -> ScaleResult:
    """Find the scale bar in an image and convert it into µm/px.

    Parameters
    ----------
    image_bgr
        BGR image (as returned by `cv2.imread`).
    reference_um
        Physical length represented by the scale bar, in micrometres.
    roi_fraction
        (x_fraction, y_fraction).  The search region is the rectangle from
        (x_fraction * W, y_fraction * H) to the bottom-right corner.

    Raises
    ------
    ValueError
        If no plausible bar-shaped component is found inside the ROI.
    """
    if reference_um <= 0:
        raise ValueError("reference_um must be positive")
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr must be an HxWx3 BGR array")

    h, w = image_bgr.shape[:2]
    x_frac, y_frac = roi_fraction
    x0 = int(w * x_frac)
    y0 = int(h * y_frac)
    roi = image_bgr[y0:, x0:]
    if roi.size == 0:
        raise ValueError("scale-bar ROI is empty; check roi_fraction")

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # dark-on-light → invert so the bar comes out as a white blob
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    # horizontal close bridges any anti-aliasing gap inside the bar
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 1))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    num, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    best_width = 0
    best: tuple[int, int, int, int] | None = None
    for i in range(1, num):  # skip background
        x, y, bw, bh, area = stats[i]
        if bh == 0:
            continue
        aspect = bw / bh
        # a scale bar is wide, thin, and carries a minimum of ink
        if bw > best_width and aspect >= 5 and area >= 20 and bw >= 10:
            best_width = int(bw)
            best = (int(x0 + x), int(y0 + y), int(bw), int(bh))

    if best is None:
        raise ValueError("scale bar not detected in ROI")

    return ScaleResult(
        um_per_px=float(reference_um) / best_width,
        bar_px_length=best_width,
        bbox_xywh=best,
    )
