"""Tests for the compare-export renderers (Markdown + CSV).

We only exercise the pure-serialisation helpers (_render_markdown,
_render_csv) — the outer endpoint is a Supabase/PostgREST
orchestration and is covered indirectly by the live compare tests.
"""

from __future__ import annotations

import csv
import io

from app.api.compare_export import _render_csv, _render_markdown


def _sample_rows() -> list[dict]:
    """Two metric rows covering: within/above classification, lit
    range available vs missing, and a wide range of numeric magnitudes
    for the formatter to handle."""
    return [
        {
            "metric": {
                "key": "co2_s_mes_s",
                "label": "S_mes/S",
                "unit": "-",
                "analysis_kind": "co2_morphometrics",
                "path": ["s_mes_s"],
            },
            "group_a": {
                "n": 12,
                "mean": 14.8, "sd": 2.1, "median": 15.0,
                "q25": 13.5, "q75": 16.5, "min": 10.0, "max": 20.0,
                "image_ids": [],
                "values": [],
            },
            "group_b": {
                "n": 9,
                "mean": 5.2, "sd": 1.3, "median": 5.0,
                "q25": 4.0, "q75": 6.0, "min": 3.0, "max": 7.5,
                "image_ids": [],
                "values": [],
            },
            "tests": {
                "welch_t_statistic": 8.2,
                "welch_p_value": 1.5e-6,
                "mann_whitney_u": 4.0,
                "mann_whitney_p_value": 2.3e-5,
            },
            "effect_size": {
                "cohens_d": 5.1,
                "hedges_g": 4.95,
                "hedges_g_ci_low": 3.2,
                "hedges_g_ci_high": 6.7,
            },
        },
        {
            "metric": {
                "key": "darcy_k_leaf",
                "label": "K_leaf",
                "unit": "kg/(s·Pa·m)",
                "analysis_kind": "darcy_flow",
                "path": ["k_leaf"],
            },
            "group_a": {
                "n": 6,
                "mean": 5e-13, "sd": 1e-13, "median": 4.5e-13,
                "q25": 3.0e-13, "q75": 6.0e-13,
                "min": 2.0e-13, "max": 8.0e-13,
                "image_ids": [],
                "values": [],
            },
            "group_b": {
                "n": 5,
                "mean": 3e-13, "sd": 0.8e-13, "median": 2.8e-13,
                "q25": 2.1e-13, "q75": 3.5e-13,
                "min": 1.8e-13, "max": 4.2e-13,
                "image_ids": [],
                "values": [],
            },
            "tests": {
                "welch_t_statistic": 2.1,
                "welch_p_value": 0.06,
                "mann_whitney_u": 10.0,
                "mann_whitney_p_value": 0.08,
            },
            "effect_size": {
                "cohens_d": 1.4,
                "hedges_g": 1.3,
                "hedges_g_ci_low": None,
                "hedges_g_ci_high": None,
            },
        },
    ]


def test_markdown_renders_with_headers_and_one_row_per_metric() -> None:
    md = _render_markdown(
        group_a_filter={"photosynthesis_type": "C3"},
        group_b_filter={"photosynthesis_type": "C4"},
        metric_rows=_sample_rows(),
    )
    # Structural checks — just make sure the output looks like
    # Markdown with the expected sections + tables.
    assert md.startswith("# Compare Report")
    assert "## Group definitions" in md
    assert "## Per-metric comparison" in md
    # One row per metric in the comparison table.
    body_lines = [ln for ln in md.splitlines() if ln.startswith("| S_mes/S ")]
    assert len(body_lines) == 1
    # Literature status column gets populated (C3 within → "within";
    # C4 within → "within" for S_mes/S=5.0).
    row = body_lines[0]
    assert "within" in row


def test_markdown_marks_out_of_range_medians() -> None:
    """Patch group B's median for S_mes/S to a C4-out-of-range value
    and confirm the literature-status column reflects 'below'."""
    rows = _sample_rows()
    # C4 S_mes/S range is [3, 8]; put group B below it.
    rows[0]["group_b"]["median"] = 1.0
    md = _render_markdown(
        group_a_filter={"photosynthesis_type": "C3"},
        group_b_filter={"photosynthesis_type": "C4"},
        metric_rows=rows,
    )
    row = next(ln for ln in md.splitlines() if ln.startswith("| S_mes/S "))
    assert "below" in row


def test_csv_renders_with_stable_header_order() -> None:
    out = _render_csv(
        group_a_filter={"photosynthesis_type": "C3"},
        group_b_filter={"photosynthesis_type": "C4"},
        metric_rows=_sample_rows(),
    )
    reader = csv.reader(io.StringIO(out))
    rows = list(reader)
    header = rows[0]
    assert header[0] == "metric_key"
    assert "welch_p" in header
    assert "hedges_g" in header
    # One data row per metric.
    assert len(rows) == 1 + len(_sample_rows())
    # Values column positions stay stable.
    key_idx = header.index("metric_key")
    assert rows[1][key_idx] == "co2_s_mes_s"


def test_csv_does_not_leak_literal_nan_string() -> None:
    """`csv.writer` writes None as empty string.  Confirm the output
    has no literal 'NaN' / 'None' tokens that would break downstream
    pandas/Excel import."""
    rows = _sample_rows()
    # Set some stats to None to exercise empty-cell behaviour.
    rows[1]["effect_size"]["hedges_g_ci_low"] = None
    rows[1]["effect_size"]["hedges_g_ci_high"] = None
    out = _render_csv(
        group_a_filter={"photosynthesis_type": "C3"},
        group_b_filter={"photosynthesis_type": "C4"},
        metric_rows=rows,
    )
    assert "NaN" not in out
    assert "None" not in out


def test_markdown_no_range_label_shown_for_unknown_types() -> None:
    """When the filter has no photosynthesis_type, every metric with
    a C3/C4-only literature entry should report 'no range' rather
    than silently mis-classifying against a fallback."""
    md = _render_markdown(
        group_a_filter={},  # no photosynthesis_type
        group_b_filter={"photosynthesis_type": "C4"},
        metric_rows=_sample_rows(),
    )
    # S_mes/S has only C3 + C4 rows (no "any"), so group A → no range
    row = next(ln for ln in md.splitlines() if ln.startswith("| S_mes/S "))
    assert "no range" in row
