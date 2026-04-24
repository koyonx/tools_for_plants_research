"""Tests for the Farquhar-von Caemmerer-Berry model."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.pipeline.farquhar import (
    DEFAULT_CONSTANTS,
    a_carbox,
    a_regen,
    a_tpu,
    arrhenius,
    cc_from_ci,
    kinetics_at,
    predict_a,
)


def test_arrhenius_at_reference_temperature_returns_k25() -> None:
    # At T = 25 C, the exponent collapses to 0 → k(T) == k(25).
    k25 = 404.9
    ea = 79430.0
    assert arrhenius(k25, ea, 25.0) == pytest.approx(k25, rel=1e-9)


def test_arrhenius_increases_with_temperature() -> None:
    k25 = 404.9
    ea = 79430.0  # Bernacchi Kc activation energy
    k30 = arrhenius(k25, ea, 30.0)
    k20 = arrhenius(k25, ea, 20.0)
    assert k30 > k25 > k20
    # Bernacchi Kc Ea is large enough (79 430 J/mol) that the Arrhenius
    # ratio k(30 C) / k(25 C) is ~1.7 — well outside the typical 1.1-1.3
    # range seen with smaller Ea constants.  Accept 1.5..2.0.
    assert 1.5 < (k30 / k25) < 2.0


def test_kinetics_at_25c_matches_bernacchi_defaults() -> None:
    kin = kinetics_at(25.0)
    assert kin["Kc_umol_mol"] == pytest.approx(DEFAULT_CONSTANTS.kc_25_umol_mol, rel=1e-9)
    assert kin["Ko_mmol_mol"] == pytest.approx(DEFAULT_CONSTANTS.ko_25_mmol_mol, rel=1e-9)
    assert kin["Gamma_star_umol_mol"] == pytest.approx(
        DEFAULT_CONSTANTS.gamma_star_25_umol_mol, rel=1e-9
    )


def test_a_carbox_zero_at_gamma_star() -> None:
    """When Cc == Gamma*, the carboxylation rate should be zero
    (compensation point by definition)."""
    gs = 42.75
    ac = a_carbox(cc=gs, vcmax=60.0, kc=404.9, ko=278.4, gamma_star=gs)
    assert float(ac) == pytest.approx(0.0, abs=1e-9)


def test_a_carbox_scales_with_vcmax() -> None:
    ac1 = float(a_carbox(cc=300.0, vcmax=60.0, kc=404.9, ko=278.4, gamma_star=42.75))
    ac2 = float(a_carbox(cc=300.0, vcmax=120.0, kc=404.9, ko=278.4, gamma_star=42.75))
    assert ac2 == pytest.approx(2.0 * ac1, rel=1e-9)


def test_a_regen_zero_at_gamma_star() -> None:
    gs = 42.75
    aj = a_regen(cc=gs, j=150.0, gamma_star=gs)
    assert float(aj) == pytest.approx(0.0, abs=1e-9)


def test_a_regen_asymptotes_to_j_over_four() -> None:
    """As Cc → infinity, Aj → J/4 (RuBP-regen ceiling)."""
    j = 200.0
    aj_hi = float(a_regen(cc=1e6, j=j, gamma_star=42.75))
    assert aj_hi == pytest.approx(j / 4.0, rel=1e-3)


def test_a_tpu_infinite_when_none() -> None:
    assert math.isinf(a_tpu(None))
    assert a_tpu(10.0) == pytest.approx(30.0, rel=1e-9)


def test_predict_a_chooses_minimum_of_curves() -> None:
    """At low Cc, A should be carboxylation-limited; at high Cc,
    RuBP-regen-limited.  Returned limitation labels must reflect that."""
    cc = np.array([50.0, 100.0, 200.0, 400.0, 800.0, 1500.0])
    pred = predict_a(cc, vcmax=60.0, j=150.0, rd=1.5, tleaf_c=25.0)
    assert pred.a_net.shape == cc.shape
    # Low Cc → A_c lowest → carboxylation-limited.
    assert pred.limitation[0] == "carboxylation"
    # High Cc → A_j should win (or tie).
    assert pred.limitation[-1] == "rubp_regen"


def test_predict_a_respects_tpu_ceiling() -> None:
    cc = np.array([1000.0])
    pred = predict_a(cc, vcmax=60.0, j=300.0, rd=1.5, tleaf_c=25.0, tpu=5.0)
    # TPU = 5 → A_p = 15; should become the binding limitation at
    # high Cc with this Vcmax/J.
    assert pred.limitation[0] == "tpu"
    assert float(pred.a_net[0]) == pytest.approx(15.0 - 1.5, rel=1e-3)


def test_predict_a_subtracts_rd() -> None:
    pred_no_rd = predict_a(np.array([400.0]), vcmax=60.0, j=150.0, rd=0.0, tleaf_c=25.0)
    pred_rd = predict_a(np.array([400.0]), vcmax=60.0, j=150.0, rd=2.0, tleaf_c=25.0)
    assert float(pred_rd.a_net[0]) == pytest.approx(float(pred_no_rd.a_net[0]) - 2.0, rel=1e-9)


def test_cc_from_ci_matches_fick() -> None:
    # Ci - Cc = A / g_m.  A=10, g_m=0.5 → Ci-Cc=20.  Ci=250 → Cc=230.
    cc = cc_from_ci(ci=250.0, a_net=10.0, g_m=0.5)
    assert float(cc) == pytest.approx(230.0, rel=1e-9)


def test_cc_from_ci_rejects_non_positive_gm() -> None:
    with pytest.raises(ValueError):
        cc_from_ci(ci=250.0, a_net=10.0, g_m=0.0)
    with pytest.raises(ValueError):
        cc_from_ci(ci=250.0, a_net=10.0, g_m=float("nan"))
