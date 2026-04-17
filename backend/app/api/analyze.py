"""Analyze endpoints — run the classical-CV pipeline on an uploaded image.

Flow
----
1. Frontend sends POST /images/{image_id}/analyze with the user's access
   token in the Authorization header and a reference length in µm.
2. We insert an `analyses` row (RLS ensures the caller owns the image).
3. Download the original file from Storage, decode, run scale + leaf mask
   + measurement, persist the result on the row.
4. Return the final row so the UI can render without polling (runs are
   currently seconds-long; BackgroundTasks / a job queue lands later if
   the pipeline gets heavier).
"""

from __future__ import annotations

import contextlib
import csv
import io
from typing import Annotated, Any

import cv2
import numpy as np
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError
from app.pipeline.measure import measure_from_mask
from app.pipeline.scale import detect_scale_bar
from app.pipeline.segment import leaf_mask

router = APIRouter()

ANALYSIS_KIND = "basic_measurement"


class AnalyzeRequest(BaseModel):
    reference_um: float = Field(..., gt=0, description="Physical length of the scale bar in µm")


def _extract_jwt(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return authorization.split(" ", 1)[1]


def _decode_image(raw: bytes) -> np.ndarray:
    arr = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=422, detail="could not decode image bytes")
    return image


@router.post("/images/{image_id}/analyze")
async def run_basic_measurement(
    image_id: str,
    payload: AnalyzeRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    jwt = _extract_jwt(authorization)
    sb = SupabaseAuthedClient(jwt)

    try:
        image_row = await sb.get_image(image_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if image_row is None:
        raise HTTPException(status_code=404, detail="image not found or not accessible")

    # Insert pending row so the client can reference it while we work.
    try:
        analysis = await sb.insert_analysis(
            {
                "image_id": image_id,
                "kind": ANALYSIS_KIND,
                "status": "running",
                "parameters": {"reference_um": payload.reference_um},
            }
        )
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    analysis_id = analysis["id"]

    try:
        raw = await sb.download_image_bytes(image_row["storage_path"])
        image_bgr = _decode_image(raw)

        scale = detect_scale_bar(image_bgr, payload.reference_um)
        mask = leaf_mask(image_bgr)
        measurement = measure_from_mask(mask, scale.um_per_px)

        result_blob = {
            "scale": {
                "um_per_px": scale.um_per_px,
                "bar_px_length": scale.bar_px_length,
                "bbox_xywh": list(scale.bbox_xywh),
            },
            "measurement": measurement.to_dict(),
            "image_shape": {"height_px": image_bgr.shape[0], "width_px": image_bgr.shape[1]},
        }
        updated = await sb.update_analysis(
            analysis_id, {"status": "done", "result": result_blob, "error": None}
        )
        return updated
    except Exception as exc:
        with contextlib.suppress(SupabaseHttpError):
            await sb.update_analysis(analysis_id, {"status": "error", "error": str(exc)})
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"pipeline failed: {exc}") from exc


@router.get("/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    sb = SupabaseAuthedClient(_extract_jwt(authorization))
    try:
        row = await sb.get_analysis(analysis_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if row is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    return row


@router.get("/analyses/{analysis_id}/csv")
async def export_analysis_csv(
    analysis_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    sb = SupabaseAuthedClient(_extract_jwt(authorization))
    try:
        row = await sb.get_analysis(analysis_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if row is None:
        raise HTTPException(status_code=404, detail="analysis not found")

    result: dict[str, Any] = row.get("result") or {}
    measurement: dict[str, Any] = result.get("measurement") or {}
    scale: dict[str, Any] = result.get("scale") or {}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric", "value", "unit"])
    writer.writerow(["um_per_px", scale.get("um_per_px", ""), "um/px"])
    writer.writerow(["leaf_area", measurement.get("leaf_area_um2", ""), "um^2"])
    writer.writerow(["leaf_mean_thickness", measurement.get("leaf_mean_thickness_um", ""), "um"])
    writer.writerow(
        ["leaf_median_thickness", measurement.get("leaf_median_thickness_um", ""), "um"]
    )
    writer.writerow(["leaf_min_thickness", measurement.get("leaf_min_thickness_um", ""), "um"])
    writer.writerow(["leaf_max_thickness", measurement.get("leaf_max_thickness_um", ""), "um"])
    writer.writerow([])
    writer.writerow(["x_um", "thickness_um"])
    xs = measurement.get("thickness_profile_x_um") or []
    ts = measurement.get("thickness_profile_um") or []
    for x, t in zip(xs, ts, strict=False):
        writer.writerow([x, t])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="analysis_{analysis_id}.csv"'},
    )
