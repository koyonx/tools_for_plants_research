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
scalar outputs our pipelines already produce.  Add new entries here
when new pipelines land; the frontend reads it via
`GET /compare/metrics`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError
from app.pipeline.stats import compare

router = APIRouter()


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    unit: str
    analysis_kind: str
    # JSON path fragments into analyses.result.  `None` elements are
    # skipped (e.g. for array-backed keys handled elsewhere); for now all
    # metrics are plain dotted paths into nested dicts.
    path: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "path": list(self.path)}


# Scalar metrics every pipeline already exposes.  Extend here when
# adding a new pipeline — the `/compare/metrics` endpoint just returns
# this list, so the UI picks up new keys automatically.
METRICS: tuple[MetricDef, ...] = (
    MetricDef(
        key="leaf_area_um2",
        label="葉断面面積",
        unit="µm²",
        analysis_kind="basic_measurement",
        path=("measurement", "leaf_area_um2"),
    ),
    MetricDef(
        key="leaf_mean_thickness_um",
        label="葉厚 平均",
        unit="µm",
        analysis_kind="basic_measurement",
        path=("measurement", "leaf_mean_thickness_um"),
    ),
    MetricDef(
        key="leaf_median_thickness_um",
        label="葉厚 中央",
        unit="µm",
        analysis_kind="basic_measurement",
        path=("measurement", "leaf_median_thickness_um"),
    ),
    MetricDef(
        key="leaf_max_thickness_um",
        label="葉厚 最大",
        unit="µm",
        analysis_kind="basic_measurement",
        path=("measurement", "leaf_max_thickness_um"),
    ),
    MetricDef(
        key="cellpose_cell_count",
        label="Cellpose 細胞数",
        unit="個",
        analysis_kind="cellpose_cells",
        path=("cell_count",),
    ),
    MetricDef(
        key="cellpose_mean_area_px",
        label="Cellpose 細胞平均面積",
        unit="px²",
        analysis_kind="cellpose_cells",
        path=("mean_area_px",),
    ),
    MetricDef(
        key="water_travel_time_mean",
        label="水経路 平均 travel time",
        unit="µm·cost",
        analysis_kind="water_path",
        path=("travel_time_mean",),
    ),
    MetricDef(
        key="water_travel_time_p50",
        label="水経路 中央 travel time",
        unit="µm·cost",
        analysis_kind="water_path",
        path=("travel_time_p50",),
    ),
    MetricDef(
        key="water_sink_count",
        label="気孔数 (water_path 経由)",
        unit="個",
        analysis_kind="water_path",
        path=("sink_count",),
    ),
)
METRICS_BY_KEY: dict[str, MetricDef] = {m.key: m for m in METRICS}


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
    metrics: list[str] = Field(..., min_length=1, max_length=20)
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
    import math

    return math.isfinite(v)


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
        metric_results.append(
            {
                "metric": m.to_dict(),
                "group_a": {
                    "image_ids": a_image_ids,
                    "values": a_values,
                    **result.group_a.to_dict(),
                },
                "group_b": {
                    "image_ids": b_image_ids,
                    "values": b_values,
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
