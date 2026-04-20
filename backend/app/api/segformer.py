"""SegFormer tissue-segmentation endpoint.

Mirrors `api/cellpose.py`: insert an `analyses` row, kick off inference
in a background task, persist result + error status, let the frontend
poll `GET /analyses/{id}`.  CPU-bound inference runs in
`asyncio.to_thread()` so the FastAPI event loop stays responsive.

Returns 503 if the checkpoint directory isn't populated — the rest of
the app keeps working while the user trains a model.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Annotated, Any

import cv2
import numpy as np
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError

router = APIRouter()

ANALYSIS_KIND = "segformer_tissue"
DEFAULT_MODEL_DIR = os.environ.get("PLANTS_SEGFORMER_DIR", "/models/segformer")


class SegFormerRequest(BaseModel):
    max_side_px: int = Field(default=1024, gt=0, le=4096)
    model_dir: str | None = Field(default=None, description="override PLANTS_SEGFORMER_DIR")


def _extract_jwt(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return authorization.split(" ", 1)[1]


def _resolve_model_dir(override: str | None) -> str:
    return override or DEFAULT_MODEL_DIR


@router.get("/analyze/segformer/status")
def segformer_status() -> dict[str, Any]:
    """Quick probe for the frontend — reports whether a checkpoint is present."""
    path = _resolve_model_dir(None)
    has_weights = any(
        (Path(path) / name).exists()
        for name in ("model.safetensors", "pytorch_model.bin")
    )
    return {
        "model_dir": path,
        "available": Path(path).exists() and has_weights,
    }


@router.post("/images/{image_id}/analyze/segformer")
async def kick_off_segformer(
    image_id: str,
    payload: SegFormerRequest,
    background_tasks: BackgroundTasks,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    jwt = _extract_jwt(authorization)
    sb = SupabaseAuthedClient(jwt)

    model_dir = _resolve_model_dir(payload.model_dir)
    if not Path(model_dir).exists():
        raise HTTPException(
            status_code=503,
            detail=(
                f"segformer checkpoint not found at {model_dir}. "
                "See models/README.md for how to train + drop-in weights."
            ),
        )

    try:
        image = await sb.get_image(image_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if image is None:
        raise HTTPException(status_code=404, detail="image not found or not accessible")

    parameters = {"max_side_px": payload.max_side_px, "model_dir": model_dir}
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
        _run_segformer_bg,
        jwt,
        analysis["id"],
        image["storage_path"],
        parameters,
    )
    return {"analysis_id": analysis["id"], "status": "running"}


async def _run_segformer_bg(
    jwt: str,
    analysis_id: str,
    storage_path: str,
    parameters: dict[str, Any],
) -> None:
    """Background job — runs SegFormer inference and persists the outcome."""
    sb = SupabaseAuthedClient(jwt)
    try:
        raw = await sb.download_image_bytes(storage_path)
        arr = np.frombuffer(raw, dtype=np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("failed to decode image bytes")

        from app.pipeline.segformer_infer import detect_tissue  # heavy import

        result = await asyncio.to_thread(
            detect_tissue,
            image,
            max_side_px=int(parameters.get("max_side_px") or 1024),
            model_dir=str(parameters.get("model_dir") or DEFAULT_MODEL_DIR),
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
