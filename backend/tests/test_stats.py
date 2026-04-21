"""Sanity tests for pipeline/stats.py.

We're not trying to re-implement scipy — just check the public surface
(result shapes, sign of effect size, coverage of bootstrap CI) so
downstream UI and reporting stay stable when we tweak the internals.
"""

from __future__ import annotations

import numpy as np

from app.pipeline.stats import _hedges_correction, compare


def test_compare_shape_and_signs() -> None:
    rng = np.random.default_rng(0)
    a = rng.normal(loc=10.0, scale=1.0, size=50)
    b = rng.normal(loc=11.5, scale=1.0, size=50)  # b larger on average
    result = compare(a.tolist(), b.tolist(), bootstrap_iters=500)

    assert result.group_a.n == 50
    assert result.group_b.n == 50
    # a < b so a - b is negative → Cohen's d & Hedges' g negative
    assert result.cohens_d is not None and result.cohens_d < 0
    assert result.hedges_g is not None and result.hedges_g < 0
    # Welch p-value should be tiny for a clear 1.5-SD gap
    assert result.welch_p_value is not None and result.welch_p_value < 0.001
    # Bootstrap CI bounded and not containing zero for this clear effect
    assert result.hedges_g_ci_low is not None
    assert result.hedges_g_ci_high is not None
    assert result.hedges_g_ci_high < 0


def test_compare_small_group_notes() -> None:
    result = compare([1.0], [1.1, 2.2, 3.3], bootstrap_iters=100)
    # welch needs n>=2 on both sides
    assert result.welch_p_value is None
    assert any("Welch" in note for note in result.notes)
    # effect sizes can't be computed either
    assert result.cohens_d is None
    assert result.hedges_g is None


def test_compare_no_variance_returns_none_effect() -> None:
    result = compare([5.0, 5.0, 5.0], [5.0, 5.0, 5.0], bootstrap_iters=100)
    # zero pooled SD → cohens_d undefined
    assert result.cohens_d is None
    assert result.hedges_g is None


def test_compare_identical_distributions() -> None:
    rng = np.random.default_rng(1)
    a = rng.normal(loc=0.0, scale=1.0, size=200)
    b = rng.normal(loc=0.0, scale=1.0, size=200)
    result = compare(a.tolist(), b.tolist(), bootstrap_iters=500)
    # effect size hovers around zero; CI should contain 0 overwhelmingly
    assert result.hedges_g is not None and abs(result.hedges_g) < 0.3
    assert (
        result.hedges_g_ci_low is not None
        and result.hedges_g_ci_high is not None
        and result.hedges_g_ci_low < 0 < result.hedges_g_ci_high
    )
    # Welch p should be "not significant" in the frequentist sense
    assert result.welch_p_value is not None and result.welch_p_value > 0.05


def test_hedges_correction_bounds() -> None:
    # correction approaches 1 as df increases
    assert 0.5 < _hedges_correction(3, 3) < 0.9
    assert _hedges_correction(100, 100) > 0.99


def test_compare_drops_nans() -> None:
    a = [1.0, 2.0, float("nan"), 3.0, 4.0]
    b = [10.0, float("inf"), 11.0, 12.0]
    result = compare(a, b, bootstrap_iters=100)
    assert result.group_a.n == 4  # NaN dropped
    assert result.group_b.n == 3  # Inf dropped
