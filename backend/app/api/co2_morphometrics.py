"""CO2-diffusion morphometrics endpoint.

Depends on completed `segformer_tissue` + `cellpose_cells` analyses on
the same image.  Runs the pixel-level morphometrics computation in a
background task (CPU-bound classical CV), upserts the `analyses` row
with `kind='co2_morphometrics'`, and returns the analysis id so the
frontend can poll `GET /analyses/{id}`.

Prereq readiness is probed by the panel directly against Supabase
(same pattern as `WaterPathPanel`) rather than via a dedicated status
endpoint here — the panel needs a live subscription anyway so a
server round-trip would just add latency.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Annotated, Any

import cv2
import numpy as np
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError

router = APIRouter()

ANALYSIS_KIND = "co2_morphometrics"
SEGFORMER_KIND = "segformer_tissue"
CELLPOSE_KIND = "cellpose_cells"
BASIC_KIND = "basic_measurement"


class Co2MorphometricsRequest(BaseModel):
    max_side_px: int = Field(default=1024, gt=0, le=4096)
    chloroplast_min_area_px: int = Field(default=6, ge=1, le=1000)
    chloroplast_max_area_ratio: float = Field(default=0.8, gt=0.0, le=1.0)


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


@router.post("/images/{image_id}/analyze/co2-morphometrics")
async def kick_off_co2_morphometrics(
    image_id: str,
    payload: Co2MorphometricsRequest,
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
        seg = await sb.latest_analysis_for(image_id, SEGFORMER_KIND, status="done")
        cells = await sb.latest_analysis_for(image_id, CELLPOSE_KIND, status="done")
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if seg is None or not isinstance(seg.get("result"), dict):
        raise HTTPException(
            status_code=412,
            detail=(
                "CO2 morphometrics requires a completed SegFormer run first. "
                "Run the SegFormer panel on this image."
            ),
        )
    if cells is None or not isinstance(cells.get("result"), dict):
        raise HTTPException(
            status_code=412,
            detail=(
                "CO2 morphometrics requires a completed Cellpose run first. "
                "Run the Cellpose panel on this image."
            ),
        )

    try:
        basic = await sb.latest_analysis_for(image_id, BASIC_KIND)
    except SupabaseHttpError:
        basic = None
    um_per_px = _scale_from(image, basic if isinstance(basic, dict) else None)

    parameters = {
        "max_side_px": payload.max_side_px,
        "chloroplast_min_area_px": payload.chloroplast_min_area_px,
        "chloroplast_max_area_ratio": payload.chloroplast_max_area_ratio,
        "source_segformer_id": seg["id"],
        "source_cellpose_id": cells["id"],
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

    storage_path = str(image["storage_path"])
    background_tasks.add_task(
        _run_co2_morphometrics_bg,
        jwt,
        analysis["id"],
        storage_path,
        seg["result"],
        cells["result"],
        parameters,
    )
    return {"analysis_id": analysis["id"], "status": "running"}


async def _run_co2_morphometrics_bg(
    jwt: str,
    analysis_id: str,
    storage_path: str,
    seg_result: dict[str, Any],
    cell_result: dict[str, Any],
    parameters: dict[str, Any],
) -> None:
    sb = SupabaseAuthedClient(jwt)
    try:
        raw = await sb.download_image_bytes(storage_path)
        arr = np.frombuffer(raw, dtype=np.uint8)
        image_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image_bgr is None:
            raise RuntimeError(f"failed to decode {Path(storage_path).name}")

        from app.pipeline.morphometrics_co2 import compute_co2_morphometrics

        result = await asyncio.to_thread(
            compute_co2_morphometrics,
            image_bgr,
            seg_result,
            cell_result,
            um_per_px=parameters.get("um_per_px"),
            max_side_px=int(parameters.get("max_side_px") or 1024),
            chloroplast_min_area_px=int(parameters.get("chloroplast_min_area_px") or 6),
            chloroplast_max_area_ratio=float(
                parameters.get("chloroplast_max_area_ratio") or 0.8
            ),
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
