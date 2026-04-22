"""Gas-exchange (LI-COR) ingestion + query endpoints.

Workflow
--------
1. Operator uploads a LI-6400 / LI-6800 / generic CSV file via
   `POST /gas-exchange/upload` (multipart).
2. The robust parser auto-detects the instrument and column layout
   (`pipeline/licor_parse.py`), normalises columns to a fixed schema,
   and preserves unrecognised columns as `raw` JSON per point.
3. We insert one `gas_exchange_sessions` row + N `gas_exchange_points`
   rows under the caller's identity, so RLS keeps each lab member's
   data isolated.
4. List / detail / delete endpoints power the dashboard UI.

Sessions are joined to images via `plant_id` / `species` so the same
plant's morphology (Cellpose / SegFormer / CO2 morphometrics) and gas
exchange (this PR) can be analysed together — the prerequisite for
the future g_m / Darcy / CO2 PDE PRs.
"""

from __future__ import annotations

import contextlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.core.supabase_http import SupabaseAuthedClient, SupabaseHttpError
from app.pipeline.licor_parse import LicorParseError, parse_file

router = APIRouter()

# Per-file size cap.  LI-6800 logs over a 12-hour A-Ci campaign rarely
# exceed a few MB; the cap is here to protect the backend memory and
# the PostgREST insert payload (default 1 MB JSON limit unless
# kong.conf is bumped).  Operator gets a clear error before the parser
# starts reading.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


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


class UploadOverrides(BaseModel):
    """Optional fields the operator can attach to the parsed session.
    All None → use whatever the file's metadata carries."""

    label: str | None = Field(default=None, max_length=200)
    plant_id: str | None = Field(default=None, max_length=200)
    species: str | None = Field(default=None, max_length=200)
    photosynthesis_type: str | None = Field(default=None, max_length=20)
    treatment: str | None = Field(default=None, max_length=200)


def _overrides_from_form(
    label: Annotated[str | None, Form()] = None,
    plant_id: Annotated[str | None, Form()] = None,
    species: Annotated[str | None, Form()] = None,
    photosynthesis_type: Annotated[str | None, Form()] = None,
    treatment: Annotated[str | None, Form()] = None,
) -> UploadOverrides:
    return UploadOverrides(
        label=label,
        plant_id=plant_id,
        species=species,
        photosynthesis_type=photosynthesis_type,
        treatment=treatment,
    )


@router.post("/gas-exchange/upload")
async def upload_gas_exchange_file(
    file: Annotated[UploadFile, File(description="LI-6400/6800 .xlsx or generic CSV/TSV")],
    overrides: Annotated[UploadOverrides, Depends(_overrides_from_form)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Parse + persist a single LI-COR file.

    Returns the inserted session row plus the count of points.  The
    raw points are NOT echoed back — fetch via GET on the returned
    session id when the UI needs them.
    """
    jwt = _extract_jwt(authorization)
    sb = SupabaseAuthedClient(jwt)
    owner_id = await _resolve_owner_id(sb)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"file is {len(raw) / 1024 / 1024:.1f} MB, "
                f"max accepted is {MAX_UPLOAD_BYTES // 1024 // 1024} MB"
            ),
        )

    try:
        parsed = parse_file(raw, file_name=file.filename)
    except LicorParseError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not parsed.points:
        raise HTTPException(
            status_code=422,
            detail=(
                "file parsed but no measurement rows were found. "
                "Make sure the file contains a LI-COR data block, not just the header."
            ),
        )

    session_payload: dict[str, Any] = {
        "owner_id": owner_id,
        "label": overrides.label,
        "instrument": parsed.instrument,
        "source_format": parsed.source_format,
        "file_name": parsed.file_name,
        "captured_at": parsed.captured_at,
        "species": overrides.species,
        "photosynthesis_type": overrides.photosynthesis_type,
        "plant_id": overrides.plant_id,
        "treatment": overrides.treatment,
        "point_count": len(parsed.points),
        "metadata": parsed.metadata,
        "notes": parsed.notes,
    }

    try:
        session = await sb.insert_gas_exchange_session(session_payload)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    session_id = session["id"]
    points_payload = [
        {
            "session_id": session_id,
            "owner_id": owner_id,
            **p.to_dict(),
        }
        for p in parsed.points
    ]
    try:
        await sb.insert_gas_exchange_points(points_payload)
    except SupabaseHttpError as e:
        # Best-effort cleanup: the session row would otherwise dangle
        # without its observations.  CASCADE on delete + the FK on
        # points means dropping the session also drops any points
        # that did land before the failure.
        with contextlib.suppress(SupabaseHttpError):
            await sb.delete_gas_exchange_session(session_id)
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    return {
        "session": session,
        "point_count": len(parsed.points),
    }


@router.get("/gas-exchange/sessions")
async def list_sessions(
    plant_id: str | None = None,
    species: str | None = None,
    photosynthesis_type: str | None = None,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    sb = SupabaseAuthedClient(_extract_jwt(authorization))
    try:
        sessions = await sb.list_gas_exchange_sessions(
            plant_id=plant_id,
            species=species,
            photosynthesis_type=photosynthesis_type,
        )
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/gas-exchange/sessions/{session_id}")
async def get_session(
    session_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    sb = SupabaseAuthedClient(_extract_jwt(authorization))
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
    return {"session": session, "points": points}


@router.delete("/gas-exchange/sessions/{session_id}")
async def delete_session(
    session_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Delete a session.  RLS ensures only the owner can succeed; the
    DB's `ON DELETE CASCADE` on `gas_exchange_points` removes the
    associated point rows."""
    sb = SupabaseAuthedClient(_extract_jwt(authorization))
    try:
        existing = await sb.get_gas_exchange_session(session_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    if existing is None:
        raise HTTPException(status_code=404, detail="session not found or not accessible")
    try:
        await sb.delete_gas_exchange_session(session_id)
    except SupabaseHttpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    return {"deleted": session_id}
