"""Report export for the /compare dashboard.

Takes the same `CompareRequest` shape the main comparison endpoint
consumes, runs the comparison, and serialises the result into a
Markdown report or CSV table suitable for direct inclusion in a
paper / supplemental info.  Each metric row is annotated with its
literature range (via `pipeline/literature_ranges`) so the operator
can see at a glance which measurements land in published bands.

We duplicate a small amount of the /compare orchestration rather
than factor it to a shared helper — keeps the main comparison
endpoint readable and lets the export evolve its own truncation /
formatting choices without ripple-through.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import Field

from app.api.compare import (
    METRICS_BY_KEY,
    CompareRequest,
    MetricDef,
    _extract,
    _latest_done_in,
    _resolve_group,
)
from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError
from app.pipeline.literature_ranges import find_range
from app.pipeline.stats import compare

router = APIRouter()


class CompareExportRequest(CompareRequest):
    """Same body as /compare + an output format selector."""

    format: str = Field(default="markdown", pattern="^(markdown|csv)$")


def _extract_jwt(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return authorization.split(" ", 1)[1]


def _fmt(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "—"
    if abs(value) < 1e-3 or abs(value) >= 1e4:
        return f"{value:.{digits}e}"
    return f"{value:.{digits}f}"


def _lit_note(metric: MetricDef, group_type: str | None, median: float | None) -> str:
    """Return a short validation flag for a group's median value:
    "within", "below (min=..)", "above (max=..)", or "no range".
    """
    range_ = find_range(metric.key, group_type)
    if range_ is None:
        return "no range"
    if median is None:
        return "—"
    if median < range_.min:
        return f"below (min={_fmt(range_.min)})"
    if median > range_.max:
        return f"above (max={_fmt(range_.max)})"
    return "within"


def _render_markdown(
    group_a_filter: dict[str, Any],
    group_b_filter: dict[str, Any],
    metric_rows: list[dict[str, Any]],
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    ga_type = group_a_filter.get("photosynthesis_type")
    gb_type = group_b_filter.get("photosynthesis_type")
    parts: list[str] = []
    parts.append("# Compare Report")
    parts.append("")
    parts.append(f"Generated at `{now}` UTC.")
    parts.append("")
    parts.append("## Group definitions")
    parts.append("")
    parts.append("| Group | Filter | N images |")
    parts.append("|---|---|---|")
    parts.append(
        f"| A | `{group_a_filter}` | {metric_rows[0]['group_a']['n'] if metric_rows else '—'} |"
    )
    parts.append(
        f"| B | `{group_b_filter}` | {metric_rows[0]['group_b']['n'] if metric_rows else '—'} |"
    )
    parts.append("")
    parts.append("## Per-metric comparison")
    parts.append("")
    parts.append(
        "| Metric | Unit | A median | B median | "
        "Welch p | MW p | Hedges g (95% CI) | A vs lit | B vs lit |"
    )
    parts.append(
        "|---|---|---|---|---|---|---|---|---|"
    )
    for row in metric_rows:
        m = row["metric"]
        a = row["group_a"]
        b = row["group_b"]
        g = row["effect_size"]["hedges_g"]
        g_lo = row["effect_size"]["hedges_g_ci_low"]
        g_hi = row["effect_size"]["hedges_g_ci_high"]
        g_cell = _fmt(g) + (
            f" [{_fmt(g_lo)}, {_fmt(g_hi)}]"
            if g_lo is not None and g_hi is not None
            else ""
        )
        parts.append(
            "| "
            + " | ".join(
                [
                    m["label"],
                    m["unit"],
                    _fmt(a.get("median")),
                    _fmt(b.get("median")),
                    _fmt(row["tests"].get("welch_p_value")),
                    _fmt(row["tests"].get("mann_whitney_p_value")),
                    g_cell,
                    _lit_note(
                        METRICS_BY_KEY[m["key"]],
                        ga_type,
                        a.get("median"),
                    ),
                    _lit_note(
                        METRICS_BY_KEY[m["key"]],
                        gb_type,
                        b.get("median"),
                    ),
                ]
            )
            + " |"
        )
    parts.append("")
    parts.append("Legend: `within` = group median lies inside the published literature range for "
                 "its photosynthesis type; `above` / `below` = measurement sits outside the "
                 "cited band; `no range` = no literature reference for this parameter / type.")
    return "\n".join(parts) + "\n"


def _render_csv(
    group_a_filter: dict[str, Any],
    group_b_filter: dict[str, Any],
    metric_rows: list[dict[str, Any]],
) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "metric_key",
            "metric_label",
            "unit",
            "group_a_n",
            "group_a_median",
            "group_a_mean",
            "group_a_sd",
            "group_a_lit_status",
            "group_b_n",
            "group_b_median",
            "group_b_mean",
            "group_b_sd",
            "group_b_lit_status",
            "welch_p",
            "mann_whitney_p",
            "hedges_g",
            "hedges_g_ci_low",
            "hedges_g_ci_high",
        ]
    )
    ga_type = group_a_filter.get("photosynthesis_type")
    gb_type = group_b_filter.get("photosynthesis_type")
    for row in metric_rows:
        m = row["metric"]
        a = row["group_a"]
        b = row["group_b"]
        writer.writerow(
            [
                m["key"],
                m["label"],
                m["unit"],
                a.get("n"),
                a.get("median"),
                a.get("mean"),
                a.get("sd"),
                _lit_note(METRICS_BY_KEY[m["key"]], ga_type, a.get("median")),
                b.get("n"),
                b.get("median"),
                b.get("mean"),
                b.get("sd"),
                _lit_note(METRICS_BY_KEY[m["key"]], gb_type, b.get("median")),
                row["tests"].get("welch_p_value"),
                row["tests"].get("mann_whitney_p_value"),
                row["effect_size"].get("hedges_g"),
                row["effect_size"].get("hedges_g_ci_low"),
                row["effect_size"].get("hedges_g_ci_high"),
            ]
        )
    return buf.getvalue()


@router.post("/compare/export")
async def export_compare(
    payload: CompareExportRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> PlainTextResponse:
    """Run a comparison and stream it out as Markdown or CSV.

    The endpoint returns `text/markdown` or `text/csv` directly so the
    frontend can trigger a download without an extra JSON→blob step.
    """
    selected: list[MetricDef] = []
    for key in payload.metrics:
        m = METRICS_BY_KEY.get(key)
        if m is None:
            raise HTTPException(status_code=400, detail=f"unknown metric {key!r}")
        selected.append(m)

    sb = SupabaseAuthedClient(_extract_jwt(authorization))
    try:
        images_a = await _resolve_group(sb, payload.group_a)
        images_b = await _resolve_group(sb, payload.group_b)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    if not images_a or not images_b:
        raise HTTPException(
            status_code=422,
            detail="one or both groups resolved to zero images; widen the filters",
        )

    union_ids = list({*(i["id"] for i in images_a), *(i["id"] for i in images_b)})
    kinds_needed = sorted({m.analysis_kind for m in selected})
    rows_by_kind: dict[str, list[dict[str, Any]]] = {}
    for kind in kinds_needed:
        try:
            rows_by_kind[kind] = await _latest_done_in(sb, union_ids, kind)
        except SupabaseHttpError as e:
            raise HTTPException(status_code=e.status, detail=e.detail) from e

    metric_rows: list[dict[str, Any]] = []
    for m in selected:
        rows = rows_by_kind.get(m.analysis_kind, [])
        latest_by_image: dict[str, dict[str, Any]] = {}
        for row in rows:
            iid = str(row["image_id"])
            if iid not in latest_by_image:
                latest_by_image[iid] = row

        a_values: list[float] = []
        for img in images_a:
            a_row = latest_by_image.get(img["id"])
            if a_row is None:
                continue
            v = _extract(a_row.get("result"), m.path)
            if v is not None:
                a_values.append(v)
        b_values: list[float] = []
        for img in images_b:
            b_row = latest_by_image.get(img["id"])
            if b_row is None:
                continue
            v = _extract(b_row.get("result"), m.path)
            if v is not None:
                b_values.append(v)

        result = compare(a_values, b_values, bootstrap_iters=payload.bootstrap_iters)
        metric_rows.append(
            {
                "metric": m.to_dict(),
                "group_a": result.group_a.to_dict(),
                "group_b": result.group_b.to_dict(),
                "tests": {
                    "welch_t_statistic": result.welch_t_statistic,
                    "welch_p_value": result.welch_p_value,
                    "mann_whitney_u": result.mann_whitney_u,
                    "mann_whitney_p_value": result.mann_whitney_p_value,
                },
                "effect_size": {
                    "cohens_d": result.cohens_d,
                    "hedges_g": result.hedges_g,
                    "hedges_g_ci_low": result.hedges_g_ci_low,
                    "hedges_g_ci_high": result.hedges_g_ci_high,
                },
            }
        )

    group_a_filter = payload.group_a.model_dump(exclude_none=True)
    group_b_filter = payload.group_b.model_dump(exclude_none=True)

    if payload.format == "markdown":
        body = _render_markdown(group_a_filter, group_b_filter, metric_rows)
        return PlainTextResponse(
            content=body,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="compare-report.md"'},
        )
    body = _render_csv(group_a_filter, group_b_filter, metric_rows)
    return PlainTextResponse(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="compare-report.csv"'},
    )
