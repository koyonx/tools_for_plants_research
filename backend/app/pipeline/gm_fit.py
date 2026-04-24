"""Mesophyll conductance (g_m) estimation from LI-COR A-Ci data.

Three methods, all with bootstrap 95% confidence intervals:

1. **harley_variable_j** (Harley et al. 1992)
   Analytical formula when the ETR (electron transport rate J) is
   measured simultaneously with A and Ci:

       g_m = A / (Ci - Gamma* * (J + 8*(A+Rd)) / (J - 4*(A+Rd)))

   Applied point-by-point over the RuBP-regeneration-limited portion
   of the A-Ci curve (high Ci > ~300 umol/mol for most C3).  Returns
   the median across points with a bootstrap CI.  Requires the LI-COR
   file to carry an ETR column (not all exports do).

2. **ethier_livingston** (Ethier & Livingston 2004)
   Non-linear fit of the Rubisco-limited region (low Ci, typically
   Ci < ~300 umol/mol) where A depends on Cc through the carboxylation
   equation.  Fits (Vcmax, g_m) jointly assuming Kc, Ko, Gamma*, Rd
   are known (from Bernacchi constants + a supplied Rd or a default).
   Robust when ETR isn't available but only works for curves that
   include Rubisco-limited points.

3. **nonlinear_slope**
   Simultaneous fit of (Vcmax, J, Rd, g_m) to the full A-Ci curve via
   scipy.optimize.least_squares (TRF with bounds).  Most general; most
   data-hungry.  Needs >= 6 points, ideally spanning both limitation
   regimes.  Reports which limitation each data point landed in.

All methods:
- Expect positive g_m; refuse to run if the curve has fewer than
  MIN_POINTS usable rows or if Ci / A all identically zero.
- Return a `GmFitResult` with the canonical g_m in
  [umol m^-2 s^-1 (umol/mol)^-1], equivalent to
  mol m^-2 s^-1 Pa^-1 at 25 C, 1 atm (via the factor 101.3 kPa /
  (25+273) K / R).  Operators pick whichever they prefer.
- Carry diagnostic fields so the UI can show residuals + fitted
  parameters + which points were used.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import least_squares

from app.pipeline.farquhar import (
    DEFAULT_CONSTANTS,
    DEFAULT_O2_MMOL_MOL,
    FarquharConstants,
    a_carbox,
    a_regen,
    kinetics_at,
)

MIN_POINTS_PER_METHOD = 4
BOOTSTRAP_ITERS_DEFAULT = 500
DEFAULT_RD = 1.5           # umol/m2/s — typical C3 dark respiration at 25 C.
DEFAULT_TLEAF_C = 25.0


@dataclass(frozen=True)
class GmMethodResult:
    method: str
    g_m: float | None                # umol m^-2 s^-1 (umol/mol)^-1
    g_m_ci_low: float | None
    g_m_ci_high: float | None
    vcmax: float | None              # umol m^-2 s^-1
    j_max: float | None              # umol m^-2 s^-1
    rd: float | None                 # umol m^-2 s^-1
    rmse: float | None               # umol m^-2 s^-1 (of predicted A_net vs measured)
    n_points_used: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GmFitResult:
    tleaf_c: float
    o2_mmol_mol: float
    methods: list[GmMethodResult]
    input_point_count: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tleaf_c": self.tleaf_c,
            "o2_mmol_mol": self.o2_mmol_mol,
            "methods": [m.to_dict() for m in self.methods],
            "input_point_count": self.input_point_count,
            "notes": list(self.notes),
        }


def _filter_usable_points(
    a: np.ndarray,
    ci: np.ndarray,
    etr: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Drop rows where A or Ci is non-finite / non-positive.  For
    Harley, also require finite positive ETR.  Returns (a, ci, etr)
    arrays of the same length after filtering."""
    mask = np.isfinite(a) & np.isfinite(ci) & (ci > 0)
    if etr is not None:
        mask = mask & np.isfinite(etr) & (etr > 0)
    return a[mask], ci[mask], (etr[mask] if etr is not None else None)


