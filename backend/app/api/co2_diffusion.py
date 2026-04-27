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

    # Kinetics ------------------------------------------------------
    kinetics_mode: str = Field(
        default="michaelis_menten",
        pattern="^(michaelis_menten|linear)$",
        description=(
            "Reaction term form.  'michaelis_menten' (default) uses the "
            "Farquhar-von Caemmerer-Berry Rubisco-limited form solved by "
            "Picard iteration.  'linear' uses R(C) = r*C (PR #13a legacy)."
        ),
    )
    vcmax_per_volume_mol_m3_s: float = Field(
        default=1.0,
        ge=0,
        le=1.0e3,
        description=(
            "Volumetric Vcmax inside the chloroplast region (M-M mode "
            "only).  ≈ Vcmax_area / chloroplast_layer_thickness; e.g. "
            "80 µmol/m^2/s over a 25-µm-equivalent layer ≈ 3 mol/m^3/s."
        ),
    )
    kc_pa: float = Field(default=27.238, gt=0, le=1.0e4)
    ko_pa: float = Field(default=16582.0, gt=0, le=1.0e6)
    o2_pa: float = Field(default=21000.0, gt=0, le=1.0e6)
    gamma_star_pa: float = Field(default=3.743, ge=0, le=100.0)
    picard_max_iter: int = Field(default=50, ge=1, le=500)
    picard_tol_pa: float = Field(default=1e-4, gt=0, le=1.0)

    reaction_rate: float = Field(
        default=1.0,
        ge=0,
        le=1000.0,
        description="Linear-mode rate r (1/s).  Ignored when kinetics_mode='michaelis_menten'.",
    )

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
        "kinetics_mode": payload.kinetics_mode,
        "vcmax_per_volume_mol_m3_s": payload.vcmax_per_volume_mol_m3_s,
        "kc_pa": payload.kc_pa,
        "ko_pa": payload.ko_pa,
        "o2_pa": payload.o2_pa,
        "gamma_star_pa": payload.gamma_star_pa,
        "picard_max_iter": payload.picard_max_iter,
        "picard_tol_pa": payload.picard_tol_pa,
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
        from app.pipeline.co2_diffusion import (
            DEFAULT_GAMMA_STAR_PA,
            DEFAULT_KC_PA,
            DEFAULT_KINETICS_MODE,
            DEFAULT_KO_PA,
            DEFAULT_O2_PA,
            DEFAULT_PICARD_MAX_ITER,
            DEFAULT_PICARD_TOL_PA,
            DEFAULT_VCMAX_PER_VOLUME,
            compute_co2_diffusion,
        )

        # Explicit `is None` defaults — same foot-gun avoidance as
        # Darcy's background task.  Ci=0 would be nonsensical but a
        # legitimate reaction_rate=0 (pure Fickian check) must pass
        # through unchanged.
        def _f(key: str, fallback: float) -> float:
            v = parameters.get(key)
            return float(v) if v is not None else fallback

        def _i(key: str, fallback: int) -> int:
            v = parameters.get(key)
            return int(v) if v is not None else fallback

        result = await asyncio.to_thread(
            compute_co2_diffusion,
            seg_result,
            co2_morphometrics_result=morph_result,
            um_per_px=parameters.get("um_per_px"),
            max_side_px=_i("max_side_px", 1024),
            ci_pa=_f("ci_pa", 25.0),
            reaction_rate=_f("reaction_rate", 1.0),
            kinetics_mode=str(parameters.get("kinetics_mode") or DEFAULT_KINETICS_MODE),
            vcmax_per_volume_mol_m3_s=_f(
                "vcmax_per_volume_mol_m3_s", DEFAULT_VCMAX_PER_VOLUME
            ),
            kc_pa=_f("kc_pa", DEFAULT_KC_PA),
            ko_pa=_f("ko_pa", DEFAULT_KO_PA),
            o2_pa=_f("o2_pa", DEFAULT_O2_PA),
            gamma_star_pa=_f("gamma_star_pa", DEFAULT_GAMMA_STAR_PA),
            picard_max_iter=_i("picard_max_iter", DEFAULT_PICARD_MAX_ITER),
            picard_tol_pa=_f("picard_tol_pa", DEFAULT_PICARD_TOL_PA),
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
