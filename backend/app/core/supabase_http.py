"""Thin httpx wrapper around the Supabase REST + Storage endpoints.

The frontend forwards the user's Supabase access token in the Authorization
header on every analyze request.  We reuse that token verbatim so PostgREST
and Storage evaluate RLS against the real caller instead of a backend
service-role identity — keeping `images.owner_id = auth.uid()` checks in
effect end-to-end.
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from app.core.config import settings


class SupabaseHttpError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"supabase error {status}: {detail}")
        self.status = status
        self.detail = detail


class SupabaseAuthedClient:
    """Issues PostgREST / Storage requests with the caller's JWT."""

    def __init__(self, user_jwt: str) -> None:
        if not user_jwt:
            raise ValueError("user_jwt is required")
        self._base = settings.supabase_internal_url.rstrip("/")
        self._headers = {
            "apikey": settings.anon_key,
            "Authorization": f"Bearer {user_jwt}",
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.request(method, f"{self._base}{path}", headers=headers, **kwargs)
        if response.status_code >= 400:
            raise SupabaseHttpError(response.status_code, response.text)
        return response

    # ---- REST -----------------------------------------------------------
    async def get_image(self, image_id: str) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            "/rest/v1/images",
            params={"id": f"eq.{image_id}", "select": "*"},
        )
        rows: list[dict[str, Any]] = response.json()
        return rows[0] if rows else None

    async def list_images(self) -> list[dict[str, Any]]:
        """Every image the caller can read (RLS-filtered)."""
        response = await self._request(
            "GET",
            "/rest/v1/images",
            params={"select": "*", "order": "created_at.desc"},
        )
        rows: list[dict[str, Any]] = response.json()
        return rows

    async def list_annotations(self, image_id: str) -> list[dict[str, Any]]:
        # Order by creation time (id breaks timestamp ties) so the
        # rasteriser's last-write-wins semantics on overlapping polygons
        # stays deterministic and matches the editor's visual layering.
        response = await self._request(
            "GET",
            "/rest/v1/annotations",
            params={
                "image_id": f"eq.{image_id}",
                "select": "*",
                "order": "created_at.asc,id.asc",
            },
        )
        rows: list[dict[str, Any]] = response.json()
        return rows

    async def insert_analysis(self, row: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/analyses",
            headers={"Prefer": "return=representation", "Content-Type": "application/json"},
            json=row,
        )
        return cast(dict[str, Any], response.json()[0])

    async def update_analysis(self, analysis_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(
            "PATCH",
            "/rest/v1/analyses",
            params={"id": f"eq.{analysis_id}"},
            headers={"Prefer": "return=representation", "Content-Type": "application/json"},
            json=patch,
        )
        return cast(dict[str, Any], response.json()[0])

    async def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            "/rest/v1/analyses",
            params={"id": f"eq.{analysis_id}", "select": "*"},
        )
        rows: list[dict[str, Any]] = response.json()
        return rows[0] if rows else None

    # ---- Storage --------------------------------------------------------
    async def download_image_bytes(self, storage_path: str) -> bytes:
        response = await self._request(
            "GET",
            f"/storage/v1/object/authenticated/images/{storage_path}",
        )
        return response.content
