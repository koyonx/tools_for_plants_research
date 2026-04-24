"""Statistical comparison between two groups of images.

Workflow
--------
1. Client picks a pair of filters (e.g. `{photosynthesis_type: "C3"}`
   vs `{photosynthesis_type: "C4"}`) and a list of metrics to compare.
2. We resolve each group to a concrete list of image ids via PostgREST
   (RLS-filtered, so users can only compare images they can read).
3. For each metric we pull the latest `done` `analyses` row of the
   required `kind`, extract the scalar value via the metric's JSON
   path, and feed both groups into `pipeline/stats.compare()`.
4. The response bundles per-metric summary stats + Welch + Mann-Whitney
   + Cohen's d + Hedges' g (with bootstrap 95% CI), plus the raw
   values so the frontend can draw box-plots / jitter.

The metric catalogue (`METRICS`) is static — it's the same handful of
scalar outputs our pipelines already produce.  It lives in
`app.pipeline.metric_catalog` so pipeline validators can reuse it
without dragging in FastAPI; add new entries there.  The frontend
reads the catalog via `GET /compare/metrics`.
"""

from __future__ import annotations

import math
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError
from app.pipeline.metric_catalog import METRICS, METRICS_BY_KEY, MetricDef
from app.pipeline.stats import compare

router = APIRouter()

# Raw per-value arrays ride in the response so the frontend can draw
# boxplots without another round-trip.  Cap at 2000 per group to keep
# the payload bounded when a user compares large cohorts — summary
# stats are still computed on the full array before truncation.
MAX_RAW_VALUES_PER_GROUP = 2000

# `MetricDef`, `METRICS`, `METRICS_BY_KEY` live in
# `app.pipeline.metric_catalog` so pipeline validators can reach the
# catalog without importing FastAPI.  Re-exported here for callers
# that still use `from app.api.compare import ...`.
__all__ = ["METRICS", "METRICS_BY_KEY", "MetricDef"]


class GroupFilter(BaseModel):
    """Column equality filters against the `images` table.  Empty /
    None fields are ignored.  Server-side we translate each non-None
    field into `eq.<value>`.  Values that happen to share between
    groups (e.g. comparing treatments within the same species) are
    supported naturally."""

    species: str | None = None
    photosynthesis_type: str | None = None
    plant_id: str | None = None
    treatment: str | None = None


class CompareRequest(BaseModel):
    group_a: GroupFilter
    group_b: GroupFilter
    # Bumped from 20 to 40 in PR #12 (Darcy) — the METRICS catalog
    # grew past 20 with the gas-exchange + darcy additions, and the
    # dashboard should be able to request them all in one go.
    metrics: list[str] = Field(..., min_length=1, max_length=40)
    bootstrap_iters: int = Field(default=2000, gt=100, le=20_000)


def _extract_jwt(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return authorization.split(" ", 1)[1]


def _extract(value: Any, path: tuple[str, ...]) -> float | None:
    cur: Any = value
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
        if cur is None:
            return None
    try:
        v = float(cur)
    except (TypeError, ValueError):
        return None
    return v if _is_finite(v) else None


def _is_finite(v: float) -> bool:
    return math.isfinite(v)


def _truncate_for_payload(values: list[float]) -> tuple[list[float], bool]:
    """Cap raw values to MAX_RAW_VALUES_PER_GROUP via stride sampling.

    Summary stats are computed on the full array BEFORE this call, so
    truncation only affects the jittered scatter overlay the frontend
    draws on top of the boxplot — it doesn't bias mean/median/p-values.
    """
    if len(values) <= MAX_RAW_VALUES_PER_GROUP:
        return values, False
    stride = max(1, len(values) // MAX_RAW_VALUES_PER_GROUP)
    return values[::stride][:MAX_RAW_VALUES_PER_GROUP], True


@router.get("/compare/metrics")
def list_metrics() -> dict[str, Any]:
    return {"metrics": [m.to_dict() for m in METRICS]}


@router.post("/compare")
async def run_compare(
    payload: CompareRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    # Resolve metric defs up-front so a typo surfaces as 400 instead of
    # a silently-empty comparison.
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

    # Batch-fetch latest done analyses per kind across the union of
    # both groups.  One round-trip per kind rather than per image.
    union_ids = list({*(i["id"] for i in images_a), *(i["id"] for i in images_b)})
    kinds_needed = sorted({m.analysis_kind for m in selected})
    rows_by_kind: dict[str, list[dict[str, Any]]] = {}
    for kind in kinds_needed:
        try:
            rows_by_kind[kind] = await _latest_done_in(sb, union_ids, kind)
        except SupabaseHttpError as e:
            raise HTTPException(status_code=e.status, detail=e.detail) from e

    metric_results: list[dict[str, Any]] = []
    for m in selected:
        rows = rows_by_kind.get(m.analysis_kind, [])
        latest_by_image: dict[str, dict[str, Any]] = {}
        for row in rows:
            image_id = str(row["image_id"])
            # rows come ordered by created_at desc; keep the first
            prev = latest_by_image.get(image_id)
            if prev is None:
                latest_by_image[image_id] = row

        a_values: list[float] = []
        a_image_ids: list[str] = []
        for img in images_a:
            a_row = latest_by_image.get(img["id"])
            if a_row is None:
                continue
            v = _extract(a_row.get("result"), m.path)
            if v is None:
                continue
            a_values.append(v)
            a_image_ids.append(img["id"])

        b_values: list[float] = []
        b_image_ids: list[str] = []
        for img in images_b:
            b_row = latest_by_image.get(img["id"])
            if b_row is None:
                continue
            v = _extract(b_row.get("result"), m.path)
            if v is None:
                continue
            b_values.append(v)
            b_image_ids.append(img["id"])

        result = compare(a_values, b_values, bootstrap_iters=payload.bootstrap_iters)
        a_values_out, a_truncated = _truncate_for_payload(a_values)
        b_values_out, b_truncated = _truncate_for_payload(b_values)
        metric_results.append(
            {
                "metric": m.to_dict(),
                "group_a": {
                    "image_ids": a_image_ids,
                    "values": a_values_out,
                    "values_truncated": a_truncated,
                    **result.group_a.to_dict(),
                },
                "group_b": {
                    "image_ids": b_image_ids,
                    "values": b_values_out,
                    "values_truncated": b_truncated,
                    **result.group_b.to_dict(),
                },
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
                "notes": result.notes,
            }
        )

    return {
        "group_a": {
            "filter": payload.group_a.model_dump(exclude_none=True),
            "image_count": len(images_a),
        },
        "group_b": {
            "filter": payload.group_b.model_dump(exclude_none=True),
            "image_count": len(images_b),
        },
        "metrics": metric_results,
    }


async def _resolve_group(
    sb: SupabaseAuthedClient, f: GroupFilter
) -> list[dict[str, Any]]:
    filters: dict[str, str] = {}
    if f.species:
        filters["species"] = f.species
    if f.photosynthesis_type:
        filters["photosynthesis_type"] = f.photosynthesis_type
    if f.plant_id:
        filters["plant_id"] = f.plant_id
    if f.treatment:
        filters["treatment"] = f.treatment
    return await sb.list_images_filtered(filters or None)


async def _latest_done_in(
    sb: SupabaseAuthedClient, image_ids: list[str], kind: str
) -> list[dict[str, Any]]:
    """Return all `done` analyses rows for the given kind whose image_id
    is in `image_ids`, ordered newest-first.  Caller keeps the first
    hit per image."""
    return await sb.list_analyses(
        image_ids=image_ids, kind=kind, status="done", order="created_at.desc"
    )
