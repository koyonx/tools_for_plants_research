"""CO2 reaction-diffusion endpoint.

Depends on a completed `segformer_tissue` analysis on the same image.
Optionally uses the chloroplast overlay from a completed
`co2_morphometrics` analysis to pin the sink region to the actual
chloroplast pixels; falls back to mesophyll cells (palisade + spongy)
when co2_morphometrics hasn't run.

Solves the PDE in a background task and persists the concentration
field, drawdown field, A_net and g_m_proxy back to `analyses` with
`kind='co2_diffusion'`.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError

router = APIRouter()

ANALYSIS_KIND = "co2_diffusion"
SEGFORMER_KIND = "segformer_tissue"
CO2_MORPH_KIND = "co2_morphometrics"
BASIC_KIND = "basic_measurement"


class Co2DiffusionRequest(BaseModel):
    max_side_px: int = Field(default=1024, gt=0, le=4096)
    ci_pa: float = Field(default=25.0, gt=0, le=1000.0)
    reaction_rate: float = Field(default=1.0, ge=0, le=1000.0)
    diffusivity: dict[str, float] | None = Field(
        default=None,
        description=(
            "Per-class CO2 diffusivity overrides in m^2/s.  Keys must be "
            "tissue class keys; non-finite or non-positive values are "
            "silently dropped."
        ),
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


@router.post("/images/{image_id}/analyze/co2-diffusion")
async def kick_off_co2_diffusion(
    image_id: str,
    payload: Co2DiffusionRequest,
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
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if seg is None or not isinstance(seg.get("result"), dict):
        raise HTTPException(
            status_code=412,
            detail=(
                "CO2 diffusion analysis requires a completed SegFormer run "
                "first.  Run the SegFormer panel on this image."
            ),
        )

    # Optional: chloroplast overlay from co2_morphometrics pins the
    # sink region to actual chloroplast pixels.  Skipped cleanly
    # when the analysis hasn't run — falls back to mesophyll cells.
    try:
        co2_morph = await sb.latest_analysis_for(image_id, CO2_MORPH_KIND, status="done")
    except SupabaseHttpError:
        co2_morph = None
    morph_result = (
        co2_morph.get("result") if isinstance(co2_morph, dict) else None
    )
    morph_id = co2_morph.get("id") if isinstance(co2_morph, dict) else None

    try:
        basic = await sb.latest_analysis_for(image_id, BASIC_KIND)
    except SupabaseHttpError:
        basic = None
    um_per_px = _scale_from(image, basic if isinstance(basic, dict) else None)

    parameters: dict[str, Any] = {
        "max_side_px": payload.max_side_px,
        "ci_pa": payload.ci_pa,
        "reaction_rate": payload.reaction_rate,
        "diffusivity_override": payload.diffusivity,
        "source_segformer_id": seg["id"],
        "source_co2_morphometrics_id": morph_id,
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
        _run_co2_diffusion_bg,
        jwt,
        analysis["id"],
        seg["result"],
        morph_result if isinstance(morph_result, dict) else None,
        parameters,
    )
    return {"analysis_id": analysis["id"], "status": "running"}


async def _run_co2_diffusion_bg(
    jwt: str,
    analysis_id: str,
    seg_result: dict[str, Any],
    morph_result: dict[str, Any] | None,
    parameters: dict[str, Any],
) -> None:
    sb = SupabaseAuthedClient(jwt)
    try:
        from app.pipeline.co2_diffusion import compute_co2_diffusion

        # Explicit `is None` defaults — same foot-gun avoidance as
        # Darcy's background task.  Ci=0 would be nonsensical but a
        # legitimate reaction_rate=0 (pure Fickian check) must pass
        # through unchanged.
        max_side = parameters.get("max_side_px")
        ci_pa = parameters.get("ci_pa")
        r_rate = parameters.get("reaction_rate")
        result = await asyncio.to_thread(
            compute_co2_diffusion,
            seg_result,
            co2_morphometrics_result=morph_result,
            um_per_px=parameters.get("um_per_px"),
            max_side_px=int(max_side) if max_side is not None else 1024,
            ci_pa=float(ci_pa) if ci_pa is not None else 25.0,
            reaction_rate=float(r_rate) if r_rate is not None else 1.0,
            diffusivity_override=parameters.get("diffusivity_override"),
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