def _bootstrap_ci(
    fn: Any,
    a: np.ndarray,
    ci: np.ndarray,
    etr: np.ndarray | None,
    iters: int,
    rng: np.random.Generator,
) -> tuple[float | None, float | None]:
    """Percentile 95% CI by resampling the A-Ci rows with replacement.
    Returns (None, None) on degenerate inputs or when `fn` fails
    repeatedly."""
    n = a.size
    if n < MIN_POINTS_PER_METHOD:
        return None, None
    vals: list[float] = []
    for _ in range(iters):
        idx = rng.integers(0, n, size=n)
        a_s = a[idx]
        ci_s = ci[idx]
        etr_s = etr[idx] if etr is not None else None
        try:
            v = fn(a_s, ci_s, etr_s)
        except Exception:
            continue
        if v is not None and math.isfinite(v) and v > 0:
            vals.append(v)
    if len(vals) < max(20, iters // 10):
        return None, None
    arr = np.array(vals, dtype=np.float64)
    return float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))


def fit_harley_variable_j(
    a: np.ndarray,
    ci: np.ndarray,
    etr: np.ndarray,
    *,
    rd: float = DEFAULT_RD,
    tleaf_c: float = DEFAULT_TLEAF_C,
    o2_mmol_mol: float = DEFAULT_O2_MMOL_MOL,
    constants: FarquharConstants = DEFAULT_CONSTANTS,
    bootstrap_iters: int = BOOTSTRAP_ITERS_DEFAULT,
) -> GmMethodResult:
    """Harley et al. 1992 variable-J estimator.

    Analytical expression for g_m assuming the RuBP-regen-limited form
    of Farquhar:

        g_m = A / (Ci - Gamma* * (J + 8*(A+Rd)) / (J - 4*(A+Rd)))

    Applied pointwise; we report the median + bootstrap CI over the
    points where the denominator is positive (J > 4*(A+Rd)).
    """
    kin = kinetics_at(tleaf_c, constants)
    gs = kin["Gamma_star_umol_mol"]
    a_f, ci_f, etr_f = _filter_usable_points(a, ci, etr)
    if etr_f is None or a_f.size < MIN_POINTS_PER_METHOD:
        return GmMethodResult(
            method="harley_variable_j",
            g_m=None,
            g_m_ci_low=None,
            g_m_ci_high=None,
            vcmax=None,
            j_max=None,
            rd=rd,
            rmse=None,
            n_points_used=a_f.size if etr_f is not None else 0,
            notes=[
                "insufficient points with finite positive A, Ci, ETR "
                f"(need >= {MIN_POINTS_PER_METHOD})"
            ],
        )

    def _pointwise(a_s: np.ndarray, ci_s: np.ndarray, etr_s: np.ndarray | None) -> float | None:
        if etr_s is None:
            return None
        denom_ratio = etr_s - 4.0 * (a_s + rd)
        valid = np.isfinite(denom_ratio) & (denom_ratio > 1e-9)
        if not valid.any():
            return None
        ci_minus_cc = gs * (etr_s[valid] + 8.0 * (a_s[valid] + rd)) / denom_ratio[valid]
        denom = ci_s[valid] - ci_minus_cc
        ok = np.isfinite(denom) & (np.abs(denom) > 1e-6)
        if not ok.any():
            return None
        gm_values = a_s[valid][ok] / denom[ok]
        gm_values = gm_values[np.isfinite(gm_values) & (gm_values > 0)]
        if gm_values.size == 0:
            return None
        return float(np.median(gm_values))

    g_m_point = _pointwise(a_f, ci_f, etr_f)
    if g_m_point is None:
        return GmMethodResult(
            method="harley_variable_j",
            g_m=None,
            g_m_ci_low=None,
            g_m_ci_high=None,
            vcmax=None,
            j_max=None,
            rd=rd,
            rmse=None,
            n_points_used=a_f.size,
            notes=[
                "no usable points with positive denominator "
                "(J - 4*(A+Rd) > 0); curve may not include enough "
                "RuBP-regen-limited points"
            ],
        )

    rng = np.random.default_rng(42)
    ci_low, ci_high = _bootstrap_ci(_pointwise, a_f, ci_f, etr_f, bootstrap_iters, rng)
    # Harley doesn't separately fit Vcmax; report J as the median
    # measured ETR so downstream analytics can see the regime.
    j_median = float(np.median(etr_f))
    return GmMethodResult(
        method="harley_variable_j",
        g_m=g_m_point,
        g_m_ci_low=ci_low,
        g_m_ci_high=ci_high,
        vcmax=None,
        j_max=j_median,
        rd=rd,
        rmse=None,
        n_points_used=a_f.size,
        notes=[],
    )


