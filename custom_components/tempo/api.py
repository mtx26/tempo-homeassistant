"""Thin asynchronous client for the private Tempo backend HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from urllib.parse import quote

import aiohttp
from yarl import URL

from .const import DEFAULT_TIMEOUT_SECONDS


class TempoError(Exception):
    """Base error raised by the Tempo API client."""


class TempoConnectionError(TempoError):
    """The backend could not be reached or answered with a server error."""


class TempoAuthError(TempoError):
    """The token was rejected (HTTP 401/403)."""


class TempoRateLimitedError(TempoError):
    """Too many requests / failed attempts (HTTP 429)."""


class TempoNotFoundError(TempoError):
    """The resource or route does not exist (HTTP 404)."""


class TempoConflictError(TempoError):
    """The request conflicts with the current state (HTTP 409)."""


class TempoApiError(TempoError):
    """Any other unexpected client-side error answered by the backend."""

    def __init__(self, status: int, message: str) -> None:
        """Store the HTTP status alongside the message."""
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def normalize_url(url: str) -> str:
    """Return a canonical base URL (lower-case scheme/host, no trailing slash)."""
    parsed = URL(url.strip())
    if not parsed.scheme or not parsed.host or parsed.scheme not in ("http", "https"):
        raise ValueError("invalid url")
    path = parsed.path.rstrip("/")
    port = f":{parsed.explicit_port}" if parsed.explicit_port is not None else ""
    return f"{parsed.scheme.lower()}://{parsed.host.lower()}{port}{path}"


def _seg(value: str) -> str:
    """Quote a single path segment."""
    return quote(value, safe="")


class TempoApiClient:
    """Client for the Tempo backend. All /v1 routes use a bearer token."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        """Initialize the client with an existing aiohttp session."""
        self._session = session
        self._base_url = normalize_url(base_url)
        self._token = token

    @property
    def base_url(self) -> str:
        """Return the normalized base URL."""
        return self._base_url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        auth: bool = True,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=json,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status in (401, 403):
                    raise TempoAuthError("authentication rejected")
                if resp.status == 429:
                    raise TempoRateLimitedError("rate limited")
                if resp.status == 404:
                    raise TempoNotFoundError(f"{method} {path} not found")
                if resp.status == 409:
                    raise TempoConflictError(await _detail(resp))
                if resp.status >= 500:
                    raise TempoConnectionError(f"server error {resp.status}")
                if resp.status >= 400:
                    raise TempoApiError(resp.status, await _detail(resp))
                if resp.status == 204:
                    return None
                body = await resp.text()
                if not body:
                    return None
                return await resp.json(content_type=None)
        except TimeoutError as err:
            raise TempoConnectionError("timeout") from err
        except aiohttp.ClientError as err:
            raise TempoConnectionError(str(err) or type(err).__name__) from err
        except ValueError as err:
            raise TempoConnectionError(f"invalid JSON from backend: {err}") from err

    # --- status -----------------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """GET /health (no authentication)."""
        return cast(dict[str, Any], await self._request("GET", "/health", auth=False))

    async def status(self) -> dict[str, Any]:
        """GET /v1/status: validates the token."""
        return cast(dict[str, Any], await self._get("/v1/status"))

    async def summary(self) -> dict[str, Any]:
        """GET /v1/summary."""
        return cast(dict[str, Any], await self._get("/v1/summary"))

    async def schedule(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """GET /v1/schedule for [start, end) (maximum 62 days)."""
        return cast(
            list[dict[str, Any]],
            await self._get(
                "/v1/schedule", params={"start": start.isoformat(), "end": end.isoformat()}
            ),
        )

    async def replan(self) -> dict[str, Any]:
        """POST /v1/replan (asynchronous; returns 202)."""
        return cast(dict[str, Any], await self._send("POST", "/v1/replan") or {})

    # --- tasks ------------------------------------------------------------------------------

    async def tasks(self) -> list[dict[str, Any]]:
        """GET /v1/tasks."""
        return cast(list[dict[str, Any]], await self._get("/v1/tasks"))

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/tasks."""
        return cast(dict[str, Any], await self._send("POST", "/v1/tasks", payload))

    async def update_task(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """PATCH /v1/tasks/{id}."""
        return cast(
            dict[str, Any], await self._send("PATCH", f"/v1/tasks/{_seg(task_id)}", payload)
        )

    # --- location / places / availability / settings -------------------------------------

    async def post_location(self, payload: dict[str, Any]) -> None:
        """POST /v1/location."""
        await self._send("POST", "/v1/location", payload)

    async def places(self) -> list[dict[str, Any]]:
        """GET /v1/places."""
        return cast(list[dict[str, Any]], await self._get("/v1/places"))

    async def put_place(self, place_id: str, payload: dict[str, Any]) -> None:
        """PUT /v1/places/{id}."""
        await self._send("PUT", f"/v1/places/{_seg(place_id)}", payload)

    async def delete_place(self, place_id: str) -> None:
        """DELETE /v1/places/{id}."""
        await self._send("DELETE", f"/v1/places/{_seg(place_id)}")

    async def availability(self) -> list[dict[str, Any]]:
        """GET /v1/availability."""
        return cast(list[dict[str, Any]], await self._get("/v1/availability"))

    async def put_availability(self, windows: list[dict[str, Any]]) -> None:
        """PUT /v1/availability (full replacement)."""
        await self._send("PUT", "/v1/availability", windows)

    async def settings(self) -> dict[str, Any]:
        """GET /v1/settings."""
        return cast(dict[str, Any], await self._get("/v1/settings"))

    async def patch_settings(self, payload: dict[str, Any]) -> None:
        """PATCH /v1/settings."""
        await self._send("PATCH", "/v1/settings", payload)

    # --- activities / blocks ----------------------------------------------------------------

    async def activities(self) -> list[dict[str, Any]]:
        """GET /v1/activities."""
        return cast(list[dict[str, Any]], await self._get("/v1/activities"))

    async def create_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/activities."""
        return cast(dict[str, Any], await self._send("POST", "/v1/activities", payload))

    async def update_activity(self, activity_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """PATCH /v1/activities/{id}."""
        return cast(
            dict[str, Any],
            await self._send("PATCH", f"/v1/activities/{_seg(activity_id)}", payload),
        )

    async def block_action(self, block_id: str, action: str) -> None:
        """POST /v1/blocks/{id}/lock|unlock|done."""
        if action not in ("lock", "unlock", "done"):
            raise ValueError(f"unsupported block action {action}")
        await self._send("POST", f"/v1/blocks/{_seg(block_id)}/{action}")

    # --- proposals / notifications / interpret ---------------------------------------------

    async def proposals(self) -> list[dict[str, Any]]:
        """GET /v1/proposals."""
        return cast(list[dict[str, Any]], await self._get("/v1/proposals"))

    async def decide_proposal(self, proposal_id: str, approve: bool) -> dict[str, Any]:
        """POST /v1/proposals/{id}/approve|reject."""
        action = "approve" if approve else "reject"
        return cast(
            dict[str, Any],
            await self._send("POST", f"/v1/proposals/{_seg(proposal_id)}/{action}") or {},
        )

    async def notifications(self, unread: bool = True) -> list[dict[str, Any]]:
        """GET /v1/notifications."""
        params = {"unread": "true"} if unread else None
        return cast(list[dict[str, Any]], await self._get("/v1/notifications", params=params))

    async def ack_notification(self, notification_id: str) -> None:
        """POST /v1/notifications/{id}/ack."""
        await self._send("POST", f"/v1/notifications/{_seg(notification_id)}/ack")

    async def interpret(self, text: str) -> dict[str, Any]:
        """POST /v1/interpret (natural-language helper, optional on older backends)."""
        return cast(dict[str, Any], await self._send("POST", "/v1/interpret", {"text": text}))

    # --- helpers ----------------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _send(self, method: str, path: str, payload: Any = None) -> Any:
        return await self._request(method, path, json=payload)


async def _detail(resp: aiohttp.ClientResponse) -> str:
    """Extract a short error detail from a FastAPI-style error body."""
    try:
        data = await resp.json(content_type=None)
    except (aiohttp.ClientError, ValueError):
        return getattr(resp, "reason", None) or "error"
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])[:300]
    return getattr(resp, "reason", None) or "error"
