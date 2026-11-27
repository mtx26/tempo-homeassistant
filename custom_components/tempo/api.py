"""Thin asynchronous client for the private Tempo backend HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

import aiohttp
from yarl import URL

from .const import CONVERSATION_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS, LLM_PROBE_SECONDS


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
        seconds: float = DEFAULT_TIMEOUT_SECONDS,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=json,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=seconds),
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

    async def post_location(self, payload: dict[str, Any]) -> None:
        """POST /v1/location: the phone's real GPS position (telemetry, not an operation)."""
        await self._send("POST", "/v1/location", payload)

    # --- reads and technical settings: the same operation catalogue as the conversation --

    async def summary(self) -> dict[str, Any]:
        """Operation get_summary."""
        return await self.result("get_summary")

    async def schedule(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Operation get_schedule for [start, end) (maximum 62 days)."""
        found = await self.result(
            "get_schedule", {"start": start.isoformat(), "end": end.isoformat()}
        )
        return list(found["items"])

    async def tasks(self) -> list[dict[str, Any]]:
        """Operation list_tasks (non-archived tasks)."""
        return list((await self.result("list_tasks"))["tasks"])

    async def places(self) -> list[dict[str, Any]]:
        """Operation list_places."""
        return list((await self.result("list_places"))["places"])

    async def context(self) -> dict[str, Any]:
        """Operation get_context: home place, tracker, horizon, margins, planned locations."""
        return await self.result("get_context")

    async def proposals(self) -> list[dict[str, Any]]:
        """Operation list_proposals (pending)."""
        return list((await self.result("list_proposals"))["proposals"])

    async def notifications(self) -> list[dict[str, Any]]:
        """Operation list_notifications (unread)."""
        found = await self.result("list_notifications", {"unread_only": True, "limit": 50})
        return list(found["notifications"])

    async def ack_notification(self, notification_id: str) -> None:
        """Operation acknowledge_notification."""
        await self.result("acknowledge_notification", {"id": notification_id})

    async def update_settings(self, values: dict[str, Any]) -> None:
        """Operation update_settings (technical setup: tracker, home place)."""
        await self.result("update_settings", values)

    async def llm(self) -> dict[str, Any]:
        """GET /v1/llm: LLM enabled, model in use, default and selectable models."""
        return cast(dict[str, Any], await self._get("/v1/llm"))

    async def set_llm_model(self, model: str | None) -> dict[str, Any]:
        """PUT /v1/llm: the backend probes the model before saving it (422 if inaccessible)."""
        return cast(
            dict[str, Any],
            await self._request("PUT", "/v1/llm", json={"model": model}, seconds=LLM_PROBE_SECONDS),
        )

    async def conversation(
        self,
        text: str,
        conversation_id: str | None,
        *,
        user_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """POST /v1/conversation: natural-language request, real outcome in ``reply``."""
        payload: dict[str, Any] = {"text": text}
        if conversation_id:
            payload["conversation_id"] = conversation_id[:100]
        if user_id:
            payload["user_id"] = user_id
        key = idempotency_key or uuid4().hex
        for last_attempt in (False, True):
            try:
                return cast(
                    dict[str, Any],
                    await self._request(
                        "POST",
                        "/v1/conversation",
                        json=payload,
                        seconds=CONVERSATION_TIMEOUT_SECONDS,
                        idempotency_key=key,
                    ),
                )
            except TempoConnectionError as err:
                if last_attempt or not isinstance(err.__cause__, aiohttp.ServerDisconnectedError):
                    raise
        raise AssertionError("conversation retry did not complete")

    async def operation(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/operations/{name}: ``{"ok": true, "result": ...}`` (422 when refused)."""
        return cast(
            dict[str, Any], await self._send("POST", f"/v1/operations/{_seg(name)}", arguments)
        )

    async def result(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run an operation and return its result."""
        outcome = await self.operation(name, arguments or {})
        return cast(dict[str, Any], outcome["result"])

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
