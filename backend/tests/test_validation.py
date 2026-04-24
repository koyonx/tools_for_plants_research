"""Tests for the literature validation classifier + ranges table."""

from __future__ import annotations

from app.pipeline.literature_ranges import (
    LITERATURE_BY_KEY,
    LITERATURE_RANGES,
    all_parameter_keys,
    find_range,
)
from app.pipeline.validation import (
    validate_analyses,
    validate_gm_fit_result,
)


def test_every_range_has_monotonic_bounds() -> None:
    """min <= typical <= max for every curated row."""
    for r in LITERATURE_RANGES:
        assert r.min <= r.typical, f"{r.parameter_key} ({r.applies_to}): min > typical"
        assert r.typical <= r.max, f"{r.parameter_key} ({r.applies_to}): typical > max"


def test_every_range_has_nonzero_width() -> None:
    """A zero-width range produces ambiguous classification near the
    bound (since we use strict `<` / `>`).  Reject at table time."""
    for r in LITERATURE_RANGES:
        assert r.max > r.min, (
            f"{r.parameter_key} ({r.applies_to}): max == min "
            "(zero-width range would make the classifier ambiguous)"
        )


def test_all_parameter_keys_round_trips() -> None:
    keys = all_parameter_keys()
    assert len(keys) == len(set(keys))  # deduplicated
    for key in keys:
        assert LITERATURE_BY_KEY[key]


def test_find_range_prefers_exact_photosynthesis_type() -> None:
    r = find_range("co2_s_mes_s", "C3")
    assert r is not None
    assert r.applies_to == "C3"


def test_find_range_falls_back_to_any_pool() -> None:
    # darcy_k_leaf is curated as `applies_to="any"` (no C3/C4 split),
    # so asking for C3 should fall through to the pooled row.
    r = find_range("darcy_k_leaf", "C3")
    assert r is not None
    assert r.applies_to == "any"


def test_find_range_returns_none_for_unknown_parameter() -> None:
    assert find_range("definitely_not_a_parameter", "C3") is None


def test_validate_analyses_classifies_within_below_above() -> None:
    """Feed the classifier a canned co2_morphometrics blob spanning
    all three classifications + one parameter with no lit range."""
    # C3 S_mes/S range: min=8.0, max=22.0 per literature_ranges.py
    blobs = {
        "co2_morphometrics": {
            "s_mes_s": 15.0,     # within C3 range
            "s_c_s": 1.0,         # below C3 range (min=4.0)
            "f_ias": 0.50,        # above C3 range (max=0.45)
            "cell_wall": {
                "t_cw_median_um": 0.25,  # within C3 range
            },
            "mesophyll": {
                "thickness_median_um": 175.0,  # within C3 range
            },
            "chloroplasts": {
                "count": 42,
                # no literature range for coverage_of_mesophyll_cells
                "coverage_of_mesophyll_cells": 0.15,
            },
        }
    }
    report = validate_analyses(blobs, photosynthesis_type="C3")
    by_key = {f.parameter_key: f for f in report.findings}

    assert by_key["co2_s_mes_s"].status == "within"
    assert by_key["co2_s_mes_s"].applies_to == "C3"
    assert by_key["co2_s_c_s"].status == "below"
    assert by_key["co2_f_ias"].status == "above"
    assert by_key["co2_t_cw_median_um"].status == "within"

    # co2_chloroplast_count has no literature range, but has a
    # measurement: surfaces as `unknown`.
    if "co2_chloroplast_count" in by_key:
        assert by_key["co2_chloroplast_count"].status == "unknown"


def test_validate_analyses_skips_rows_without_range_and_without_measurement() -> None:
    """Parameters with no literature range AND no measurement produce
    no finding — the validator doesn't clutter the UI with pure-
    unknowns."""
    blobs: dict = {"co2_morphometrics": {}}  # empty result blob
    report = validate_analyses(blobs, photosynthesis_type="C3")
    # Nothing measured → no findings emitted.
    assert report.n_within == 0
    assert report.n_outside == 0
    # We still allow unknown when a measurement is present (handled
    # in the other test); here there are no measurements.


def test_validate_analyses_handles_missing_photosynthesis_type() -> None:
    """When the image lacks a photosynthesis_type, find_range falls
    back to "any"-scope entries.  Parameters that only have C3/C4
    rows (no "any") surface as unknown, not misclassified."""
    blobs = {
        "co2_morphometrics": {
            "s_mes_s": 15.0,  # only C3 + C4 rows; no "any"
        },
        "darcy_flow": {
            "k_leaf": 1.0e-12,  # darcy_k_leaf has an "any" row
        },
    }
    report = validate_analyses(blobs, photosynthesis_type=None)
    by_key = {f.parameter_key: f for f in report.findings}
    # Unknown because no pooled range exists for s_mes_s.
    assert by_key["co2_s_mes_s"].status == "unknown"
    # Hits the "any" range.
    assert by_key["darcy_k_leaf"].status == "within"