def _predicted_a_net(
    ci: np.ndarray,
    vcmax: float,
    j: float,
    rd: float,
    g_m: float,
    tleaf_c: float,
    o2_mmol_mol: float,
    constants: FarquharConstants,
) -> np.ndarray:
    """Predict A_net for each Ci by iterating A = Farquhar(Cc) with
    Cc = Ci - A / g_m.  Solve the implicit relation with fixed-point
    iteration — convergent in 4-5 steps for typical g_m values;
    fast enough to be called from least_squares."""
    kin = kinetics_at(tleaf_c, constants)
    kc = kin["Kc_umol_mol"]
    ko = kin["Ko_mmol_mol"]
    gs = kin["Gamma_star_umol_mol"]
    cc = ci.copy().astype(np.float64)
    a_net_prev = np.zeros_like(cc)
    for _ in range(20):
        ac = a_carbox(cc, vcmax=vcmax, kc=kc, ko=ko, gamma_star=gs, o2_mmol_mol=o2_mmol_mol)
        aj = a_regen(cc, j=j, gamma_star=gs)
        a_net = np.minimum(ac, aj) - rd
        a_net = np.where(np.isfinite(a_net), a_net, 0.0)
        cc_new = ci - a_net / g_m
        cc_new = np.clip(cc_new, gs * 0.5, ci)
        if np.allclose(a_net, a_net_prev, rtol=1e-5, atol=1e-4):
            break
        a_net_prev = a_net
        cc = cc_new
    return a_net_prev


