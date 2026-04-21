"""Synthetic-image tests for CO2 morphometrics.

Each case builds an image + SegFormer polygon blob + Cellpose polygon
blob with known ground truth so the pipeline's arithmetic (S_mes/S,
f_ias, T_cw, chloroplast detection) can be validated without real data.

Coordinates are in the original-image grid (same convention as the
live pipelines).
"""

from __future__ import annotations

import json
import math
from typing import Any

import cv2
import numpy as np
import pytest

from app.pipeline.morphometrics_co2 import (
    MESOPHYLL_CLASSES,
    compute_co2_morphometrics,
)


def _square_polygon(cx: int, cy: int, half: int) -> list[list[float]]:
    return [
        [float(cx - half), float(cy - half)],
        [float(cx + half), float(cy - half)],
        [float(cx + half), float(cy + half)],
        [float(cx - half), float(cy + half)],
    ]


def _mesophyll_polygon_blob(
    x0: int, y0: int, x1: int, y1: int, class_key: str = "palisade"
) -> dict[str, Any]:
    return {
        "class_key": class_key,
        "polygon": [
            [float(x0), float(y0)],
            [float(x1), float(y0)],
            [float(x1), float(y1)],
            [float(x0), float(y1)],
        ],
        "area_px": (x1 - x0) * (y1 - y0),
        "holes": [],
    }


def _blank_image(height: int, width: int, colour: tuple[int, int, int] = (200, 200, 200)) -> np.ndarray:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:, :] = colour
    return img


def _segformer_blob(
    polygons: list[dict[str, Any]], h: int, w: int
) -> dict[str, Any]:
    return {
        "polygons": polygons,
        "image_shape": {"height_px": h, "width_px": w},
        "classes": list(MESOPHYLL_CLASSES),
        "coverage": [],
        "downsample_factor": 1.0,
    }


def _cellpose_blob(cells: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cells": cells,
        "cell_count": len(cells),
        "downsample_factor": 1.0,
        "mean_area_px": 0.0,
        "median_area_px": 0.0,
        "model": "cyto3",
    }


def test_empty_mesophyll_yields_null_scalars() -> None:
    """With no palisade/spongy polygons, S_mes/S, f_ias, T_cw all null."""
    h, w = 200, 400
    image = _blank_image(h, w)
    seg = _segformer_blob([], h, w)
    cp = _cellpose_blob([])
    res = compute_co2_morphometrics(image, seg, cp, um_per_px=1.0, max_side_px=400)
    d = res.to_dict()
    assert d["s_mes_s"] is None
    assert d["s_c_s"] is None
    assert d["f_ias"] is None
    assert d["mesophyll"]["area_px"] == 0
    assert d["cell_wall"]["gap_pixel_count"] == 0
    assert "no palisade/spongy polygons" in " ".join(d["notes"])
    # Payload must round-trip through strict JSON (no NaN/Inf sentinels).
    json.dumps(d, allow_nan=False)


def test_s_mes_s_matches_ias_exposed_boundary_length() -> None:
    """One 40x40 cell sitting in the middle of a 200x40 mesophyll
    strip.  All four cell sides face the IAS (mesophyll surrounds
    the cell on every edge), so the IAS-exposed boundary length ≈
    cell perimeter = 4 * 40 = 160 px.  Leaf section length = 200 px
    (major axis of the mesophyll rectangle).  Expected S_mes/S ≈
    160 / 200 = 0.8.  Allow a wider tolerance than the old raw-
    perimeter test because the raster boundary length from
    MORPH_GRADIENT has edge-discretisation jitter.
    """
    h, w = 100, 400
    image = _blank_image(h, w)
    # Mesophyll strip: x=[100, 300], y=[30, 70] → major axis = 200.
    seg = _segformer_blob([_mesophyll_polygon_blob(100, 30, 300, 70)], h, w)
    cell = {
        "polygon": _square_polygon(200, 50, 20),  # 40x40 centered at (200, 50)
        "centroid": [200.0, 50.0],
        "area_px": 1600,
    }
    cp = _cellpose_blob([cell])
    res = compute_co2_morphometrics(image, seg, cp, um_per_px=1.0, max_side_px=400)
    assert res.s_mes_s is not None
    assert res.s_mes_s == pytest.approx(0.8, rel=0.2)
    # f_ias = 1 - 1600 / (200*40) = 1 - 0.2 = 0.8
    assert res.f_ias is not None
    assert res.f_ias == pytest.approx(0.8, rel=0.05)
    assert res.mesophyll_cells.count == 1


