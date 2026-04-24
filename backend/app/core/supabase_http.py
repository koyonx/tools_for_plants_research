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

    # ---- batch_runs -----------------------------------------------------
    async def insert_batch_run(self, row: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/batch_runs",
            headers={"Prefer": "return=representation", "Content-Type": "application/json"},
            json=row,
        )
        return cast(dict[str, Any], response.json()[0])

    async def update_batch_run(self, batch_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(
            "PATCH",
            "/rest/v1/batch_runs",
            params={"id": f"eq.{batch_id}"},
            headers={"Prefer": "return=representation", "Content-Type": "application/json"},
            json=patch,
        )
        return cast(dict[str, Any], response.json()[0])

    async def get_batch_run(self, batch_id: str) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            "/rest/v1/batch_runs",
            params={"id": f"eq.{batch_id}", "select": "*"},
        )
        rows: list[dict[str, Any]] = response.json()
        return rows[0] if rows else None

    # ---- gas_exchange (PR #11) ------------------------------------------
    async def insert_gas_exchange_session(self, row: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/gas_exchange_sessions",
            headers={"Prefer": "return=representation", "Content-Type": "application/json"},
            json=row,
        )
        return cast(dict[str, Any], response.json()[0])

    async def insert_gas_exchange_points(
        self, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Bulk-insert points.  Empty list → no-op (PostgREST rejects
        zero-row POST bodies)."""
        if not rows:
            return []
        response = await self._request(
            "POST",
            "/rest/v1/gas_exchange_points",
            headers={"Prefer": "return=representation", "Content-Type": "application/json"},
            json=rows,
        )
        out: list[dict[str, Any]] = response.json()
        return out

    async def list_gas_exchange_sessions(
        self,
        *,
        plant_id: str | None = None,
        species: str | None = None,
        photosynthesis_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """RLS-filtered session list, ordered newest-first."""
        params: dict[str, str] = {
            "select": "*",
            "order": "captured_at.desc.nullslast,created_at.desc",
        }
        if plant_id:
            params["plant_id"] = f"eq.{plant_id}"
        if species:
            params["species"] = f"eq.{species}"
        if photosynthesis_type:
            params["photosynthesis_type"] = f"eq.{photosynthesis_type}"
        response = await self._request("GET", "/rest/v1/gas_exchange_sessions", params=params)
        rows: list[dict[str, Any]] = response.json()
        return rows

    async def get_gas_exchange_session(self, session_id: str) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            "/rest/v1/gas_exchange_sessions",
            params={"id": f"eq.{session_id}", "select": "*"},
        )
        rows: list[dict[str, Any]] = response.json()
        return rows[0] if rows else None

    async def list_gas_exchange_points(
        self,
        session_id: str,
        *,
        page_size: int = 1000,
        max_rows: int = 20_000,
    ) -> list[dict[str, Any]]:
        """All points for a session, ordered by obs_index.  Walks Range
        headers like list_analyses since LI-COR A-Ci curves with many
        steps can produce >1000 observations per session."""
        params = {
            "select": "*",
            "session_id": f"eq.{session_id}",
            "order": "obs_index.asc",
        }
        all_rows: list[dict[str, Any]] = []
        offset = 0
        while offset < max_rows:
            headers = {
                "Range-Unit": "items",
                "Range": f"{offset}-{offset + page_size - 1}",
            }
            response = await self._request(
                "GET", "/rest/v1/gas_exchange_points", params=params, headers=headers
            )
            page: list[dict[str, Any]] = response.json()
            if not page:
                break
            all_rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return all_rows

    async def delete_gas_exchange_session(self, session_id: str) -> None:
        await self._request(
            "DELETE",
            "/rest/v1/gas_exchange_sessions",
            params={"id": f"eq.{session_id}"},
        )

    # ---- gm_fits (PR #13b) ----------------------------------------------
    async def insert_gm_fit(self, row: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/gm_fits",
            headers={"Prefer": "return=representation", "Content-Type": "application/json"},
            json=row,
        )
        return cast(dict[str, Any], response.json()[0])

    async def list_gm_fits_for_session(self, session_id: str) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            "/rest/v1/gm_fits",
            params={
                "session_id": f"eq.{session_id}",
                "select": "*",
                "order": "created_at.desc",
            },
        )
        rows: list[dict[str, Any]] = response.json()
        return rows

    async def get_gm_fit(self, fit_id: str) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            "/rest/v1/gm_fits",
            params={"id": f"eq.{fit_id}", "select": "*"},
        )
        rows: list[dict[str, Any]] = response.json()
        return rows[0] if rows else None

    async def latest_gm_fit_for_session(self, session_id: str) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            "/rest/v1/gm_fits",
            params={
                "session_id": f"eq.{session_id}",
                "select": "*",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        rows: list[dict[str, Any]] = response.json()
        return rows[0] if rows else None

    async def list_images_filtered(
        self,
        filters: dict[str, str] | None = None,
        *,
        page_size: int = 1000,
        max_rows: int = 20_000,
    ) -> list[dict[str, Any]]:
        """RLS-filtered image list with optional column = value filters.

        `filters` keys are column names, values become eq.<value>.  Used
        by both the batch-selector UI and the compare endpoint — the
        latter means large C3 vs C4 cohorts can exceed PostgREST's
        default 1000-row cap, so walk `Range` headers until we see a
        short page or hit max_rows.
        """
        params: dict[str, str] = {"select": "*", "order": "created_at.desc"}
        for col, val in (filters or {}).items():
            params[col] = f"eq.{val}"

        all_rows: list[dict[str, Any]] = []
        offset = 0
        while offset < max_rows:
            headers = {
                "Range-Unit": "items",
                "Range": f"{offset}-{offset + page_size - 1}",
            }
            response = await self._request(
                "GET", "/rest/v1/images", params=params, headers=headers
            )
            page: list[dict[str, Any]] = response.json()
            if not page:
                break
            all_rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return all_rows

    async def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            "/rest/v1/analyses",
            params={"id": f"eq.{analysis_id}", "select": "*"},
        )
        rows: list[dict[str, Any]] = response.json()
        return rows[0] if rows else None

    async def list_analyses(
        self,
        *,
        image_ids: list[str] | None = None,
        kind: str | None = None,
        status: str | None = None,
        order: str = "created_at.desc",
        page_size: int = 1000,
        max_rows: int = 20_000,
    ) -> list[dict[str, Any]]:
        """Filtered analyses list.  `image_ids` becomes a PostgREST
        `in.(...)` so one round-trip covers N images.

        PostgREST / Supabase ships a default `PGRST_DB_MAX_ROWS=1000`
        cap on many deployments, so a single-page fetch can silently
        drop older rows for queries with many matches (e.g. several
        pipeline reruns across 100+ images).  Walk `Range` headers
        until we observe fewer-than-page-size rows or hit `max_rows`.
        """
        params: dict[str, str] = {"select": "*", "order": order}
        if image_ids:
            params["image_id"] = "in.(" + ",".join(image_ids) + ")"
        if kind:
            params["kind"] = f"eq.{kind}"
        if status:
            params["status"] = f"eq.{status}"

        all_rows: list[dict[str, Any]] = []
        offset = 0
        while offset < max_rows:
            headers = {
                "Range-Unit": "items",
                "Range": f"{offset}-{offset + page_size - 1}",
            }
            response = await self._request(
                "GET", "/rest/v1/analyses", params=params, headers=headers
            )
            page: list[dict[str, Any]] = response.json()
            if not page:
                break
            all_rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return all_rows

    async def latest_analysis_for(
        self,
        image_id: str,
        kind: str,
        *,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        """Most recent analyses row for the given image + kind, or None.

        Pass `status="done"` to only consider completed runs — useful so
        a freshly-failed retry doesn't shadow an older successful result
        in workflows that depend on the previous output.
        """
        params: dict[str, str] = {
            "image_id": f"eq.{image_id}",
            "kind": f"eq.{kind}",
            "select": "*",
            "order": "created_at.desc",
            "limit": "1",
        }
        if status is not None:
            params["status"] = f"eq.{status}"
        response = await self._request("GET", "/rest/v1/analyses", params=params)
        rows: list[dict[str, Any]] = response.json()
        return rows[0] if rows else None

    # ---- Auth -----------------------------------------------------------
    async def get_user_identity(self) -> dict[str, Any] | None:
        """Resolve the caller's auth.users row via GoTrue's `/user` endpoint.

        Returns the user payload (id, email, ...) or `None` if the JWT is
        anonymous / service-role with no user context.
        """
        response = await self._request("GET", "/auth/v1/user")
        body = response.json()
        return body if isinstance(body, dict) and body.get("id") else None

    # ---- Storage --------------------------------------------------------
    async def download_image_bytes(self, storage_path: str) -> bytes:
        response = await self._request(
            "GET",
            f"/storage/v1/object/authenticated/images/{storage_path}",
        )
        return response.content
