"""Curated literature ranges for C3 / C4 morphology, gas-exchange,
flow, and g_m parameters — used to sanity-check measured values.

Every range is tagged with the photosynthesis type it applies to
("C3", "C4", "C3-C4", "CAM", or the pooled "any") so the validator
can pick the right comparator for each image / session.  Ranges
come from the standard review / primary sources cited inline; the
(min, typical, max) triple reflects the paper's stated range for
healthy well-watered plants at ~25 C and ambient CO2.

**Unit caveat for g_m and related rates**: published values use one
of several conventions.  We store ranges in the units the pipelines
emit (see each field's `unit` string) and document the conversion
factor when it's non-trivial — specifically:

- Our `g_m_proxy` (PR #13a) is mol m^-2 s^-1 Pa^-1 (SI).
- Our fitted `g_m` from Farquhar (PR #13b) is
  umol m^-2 s^-1 (umol/mol)^-1, which at 1 atm ~= 1 bar equals
  numerically the same as mol m^-2 s^-1 bar^-1 that Flexas 2008
  uses.  A value of 0.3 in our internal units is directly
  comparable to 0.3 in Flexas's tables.

For morphology metrics (S_mes/S, S_c/S, f_ias, T_cw) our values are
2-D raster proxies — Tosens et al. use light-microscopy corrected
2-D or TEM 3-D estimates.  We encode the 2-D proxy ranges that match
the pipeline's output, with a citation for the 3-D correction factor
operators can apply downstream.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class LiteratureRange:
    """A single parameter-range cell in the literature table.

    `parameter_key` matches the key used in `/compare`'s METRICS catalog
    so the validator can find the right range by key.  `applies_to`
    is the photosynthesis-type scope ("C3" | "C4" | "C3-C4" | "CAM"
    | "any").  `min`/`typical`/`max` are numeric bounds in the declared
    unit.  `source` is an author-year short citation; operators can
    look up the full bibliography in the dashboard's literature page.
    """

    parameter_key: str
    applies_to: str
    min: float
    typical: float
    max: float
    unit: str
    source: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Central table.  Extend as new references land; tests exercise the
# structural properties (monotonic min <= typical <= max, known
# parameter keys) so accidental data-entry errors are caught.
LITERATURE_RANGES: tuple[LiteratureRange, ...] = (
    # --- morphology (CO2-morphometrics, PR #10) ---
    LiteratureRange(
        parameter_key="co2_s_mes_s",
        applies_to="C3",
        min=8.0, typical=15.0, max=22.0,
        unit="-",
        source="Tosens et al. 2012 (Plant Physiol)",
        note="2-D cross-section S_mes/S for well-watered C3 leaves; "
             "Tosens's 3-D correction factor ~1.1-1.3",
    ),
    LiteratureRange(
        parameter_key="co2_s_mes_s",
        applies_to="C4",
        min=3.0, typical=5.0, max=8.0,
        unit="-",
        source="Tomás et al. 2013 (Plant Cell Environ)",
        note="C4 leaves have thinner mesophyll + bundle-sheath "
             "dominated geometry",
    ),
    LiteratureRange(
        parameter_key="co2_s_c_s",
        applies_to="C3",
        min=4.0, typical=10.0, max=18.0,
        unit="-",
        source="Tosens et al. 2012",
    ),
    LiteratureRange(
        parameter_key="co2_s_c_s",
        applies_to="C4",
        min=1.5, typical=3.0, max=5.0,
        unit="-",
        source="Tomás et al. 2013",
    ),
    LiteratureRange(
        parameter_key="co2_f_ias",
        applies_to="C3",
        min=0.15, typical=0.28, max=0.45,
        unit="-",
        source="Terashima et al. 2011 (J Plant Res)",
        note="Intercellular air-space fraction in mesophyll",
    ),
    LiteratureRange(
        parameter_key="co2_f_ias",
        applies_to="C4",
        min=0.05, typical=0.10, max=0.20,
        unit="-",
        source="Dengler & Nelson 1999 (C4 Plant Biology ch. 5)",
        note="C4 mesophyll is more densely packed",
    ),
    LiteratureRange(
        parameter_key="co2_t_cw_median_um",
        applies_to="C3",
        min=0.1, typical=0.2, max=0.5,
        unit="um",
        source="Evans et al. 2009 (Plant Cell Environ)",
        note="TEM-measured cell-wall thickness; our DT proxy is "
             "measured on light-microscopy polygons and skews larger",
    ),
    LiteratureRange(
        parameter_key="co2_t_cw_median_um",
        applies_to="C4",
        min=0.15, typical=0.3, max=0.6,
        unit="um",
        source="Evans et al. 2009",
    ),
    LiteratureRange(
        parameter_key="co2_mesophyll_thickness_median_um",
        applies_to="C3",
        min=80.0, typical=150.0, max=250.0,
        unit="um",
        source="Terashima et al. 2011",
    ),
    LiteratureRange(
        parameter_key="co2_mesophyll_thickness_median_um",
        applies_to="C4",
        min=40.0, typical=80.0, max=130.0,
        unit="um",
        source="Dengler & Nelson 1999",
    ),
    # --- Darcy hydraulic conductance (PR #12) ---
    LiteratureRange(
        parameter_key="darcy_k_leaf",
        applies_to="any",
        min=1.0e-14, typical=1.0e-12, max=1.0e-10,
        unit="kg/(s*Pa*m)",
        source="model-internal (PR #12 synthetic ranges)",
        note="K_leaf from our 2-D FV solver; absolute value depends "
             "on permeability overrides.  Ratios between groups are "
             "the research-meaningful signal, not the absolute value",
    ),
    # --- CO2 diffusion PDE (PR #13a) ---
    LiteratureRange(
        parameter_key="co2_g_m_proxy",
        applies_to="C3",
        min=0.05, typical=0.25, max=0.6,
        unit="mol/(m^2*s*Pa)",
        source="Flexas et al. 2008 (Plant Cell Environ) — review",
        note="PR #13a's geometry-only proxy; expect systematic offset "
             "from Flexas in-vivo values",
    ),
    LiteratureRange(
        parameter_key="co2_g_m_proxy",
        applies_to="C4",
        min=0.03, typical=0.15, max=0.4,
        unit="mol/(m^2*s*Pa)",
        source="Flexas et al. 2008",
    ),
    LiteratureRange(
        parameter_key="co2_cc_mean_pa",
        applies_to="any",
        min=5.0, typical=18.0, max=30.0,
        unit="Pa",
        source="Flexas et al. 2008",
        note="Chloroplastic CO2 at ambient Ci ~25 Pa, well-watered",
    ),
    LiteratureRange(
        parameter_key="co2_drawdown_mean_pa",
        applies_to="any",
        min=2.0, typical=8.0, max=20.0,
        unit="Pa",
        source="Flexas et al. 2008",
        note="Ci - Cc drawdown in typical C3/C4",
    ),
    # --- Farquhar fit parameters (PR #13b) ---
    # Keys with `fit_params.` prefix match the validation output path
    # for per-method GmFitResult values (see pipeline/validation.py).
    LiteratureRange(
        parameter_key="gm_fit.g_m",
        applies_to="C3",
        min=0.05, typical=0.25, max=0.6,
        unit="mol/(m^2*s)/(umol/mol)",
        source="Flexas et al. 2008 — review; equivalent to "
               "mol/m^2/s/bar at 1 atm",
    ),
    LiteratureRange(
        parameter_key="gm_fit.g_m",
        applies_to="C4",
        min=0.03, typical=0.15, max=0.4,
        unit="mol/(m^2*s)/(umol/mol)",
        source="Flexas et al. 2008",
    ),
    LiteratureRange(
        parameter_key="gm_fit.vcmax",
        applies_to="C3",
        min=30.0, typical=80.0, max=200.0,
        unit="umol/(m^2*s)",
        source="Wullschleger 1993 (J Exp Bot) — 109-species review",
    ),
    LiteratureRange(
        parameter_key="gm_fit.vcmax",
        applies_to="C4",
        min=20.0, typical=50.0, max=100.0,
        unit="umol/(m^2*s)",
        source="Wullschleger 1993 — C4 subset is smaller due to PEP "
               "handling the primary carboxylation",
    ),
    LiteratureRange(
        parameter_key="gm_fit.j_max",
        applies_to="C3",
        min=60.0, typical=160.0, max=300.0,
        unit="umol/(m^2*s)",
        source="Wullschleger 1993",
    ),
    LiteratureRange(
        parameter_key="gm_fit.j_max",
        applies_to="C4",
        min=100.0, typical=220.0, max=400.0,
        unit="umol/(m^2*s)",
        source="Wullschleger 1993",
    ),
    LiteratureRange(
        parameter_key="gm_fit.rd",
        applies_to="any",
        min=0.2, typical=1.2, max=3.0,
        unit="umol/(m^2*s)",
        source="Atkin et al. 2005 (New Phytol)",
        note="Leaf dark respiration at 25 C, well-watered",
    ),
    # --- Basic measurement + LI-COR spot checks ---
    LiteratureRange(
        parameter_key="leaf_mean_thickness_um",
        applies_to="C3",
        min=80.0, typical=180.0, max=350.0,
        unit="um",
        source="Poorter et al. 2009 (New Phytol) — meta-analysis",
    ),
    LiteratureRange(
        parameter_key="leaf_mean_thickness_um",
        applies_to="C4",
        min=50.0, typical=120.0, max=220.0,
        unit="um",
        source="Poorter et al. 2009",
    ),
)

# Indexed by parameter_key for O(1) lookup at validation time.
LITERATURE_BY_KEY: dict[str, list[LiteratureRange]] = {}
for _r in LITERATURE_RANGES:
    LITERATURE_BY_KEY.setdefault(_r.parameter_key, []).append(_r)


def find_range(
    parameter_key: str,
    photosynthesis_type: str | None,
) -> LiteratureRange | None:
    """Return the best-matching range for this parameter + photosynthesis
    type, or None if no range applies.

    Resolution order:
      1. Exact photosynthesis_type match (C3 / C4 / C3-C4 / CAM).
      2. "any" pooled range as fallback.
      3. None when neither is defined.
    """
    candidates = LITERATURE_BY_KEY.get(parameter_key, [])
    if not candidates:
        return None
    if photosynthesis_type:
        for r in candidates:
            if r.applies_to == photosynthesis_type:
                return r
    for r in candidates:
        if r.applies_to == "any":
            return r
    return None


def all_parameter_keys() -> list[str]:
    """Every parameter_key with at least one literature range."""
    return sorted(LITERATURE_BY_KEY.keys())
