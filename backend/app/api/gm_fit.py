"""Mesophyll conductance (g_m) fit endpoints.

Consumes a `gas_exchange_sessions` row's point list (from PR #11) and
runs the three g_m estimation methods (Harley variable-J, Ethier-
Livingston, joint non-linear slope) defined in pipeline/gm_fit.py.

Results persist in `gm_fits` with RLS-protected ownership.  The UI
shows all three side-by-side so the operator can pick whichever
method has the best fit quality for the session's regime (ETR
presence, number of points, Ci range coverage).
"""

from __future__ import annotations

from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError
from app.pipeline.gm_fit import fit_all

router = APIRouter()


class GmFitRequest(BaseModel):
    # Temperature override.  When None, try to infer from the median
    # Tleaf column in the session points; fall back to 25 C.
    tleaf_c: float | None = Field(default=None, ge=-5.0, le=50.0)
    # Rd override; when None, nonlinear_slope fits Rd too.
    rd: float | None = Field(default=None, ge=0.0, le=10.0)
    # Ambient O2 partial pressure proxy.
    o2_mmol_mol: float = Field(default=210.0, gt=0.0, le=1000.0)
    # Bootstrap iterations for the 95% CI.  Set to 0 to skip CIs
    # (useful for interactive fits where responsiveness trumps
    # confidence).  Default 500 is fast enough for the UI (~1s).
    bootstrap_iters: int = Field(default=500, ge=0, le=5000)


def _extract_jwt(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return authorization.split(" ", 1)[1]


async def _resolve_owner_id(sb: SupabaseAuthedClient) -> str:
    try:
        me = await sb.get_user_identity()
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    owner_id = me.get("id") if me else None
    if not owner_id:
        raise HTTPException(status_code=401, detail="unable to resolve caller identity")
    return str(owner_id)


def _infer_tleaf(points: list[dict[str, Any]], override: float | None) -> float:
    """Pick the temperature to run the fit at.

    Priority: explicit override → median of the session's Tleaf column
    → 25 C.  The point-wise Tleaf column is what LI-COR actually
    measured on-leaf; if several points agree to within 2 C we use the
    median, which is more robust than any single row.
    """
    if override is not None:
        return float(override)
    leaf_temps = [
        p.get("leaf_temp_c") for p in points
        if p.get("leaf_temp_c") is not None
    ]
    values = [float(t) for t in leaf_temps if isinstance(t, int | float)]
    if values:
        return float(np.median(values))
    return 25.0


@router.post("/gas-exchange/sessions/{session_id}/gm-fit")
async def run_gm_fit(
    session_id: str,
    payload: GmFitRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    jwt = _extract_jwt(authorization)
    sb = SupabaseAuthedClient(jwt)
    owner_id = await _resolve_owner_id(sb)

    try:
        session = await sb.get_gas_exchange_session(session_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if session is None:
        raise HTTPException(status_code=404, detail="session not found or not accessible")

    try:
        points = await sb.list_gas_exchange_points(session_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if len(points) < 4:
        raise HTTPException(
            status_code=422,
            detail=(
                f"session has only {len(points)} points; need >= 4 for a "
                "g_m fit.  Check the LI-COR file covered an A-Ci curve, "
                "not just a single point"
            ),
        )

    a_vals: list[float] = []
    ci_vals: list[float] = []
    etr_vals: list[float] = []
    etr_present = 0
    for pt in points:
        a_raw = pt.get("photo_a")
        ci_raw = pt.get("ci_ppm")
        if not isinstance(a_raw, int | float) or not isinstance(ci_raw, int | float):
            continue
        a_vals.append(float(a_raw))
        ci_vals.append(float(ci_raw))
        # LI-6800 stores ETR in a column that isn't one of our typed
        # columns (it lives in `raw` as `ETR` / `J`).  Pull whichever
        # is present and finite.
        raw_block = pt.get("raw") or {}
        etr_candidate = None
        # LI-COR exports use a variety of casings for the electron-
        # transport-rate column: LI-6400 writes "ETR", LI-6800 writes
        # "J" (on newer firmware) or "ETR"; operator-edited CSVs
        # sometimes lowercase either.  Check all seen variants.
        if isinstance(raw_block, dict):
            for key in ("ETR", "J", "Etr", "etr", "j"):
                v = raw_block.get(key)
                if isinstance(v, int | float):
                    etr_candidate = float(v)
                    break
        if etr_candidate is not None and np.isfinite(etr_candidate) and etr_candidate > 0:
            etr_vals.append(etr_candidate)
            etr_present += 1
        else:
            etr_vals.append(float("nan"))

    if len(a_vals) < 4:
        raise HTTPException(
            status_code=422,
            detail=(
                f"only {len(a_vals)} points have numeric A and Ci; "
                "need >= 4 with finite values"
            ),
        )

    a_arr = np.array(a_vals, dtype=np.float64)
    ci_arr = np.array(ci_vals, dtype=np.float64)
    etr_arr: np.ndarray | None = None
    # Need at least 4 finite ETR values to run Harley; otherwise skip
    # and let the consolidated fit_all emit the skip note.
    if etr_present >= 4:
        etr_arr = np.array(etr_vals, dtype=np.float64)

    tleaf_used = _infer_tleaf(points, payload.tleaf_c)

    fit_result = fit_all(
        a_arr,
        ci_arr,
        etr=etr_arr,
        rd=payload.rd,
        tleaf_c=tleaf_used,
        o2_mmol_mol=payload.o2_mmol_mol,
        bootstrap_iters=payload.bootstrap_iters,
    )

    # Persist the full per-method blob.  Keep tleaf_c / rd / o2 as
    # first-class columns so cross-session comparisons in /compare
    # don't have to unpack jsonb for them.
    row_payload: dict[str, Any] = {
        "session_id": session_id,
        "owner_id": owner_id,
        "tleaf_c": tleaf_used,
        "rd_pa": payload.rd,
        "o2_mmol_mol": payload.o2_mmol_mol,
        "result": fit_result.to_dict(),
        "notes": "; ".join(fit_result.notes) if fit_result.notes else None,
    }
    try:
        persisted = await sb.insert_gm_fit(row_payload)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    return {
        "fit_id": persisted["id"],
        "session_id": session_id,
        "result": fit_result.to_dict(),
    }


@router.get("/gas-exchange/sessions/{session_id}/gm-fits")
async def list_gm_fits(
    session_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    sb = SupabaseAuthedClient(_extract_jwt(authorization))
    try:
        fits = await sb.list_gm_fits_for_session(session_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    return {"fits": fits, "count": len(fits)}


@router.get("/gas-exchange/gm-fits/{fit_id}")
async def get_gm_fit(
    fit_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    sb = SupabaseAuthedClient(_extract_jwt(authorization))
    try:
        row = await sb.get_gm_fit(fit_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if row is None:
        raise HTTPException(status_code=404, detail="gm_fit not found or not accessible")
    return row