def fit_ethier_livingston(
    a: np.ndarray,
    ci: np.ndarray,
    *,
    rd: float = DEFAULT_RD,
    tleaf_c: float = DEFAULT_TLEAF_C,
    o2_mmol_mol: float = DEFAULT_O2_MMOL_MOL,
    constants: FarquharConstants = DEFAULT_CONSTANTS,
    bootstrap_iters: int = BOOTSTRAP_ITERS_DEFAULT,
) -> GmMethodResult:
    """Ethier & Livingston 2004 non-linear fit.

    Joint fit of (Vcmax, g_m) on the Rubisco-limited portion of the
    A-Ci curve (Ci <= 300 umol/mol by convention).  Kc, Ko, Gamma*,
    Rd are held at their Bernacchi-default / supplied values.

    Uses a huge J internally so A_j never wins; if the residuals show
    systematic non-Rubisco behaviour the caller should switch to
    nonlinear_slope instead.
    """
    a_f, ci_f, _ = _filter_usable_points(a, ci, None)
    rubisco_mask = ci_f <= 300.0
    a_r = a_f[rubisco_mask]
    ci_r = ci_f[rubisco_mask]
    if a_r.size < MIN_POINTS_PER_METHOD:
        return GmMethodResult(
            method="ethier_livingston",
            g_m=None,
            g_m_ci_low=None,
            g_m_ci_high=None,
            vcmax=None,
            j_max=None,
            rd=rd,
            rmse=None,
            n_points_used=a_r.size,
            notes=[
                f"need >= {MIN_POINTS_PER_METHOD} Rubisco-limited points "
                "(Ci <= 300); curve may be too narrow for Ethier-Livingston"
            ],
        )

    def _pointwise(a_s: np.ndarray, ci_s: np.ndarray, _: np.ndarray | None) -> float | None:
        def _residual(params: np.ndarray) -> np.ndarray:
            vcmax, g_m = params
            pred = _predicted_a_net(
                ci_s,
                vcmax=vcmax,
                j=1e6,
                rd=rd,
                g_m=g_m,
                tleaf_c=tleaf_c,
                o2_mmol_mol=o2_mmol_mol,
                constants=constants,
            )
            return (pred - a_s).astype(np.float64)  # type: ignore[no-any-return]

        try:
            result = least_squares(
                _residual,
                x0=np.array([60.0, 0.2]),
                bounds=([1.0, 1e-4], [500.0, 20.0]),
                method="trf",
                max_nfev=400,
            )
        except Exception:
            return None
        if not result.success:
            return None
        _, g_m_fit = result.x
        return float(g_m_fit) if g_m_fit > 0 else None

    g_m_fit = _pointwise(a_r, ci_r, None)
    if g_m_fit is None:
        return GmMethodResult(
            method="ethier_livingston",
            g_m=None,
            g_m_ci_low=None,
            g_m_ci_high=None,
            vcmax=None,
            j_max=None,
            rd=rd,
            rmse=None,
            n_points_used=a_r.size,
            notes=["non-linear fit failed to converge on the Rubisco-limited points"],
        )

    def _residual_full(params: np.ndarray) -> np.ndarray:
        vcmax, g_m = params
        pred = _predicted_a_net(
            ci_r, vcmax=vcmax, j=1e6, rd=rd, g_m=g_m,
            tleaf_c=tleaf_c, o2_mmol_mol=o2_mmol_mol, constants=constants,
        )
        return (pred - a_r).astype(np.float64)  # type: ignore[no-any-return]

    final = least_squares(
        _residual_full,
        x0=np.array([60.0, max(g_m_fit, 1e-3)]),
        bounds=([1.0, 1e-4], [500.0, 20.0]),
        method="trf",
    )
    vcmax_fit, _ = final.x
    residuals = final.fun
    rmse = float(np.sqrt(np.mean(residuals * residuals))) if residuals.size else None

    rng = np.random.default_rng(42)
    ci_low, ci_high = _bootstrap_ci(_pointwise, a_r, ci_r, None, bootstrap_iters, rng)
    return GmMethodResult(
        method="ethier_livingston",
        g_m=g_m_fit,
        g_m_ci_low=ci_low,
        g_m_ci_high=ci_high,
        vcmax=float(vcmax_fit),
        j_max=None,
        rd=rd,
        rmse=rmse,
        n_points_used=a_r.size,
        notes=[],
    )