def test_f_ias_is_zero_when_cells_fill_mesophyll() -> None:
    """Single large cell covering the entire mesophyll rectangle → f_ias ≈ 0."""
    h, w = 100, 200
    image = _blank_image(h, w)
    seg = _segformer_blob([_mesophyll_polygon_blob(50, 25, 150, 75)], h, w)
    cell = {
        "polygon": [
            [50.0, 25.0],
            [150.0, 25.0],
            [150.0, 75.0],
            [50.0, 75.0],
        ],
        "centroid": [100.0, 50.0],
        "area_px": 5000,
    }
    cp = _cellpose_blob([cell])
    res = compute_co2_morphometrics(image, seg, cp, um_per_px=1.0, max_side_px=400)
    assert res.f_ias is not None
    assert res.f_ias == pytest.approx(0.0, abs=0.05)


def test_cells_outside_mesophyll_are_excluded() -> None:
    """A cell whose centroid falls outside the mesophyll mask must not
    contribute to S_mes/S or the cell aggregate counts."""
    h, w = 100, 400
    image = _blank_image(h, w)
    seg = _segformer_blob([_mesophyll_polygon_blob(100, 30, 300, 70)], h, w)
    inside = {
        "polygon": _square_polygon(200, 50, 15),
        "centroid": [200.0, 50.0],
        "area_px": 900,
    }
    outside = {
        "polygon": _square_polygon(20, 80, 10),
        "centroid": [20.0, 80.0],
        "area_px": 400,
    }
    cp = _cellpose_blob([inside, outside])
    res = compute_co2_morphometrics(image, seg, cp, um_per_px=1.0, max_side_px=400)
    assert res.mesophyll_cells.count == 1


def test_um_per_px_scales_linear_quantities() -> None:
    """Perimeters scale with um_per_px; S_mes/S is dimensionless and invariant."""
    h, w = 100, 400
    image = _blank_image(h, w)
    seg = _segformer_blob([_mesophyll_polygon_blob(100, 30, 300, 70)], h, w)
    cell = {
        "polygon": _square_polygon(200, 50, 20),
        "centroid": [200.0, 50.0],
        "area_px": 1600,
    }
    cp = _cellpose_blob([cell])

    res_px = compute_co2_morphometrics(image, seg, cp, um_per_px=None, max_side_px=400)
    res_um = compute_co2_morphometrics(image, seg, cp, um_per_px=2.0, max_side_px=400)
    # S_mes/S is the same in both runs (scale cancels).
    assert res_px.s_mes_s == pytest.approx(res_um.s_mes_s or 0.0, rel=1e-6)
    # µm metrics are None without a scale and set with one.
    assert res_px.mesophyll.area_um2 is None
    assert res_um.mesophyll.area_um2 is not None
    # 200 x 40 px² x 4 (µm/px)² = 32000 µm²
    assert res_um.mesophyll.area_um2 == pytest.approx(32000.0, rel=0.05)
    # Perimeter in µm: 160 px * 2 = 320 µm
    assert res_um.mesophyll_cells.perimeter_total_um is not None
    assert res_um.mesophyll_cells.perimeter_total_um == pytest.approx(320.0, rel=0.05)


def test_chloroplast_detection_finds_green_spots_inside_cells() -> None:
    """A cell painted beige with two bright-green blobs pressed against
    the cell wall (where real chloroplasts line up in mesophyll tissue).
    Expect chloroplast_count >= 2 via the LAB a* Otsu path, and S_c/S
    positive because the blobs sit adjacent to the IAS-facing cell
    wall — per the Evans / Tosens convention, S_c is chloroplast
    surface actually exposed to the gas phase, not interior blobs.
    """
    h, w = 200, 400
    # Beige background within mesophyll
    image = _blank_image(h, w, colour=(170, 195, 215))
    seg = _segformer_blob([_mesophyll_polygon_blob(100, 50, 300, 150)], h, w)
    # Paint a cell rectangle lighter, then place two dark-green blobs
    # near its boundary (~5 px from the edge) — simulating mesophyll
    # chloroplasts that press against the cell wall in vivo.
    cv2.rectangle(image, (150, 70), (250, 130), (180, 210, 225), thickness=-1)
    cv2.circle(image, (175, 75), 6, (10, 110, 10), thickness=-1)  # top-edge
    cv2.circle(image, (245, 110), 6, (10, 110, 10), thickness=-1)  # right-edge
    cell = {
        "polygon": [
            [150.0, 70.0],
            [250.0, 70.0],
            [250.0, 130.0],
            [150.0, 130.0],
        ],
        "centroid": [200.0, 100.0],
        "area_px": 6000,
    }
    cp = _cellpose_blob([cell])
    res = compute_co2_morphometrics(
        image, seg, cp, um_per_px=1.0, max_side_px=400, chloroplast_min_area_px=4
    )
    assert res.chloroplasts.count >= 2
    # Overlay render produces a non-empty PNG payload.
    assert len(res.chloroplast_overlay_png_base64) > 0
    # S_c/S should be positive when chloroplasts sit along the wall.
    assert res.s_c_s is not None
    assert res.s_c_s > 0


