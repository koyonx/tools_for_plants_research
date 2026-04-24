"""Literature-validation endpoints.

Pull the latest analyses for an image (or gm_fits for a session),
run the pipeline-level classifier, and return a flat findings list
suitable for direct rendering in the dashboard.  Pure compute — no
background task, no DB writes.  A separate `/literature/ranges`
endpoint exposes the full ranges table so the frontend's literature
page can render without hitting the bundled TypeScript copy (single
source of truth = backend).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError
from app.pipeline.literature_ranges import LITERATURE_RANGES, all_parameter_keys
from app.pipeline.validation import validate_analyses, validate_gm_fit_result

router = APIRouter()

_KINDS_TO_VALIDATE: tuple[str, ...] = (
    "basic_measurement",
    "cellpose_cells",
    "segformer_tissue",
    "water_path",
    "darcy_flow",
    "co2_morphometrics",
    "co2_diffusion",
)


def _extract_jwt(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return authorization.split(" ", 1)[1]


@router.get("/literature/ranges")
def list_literature_ranges() -> dict[str, Any]:
    """Expose the curated ranges table so the dashboard literature
    page renders from the same source the validator uses."""
    return {
        "ranges": [r.to_dict() for r in LITERATURE_RANGES],
        "parameter_keys": all_parameter_keys(),
    }


@router.post("/images/{image_id}/validate")
async def validate_image(
    image_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    sb = SupabaseAuthedClient(_extract_jwt(authorization))
    try:
        image = await sb.get_image(image_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if image is None:
        raise HTTPException(status_code=404, detail="image not found or not accessible")

    # One PostgREST call per kind would be 7 round-trips; batch through
    # the existing `list_analyses(image_ids=[..])` helper instead —
    # single call covers all kinds, we filter client-side and keep the
    # latest-done row per kind.
    try:
        rows = await sb.list_analyses(image_ids=[image_id], status="done")
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    latest_by_kind: dict[str, dict[str, Any]] = {}
    for row in rows:
        kind = row.get("kind")
        if not isinstance(kind, str) or kind not in _KINDS_TO_VALIDATE:
            continue
        result = row.get("result")
        if not isinstance(result, dict):
            continue
        # rows come ordered by created_at desc; keep the first we see.
        latest_by_kind.setdefault(kind, result)

    photosynthesis_type = image.get("photosynthesis_type") if isinstance(image, dict) else None
    if isinstance(photosynthesis_type, str) and photosynthesis_type == "unknown":
        # Treat "unknown" same as missing for range lookup — the
        # pooled "any" fallback is the right comparator.
        photosynthesis_type = None

    report = validate_analyses(latest_by_kind, photosynthesis_type)
    return {
        "image_id": image_id,
        "photosynthesis_type": photosynthesis_type,
        "analyses_considered": sorted(latest_by_kind.keys()),
        "report": report.to_dict(),
    }


@router.post("/gas-exchange/sessions/{session_id}/validate")
async def validate_session(
    session_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    sb = SupabaseAuthedClient(_extract_jwt(authorization))
    try:
        session = await sb.get_gas_exchange_session(session_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if session is None:
        raise HTTPException(status_code=404, detail="session not found or not accessible")

    try:
        fit = await sb.latest_gm_fit_for_session(session_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if fit is None:
        raise HTTPException(
            status_code=412,
            detail=(
                "no gm_fit has been run for this session yet — "
                "run the g_m fit first, then validate"
            ),
        )

    photosynthesis_type = session.get("photosynthesis_type") if isinstance(session, dict) else None
    if isinstance(photosynthesis_type, str) and photosynthesis_type == "unknown":
        photosynthesis_type = None

    fit_result_blob = fit.get("result") if isinstance(fit, dict) else None
    if not isinstance(fit_result_blob, dict):
        raise HTTPException(
            status_code=500,
            detail="gm_fit row has no result blob; cannot validate",
        )

    report = validate_gm_fit_result(fit_result_blob, photosynthesis_type)
    return {
        "session_id": session_id,
        "fit_id": fit["id"] if isinstance(fit, dict) else None,
        "photosynthesis_type": photosynthesis_type,
        "report": report.to_dict(),
    }
