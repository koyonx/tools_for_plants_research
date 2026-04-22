"""Synthetic-image tests for CO2 morphometrics.

Each case builds an image + SegFormer polygon blob + Cellpose polygon
blob with known ground truth so the pipeline's arithmetic (S_mes/S,
f_ias, T_cw, chloroplast detection) can be validated without real data.

Coordinates are in the original-image grid (same convention as the
live pipelines).
"""

from __future__ import annotations

import json
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
    """One 40x40 cell FULLY INTERIOR to an 80x200 mesophyll rectangle.
    Every one of the cell's four sides is surrounded by IAS, so the
    IAS-exposed boundary length ≈ cell perimeter = 4 * 40 = 160 px.
    Leaf section length = 200 px (major axis of the mesophyll
    rectangle).  Expected S_mes/S ≈ 160 / 200 = 0.8.

    This is the round-2 replacement for the original test: the
    previous geometry had the cell touching the top/bottom edges of
    the mesophyll strip, so only left/right sides faced IAS (80 px
    total), but a two-sided MORPH_GRADIENT bug in the boundary
    extraction happened to double the count back to 160 and pass the
    assertion.  Interior geometry + tighter rel tolerance now
    actually exercises the one-sided adjacency code.
    """
    h, w = 200, 400
    image = _blank_image(h, w)
    # Mesophyll rectangle: x=[100, 300], y=[60, 140] → 200x80.  Cell
    # centered at (200, 100) with half=20 → 40x40 at [180..220] x
    # [80..120] — no cell pixel touches a mesophyll edge.
    seg = _segformer_blob([_mesophyll_polygon_blob(100, 60, 300, 140)], h, w)
    cell = {
        "polygon": _square_polygon(200, 100, 20),
        "centroid": [200.0, 100.0],
        "area_px": 1600,
    }
    cp = _cellpose_blob([cell])
    res = compute_co2_morphometrics(image, seg, cp, um_per_px=1.0, max_side_px=400)
    assert res.s_mes_s is not None
    assert res.s_mes_s == pytest.approx(0.8, rel=0.1)
    # f_ias = 1 - 1600 / (200*80) = 1 - 0.1 = 0.9
    assert res.f_ias is not None
    assert res.f_ias == pytest.approx(0.9, rel=0.05)
    assert res.mesophyll_cells.count == 1


def test_s_c_s_regression_pin_on_known_wall_adjacent_chloroplast() -> None:
    """Regression pin for S_c/S magnitude on a fully-specified geometry.

    One 40x40 cell interior to an 80x200 mesophyll rectangle, with a
    single 12x4 rectangular chloroplast pressed along the top-inner
    wall of the cell (y ~= 81..85).  Expected S_c/S:

      exposed boundary top-edge of cell ≈ 40 px.
      chloroplast-dilated reach = 2 orig-px ⇒ ~12 px of top edge
        is chloroplast-lined (one chloroplast blob, ~12 px wide).
      section_length = 200 px (major axis).
      S_c/S ≈ 12 / 200 = 0.06.

    Tolerated at rel=0.3 — the rasterised blob's actual pixel extent
    after Otsu + morph-open has a couple px of jitter.  The key
    property this test protects against is silent 2x shifts in the
    S_c/S magnitude from future refactors of the boundary or reach
    definitions (rounds 1-3 caught three such shifts).
    """
    h, w = 200, 400
    image = _blank_image(h, w, colour=(170, 195, 215))
    seg = _segformer_blob([_mesophyll_polygon_blob(100, 60, 300, 140)], h, w)
    # Lighter cell rectangle, 40x40 at [180..220] x [80..120]
    cv2.rectangle(image, (180, 80), (220, 120), (180, 210, 225), thickness=-1)
    # Chloroplast strip along top wall: y=[82..86], x=[184..196]
    cv2.rectangle(image, (184, 82), (196, 86), (10, 110, 10), thickness=-1)
    cell = {
        "polygon": _square_polygon(200, 100, 20),
        "centroid": [200.0, 100.0],
        "area_px": 1600,
    }
    cp = _cellpose_blob([cell])
    res = compute_co2_morphometrics(
        image, seg, cp, um_per_px=1.0, max_side_px=400, chloroplast_min_area_px=4
    )
    assert res.chloroplasts.count >= 1
    assert res.s_c_s is not None
    assert res.s_c_s == pytest.approx(0.06, rel=0.3)


def test_t_cw_null_when_cells_fill_mesophyll_exactly() -> None:
    """When the Cellpose cell union covers the entire mesophyll
    polygon, there are no IAS gap pixels for the distance transform
    to sample.  T_cw must be None with a note — a zero would be
    picked up by the compare dashboard as a real datapoint.
    """
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
    assert res.cell_wall.t_cw_median_um is None
    assert res.cell_wall.t_cw_p95_um is None
    assert res.cell_wall.t_cw_median_px is None
    assert res.cell_wall.gap_pixel_count == 0
    assert any("cells fill the mesophyll polygon" in n for n in res.notes)


def test_shared_wall_between_touching_cells_is_not_counted() -> None:
    """Two cells touching along a shared wall + each also facing IAS
    on the outer sides.  The IAS-exposed boundary must NOT include
    the shared wall (that's 2 * 40 = 80 px of wall that cannot
    exchange gas), so L_mes,IAS ≈ 2 * (4*40) - 2*40 = 240 px, not
    320.  Asserts the one-sided adjacency form excludes shared walls
    by construction.
    """
    h, w = 200, 400
    image = _blank_image(h, w)
    # Mesophyll rectangle 200x120; two 40x40 cells sharing the wall
    # at x=200, centers at (180, 100) and (220, 100).
    seg = _segformer_blob([_mesophyll_polygon_blob(100, 40, 300, 160)], h, w)
    cell_a = {
        "polygon": _square_polygon(180, 100, 20),  # x=[160..200]
        "centroid": [180.0, 100.0],
        "area_px": 1600,
    }
    cell_b = {
        "polygon": _square_polygon(220, 100, 20),  # x=[200..240]
        "centroid": [220.0, 100.0],
        "area_px": 1600,
    }
    cp = _cellpose_blob([cell_a, cell_b])
    res = compute_co2_morphometrics(image, seg, cp, um_per_px=1.0, max_side_px=400)
    assert res.s_mes_s is not None
    # Total IAS-exposed perimeter = 2*160 px (both cells' perimeters)
    # minus 2*40 px (the shared wall counted from each side).  So
    # expected = 240 px over section length 200 px = 1.2.
    assert res.s_mes_s == pytest.approx(1.2, rel=0.15)


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