def test_interior_chloroplasts_do_not_count_toward_s_c_s() -> None:
    """A green blob well away from the cell wall must not be counted
    toward S_c/S — chloroplasts that don't face IAS can't exchange gas.
    The count is still positive (the detector saw them), but the
    IAS-adjacent boundary length stays zero so the ratio is null.
    """
    h, w = 200, 400
    image = _blank_image(h, w, colour=(170, 195, 215))
    seg = _segformer_blob([_mesophyll_polygon_blob(100, 50, 300, 150)], h, w)
    cv2.rectangle(image, (150, 70), (250, 130), (180, 210, 225), thickness=-1)
    # Center-of-cell only — 25+ px from every cell wall.
    cv2.circle(image, (200, 100), 5, (10, 110, 10), thickness=-1)
    cell = {
        "polygon": [
            [150.0, 70.0],
            [250.0, 70.0],
            [250.0, 130.0],
            [150.0, 130.0],
        ],
        "centroid": [200.0, 100.0],
        "area_px": 6000,
    }
    cp = _cellpose_blob([cell])
    res = compute_co2_morphometrics(
        image, seg, cp, um_per_px=1.0, max_side_px=400, chloroplast_min_area_px=4
    )
    assert res.chloroplasts.count >= 1
    assert res.s_c_s is None


def test_low_contrast_skips_chloroplast_detection() -> None:
    """Images where the LAB a* inside-cell range is tiny (flat stain)
    must NOT emit speculative chloroplast blobs — the detector's
    contrast guard kicks in."""
    h, w = 200, 400
    image = _blank_image(h, w, colour=(160, 160, 160))  # grey, flat a*
    seg = _segformer_blob([_mesophyll_polygon_blob(100, 50, 300, 150)], h, w)
    cell = {
        "polygon": [
            [150.0, 70.0],
            [250.0, 70.0],
            [250.0, 130.0],
            [150.0, 130.0],
        ],
        "centroid": [200.0, 100.0],
        "area_px": 6000,
    }
    cp = _cellpose_blob([cell])
    res = compute_co2_morphometrics(image, seg, cp, um_per_px=1.0, max_side_px=400)
    assert res.chloroplasts.count == 0
    assert res.s_c_s is None


def test_t_cw_is_positive_when_two_cells_sit_in_mesophyll_with_a_gap() -> None:
    """Two adjacent cells separated by a 10-pixel gap.  The mesophyll
    polygon hugs the cells tightly so the only gap region is the
    10-pixel strip between them.  Distance transform of that strip
    peaks at ~5 px at the midline → p95 ≈ 5 px → 10 µm with
    um_per_px=2.0."""
    h, w = 100, 400
    image = _blank_image(h, w)
    # Mesophyll hugs cells x=[130..220] so the only DT sample region is
    # the 10-px inter-cell gap.  Otherwise the large empty margin
    # dominates the p95 estimator.
    seg = _segformer_blob([_mesophyll_polygon_blob(130, 30, 220, 70)], h, w)
    cell_a = {
        "polygon": _square_polygon(150, 50, 20),
        "centroid": [150.0, 50.0],
        "area_px": 1600,
    }
    cell_b = {
        "polygon": _square_polygon(200, 50, 20),
        "centroid": [200.0, 50.0],
        "area_px": 1600,
    }
    cp = _cellpose_blob([cell_a, cell_b])
    res = compute_co2_morphometrics(image, seg, cp, um_per_px=2.0, max_side_px=400)
    assert res.cell_wall.gap_pixel_count > 0
    # p95 in original px ≈ 5 (half of a 10-px gap); µm conversion: ≈10 µm.
    assert res.cell_wall.t_cw_p95_um is not None
    assert res.cell_wall.t_cw_p95_um == pytest.approx(10.0, abs=4.0)


def test_result_round_trips_through_strict_json() -> None:
    """No NaN / Inf in the top-level dict — Starlette's JSON renderer
    rejects non-finite floats, and compare.py downstream needs to hand
    the blob back to the frontend unchanged."""
    h, w = 100, 400
    image = _blank_image(h, w)
    seg = _segformer_blob([_mesophyll_polygon_blob(100, 30, 300, 70)], h, w)
    cp = _cellpose_blob([])
    res = compute_co2_morphometrics(image, seg, cp, um_per_px=1.0, max_side_px=400)
    s = json.dumps(res.to_dict(), allow_nan=False)
    assert "NaN" not in s
    assert not math.isnan(float("nan") if False else 0.0)  # sanity
