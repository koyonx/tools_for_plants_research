"""Bulk-analyze endpoint — fan a list of pipelines over a list of images.

Flow
----
1. Client POSTs `{image_ids, pipeline_kinds, label?}`.
2. We insert a `batch_runs` row (status='running') and kick off a single
   background task that walks the image-by-pipeline matrix.
3. Per pipeline, the background task re-uses the same helpers that the
   per-image endpoints do (so behaviour, RLS, error handling stay in
   lock-step).
4. When done, `status` flips to `done` / `partial` / `error` and
   `succeeded`, `failed`, `analysis_ids` are filled.

The enumeration + dispatch is intentionally explicit (no auto-import of
all analyze modules) so deleting or renaming a pipeline trips a type
error here instead of silently disappearing from batch runs.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated, Any, Literal

import cv2
import numpy as np
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError

router = APIRouter()

SUPPORTED_KINDS = (
    "basic_measurement",
    "cellpose_cells",
    "segformer_tissue",
    "water_path",
)
PipelineKind = Literal[
    "basic_measurement",
    "cellpose_cells",
    "segformer_tissue",
    "water_path",
]


class BatchRequest(BaseModel):
    image_ids: list[str] = Field(..., min_length=1, max_length=500)
    pipeline_kinds: list[PipelineKind] = Field(..., min_length=1)
    label: str | None = Field(default=None, max_length=200)
    # Optional per-pipeline parameters.  Missing → defaults.
    reference_um: float = Field(default=100.0, gt=0)
    max_side_px: int = Field(default=1024, gt=0, le=4096)


def _extract_jwt(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return authorization.split(" ", 1)[1]


@router.post("/batches")
async def create_batch_run(
    payload: BatchRequest,
    background_tasks: BackgroundTasks,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    jwt = _extract_jwt(authorization)
    sb = SupabaseAuthedClient(jwt)

    # Validate each image exists + caller can write its analyses.
    for image_id in payload.image_ids:
        try:
            image = await sb.get_image(image_id)
        except SupabaseHttpError as e:
            raise HTTPException(status_code=e.status, detail=e.detail) from e
        if image is None:
            raise HTTPException(
                status_code=404, detail=f"image {image_id!r} not found or not accessible"
            )

    # Caller identity — owner_id of the batch_runs row must match auth.uid.
    try:
        me = await sb.get_user_identity()
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    owner_id = me.get("id") if me else None
    if not owner_id:
        raise HTTPException(status_code=401, detail="unable to resolve caller identity")

    total = len(payload.image_ids) * len(payload.pipeline_kinds)
    try:
        batch = await sb.insert_batch_run(
            {
                "owner_id": owner_id,
                "label": payload.label,
                "pipeline_kinds": list(payload.pipeline_kinds),
                "image_ids": payload.image_ids,
                "status": "running",
                "total": total,
                "criteria": {
                    "reference_um": payload.reference_um,
                    "max_side_px": payload.max_side_px,
                },
            }
        )
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    background_tasks.add_task(
        _run_batch_bg,
        jwt,
        batch["id"],
        payload.image_ids,
        list(payload.pipeline_kinds),
        {
            "reference_um": payload.reference_um,
            "max_side_px": payload.max_side_px,
        },
    )
    return batch


@router.get("/batches/{batch_id}")
async def get_batch(
    batch_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    sb = SupabaseAuthedClient(_extract_jwt(authorization))
    try:
        row = await sb.get_batch_run(batch_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if row is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return row


async def _run_pipeline(
    sb: SupabaseAuthedClient,
    kind: str,
    image_id: str,
    params: dict[str, Any],
) -> str:
    """Run one pipeline against one image, return the new analyses.id.

    We go through the `analyses` table directly rather than routing HTTP
    calls back into this backend — that avoids double-RLS-validation and
    keeps the batch task entirely in-process.
    """
    image = await sb.get_image(image_id)
    if image is None:
        raise RuntimeError(f"image {image_id!r} disappeared mid-batch")

    # Pre-fetch image bytes once per pipeline call.  Not cached across
    # pipelines because each pipeline has its own downsampling, which we
    # can't share safely.
    storage_path = image["storage_path"]
    raw = await sb.download_image_bytes(storage_path)
    arr = np.frombuffer(raw, dtype=np.uint8)
    image_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise RuntimeError(f"failed to decode {storage_path!r}")

    if kind == "basic_measurement":
        from app.pipeline.measure import measure_from_mask
        from app.pipeline.scale import detect_scale_bar
        from app.pipeline.segment import leaf_mask

        scale = detect_scale_bar(image_bgr, float(params["reference_um"]))
        mask = leaf_mask(image_bgr)
        measurement = measure_from_mask(mask, scale.um_per_px)
        basic_blob: dict[str, Any] = {
            "scale": {
                "um_per_px": scale.um_per_px,
                "bar_px_length": scale.bar_px_length,
                "bbox_xywh": list(scale.bbox_xywh),
            },
            "measurement": measurement.to_dict(),
            "image_shape": {"height_px": image_bgr.shape[0], "width_px": image_bgr.shape[1]},
        }
        row = await sb.insert_analysis(
            {
                "image_id": image_id,
                "kind": kind,
                "status": "done",
                "parameters": {"reference_um": float(params["reference_um"])},
                "result": basic_blob,
            }
        )
        return str(row["id"])

    if kind == "cellpose_cells":
        from app.pipeline.cellpose_infer import detect_cells

        cp_result = await asyncio.to_thread(
            detect_cells,
            image_bgr,
            max_side_px=int(params["max_side_px"]),
        )
        cp_blob = cp_result.to_dict()
        cp_blob["image_shape"] = {
            "height_px": image_bgr.shape[0],
            "width_px": image_bgr.shape[1],
        }
        row = await sb.insert_analysis(
            {
                "image_id": image_id,
                "kind": kind,
                "status": "done",
                "parameters": {"max_side_px": int(params["max_side_px"])},
                "result": cp_blob,
            }
        )
        return str(row["id"])

    if kind == "segformer_tissue":
        from app.pipeline.segformer_infer import detect_tissue

        seg_result = await asyncio.to_thread(
            detect_tissue,
            image_bgr,
            max_side_px=int(params["max_side_px"]),
        )
        seg_blob = seg_result.to_dict()
        seg_blob["image_shape"] = {
            "height_px": image_bgr.shape[0],
            "width_px": image_bgr.shape[1],
        }
        row = await sb.insert_analysis(
            {
                "image_id": image_id,
                "kind": kind,
                "status": "done",
                "parameters": {"max_side_px": int(params["max_side_px"])},
                "result": seg_blob,
            }
        )
        return str(row["id"])

    if kind == "water_path":
        # Water-path depends on an already-done SegFormer run on the
        # same image.  Look it up and error if it's not there.
        from app.pipeline.water_path import compute_water_path

        seg = await sb.latest_analysis_for(image_id, "segformer_tissue", status="done")
        if seg is None or not isinstance(seg.get("result"), dict):
            raise RuntimeError(
                "water_path requires a prior segformer_tissue run on the same image"
            )
        basic = await sb.latest_analysis_for(image_id, "basic_measurement", status="done")
        um_per_px: float | None = image.get("scale_um_per_px")
        if not um_per_px and isinstance(basic, dict):
            basic_result_blob = basic.get("result")
            if isinstance(basic_result_blob, dict):
                s = (basic_result_blob.get("scale") or {}).get("um_per_px")
                if isinstance(s, int | float) and s > 0:
                    um_per_px = float(s)
        water_result = await asyncio.to_thread(
            compute_water_path,
            seg["result"],
            um_per_px=um_per_px,
            max_side_px=int(params["max_side_px"]),
        )
        row = await sb.insert_analysis(
            {
                "image_id": image_id,
                "kind": kind,
                "status": "done",
                "parameters": {
                    "max_side_px": int(params["max_side_px"]),
                    "source_segformer_id": seg["id"],
                    "um_per_px": um_per_px,
                },
                "result": water_result.to_dict(),
            }
        )
        return str(row["id"])

    raise RuntimeError(f"unknown pipeline kind: {kind!r}")


async def _run_batch_bg(
    jwt: str,
    batch_id: str,
    image_ids: list[str],
    pipeline_kinds: list[str],
    params: dict[str, Any],
) -> None:
    sb = SupabaseAuthedClient(jwt)
    succeeded = 0
    failed = 0
    analysis_ids: list[str] = []
    last_error: str | None = None

    # Preserve user-specified pipeline order so dependencies resolve
    # correctly (e.g. segformer_tissue before water_path).
    for image_id in image_ids:
        for kind in pipeline_kinds:
            try:
                aid = await _run_pipeline(sb, kind, image_id, params)
                analysis_ids.append(aid)
                succeeded += 1
            except Exception as exc:
                failed += 1
                last_error = f"{kind}@{image_id[:8]}: {exc}"
            # Periodic progress update so the UI has something to poll.
            if (succeeded + failed) % 4 == 0:
                with contextlib.suppress(SupabaseHttpError):
                    await sb.update_batch_run(
                        batch_id,
                        {
                            "succeeded": succeeded,
                            "failed": failed,
                            "analysis_ids": analysis_ids,
                        },
                    )

    final_status = (
        "done" if failed == 0 else ("error" if succeeded == 0 else "partial")
    )
    with contextlib.suppress(SupabaseHttpError):
        await sb.update_batch_run(
            batch_id,
            {
                "status": final_status,
                "succeeded": succeeded,
                "failed": failed,
                "analysis_ids": analysis_ids,
                "error": last_error if failed else None,
            },
        )