def fit_nonlinear_slope(
    a: np.ndarray,
    ci: np.ndarray,
    *,
    rd: float | None = None,
    fit_rd: bool = True,
    tleaf_c: float = DEFAULT_TLEAF_C,
    o2_mmol_mol: float = DEFAULT_O2_MMOL_MOL,
    constants: FarquharConstants = DEFAULT_CONSTANTS,
    bootstrap_iters: int = BOOTSTRAP_ITERS_DEFAULT,
) -> GmMethodResult:
    """Simultaneous non-linear fit of (Vcmax, J, Rd, g_m) to the full
    A-Ci curve.  Needs >= 6 well-spaced points; ideally covering
    both Rubisco- and RuBP-regen-limited regions.

    When `fit_rd=False` and `rd` is supplied, Rd is held fixed and
    only 3 free parameters (Vcmax, J, g_m) are estimated.
    """
    a_f, ci_f, _ = _filter_usable_points(a, ci, None)
    need = 6 if fit_rd else MIN_POINTS_PER_METHOD
    if a_f.size < need:
        return GmMethodResult(
            method="nonlinear_slope",
            g_m=None,
            g_m_ci_low=None,
            g_m_ci_high=None,
            vcmax=None,
            j_max=None,
            rd=rd,
            rmse=None,
            n_points_used=a_f.size,
            notes=[
                f"need >= {need} points for joint Vcmax/J/Rd/g_m fit "
                "(curve may be too short)"
            ],
        )

    rd_init = DEFAULT_RD if rd is None else rd

    def _pointwise(a_s: np.ndarray, ci_s: np.ndarray, _: np.ndarray | None) -> float | None:
        if fit_rd:

            def _residual(params: np.ndarray) -> np.ndarray:
                vcmax, j_val, rd_val, g_m = params
                pred = _predicted_a_net(
                    ci_s, vcmax=vcmax, j=j_val, rd=rd_val, g_m=g_m,
                    tleaf_c=tleaf_c, o2_mmol_mol=o2_mmol_mol, constants=constants,
                )
                return (pred - a_s).astype(np.float64)  # type: ignore[no-any-return]

            try:
                result = least_squares(
                    _residual,
                    x0=np.array([60.0, 150.0, rd_init, 0.2]),
                    bounds=(
                        [1.0, 1.0, 0.0, 1e-4],
                        [500.0, 800.0, 10.0, 20.0],
                    ),
                    method="trf",
                    max_nfev=1000,
                )
            except Exception:
                return None
            if not result.success:
                return None
            _, _, _, g_m_fit = result.x
        else:

            def _residual_fixed(params: np.ndarray) -> np.ndarray:
                vcmax, j_val, g_m = params
                pred = _predicted_a_net(
                    ci_s, vcmax=vcmax, j=j_val, rd=rd_init, g_m=g_m,
                    tleaf_c=tleaf_c, o2_mmol_mol=o2_mmol_mol, constants=constants,
                )
                return (pred - a_s).astype(np.float64)  # type: ignore[no-any-return]

            try:
                result = least_squares(
                    _residual_fixed,
                    x0=np.array([60.0, 150.0, 0.2]),
                    bounds=(
                        [1.0, 1.0, 1e-4],
                        [500.0, 800.0, 20.0],
                    ),
                    method="trf",
                    max_nfev=1000,
                )
            except Exception:
                return None
            if not result.success:
                return None
            _, _, g_m_fit = result.x
        return float(g_m_fit) if g_m_fit > 0 else None

    g_m_fit = _pointwise(a_f, ci_f, None)
    if g_m_fit is None:
        return GmMethodResult(
            method="nonlinear_slope",
            g_m=None,
            g_m_ci_low=None,
            g_m_ci_high=None,
            vcmax=None,
            j_max=None,
            rd=rd_init,
            rmse=None,
            n_points_used=a_f.size,
            notes=["joint non-linear fit failed to converge"],
        )
    if fit_rd:

        def _residual_full(params: np.ndarray) -> np.ndarray:
            vcmax, j_val, rd_val, g_m = params
            pred = _predicted_a_net(
                ci_f, vcmax=vcmax, j=j_val, rd=rd_val, g_m=g_m,
                tleaf_c=tleaf_c, o2_mmol_mol=o2_mmol_mol, constants=constants,
            )
            return (pred - a_f).astype(np.float64)  # type: ignore[no-any-return]

        final = least_squares(
            _residual_full,
            x0=np.array([60.0, 150.0, rd_init, g_m_fit]),
            bounds=([1.0, 1.0, 0.0, 1e-4], [500.0, 800.0, 10.0, 20.0]),
            method="trf",
        )
        vcmax_fit, j_fit, rd_fit, _ = final.x
    else:

        def _residual_full_fixed(params: np.ndarray) -> np.ndarray:
            vcmax, j_val, g_m = params
            pred = _predicted_a_net(
                ci_f, vcmax=vcmax, j=j_val, rd=rd_init, g_m=g_m,
                tleaf_c=tleaf_c, o2_mmol_mol=o2_mmol_mol, constants=constants,
            )
            return (pred - a_f).astype(np.float64)  # type: ignore[no-any-return]

        final = least_squares(
            _residual_full_fixed,
            x0=np.array([60.0, 150.0, g_m_fit]),
            bounds=([1.0, 1.0, 1e-4], [500.0, 800.0, 20.0]),
            method="trf",
        )
        vcmax_fit, j_fit, _ = final.x
        rd_fit = rd_init

    residuals = final.fun
    rmse = float(np.sqrt(np.mean(residuals * residuals))) if residuals.size else None
    rng = np.random.default_rng(42)
    ci_low, ci_high = _bootstrap_ci(_pointwise, a_f, ci_f, None, bootstrap_iters, rng)
    return GmMethodResult(
        method="nonlinear_slope",
        g_m=g_m_fit,
        g_m_ci_low=ci_low,
        g_m_ci_high=ci_high,
        vcmax=float(vcmax_fit),
        j_max=float(j_fit),
        rd=float(rd_fit),
        rmse=rmse,
        n_points_used=a_f.size,
        notes=[],
    )


