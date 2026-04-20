"""Cellpose detection endpoint.

Runs the Cellpose generalist cell segmenter over an uploaded image in
the background so the HTTP request returns in <1 s; the frontend polls
`GET /analyses/{id}` to observe progress (status → `running` →
`done` / `error`).  The caller's Supabase JWT is reused inside the
background task so RLS still enforces ownership end-to-end.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated, Any

import cv2
import numpy as np
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError

router = APIRouter()

ANALYSIS_KIND = "cellpose_cells"


class CellposeRequest(BaseModel):
    max_side_px: int = Field(default=1024, gt=0, le=4096)
    diameter: float | None = Field(default=None, gt=0)
    model_name: str = Field(default="cyto3")


def _extract_jwt(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return authorization.split(" ", 1)[1]


@router.post("/images/{image_id}/analyze/cellpose")
async def kick_off_cellpose(
    image_id: str,
    payload: CellposeRequest,
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

    parameters = payload.model_dump()
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
        _run_cellpose_bg,
        jwt,
        analysis["id"],
        image["storage_path"],
        parameters,
    )
    return {"analysis_id": analysis["id"], "status": "running"}


async def _run_cellpose_bg(
    jwt: str,
    analysis_id: str,
    storage_path: str,
    parameters: dict[str, Any],
) -> None:
    """Background job — runs Cellpose and persists the outcome."""
    sb = SupabaseAuthedClient(jwt)
    try:
        raw = await sb.download_image_bytes(storage_path)
        arr = np.frombuffer(raw, dtype=np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("failed to decode image bytes")

        # Heavy import deferred so other pipeline endpoints keep working
        # even if torch/cellpose aren't available in the running image.
        from app.pipeline.cellpose_infer import detect_cells

        # Cellpose inference is a CPU-bound 30-60 s call.  Running it
        # directly on the FastAPI event loop (where async BackgroundTasks
        # execute) would freeze every other request - health, polling for
        # the same analysis, other users - for the duration.  Hand it off
        # to the default thread-pool executor instead.
        result = await asyncio.to_thread(
            detect_cells,
            image,
            max_side_px=int(parameters.get("max_side_px") or 1024),
            diameter=parameters.get("diameter"),
            model_name=str(parameters.get("model_name") or "cyto3"),
        )
        blob = result.to_dict()
        blob["image_shape"] = {"height_px": image.shape[0], "width_px": image.shape[1]}
        await sb.update_analysis(
            analysis_id,
            {"status": "done", "result": blob, "error": None},
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            await sb.update_analysis(
                analysis_id,
                {"status": "error", "error": str(exc)},
            )
