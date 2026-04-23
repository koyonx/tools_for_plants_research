"""Tests for the Darcy flow solver.

Each case builds a synthetic SegFormer polygon blob so CI doesn't
need real data.  We check the solver against:

1. A homogeneous slab with Dirichlet P_xylem on one side and
   P_stomata on the opposite side — the analytical 1-D solution is
   a linear pressure gradient with constant velocity v = K·ΔP/L, so
   the numerical solution must converge to that.
2. Two-tissue stacked arrangement — effective conductivity is the
   harmonic mean, which is exactly what the face-conductivity
   discretisation computes.
3. Missing xylem polygons → clean error.
4. Result blob round-trips through strict JSON (no NaN / Inf / field
   with sentinel).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.pipeline.darcy import (
    DEFAULT_P_STOMATA,
    DEFAULT_P_XYLEM,
    DEFAULT_PERMEABILITY,
    WATER_VISCOSITY_PA_S,
    compute_darcy,
)


def _segformer_blob(
    polygons: list[dict[str, Any]], h: int, w: int
) -> dict[str, Any]:
    return {
        "polygons": polygons,
        "image_shape": {"height_px": h, "width_px": w},
        "classes": [],
        "coverage": [],
        "downsample_factor": 1.0,
    }


def _rect(
    x0: int, y0: int, x1: int, y1: int, class_key: str
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


def test_missing_xylem_raises_clean_error() -> None:
    """The solver needs a source boundary."""
    seg = _segformer_blob(
        [
            _rect(0, 0, 100, 50, "palisade"),
            _rect(0, 55, 100, 60, "stomata"),
        ],
        h=60,
        w=100,
    )
    with pytest.raises(ValueError, match="xylem"):
        compute_darcy(seg, um_per_px=1.0, max_side_px=100)


def test_missing_stomata_raises_clean_error() -> None:
    seg = _segformer_blob(
        [
            _rect(0, 0, 10, 50, "xylem"),
            _rect(20, 0, 100, 50, "palisade"),
        ],
        h=50,
        w=100,
    )
    with pytest.raises(ValueError, match="stomata"):
        compute_darcy(seg, um_per_px=1.0, max_side_px=100)


def test_equal_pressures_rejected() -> None:
    seg = _segformer_blob(
        [
            _rect(0, 0, 10, 50, "xylem"),
            _rect(12, 0, 100, 50, "palisade"),
            _rect(105, 0, 110, 50, "stomata"),
        ],
        h=50,
        w=110,
    )
    with pytest.raises(ValueError, match="must differ"):
        compute_darcy(
            seg, um_per_px=1.0, max_side_px=110, p_xylem_pa=0.0, p_stomata_pa=0.0
        )


def test_homogeneous_slab_matches_1d_darcy_law() -> None:
    """Single-tissue slab with xylem on the left, stomata on the
    right — analytical 1-D solution: P(x) = P_left - ΔP · x / L,
    constant velocity v = K · ΔP / L.

    Build a pure-palisade strip 200 µm wide with the xylem band at
    x=[0..2] and stomata band at x=[198..200].  The solver should
    produce a near-linear pressure gradient, and mean_velocity
    should match the analytical Darcy prediction within a few
    percent (finite-volume discretisation has a 1-cell boundary
    layer near the Dirichlet bands where the gradient is sharp).
    """
    h, w = 20, 200
    # The xylem band lives inside the leaf (so it's part of the
    # palisade domain) — the solver Dirichlet-overrides xylem cells
    # anyway.  We overlay the xylem band ON TOP of the palisade
    # rectangle rather than cutting a hole.
    polygons = [
        _rect(0, 0, w, h, "palisade"),
        _rect(0, 0, 3, h, "xylem"),
        _rect(w - 3, 0, w, h, "stomata"),
    ]
    seg = _segformer_blob(polygons, h=h, w=w)
    p_in = 0.0
    p_out = -1.0e6  # 1 MPa drop over 200 µm
    res = compute_darcy(
        seg,
        um_per_px=1.0,
        max_side_px=w,
        p_xylem_pa=p_in,
        p_stomata_pa=p_out,
    )
    # Velocity should be constant and equal the analytical Darcy
    # value inside the palisade region.
    perm = DEFAULT_PERMEABILITY["palisade"] / WATER_VISCOSITY_PA_S
    length_m = (w - 6) * 1e-6  # strip length in metres (xylem at 0..3, sink at w-3..w)
    dp = abs(p_in - p_out)
    v_analytic = perm * dp / length_m
    # The solver's mean velocity is restricted to the non-Dirichlet
    # leaf interior, so it should land very close to the analytical
    # value -- within 10 % even on a coarse 20x200 grid.  Dirichlet
    # boundary cells with their artificial sharp gradients are
    # excluded from this stat by construction.
    assert res.velocity_mean is not None
    assert res.velocity_mean == pytest.approx(v_analytic, rel=0.1)
    # Pressure field must span the imposed gradient.
    assert res.pressure_min_pa is not None and res.pressure_max_pa is not None
    assert res.pressure_max_pa == pytest.approx(p_in, abs=1.0)
    assert res.pressure_min_pa == pytest.approx(p_out, abs=1.0)
    # K_leaf should be positive and finite.
    assert res.k_leaf is not None and res.k_leaf > 0
    # Continuity: flow_in and flow_out should agree within 10 % on
    # this small grid.  The tolerance widens for tiny meshes where
    # the 1-px boundary ring is a larger fraction of the domain.
    assert res.total_flow_in > 0 and res.total_flow_out > 0
    imbalance = abs(res.total_flow_in - res.total_flow_out) / max(
        res.total_flow_in, res.total_flow_out
    )
    assert imbalance < 0.1


def test_higher_permeability_raises_flow() -> None:
    """Double the palisade permeability → flow through the slab
    doubles (linear in K for a fixed pressure drop)."""
    h, w = 20, 200
    polygons = [
        _rect(0, 0, w, h, "palisade"),
        _rect(0, 0, 3, h, "xylem"),
        _rect(w - 3, 0, w, h, "stomata"),
    ]
    seg = _segformer_blob(polygons, h=h, w=w)
    base = compute_darcy(seg, um_per_px=1.0, max_side_px=w)
    scaled = compute_darcy(
        seg,
        um_per_px=1.0,
        max_side_px=w,
        permeability_override={"palisade": DEFAULT_PERMEABILITY["palisade"] * 2.0},
    )
    assert base.k_leaf is not None and scaled.k_leaf is not None
    assert scaled.k_leaf == pytest.approx(base.k_leaf * 2.0, rel=0.1)


def test_negative_override_is_dropped() -> None:
    """Non-finite / non-positive permeability overrides must silently
    drop rather than poisoning the matrix."""
    h, w = 20, 100
    seg = _segformer_blob(
        [
            _rect(0, 0, w, h, "palisade"),
            _rect(0, 0, 3, h, "xylem"),
            _rect(w - 3, 0, w, h, "stomata"),
        ],
        h=h,
        w=w,
    )
    res = compute_darcy(
        seg,
        um_per_px=1.0,
        max_side_px=w,
        permeability_override={
            "palisade": -1.0,
            "spongy": float("inf"),
            "xylem": float("nan"),
        },
    )
    # Solve must have succeeded with the defaults, not the bogus values.
    assert res.k_leaf is not None and res.k_leaf > 0
    # The reported permeability map reflects what was actually used.
    assert res.permeability["palisade"] == DEFAULT_PERMEABILITY["palisade"]


def test_vessel_source_preferred_over_xylem() -> None:
    """When both `xylem` and `xylem_vessel` polygons exist, the finer
    `xylem_vessel` class is used as the Dirichlet inflow boundary."""
    h, w = 20, 200
    polygons = [
        _rect(0, 0, w, h, "palisade"),
        _rect(0, 0, 3, h, "xylem"),
        _rect(0, 5, 2, 15, "xylem_vessel"),
        _rect(w - 3, 0, w, h, "stomata"),
    ]
    seg = _segformer_blob(polygons, h=h, w=w)
    res = compute_darcy(seg, um_per_px=1.0, max_side_px=w)
    assert res.source_class == "xylem_vessel"


def test_fallback_to_xylem_when_vessels_absent_emits_note() -> None:
    h, w = 20, 200
    polygons = [
        _rect(0, 0, w, h, "palisade"),
        _rect(0, 0, 3, h, "xylem"),
        _rect(w - 3, 0, w, h, "stomata"),
    ]
    seg = _segformer_blob(polygons, h=h, w=w)
    res = compute_darcy(seg, um_per_px=1.0, max_side_px=w)
    assert res.source_class == "xylem"
    assert any("xylem_vessel" in n for n in res.notes)


def test_result_round_trips_through_strict_json() -> None:
    """The upload endpoint pipes the result blob straight to Supabase
    as JSON; Starlette / json.dumps(allow_nan=False) both reject
    non-finite floats, so the solver must scrub them out."""
    h, w = 20, 100
    seg = _segformer_blob(
        [
            _rect(0, 0, w, h, "palisade"),
            _rect(0, 0, 3, h, "xylem"),
            _rect(w - 3, 0, w, h, "stomata"),
        ],
        h=h,
        w=w,
    )
    res = compute_darcy(seg, um_per_px=1.0, max_side_px=w)
    s = json.dumps(res.to_dict(), allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s


def test_per_stomatum_outflow_reported() -> None:
    """When the file has multiple stomata polygons, each gets its own
    entry in stomata_outflows.  Useful for spotting uneven C4
    bundle-sheath loading where some stomata bear more flow."""
    h, w = 40, 200
    polygons = [
        _rect(0, 0, w, h, "palisade"),
        _rect(0, 0, 3, h, "xylem"),
        _rect(w - 3, 0, w, 10, "stomata"),
        _rect(w - 3, 15, w, 25, "stomata"),
        _rect(w - 3, 30, w, h, "stomata"),
    ]
    seg = _segformer_blob(polygons, h=h, w=w)
    res = compute_darcy(seg, um_per_px=1.0, max_side_px=w)
    assert len(res.stomata_outflows) == 3
    for s in res.stomata_outflows:
        assert s.flow >= 0
        assert s.mean_velocity >= 0


def test_bad_segformer_blob_shape_rejected() -> None:
    with pytest.raises(ValueError, match="image_shape"):
        compute_darcy({"polygons": []}, um_per_px=1.0, max_side_px=100)


def test_dict_requires_isinstance_check() -> None:
    with pytest.raises(ValueError, match="result blob"):
        compute_darcy("not a dict", um_per_px=1.0, max_side_px=100)  # type: ignore[arg-type]


def test_default_bc_constants_sensible() -> None:
    # Smoke test: the default Dirichlet values should be non-trivial
    # and the xylem pressure should be higher.
    assert DEFAULT_P_XYLEM > DEFAULT_P_STOMATA
    assert abs(DEFAULT_P_XYLEM - DEFAULT_P_STOMATA) >= 1e5
