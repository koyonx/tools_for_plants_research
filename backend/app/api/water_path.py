"""Water-transport endpoint — Fast Marching from xylem vessels to stomata.

Depends on a prior `segformer_tissue` analysis on the same image (we
read its polygons + image_shape blob).  Inserts an `analyses` row with
`kind='water_path'`, runs the FMM in a background task off the event
loop (`asyncio.to_thread`), and persists the result.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError

router = APIRouter()

ANALYSIS_KIND = "water_path"
SEGFORMER_KIND = "segformer_tissue"
BASIC_KIND = "basic_measurement"


class WaterPathRequest(BaseModel):
    max_side_px: int = Field(default=1024, gt=0, le=4096)
    resistance: dict[str, float] | None = Field(
        default=None,
        description="Per-class resistance overrides (keys must be tissue class keys)",
    )


def _extract_jwt(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return authorization.split(" ", 1)[1]


def _scale_from(image: dict[str, Any], basic_row: dict[str, Any] | None) -> float | None:
    scale = image.get("scale_um_per_px")
    if isinstance(scale, int | float) and scale > 0:
        return float(scale)
    if basic_row and isinstance(basic_row.get("result"), dict):
        s = basic_row["result"].get("scale", {}).get("um_per_px")
        if isinstance(s, int | float) and s > 0:
            return float(s)
    return None


@router.post("/images/{image_id}/analyze/water-path")
async def kick_off_water_path(
    image_id: str,
    payload: WaterPathRequest,
    background_tasks: BackgroundTasks,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    jwt = _extract_jwt(authorization)
    sb = SupabaseAuthedClient(jwt)

    try:
        image = await sb.get_image(image_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if image is None:
        raise HTTPException(status_code=404, detail="image not found or not accessible")

    try:
        seg = await sb.latest_analysis_for(image_id, SEGFORMER_KIND)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if seg is None or not isinstance(seg.get("result"), dict) or seg.get("status") != "done":
        raise HTTPException(
            status_code=412,
            detail=(
                "water-path analysis requires a completed SegFormer run for this image. "
                "Run the SegFormer panel first."
            ),
        )

    try:
        basic = await sb.latest_analysis_for(image_id, BASIC_KIND)
    except SupabaseHttpError:
        basic = None
    um_per_px = _scale_from(image, basic if isinstance(basic, dict) else None)

    parameters = {
        "max_side_px": payload.max_side_px,
        "resistance_override": payload.resistance,
        "source_segformer_id": seg["id"],
        "um_per_px": um_per_px,
    }

    try:
        analysis = await sb.insert_analysis(
            {
                "image_id": image_id,
                "kind": ANALYSIS_KIND,
                "status": "running",
                "parameters": parameters,
            }
        )
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    background_tasks.add_task(
        _run_water_path_bg,
        jwt,
        analysis["id"],
        seg["result"],
        parameters,
    )
    return {"analysis_id": analysis["id"], "status": "running"}


async def _run_water_path_bg(
    jwt: str,
    analysis_id: str,
    seg_result: dict[str, Any],
    parameters: dict[str, Any],
) -> None:
    sb = SupabaseAuthedClient(jwt)
    try:
        # Heavy import deferred so backends without the ml extra still boot.
        from app.pipeline.water_path import compute_water_path

        result = await asyncio.to_thread(
            compute_water_path,
            seg_result,
            um_per_px=parameters.get("um_per_px"),
            max_side_px=int(parameters.get("max_side_px") or 1024),
            resistance_override=parameters.get("resistance_override"),
        )
        await sb.update_analysis(
            analysis_id,
            {"status": "done", "result": result.to_dict(), "error": None},
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            await sb.update_analysis(
                analysis_id,
                {"status": "error", "error": str(exc)},
            )