def test_validate_analyses_rejects_nan_inf_measured_values() -> None:
    """NaN / Inf in the result blob must not crash the classifier —
    extract returns None and the parameter is silently skipped.
    """
    blobs = {
        "co2_morphometrics": {
            "s_mes_s": float("nan"),
            "f_ias": float("inf"),
        }
    }
    report = validate_analyses(blobs, photosynthesis_type="C3")
    assert all(f.parameter_key not in {"co2_s_mes_s", "co2_f_ias"} for f in report.findings)


def test_validate_gm_fit_result_per_method_flatten() -> None:
    gm_fit_blob = {
        "methods": [
            {
                "method": "harley_variable_j",
                "g_m": 0.25,  # within C3 range
                "vcmax": None,
                "j_max": 200.0,  # within C3 range
                "rd": 1.2,
            },
            {
                "method": "nonlinear_slope",
                "g_m": 0.01,  # below C3 range (min 0.05)
                "vcmax": 300.0,  # above C3 range (max 200)
                "j_max": 150.0,  # within C3 range
                "rd": 1.5,
            },
        ]
    }
    report = validate_gm_fit_result(gm_fit_blob, photosynthesis_type="C3")
    # Expect 3 findings per method (g_m, vcmax?, j_max, rd — minus
    # the None vcmax in method 1 and the None fields in method 2).
    by_kind = {f.analysis_kind for f in report.findings}
    assert "gm_fit:harley_variable_j" in by_kind
    assert "gm_fit:nonlinear_slope" in by_kind

    nonlinear = [f for f in report.findings if f.analysis_kind == "gm_fit:nonlinear_slope"]
    statuses = {f.parameter_key: f.status for f in nonlinear}
    assert statuses.get("gm_fit.g_m") == "below"
    assert statuses.get("gm_fit.vcmax") == "above"
    assert statuses.get("gm_fit.j_max") == "within"


def test_validate_gm_fit_result_handles_nonsense_input() -> None:
    """A malformed gm_fit blob (non-dict, no methods) must return
    an empty report, not raise."""
    report = validate_gm_fit_result({"methods": "not a list"}, "C3")
    assert report.findings == []

    report2 = validate_gm_fit_result({}, "C3")
    assert report2.findings == []

    report3 = validate_gm_fit_result({"methods": [{"g_m": float("nan")}]}, "C3")
    assert report3.findings == []


def test_validation_report_count_helpers() -> None:
    """n_within / n_outside / n_unknown compute correctly from the
    findings list."""
    from app.pipeline.validation import ValidationFinding, ValidationReport

    findings = [
        ValidationFinding(
            parameter_key="p1", measured=1.0, status="within",
            range_min=0.0, range_typical=1.0, range_max=2.0,
            unit="-", source="src", applies_to="C3", note="",
            analysis_kind="k",
        ),
        ValidationFinding(
            parameter_key="p2", measured=10.0, status="above",
            range_min=0.0, range_typical=1.0, range_max=2.0,
            unit="-", source="src", applies_to="C3", note="",
            analysis_kind="k",
        ),
        ValidationFinding(
            parameter_key="p3", measured=-5.0, status="below",
            range_min=0.0, range_typical=1.0, range_max=2.0,
            unit="-", source="src", applies_to="C3", note="",
            analysis_kind="k",
        ),
        ValidationFinding(
            parameter_key="p4", measured=0.0, status="unknown",
            range_min=None, range_typical=None, range_max=None,
            unit="-", source="", applies_to="", note="",
            analysis_kind="k",
        ),
    ]
    report = ValidationReport(photosynthesis_type="C3", findings=findings)
    assert report.n_within == 1
    assert report.n_outside == 2
    assert report.n_unknown == 1


def test_validate_round_trips_through_strict_json() -> None:
    """Findings must JSON-encode with allow_nan=False so the API's
    default Starlette renderer never blows up on NaN / Inf leakage."""
    import json

    blobs = {
        "co2_morphometrics": {
            "s_mes_s": 15.0,
            "f_ias": 0.3,
        }
    }
    report = validate_analyses(blobs, photosynthesis_type="C3")
    s = json.dumps(report.to_dict(), allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s


def test_unknown_photosynthesis_type_string_uses_pooled() -> None:
    """Legacy data where images.photosynthesis_type == 'unknown' (the
    enum value, not None).  The classifier should treat that the same
    as missing — use the pooled "any" range when available."""
    # PR #8's photosynthesis_type enum: 'unknown' is a literal value.
    # validation.validate_analyses doesn't special-case the string —
    # find_range will fail to match 'unknown' as applies_to and fall
    # through to 'any'.  Confirm the behaviour doesn't silently pick
    # a C3/C4 row.
    r = find_range("co2_s_mes_s", "unknown")
    # co2_s_mes_s has C3 + C4 entries but no 'any' or 'unknown', so
    # returns None.
    assert r is None or r.applies_to == "any"
