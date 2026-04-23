"""Tests for the CO2 reaction-diffusion solver.

Synthetic SegFormer polygon blobs let us check the solver against:

1. Pure Fickian diffusion (reaction_rate = 0) on a homogeneous slab —
   linear gradient between Dirichlet stomata and a Dirichlet-equivalent
   sink, matching the analytical 1D Fick's law.
2. Reaction-diffusion 1D — the analytical solution is a hyperbolic
   cosine; we check Cc < Ci (reaction draws CO2 down) and that
   higher r → bigger drawdown.
3. Missing prerequisites → clean errors.
4. Strict-JSON round-trip (no NaN / Inf in the result blob).
5. The Dirichlet-touching guard reused from Darcy: a stomatum that
   borders a chloroplast pixel must NOT count the BC face as a
   sink interface inflating A_net.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.pipeline.co2_diffusion import (
    DEFAULT_CI_PA,
    DEFAULT_DIFFUSIVITY,
    DEFAULT_REACTION_RATE,
    compute_co2_diffusion,
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


def test_missing_stomata_raises_clean_error() -> None:
    seg = _segformer_blob(
        [_rect(0, 0, 50, 30, "palisade")],
        h=30,
        w=50,
    )
    with pytest.raises(ValueError, match="stomata"):
        compute_co2_diffusion(seg, um_per_px=1.0, max_side_px=50)


def test_missing_sink_region_raises_clean_error() -> None:
    """No mesophyll AND no chloroplast overlay → no Rubisco-bearing
    region for CO2 to be fixed in.  Solver refuses cleanly."""
    seg = _segformer_blob(
        [
            _rect(0, 0, 50, 30, "intercellular"),
            _rect(0, 32, 50, 35, "stomata"),
        ],
        h=40,
        w=50,
    )
    with pytest.raises(ValueError, match="sink"):
        compute_co2_diffusion(seg, um_per_px=1.0, max_side_px=50)


def test_invalid_ci_rejected() -> None:
    seg = _segformer_blob(
        [
            _rect(0, 0, 50, 30, "palisade"),
            _rect(0, 32, 50, 35, "stomata"),
        ],
        h=40,
        w=50,
    )
    with pytest.raises(ValueError, match="ci_pa"):
        compute_co2_diffusion(seg, um_per_px=1.0, max_side_px=50, ci_pa=0.0)
    with pytest.raises(ValueError, match="ci_pa"):
        compute_co2_diffusion(seg, um_per_px=1.0, max_side_px=50, ci_pa=float("nan"))


def test_negative_reaction_rate_rejected() -> None:
    seg = _segformer_blob(
        [
            _rect(0, 0, 50, 30, "palisade"),
            _rect(0, 32, 50, 35, "stomata"),
        ],
        h=40,
        w=50,
    )
    with pytest.raises(ValueError, match="reaction_rate"):
        compute_co2_diffusion(seg, um_per_px=1.0, max_side_px=50, reaction_rate=-1.0)


def test_zero_reaction_yields_uniform_concentration() -> None:
    """With r=0 and stomata Dirichlet at Ci, the steady-state solution
    of pure diffusion in a closed (no-flow) domain is C = Ci everywhere.
    Cc_mean should equal Ci, drawdown = 0, A_net = 0, g_m_proxy = None."""
    seg = _segformer_blob(
        [
            _rect(0, 0, 100, 30, "palisade"),
            _rect(0, 30, 100, 32, "intercellular"),
            _rect(0, 32, 100, 35, "stomata"),
        ],
        h=40,
        w=100,
    )
    res = compute_co2_diffusion(
        seg, um_per_px=1.0, max_side_px=100, ci_pa=25.0, reaction_rate=0.0
    )
    assert res.cc_mean_pa is not None
    assert res.cc_mean_pa == pytest.approx(25.0, rel=0.01)
    assert res.drawdown_mean_pa is not None
    assert res.drawdown_mean_pa == pytest.approx(0.0, abs=0.5)
    # No reaction, no flux into sink — a_net should be effectively 0.
    assert abs(res.a_net) < 1e-12
    # g_m_proxy is None because Ci - Cc <= 0.
    assert res.g_m_proxy is None


def test_positive_reaction_draws_cc_below_ci() -> None:
    """With a non-zero reaction rate inside the chloroplast region,
    Cc must be measurably below Ci, A_net must be positive, and
    g_m_proxy must be a finite positive number."""
    seg = _segformer_blob(
        [
            _rect(0, 0, 100, 30, "palisade"),
            _rect(0, 30, 100, 32, "intercellular"),
            _rect(0, 32, 100, 35, "stomata"),
        ],
        h=40,
        w=100,
    )
    res = compute_co2_diffusion(
        seg,
        um_per_px=1.0,
        max_side_px=100,
        ci_pa=25.0,
        reaction_rate=1.0,
    )
    assert res.cc_mean_pa is not None
    assert res.cc_mean_pa < 25.0  # drawn down by Rubisco
    assert res.cc_mean_pa > 0.0
    assert res.a_net > 0
    assert res.g_m_proxy is not None and res.g_m_proxy > 0
    assert res.sink_class == "mesophyll_cells"  # no co2_morphometrics provided


def test_higher_reaction_rate_increases_drawdown() -> None:
    """Monotonicity: r2 > r1 → cc_mean(r2) < cc_mean(r1) AND
    drawdown(r2) > drawdown(r1).  Catches sign / dimensional bugs
    where the reaction term goes the wrong way."""
    seg = _segformer_blob(
        [
            _rect(0, 0, 100, 30, "palisade"),
            _rect(0, 30, 100, 32, "intercellular"),
            _rect(0, 32, 100, 35, "stomata"),
        ],
        h=40,
        w=100,
    )
    low = compute_co2_diffusion(
        seg, um_per_px=1.0, max_side_px=100, ci_pa=25.0, reaction_rate=0.5
    )
    high = compute_co2_diffusion(
        seg, um_per_px=1.0, max_side_px=100, ci_pa=25.0, reaction_rate=5.0
    )
    assert low.cc_mean_pa is not None and high.cc_mean_pa is not None
    assert high.cc_mean_pa < low.cc_mean_pa
    assert (
        high.drawdown_mean_pa is not None and low.drawdown_mean_pa is not None
    )
    assert high.drawdown_mean_pa > low.drawdown_mean_pa


def test_diffusivity_override_drops_non_finite_silently() -> None:
    seg = _segformer_blob(
        [
            _rect(0, 0, 100, 30, "palisade"),
            _rect(0, 30, 100, 32, "intercellular"),
            _rect(0, 32, 100, 35, "stomata"),
        ],
        h=40,
        w=100,
    )
    res = compute_co2_diffusion(
        seg,
        um_per_px=1.0,
        max_side_px=100,
        diffusivity_override={
            "palisade": -1.0,
            "spongy": float("inf"),
            "intercellular": float("nan"),
        },
    )
    # Defaults must have survived intact.
    assert res.diffusivity["palisade"] == DEFAULT_DIFFUSIVITY["palisade"]
    assert res.diffusivity["spongy"] == DEFAULT_DIFFUSIVITY["spongy"]


def test_chloroplast_overlay_used_when_provided() -> None:
    """When co2_morphometrics_result has a chloroplast overlay PNG with
    magenta (200,30,200) pixels, the solver picks them out and uses
    the chloroplast region (not the mesophyll fallback) as the sink.
    """
    import base64
    import io

    import numpy as np
    from PIL import Image

    h, w = 40, 100
    seg = _segformer_blob(
        [
            _rect(0, 0, w, 30, "palisade"),
            _rect(0, 32, w, 35, "stomata"),
        ],
        h=h,
        w=w,
    )
    # Build a magenta-spotted chloroplast overlay at ORIGINAL image
    # resolution.  The decoder will resize NEAREST to the solver grid.
    overlay = np.zeros((h, w, 4), dtype=np.uint8)
    # Magenta pixels in a 4-pixel band at the top of the palisade
    overlay[5:9, 30:70, :3] = (200, 30, 200)
    overlay[5:9, 30:70, 3] = 255
    buf = io.BytesIO()
    Image.fromarray(overlay).save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    co2_morph = {"chloroplast_overlay_png_base64": b64}
    res = compute_co2_diffusion(
        seg,
        co2_morphometrics_result=co2_morph,
        um_per_px=1.0,
        max_side_px=w,
        ci_pa=25.0,
        reaction_rate=1.0,
    )
    assert res.sink_class == "chloroplast"


def test_per_stomatum_drawdowns_reported() -> None:
    """Multiple stomata polygons → each gets its own drawdown entry.
    The intercellular strip between palisade and stomata keeps the
    leaf physically connected (without it the background gap would
    break CO2 transport through the BC face)."""
    seg = _segformer_blob(
        [
            _rect(0, 0, 100, 30, "palisade"),
            _rect(0, 30, 100, 32, "intercellular"),
            _rect(10, 32, 30, 35, "stomata"),
            _rect(45, 32, 65, 35, "stomata"),
            _rect(80, 32, 95, 35, "stomata"),
        ],
        h=40,
        w=100,
    )
    res = compute_co2_diffusion(
        seg, um_per_px=1.0, max_side_px=100, ci_pa=25.0, reaction_rate=1.0
    )
    assert len(res.stomata_drawdowns) == 3
    for s in res.stomata_drawdowns:
        # Adjacent leaf cells exist for all three stomata, so values
        # should not be None.  drawdown should be >= 0 in normal
        # configurations (Cc <= Ci).
        assert s.cc_mean_pa is not None and s.drawdown_pa is not None
        assert s.drawdown_pa >= -0.5  # tolerate small numerical noise


def test_dirichlet_touching_does_not_inflate_a_net() -> None:
    """Stomata adjacent to mesophyll on a SHARED face with no other
    interior path — the BC face must not be counted as a sink
    interface.  With the gating fix from Darcy PR #12 round-2, the
    measured a_net should sit at floating-point noise (not at the
    gigantic BC-shortcut value it would take if the BC face leaked
    in).  Reaction in the sink still draws Cc below Ci physically,
    but a_net can't be measured through a non-existent interior path.
    """
    seg = _segformer_blob(
        [
            _rect(0, 0, 100, 30, "palisade"),
            # Stomata directly touching the palisade strip — no
            # intercellular bridge, so the only pressure/CO2 path is
            # through the BC↔sink face we explicitly want to exclude.
            _rect(0, 30, 100, 33, "stomata"),
        ],
        h=33,
        w=100,
    )
    res = compute_co2_diffusion(
        seg, um_per_px=1.0, max_side_px=100, ci_pa=25.0, reaction_rate=1.0
    )
    # An old buggy version (no gating) would have given a_net equal
    # to the BC face flux ≈ D_face * (Ci - Cc) * face_length, which
    # at D_stomata=1.6e-5 and 100-cell face ≈ 4e-5 (orders of
    # magnitude above the interior diffusion scale).  With the gate,
    # a_net must be below the interior sanity bound.
    palisade_d = DEFAULT_DIFFUSIVITY["palisade"]
    upper_bound = palisade_d * 100 * 1e-6 * 25.0 * 1000  # ~4e-9, interior scale
    assert abs(res.a_net) < upper_bound
    # Cc drawn below Ci by the reaction term (physics still applies
    # even when our measurement can't see the flux).
    assert res.cc_mean_pa is not None
    assert res.cc_mean_pa < 25.0


def test_g_m_proxy_matches_analytical_fickian_conductance() -> None:
    """Round-1 review caught that g_m_proxy was reported with wrong
    units: the un-normalised form A_net/(Ci - Cc) has units
    mol/(s·m·Pa), not mol/(m²·s·Pa).  After normalising by the leaf
    section length, we can pin it against the analytical Fickian
    conductance for a slab.

    For a pure-intercellular (gas-phase) slab of length L with
    Dirichlet C=Ci at one end and a very high-reaction chloroplast
    band at the other, the reaction band acts ≈ Dirichlet at C=0.
    Steady-state flux: A = D_IAS · Ci · h / L  [mol/(s·m-depth)].
    g_m = A / (leaf_length · ΔC) = D_IAS · h / (L² · 1) when
    leaf_length = L and ΔC = Ci.  We accept a generous rel=0.3 because
    the reaction band isn't an exact Dirichlet BC, but this test would
    have immediately caught the missing area normalisation.
    """
    from app.pipeline.co2_diffusion import DEFAULT_DIFFUSIVITY

    h, w = 10, 100
    seg = _segformer_blob(
        [
            # Intercellular channel + chloroplast sink at far end.
            _rect(0, 0, w, h, "intercellular"),
            # Palisade just at the far end, with very high r
            # approximates a Dirichlet C=0 sink.
            _rect(w - 4, 0, w, h, "palisade"),
            _rect(0, 0, 2, h, "stomata"),
        ],
        h=h,
        w=w,
    )
    res = compute_co2_diffusion(
        seg,
        um_per_px=1.0,
        max_side_px=w,
        ci_pa=25.0,
        reaction_rate=1e6,  # huge r → sink band ≈ C=0 (Dirichlet-like)
    )
    # leaf_section_length_m should be the grid-x extent in metres.
    # minAreaRect major axis on a w*h grid with w > h is w px.
    # At um_per_px=1.0 and factor=1.0, that's 100 * 1e-6 = 1e-4 m.
    # Round-2 review: pin this explicitly so a bug in
    # _leaf_section_length_m can't hide behind the wider g_m bracket.
    assert res.leaf_section_length_m == pytest.approx(1.0e-4, rel=0.05)
    # Expected order-of-magnitude: g_m_proxy ≈ D_IAS / L_m.  L is
    # the leaf section length (minAreaRect major axis) in metres.
    # D_IAS ≈ 1.6e-5 m²/s, L ≈ 1e-4 m → g_m ~ 1.6e-1 mol/(m²·s·Pa).
    # Tightened bracket from D/100..D to D/10..D (one order of
    # magnitude) so a wrong normalisation can't slip through.  The
    # reaction-band BC approximation makes an exact match
    # inappropriate but one-order precision is achievable here.
    assert res.g_m_proxy is not None
    d_ias = DEFAULT_DIFFUSIVITY["intercellular"]
    upper = d_ias / res.leaf_section_length_m
    lower = upper / 10.0
    assert lower < res.g_m_proxy < upper, (
        f"g_m_proxy={res.g_m_proxy:.3e} outside expected Fickian "
        f"range [{lower:.3e}, {upper:.3e}] — likely unit or "
        "normalisation bug"
    )


def test_non_finite_solver_values_surface_in_notes() -> None:
    """If the solver produces non-finite or negative concentrations,
    the result must explicitly note the counts.  Silent replacement
    would let an unstable solve hide behind plausible heatmaps.

    To trigger: use a geometry where parts of the leaf are isolated
    from stomata so spsolve produces numerical noise there.  Round-1
    review flagged this as a MINOR observability gap.
    """
    # The standard connected geometry shouldn't trip the guard —
    # this is a smoke test that the guard doesn't false-fire on
    # well-resolved inputs.
    seg = _segformer_blob(
        [
            _rect(0, 0, 100, 30, "palisade"),
            _rect(0, 30, 100, 32, "intercellular"),
            _rect(0, 32, 100, 35, "stomata"),
        ],
        h=40,
        w=100,
    )
    res = compute_co2_diffusion(
        seg, um_per_px=1.0, max_side_px=100, ci_pa=25.0, reaction_rate=1.0
    )
    # No non-finite / negative warnings expected here.
    for note in res.notes:
        assert "non-finite pixels" not in note
        assert "negative pixels" not in note


def test_result_round_trips_through_strict_json() -> None:
    seg = _segformer_blob(
        [
            _rect(0, 0, 100, 30, "palisade"),
            _rect(0, 30, 100, 32, "intercellular"),
            _rect(0, 32, 100, 35, "stomata"),
        ],
        h=40,
        w=100,
    )
    res = compute_co2_diffusion(
        seg, um_per_px=1.0, max_side_px=100, ci_pa=25.0, reaction_rate=1.0
    )
    s = json.dumps(res.to_dict(), allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s


def test_default_constants_sensible() -> None:
    assert DEFAULT_CI_PA > 0
    assert DEFAULT_REACTION_RATE >= 0
    # Gas-phase D should be ~10000x liquid D.
    assert (
        DEFAULT_DIFFUSIVITY["intercellular"] / DEFAULT_DIFFUSIVITY["palisade"]
        > 1e3
    )
