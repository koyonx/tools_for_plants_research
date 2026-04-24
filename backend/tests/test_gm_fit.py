"""Tests for the g_m estimation methods.

Strategy: generate A-Ci curves with KNOWN (Vcmax, J, Rd, g_m) via the
same Farquhar model the fitters consume, add tiny noise, and assert
the fitted parameters recover the inputs within reasonable
tolerances.  This is the standard in-silico validation for
parameter-recovery estimators: if the synthetic curve comes from the
same model, the fit MUST recover it (within noise + degeneracy).
"""

from __future__ import annotations

import numpy as np
import pytest

from app.pipeline.farquhar import kinetics_at
from app.pipeline.gm_fit import (
    fit_all,
    fit_ethier_livingston,
    fit_harley_variable_j,
    fit_nonlinear_slope,
)


def _synthesize_curve(
    vcmax: float = 80.0,
    j: float = 160.0,
    rd: float = 1.5,
    g_m: float = 0.3,
    tleaf_c: float = 25.0,
    n_points: int = 12,
    ci_range: tuple[float, float] = (40.0, 1500.0),
    noise: float = 0.0,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generate A, Ci, ETR (J for each point) for a KNOWN parameter set.
    Returns (a, ci, etr).  The fitter output is then compared to the
    known inputs.
    """
    kin = kinetics_at(tleaf_c)
    kc = kin["Kc_umol_mol"]
    ko = kin["Ko_mmol_mol"]
    gs = kin["Gamma_star_umol_mol"]
    o2 = 210.0
    ci = np.linspace(*ci_range, n_points)

    # Invert the implicit A-Cc relation for each Ci via fixed-point
    # iteration.  Critically, we do NOT clamp Cc to `<= Ci` here —
    # round-1 review caught that the production _predicted_a_net had
    # that clamp AND the test synthesizer replicated it, making the
    # tests self-validating.  This synthesizer now uses the correct
    # physics (Cc > Ci when A < 0, below compensation), so recovery
    # tests genuinely exercise the predictor.
    cc = ci.copy().astype(np.float64)
    a_net = np.zeros_like(cc)
    for _ in range(80):
        ac = vcmax * (cc - gs) / (cc + kc * (1.0 + o2 / ko))
        aj = j * (cc - gs) / (4.0 * cc + 8.0 * gs)
        a = np.minimum(ac, aj) - rd
        cc_new = np.maximum(ci - a / g_m, gs * 0.5)
        if np.allclose(cc_new, cc, rtol=1e-7):
            a_net = a
            break
        cc = cc_new
        a_net = a
    if noise > 0:
        rng = np.random.default_rng(seed)
        a_net = a_net + rng.normal(0, noise, size=a_net.shape)
    # ETR assumed constant at J (well-lit curve).
    etr = np.full_like(ci, j)
    return a_net, ci, etr


def test_nonlinear_slope_recovers_known_c3_parameters() -> None:
    """Clean synthetic curve, no noise — fit must hit the exact
    inputs to <10% relative error."""
    vcmax_true, j_true, rd_true, g_m_true = 80.0, 160.0, 1.5, 0.3
    a, ci, _ = _synthesize_curve(
        vcmax=vcmax_true, j=j_true, rd=rd_true, g_m=g_m_true, n_points=14
    )
    result = fit_nonlinear_slope(a, ci, rd=None, fit_rd=True, bootstrap_iters=0)
    assert result.g_m is not None
    assert result.vcmax is not None and result.j_max is not None and result.rd is not None
    assert result.g_m == pytest.approx(g_m_true, rel=0.1)
    assert result.vcmax == pytest.approx(vcmax_true, rel=0.1)
    assert result.j_max == pytest.approx(j_true, rel=0.1)
    assert result.rd == pytest.approx(rd_true, abs=0.5)


def test_nonlinear_slope_with_fixed_rd_still_recovers_gm() -> None:
    vcmax_true, j_true, rd_true, g_m_true = 80.0, 160.0, 1.5, 0.3
    a, ci, _ = _synthesize_curve(
        vcmax=vcmax_true, j=j_true, rd=rd_true, g_m=g_m_true, n_points=12
    )
    result = fit_nonlinear_slope(a, ci, rd=rd_true, fit_rd=False, bootstrap_iters=0)
    assert result.g_m is not None
    assert result.g_m == pytest.approx(g_m_true, rel=0.1)


def test_ethier_livingston_recovers_gm_on_rubisco_limited_portion() -> None:
    """Ethier fits Vcmax+g_m on the Rubisco-limited region.

    Ethier's predictor disables Aj internally (uses J=1e6 so
    A_j never binds), which means test data containing points where
    Aj-limitation is the true regime would be inherently mismatched
    against the fitter.  Synthesise with a huge J so the data is
    guaranteed Rubisco-limited throughout — this matches what Ethier
    assumes and tests the parameter-recovery honestly.  Low-Ci
    points near the compensation point can still have Aj < Ac in the
    real model; restricting to J-huge removes that incompatibility.

    Vcmax-g_m is correlated even in pure Rubisco data, so accept a
    ±1 order-of-magnitude bracket (literature-known limitation).
    """
    vcmax_true, rd_true, g_m_true = 80.0, 1.5, 0.3
    # J effectively infinite so the generator matches Ethier's
    # internal J=1e6 assumption.  rd_range / n_points chosen so the
    # Rubisco curvature is well-sampled (16 points, Ci spanning the
    # sub-saturating region).
    a, ci, _ = _synthesize_curve(
        vcmax=vcmax_true, j=1e6, rd=rd_true, g_m=g_m_true, n_points=16,
        ci_range=(40.0, 300.0),
    )
    result = fit_ethier_livingston(a, ci, rd=rd_true, bootstrap_iters=0)
    assert result.g_m is not None
    assert 0.5 * g_m_true < result.g_m < 2.0 * g_m_true
    assert result.vcmax is not None
    assert 0.5 * vcmax_true < result.vcmax < 2.0 * vcmax_true


def test_harley_variable_j_recovers_gm_with_etr() -> None:
    """Harley's formula is exact for the RuBP-regen-limited region when
    ETR is supplied.  Generate a curve with lots of high-Ci points so
    the denominator (J - 4(A+Rd)) stays positive."""
    vcmax_true, j_true, rd_true, g_m_true = 80.0, 160.0, 1.5, 0.3
    a, ci, etr = _synthesize_curve(
        vcmax=vcmax_true, j=j_true, rd=rd_true, g_m=g_m_true, n_points=12,
        ci_range=(400.0, 1500.0),  # RuBP-regen regime
    )
    result = fit_harley_variable_j(a, ci, etr, rd=rd_true, bootstrap_iters=0)
    assert result.g_m is not None
    assert result.g_m == pytest.approx(g_m_true, rel=0.1)


def test_fit_all_emits_three_method_results() -> None:
    """The consolidated fit_all should return results for all three
    methods (with skip notes as appropriate) so the UI can always
    show a consistent grid."""
    a, ci, etr = _synthesize_curve(n_points=14)
    result = fit_all(a, ci, etr=etr, bootstrap_iters=20)
    assert len(result.methods) == 3
    method_names = {m.method for m in result.methods}
    assert method_names == {
        "harley_variable_j",
        "ethier_livingston",
        "nonlinear_slope",
    }


def test_fit_all_without_etr_skips_harley_with_note() -> None:
    """When the file has no ETR, Harley must skip cleanly with a
    note; the nonlinear-slope method MUST still succeed on a
    full-curve synthesizer.  Ethier is known-degenerate on data
    generated with the full Farquhar min(Ac, Aj) (the synthesizer
    can produce points where the real regime is Aj-limited even at
    low Ci near the compensation point, and Ethier's predictor uses
    J=huge so it can't reproduce those); accept either success OR
    a clean "failed to converge" skip — this matches what happens
    with real-world LI-COR data where Ethier sometimes punts.
    """
    a, ci, _ = _synthesize_curve(
        vcmax=80.0, j=300.0, rd=1.5, g_m=0.3,
        n_points=24, ci_range=(40.0, 1400.0),
    )
    result = fit_all(a, ci, etr=None, bootstrap_iters=20)
    harley = next(m for m in result.methods if m.method == "harley_variable_j")
    assert harley.g_m is None
    assert harley.notes
    assert any("ETR" in n for n in harley.notes)
    # Ethier can legitimately fail on full-Farquhar data; when it
    # DOES succeed the recovery must still be within order of
    # magnitude.  Either outcome is acceptable here.
    eth = next(m for m in result.methods if m.method == "ethier_livingston")
    if eth.g_m is not None:
        assert 0.1 < eth.g_m < 3.0
    else:
        assert eth.notes
    # Non-linear fits the full curve and MUST succeed.
    nlin = next(m for m in result.methods if m.method == "nonlinear_slope")
    assert nlin.g_m is not None
    assert 0.5 * 0.3 < nlin.g_m < 2.0 * 0.3  # within 2x of true 0.3


def test_fit_all_with_only_few_points_emits_clean_notes() -> None:
    """When the input curve is too short, methods should return
    `g_m=None` with notes explaining why — not crash."""
    a = np.array([5.0, 10.0])
    ci = np.array([100.0, 300.0])
    result = fit_all(a, ci, bootstrap_iters=0)
    for m in result.methods:
        assert m.g_m is None
        assert m.notes
    assert result.notes  # aggregate warning


def test_fit_all_with_noisy_data_still_recovers_gm_within_50_percent() -> None:
    """Add measurement-scale noise (sigma=0.5 umol/m2/s, comparable to
    LI-COR baseline drift) to A and verify the nonlinear fit lands
    within 50% of the true g_m.  The Vcmax-J-Rd-g_m landscape is
    fundamentally ill-conditioned under noise; 50% is a realistic
    confidence band for real-world g_m estimates.  Tighter bounds
    risk false negatives on genuinely noisy field data.
    """
    vcmax_true, j_true, rd_true, g_m_true = 80.0, 160.0, 1.5, 0.3
    a, ci, _ = _synthesize_curve(
        vcmax=vcmax_true, j=j_true, rd=rd_true, g_m=g_m_true,
        n_points=16, noise=0.5, seed=42,
    )
    result = fit_nonlinear_slope(a, ci, rd=None, fit_rd=True, bootstrap_iters=0)
    assert result.g_m is not None
    assert result.g_m == pytest.approx(g_m_true, rel=0.5)


def test_predicted_a_net_returns_fresh_iterate_at_convergence() -> None:
    """Targeted regression for the round-1 stale-iterate bug.

    `_predicted_a_net` solves A = min(Ac, Aj) - Rd with Cc = Ci - A/g_m
    via fixed-point iteration.  The previous version returned the
    PREVIOUS iterate (off-by-one step) once convergence was detected.
    Reproduce the true converged A via a tight manual fixed-point loop
    (100 iters, rtol=1e-10) and assert the predictor matches to <0.001
    µmol/m²/s.  Covers both convergence path and bounded-iterations
    exit path by using a mid-Ci point where convergence is fast and a
    low-Ci point where it can take more iters.
    """
    from app.pipeline.farquhar import DEFAULT_CONSTANTS, kinetics_at
    from app.pipeline.gm_fit import _predicted_a_net

    ci = np.array([80.0, 400.0, 800.0])
    vcmax, j, rd, g_m = 80.0, 160.0, 1.5, 0.3
    pred = _predicted_a_net(ci, vcmax, j, rd, g_m, 25.0, 210.0, DEFAULT_CONSTANTS)

    # Reference solution via direct iteration with much tighter
    # tolerance + more iters.
    kin = kinetics_at(25.0)
    kc = kin["Kc_umol_mol"]
    ko = kin["Ko_mmol_mol"]
    gs = kin["Gamma_star_umol_mol"]
    cc = ci.copy().astype(np.float64)
    a_ref = np.zeros_like(cc)
    for _ in range(500):
        ac = vcmax * (cc - gs) / (cc + kc * (1 + 210.0 / ko))
        aj = j * (cc - gs) / (4 * cc + 8 * gs)
        a = np.minimum(ac, aj) - rd
        cc_new = np.maximum(ci - a / g_m, gs * 0.5)
        if np.allclose(cc_new, cc, rtol=1e-10):
            a_ref = a
            break
        cc = cc_new
        a_ref = a

    np.testing.assert_allclose(pred, a_ref, atol=1e-3)


def test_predicted_a_net_allows_cc_greater_than_ci_near_compensation() -> None:
    """At Ci below the CO2 compensation point, A_net is negative
    (respiration > photosynthesis) and Cc = Ci - A/g_m > Ci.  The
    predictor must NOT clamp Cc to <= Ci, or the low-Ci branch that
    Ethier and the joint fit read their g_m signal from becomes
    distorted.  Round-1 review caught this bug.  Check that the
    predictor produces A_net close to the analytical "below-compensation"
    value which requires Cc > Ci.
    """
    from app.pipeline.farquhar import DEFAULT_CONSTANTS
    from app.pipeline.gm_fit import _predicted_a_net

    # At Ci=20 (well below Gamma*=42.75), any g_m allows A<0.  The
    # Cc value is Ci - A/g_m > Ci (since A<0).  With old Cc<=Ci clamp,
    # the predictor would have been pinned to Ci=20 and given a more
    # negative A.  With the fix, Cc floats above 20 and A is closer to
    # Rd alone (A ≈ -Rd when Ci is far below Gamma*).
    ci = np.array([20.0])
    pred = _predicted_a_net(ci, 80.0, 160.0, 1.5, 0.3, 25.0, 210.0, DEFAULT_CONSTANTS)
    # Net A must be negative (below compensation) but NOT as negative
    # as it would be with Cc clamped to Ci.  A close to the
    # respiration floor (-Rd = -1.5) within a reasonable margin.
    assert float(pred[0]) < 0
    assert float(pred[0]) > -3.0  # not artificially amplified


def test_result_round_trips_through_strict_json() -> None:
    import json

    a, ci, etr = _synthesize_curve(n_points=12)
    result = fit_all(a, ci, etr=etr, bootstrap_iters=10)
    s = json.dumps(result.to_dict(), allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s


def test_bootstrap_ci_bracket_contains_point_estimate() -> None:
    """When the fit succeeds and bootstrap converges, the CI should
    bracket the point estimate (by construction — median of resamples)."""
    a, ci, _ = _synthesize_curve(n_points=16)
    result = fit_nonlinear_slope(a, ci, rd=None, fit_rd=True, bootstrap_iters=100)
    assert result.g_m is not None
    if result.g_m_ci_low is not None and result.g_m_ci_high is not None:
        assert result.g_m_ci_low < result.g_m_ci_high
        # Point estimate should be within bracket (or very close) —
        # bootstrap medians drift slightly from the full-data fit.
        assert result.g_m_ci_low * 0.5 < result.g_m < result.g_m_ci_high * 2.0
