"""Training-data export endpoints.

Clients hand off to these for two things:
1. `GET /images/{id}/mask.png` — render a single image's current
   annotation state as a semantic mask (useful as an editor preview).
2. `GET /training/export.zip` — bundle every image the caller can
   read, together with its rasterised mask and a class-index manifest,
   so trainers for SegFormer / DeepLab / Cellpose can consume the
   archive directly.

Both routes authenticate with the caller's Supabase JWT, so RLS stays
in effect end-to-end — we never need the service role here.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from typing import Annotated, Any

import cv2
import numpy as np
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError
from app.pipeline.rasterize import classes_manifest, rasterize_annotations

router = APIRouter()


def _extract_jwt(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return authorization.split(" ", 1)[1]


def _encode_mask_png(mask: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", mask)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to encode mask PNG")
    return bytes(buf.tobytes())


@router.get("/images/{image_id}/mask.png")
async def get_image_mask(
    image_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    jwt = _extract_jwt(authorization)
    sb = SupabaseAuthedClient(jwt)

    try:
        image = await sb.get_image(image_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if image is None:
        raise HTTPException(status_code=404, detail="image not found or not accessible")

    h = image.get("height_px")
    w = image.get("width_px")
    if not h or not w:
        raise HTTPException(status_code=422, detail="image has no recorded dimensions")

    try:
        annotations = await sb.list_annotations(image_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    mask = rasterize_annotations(annotations, int(h), int(w))
    png = _encode_mask_png(mask)
    return StreamingResponse(
        iter([png]),
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="mask_{image_id}.png"',
            "X-Annotation-Count": str(len(annotations)),
        },
    )


@router.get("/training/export.zip")
async def export_training_data(
    authorization: Annotated[str | None, Header()] = None,
    include_unlabelled: bool = False,
) -> StreamingResponse:
    """Bundle image+mask pairs for offline model training.

    Only images with at least one annotation are included by default
    (unlabelled images add no signal and bloat the archive).  Pass
    `?include_unlabelled=true` if the trainer wants the raw set too.
    """
    jwt = _extract_jwt(authorization)
    sb = SupabaseAuthedClient(jwt)

    try:
        images = await sb.list_images()
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    buffer = io.BytesIO()
    included = 0
    skipped_no_ann = 0
    skipped_no_dims = 0

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("classes.json", json.dumps(classes_manifest(), ensure_ascii=False, indent=2))

        index_rows: list[dict[str, Any]] = []

        for image in images:
            image_id = image["id"]
            h = image.get("height_px")
            w = image.get("width_px")
            if not h or not w:
                skipped_no_dims += 1
                continue

            try:
                annotations = await sb.list_annotations(image_id)
            except SupabaseHttpError:
                continue
            if not annotations and not include_unlabelled:
                skipped_no_ann += 1
                continue

            try:
                raw = await sb.download_image_bytes(image["storage_path"])
            except SupabaseHttpError:
                continue

            # Derive a reasonable extension from the stored filename/type.
            ext = os.path.splitext(image.get("original_filename") or "")[1].lower()
            if ext not in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}:
                ct = (image.get("content_type") or "").lower()
                ext = ".png" if ct.endswith("png") else ".jpg"

            mask = rasterize_annotations(annotations, int(h), int(w))
            mask_png = _encode_mask_png(mask)

            zf.writestr(f"images/{image_id}{ext}", raw)
            zf.writestr(f"masks/{image_id}.png", mask_png)
            index_rows.append(
                {
                    "image_id": image_id,
                    "image_path": f"images/{image_id}{ext}",
                    "mask_path": f"masks/{image_id}.png",
                    "width_px": int(w),
                    "height_px": int(h),
                    "annotation_count": len(annotations),
                    "visibility": image.get("visibility"),
                    "original_filename": image.get("original_filename"),
                }
            )
            included += 1

        zf.writestr(
            "index.json",
            json.dumps(
                {
                    "images": index_rows,
                    "counts": {
                        "included": included,
                        "skipped_no_annotation": skipped_no_ann,
                        "skipped_no_dimensions": skipped_no_dims,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    buffer.seek(0)
    data = buffer.getvalue()
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="plants-research-training.zip"',
            "X-Image-Count": str(included),
            "X-Skipped-No-Annotation": str(skipped_no_ann),
            "X-Skipped-No-Dimensions": str(skipped_no_dims),
        },
    )
