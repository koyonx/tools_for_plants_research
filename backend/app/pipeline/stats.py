"""Statistical helpers for group-vs-group comparisons.

All functions take two 1-D arrays (not necessarily equal length) and
return plain Python floats / dicts so the result blob round-trips
cleanly through JSON.  Keep the math self-contained — scipy is already
a dependency, but we avoid exposing its datatypes in the public surface
so downstream reporting stays simple.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy import stats

DEFAULT_BOOTSTRAP_ITERS = 2000
DEFAULT_CI = 0.95


@dataclass(frozen=True)
class GroupStats:
    """Summary stats.  All numeric fields are `None` when the group is
    empty — `NaN` would be the obvious sentinel, but Starlette's JSON
    renderer rejects non-finite floats so the endpoint would 500
    instead of returning a clean "n=0" row.
    """

    n: int
    mean: float | None
    sd: float | None
    median: float | None
    q25: float | None
    q75: float | None
    min: float | None
    max: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComparisonResult:
    group_a: GroupStats
    group_b: GroupStats
    welch_t_statistic: float | None
    welch_p_value: float | None
    mann_whitney_u: float | None
    mann_whitney_p_value: float | None
    cohens_d: float | None
    hedges_g: float | None
    # Bootstrap CI for Hedges' g (preferred effect size for small samples).
    hedges_g_ci_low: float | None
    hedges_g_ci_high: float | None
    # Free-form notes (e.g. "group too small") so the UI can surface
    # why a test was skipped without parsing NaN semantics.
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _summarise(values: np.ndarray) -> GroupStats:
    n = int(values.size)
    if n == 0:
        return GroupStats(0, None, None, None, None, None, None, None)
    return GroupStats(
        n=n,
        mean=float(values.mean()),
        sd=float(values.std(ddof=1)) if n > 1 else 0.0,
        median=float(np.median(values)),
        q25=float(np.quantile(values, 0.25)),
        q75=float(np.quantile(values, 0.75)),
        min=float(values.min()),
        max=float(values.max()),
    )


def _cohens_d(a: np.ndarray, b: np.ndarray) -> float | None:
    """Pooled-SD Cohen's d.  Returns None if the pooled SD is zero or
    one of the groups is too small (n<2)."""
    if a.size < 2 or b.size < 2:
        return None
    var_a = float(a.var(ddof=1))
    var_b = float(b.var(ddof=1))
    pooled = math.sqrt(((a.size - 1) * var_a + (b.size - 1) * var_b) / (a.size + b.size - 2))
    if pooled == 0.0:
        return None
    return (float(a.mean()) - float(b.mean())) / pooled


def _hedges_correction(n_a: int, n_b: int) -> float:
    """Small-sample bias correction J used to turn Cohen's d into Hedges' g."""
    df = n_a + n_b - 2
    if df <= 1:
        return 1.0
    # J ≈ 1 - 3 / (4*df - 1); analytical form uses gamma but the
    # approximation is < 0.1% different for df >= 10 and cheaper.
    return 1.0 - 3.0 / (4.0 * df - 1.0)


def _hedges_g(a: np.ndarray, b: np.ndarray) -> float | None:
    d = _cohens_d(a, b)
    if d is None:
        return None
    return d * _hedges_correction(a.size, b.size)


def _bootstrap_g_ci(
    a: np.ndarray,
    b: np.ndarray,
    iters: int = DEFAULT_BOOTSTRAP_ITERS,
    ci: float = DEFAULT_CI,
    rng: np.random.Generator | None = None,
) -> tuple[float, float] | None:
    """Percentile bootstrap CI for Hedges' g.

    Returns (lo, hi) — or None when either group is too small.  Using the
    percentile method instead of BCa keeps the math simple and is fine
    for moderate n and non-pathological distributions.
    """
    if a.size < 2 or b.size < 2:
        return None
    rng = rng or np.random.default_rng(42)
    samples: list[float] = []
    a_idx = rng.integers(0, a.size, size=(iters, a.size))
    b_idx = rng.integers(0, b.size, size=(iters, b.size))
    for i in range(iters):
        ra = a[a_idx[i]]
        rb = b[b_idx[i]]
        g = _hedges_g(ra, rb)
        if g is not None and math.isfinite(g):
            samples.append(g)
    if not samples:
        return None
    arr = np.array(samples, dtype=np.float64)
    alpha = (1.0 - ci) / 2.0
    lo, hi = float(np.quantile(arr, alpha)), float(np.quantile(arr, 1.0 - alpha))
    return lo, hi


def compare(
    a_values: list[float] | np.ndarray,
    b_values: list[float] | np.ndarray,
    *,
    bootstrap_iters: int = DEFAULT_BOOTSTRAP_ITERS,
) -> ComparisonResult:
    """Run Welch's t, Mann-Whitney U, and effect-size machinery on two
    groups.  Values must be finite; NaNs are dropped up front."""
    a = np.asarray([v for v in a_values if math.isfinite(float(v))], dtype=np.float64)
    b = np.asarray([v for v in b_values if math.isfinite(float(v))], dtype=np.float64)
    notes: list[str] = []

    welch_t: float | None = None
    welch_p: float | None = None
    if a.size >= 2 and b.size >= 2:
        res = stats.ttest_ind(a, b, equal_var=False, nan_policy="omit")
        welch_t = float(res.statistic) if math.isfinite(res.statistic) else None
        welch_p = float(res.pvalue) if math.isfinite(res.pvalue) else None
    else:
        notes.append("Welch's t skipped: need n>=2 in both groups")

    mw_u: float | None = None
    mw_p: float | None = None
    if a.size >= 1 and b.size >= 1:
        try:
            res = stats.mannwhitneyu(a, b, alternative="two-sided")
            mw_u = float(res.statistic)
            mw_p = float(res.pvalue)
        except ValueError as e:
            notes.append(f"Mann-Whitney skipped: {e}")

    d = _cohens_d(a, b)
    g = _hedges_g(a, b)
    ci = _bootstrap_g_ci(a, b, iters=bootstrap_iters)

    return ComparisonResult(
        group_a=_summarise(a),
        group_b=_summarise(b),
        welch_t_statistic=welch_t,
        welch_p_value=welch_p,
        mann_whitney_u=mw_u,
        mann_whitney_p_value=mw_p,
        cohens_d=d,
        hedges_g=g,
        hedges_g_ci_low=ci[0] if ci else None,
        hedges_g_ci_high=ci[1] if ci else None,
        notes=notes,
    )
