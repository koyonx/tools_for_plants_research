"""Farquhar-von Caemmerer-Berry (FvCB) photosynthesis model.

The standard C3 biochemical model for A-Cc curves:

    A_c  = Vcmax * (Cc - Gamma_star) / (Cc + Kc * (1 + O / Ko))       # Rubisco-limited
    A_j  = J     * (Cc - Gamma_star) / (4 * Cc + 8 * Gamma_star)      # RuBP-regen-limited
    A_p  = 3 * TPU                                                     # Triose-phosphate-limited
    A    = min(A_c, A_j, A_p) - Rd

Constants follow Bernacchi et al. 2001 (PCE) — the reference set used
throughout the g_m literature:

    Kc(25 C)     = 404.9 µmol mol^-1        Ea = 79 430 J/mol
    Ko(25 C)     = 278.4  mmol mol^-1       Ea = 36 380 J/mol
    Gamma*(25 C) =  42.75 µmol mol^-1       Ea = 37 830 J/mol
    Rd  Ea       =  46 390 J/mol
    Vcmax Ea     =  65 330 J/mol            (deactivation not used here
                                             — typical leaf temps 15-35 C)
    J     Ea     =  43 790 J/mol

These are temperature-corrected via the simple Arrhenius form
`k(T) = k(25 C) * exp(Ea / R * (1/298.15 - 1/T))`, accurate to
< 5 % over the 15-35 C range that covers typical LI-COR
measurements.  Peaked functions (with high-T deactivation) are
available in the literature but not needed for this PR's regression
target; operators can override any constant.

All public functions take SI-ish units:

    Cc, Ci, Kc, Gamma_star: µmol / mol (i.e. ppm, partial-pressure
        units at 1 atm).
    Vcmax, J, Rd, A:        µmol m^-2 s^-1 (standard gas-exchange).
    O (oxygen):             mmol / mol (~210 for ambient air).
    Tleaf:                  degrees Celsius.

The model output A is in µmol m^-2 s^-1 and agrees with LI-COR
`Photo` / `A` column units directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

# Universal gas constant, J / (mol K).
R_GAS = 8.314462618
# Reference temperature for rate constants, Kelvin (= 25 C).
T_REF_K = 298.15
# Ambient oxygen partial-pressure proxy: 21% O2 at 1 atm = 210 mmol/mol.
DEFAULT_O2_MMOL_MOL = 210.0


# Bernacchi 2001 reference values + activation energies.
# Keep these as a single dataclass so operators can construct alt
# parameter sets (e.g. C4 plants with different kinetics) without
# touching the fitting code.
@dataclass(frozen=True)
class FarquharConstants:
    # Reference (25 C) values.
    kc_25_umol_mol: float = 404.9
    ko_25_mmol_mol: float = 278.4
    gamma_star_25_umol_mol: float = 42.75
    # Activation energies (J / mol).
    ea_kc: float = 79430.0
    ea_ko: float = 36380.0
    ea_gamma_star: float = 37830.0
    ea_vcmax: float = 65330.0
    ea_j: float = 43790.0
    ea_rd: float = 46390.0


DEFAULT_CONSTANTS = FarquharConstants()

LimitationLabel = Literal["carboxylation", "rubp_regen", "tpu"]


def arrhenius(k_25: float, ea_j_mol: float, tleaf_c: float) -> float:
    """Simple Arrhenius temperature correction.

    k(T) = k(25 C) * exp(Ea / R * (1/298.15 - 1/T))
    """
    t_k = tleaf_c + 273.15
    return float(k_25 * np.exp(ea_j_mol / R_GAS * (1.0 / T_REF_K - 1.0 / t_k)))


def kinetics_at(
    tleaf_c: float,
    constants: FarquharConstants = DEFAULT_CONSTANTS,
) -> dict[str, float]:
    """Return the temperature-corrected Kc, Ko, Gamma_star at `tleaf_c`.

    Keeps Vcmax / J / Rd out of this return since those are
    fitted-per-session quantities; only the Rubisco kinetic constants
    are fixed-across-samples and temperature-corrected here.  Operators
    who want temperature-corrected Vcmax / J / Rd can apply
    :func:`arrhenius` directly with the Ea values from ``constants``.
    """
    return {
        "Kc_umol_mol": arrhenius(constants.kc_25_umol_mol, constants.ea_kc, tleaf_c),
        "Ko_mmol_mol": arrhenius(constants.ko_25_mmol_mol, constants.ea_ko, tleaf_c),
        "Gamma_star_umol_mol": arrhenius(
            constants.gamma_star_25_umol_mol, constants.ea_gamma_star, tleaf_c
        ),
    }


def _array(x: float | np.ndarray) -> np.ndarray:
    return np.asarray(x, dtype=np.float64)


def a_carbox(
    cc: float | np.ndarray,
    vcmax: float,
    kc: float,
    ko: float,
    gamma_star: float,
    o2_mmol_mol: float = DEFAULT_O2_MMOL_MOL,
) -> np.ndarray:
    """Rubisco-limited gross CO2 assimilation rate.

    Ac = Vcmax * (Cc - Gamma*) / (Cc + Kc * (1 + O/Ko))

    Cc, Kc, Gamma*: µmol/mol.  Ko, O: mmol/mol.  (O/Ko is dimensionless
    once the unit ratio is consistent.)  Vcmax: µmol/m²/s.
    """
    cc_a = _array(cc)
    denom = cc_a + kc * (1.0 + o2_mmol_mol / ko)
    return vcmax * (cc_a - gamma_star) / denom


def a_regen(
    cc: float | np.ndarray,
    j: float,
    gamma_star: float,
) -> np.ndarray:
    """RuBP-regeneration-limited gross assimilation rate.

    Aj = J * (Cc - Gamma*) / (4 Cc + 8 Gamma*)
    """
    cc_a = _array(cc)
    return j * (cc_a - gamma_star) / (4.0 * cc_a + 8.0 * gamma_star)


def a_tpu(tpu: float | None) -> float:
    """Triose-phosphate-utilisation-limited gross assimilation.
    Ap = 3 * TPU.  Returns +inf when TPU is None so min() ignores it."""
    if tpu is None:
        return float("inf")
    return 3.0 * tpu


@dataclass(frozen=True)
class FarquharPrediction:
    """Predicted A and which limitation is active at each Cc point."""

    a_gross: np.ndarray
    a_net: np.ndarray  # a_gross - Rd
    a_c: np.ndarray
    a_j: np.ndarray
    a_p: np.ndarray | None
    limitation: list[str]   # LimitationLabel per point


def predict_a(
    cc: float | np.ndarray,
    vcmax: float,
    j: float,
    rd: float,
    tleaf_c: float,
    tpu: float | None = None,
    o2_mmol_mol: float = DEFAULT_O2_MMOL_MOL,
    constants: FarquharConstants = DEFAULT_CONSTANTS,
) -> FarquharPrediction:
    """Predict net A from chloroplastic CO2 (Cc) using the full FvCB
    model (Ac / Aj / Ap, take the minimum, subtract Rd).

    Returns the gross and net A arrays plus each limitation curve so
    downstream code can diagnose which regime the fit landed in.
    """
    kin = kinetics_at(tleaf_c, constants)
    ac = a_carbox(
        cc,
        vcmax=vcmax,
        kc=kin["Kc_umol_mol"],
        ko=kin["Ko_mmol_mol"],
        gamma_star=kin["Gamma_star_umol_mol"],
        o2_mmol_mol=o2_mmol_mol,
    )
    aj = a_regen(cc, j=j, gamma_star=kin["Gamma_star_umol_mol"])
    ap_val = a_tpu(tpu)
    ap_arr: np.ndarray | None = (
        np.full_like(ac, ap_val) if np.isfinite(ap_val) else None
    )
    a_gross = (
        np.minimum(np.minimum(ac, aj), ap_arr)
        if ap_arr is not None
        else np.minimum(ac, aj)
    )
    a_net = a_gross - rd
    # Limitation labels — index of the argmin in (ac, aj, ap).
    stack = np.stack(
        [ac, aj, ap_arr if ap_arr is not None else np.full_like(ac, np.inf)], axis=0
    )
    lim_idx = np.argmin(stack, axis=0)
    labels: list[str] = []
    for i in np.atleast_1d(lim_idx):
        if int(i) == 0:
            labels.append("carboxylation")
        elif int(i) == 1:
            labels.append("rubp_regen")
        else:
            labels.append("tpu")
    return FarquharPrediction(
        a_gross=np.atleast_1d(a_gross),
        a_net=np.atleast_1d(a_net),
        a_c=np.atleast_1d(ac),
        a_j=np.atleast_1d(aj),
        a_p=ap_arr,
        limitation=labels,
    )


def cc_from_ci(
    ci: float | np.ndarray,
    a_net: float | np.ndarray,
    g_m: float,
) -> np.ndarray:
    """Chloroplastic CO2 from intercellular CO2 and net A.

    Ci - Cc = A / g_m  (Fick's law across the mesophyll layer).  A is
    the NET assimilation (µmol/m²/s), g_m is mesophyll conductance
    (µmol m^-2 s^-1 (µmol/mol)^-1 == dimensionless once Ci and Cc are
    in the same µmol/mol units and A is µmol/m²/s).  Returned in the
    same unit as Ci (µmol/mol).
    """
    ci_a = _array(ci)
    a_a = _array(a_net)
    if g_m <= 0 or not np.isfinite(g_m):
        raise ValueError("g_m must be a positive finite number")
    return ci_a - a_a / g_m  # type: ignore[no-any-return]