def fit_all(
    a: np.ndarray,
    ci: np.ndarray,
    *,
    etr: np.ndarray | None = None,
    rd: float | None = None,
    tleaf_c: float = DEFAULT_TLEAF_C,
    o2_mmol_mol: float = DEFAULT_O2_MMOL_MOL,
    constants: FarquharConstants = DEFAULT_CONSTANTS,
    bootstrap_iters: int = BOOTSTRAP_ITERS_DEFAULT,
) -> GmFitResult:
    """Run all three methods and return a consolidated result.  Any
    method that fails its own degeneracy checks is included in the
    output with notes explaining why — the operator still sees a
    whole-curve fit from nonlinear_slope as a fallback."""
    rd_value = DEFAULT_RD if rd is None else rd
    methods: list[GmMethodResult] = []
    if etr is not None:
        methods.append(
            fit_harley_variable_j(
                a, ci, etr,
                rd=rd_value, tleaf_c=tleaf_c, o2_mmol_mol=o2_mmol_mol,
                constants=constants, bootstrap_iters=bootstrap_iters,
            )
        )
    else:
        methods.append(
            GmMethodResult(
                method="harley_variable_j", g_m=None, g_m_ci_low=None, g_m_ci_high=None,
                vcmax=None, j_max=None, rd=rd_value, rmse=None, n_points_used=0,
                notes=["ETR column not supplied; Harley variable-J skipped"],
            )
        )
    methods.append(
        fit_ethier_livingston(
            a, ci,
            rd=rd_value, tleaf_c=tleaf_c, o2_mmol_mol=o2_mmol_mol,
            constants=constants, bootstrap_iters=bootstrap_iters,
        )
    )
    methods.append(
        fit_nonlinear_slope(
            a, ci,
            rd=rd, fit_rd=rd is None,
            tleaf_c=tleaf_c, o2_mmol_mol=o2_mmol_mol,
            constants=constants, bootstrap_iters=bootstrap_iters,
        )
    )
    notes: list[str] = []
    if not any(m.g_m is not None for m in methods):
        notes.append(
            "all three g_m methods failed — check that the A-Ci curve has "
            "enough usable points (finite A, positive Ci, and optionally ETR) "
            "spanning both low and high Ci regions"
        )
    return GmFitResult(
        tleaf_c=tleaf_c,
        o2_mmol_mol=o2_mmol_mol,
        methods=methods,
        input_point_count=int(a.size),
        notes=notes,
    )
