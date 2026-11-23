"""API client unit tests."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tempo.api import (
    TempoApiClient,
    TempoApiError,
    TempoConnectionError,
    normalize_url,
)

from .conftest import BASE, TOKEN, calls


def test_normalize_url() -> None:
    """URLs are canonicalized for the unique id."""
    assert normalize_url(" HTTP://Tempo-API:8000/ ") == "http://tempo-api:8000"
    assert normalize_url("https://host/tempo/") == "https://host/tempo"
    for bad in ("tempo-api:8000", "ftp://x", "", "http://"):
        with pytest.raises(ValueError):
            normalize_url(bad)


async def test_health_without_auth_and_quoting(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """/health has no Authorization header; ids are quoted in paths."""
    client = TempoApiClient(async_get_clientsession(hass), BASE, TOKEN)
    aioclient_mock.get(f"{BASE}/health", json={"status": "ok"})
    aioclient_mock.post(f"{BASE}/v1/blocks/a%2Fb/lock", status=204)
    assert await client.health() == {"status": "ok"}
    assert "Authorization" not in calls(aioclient_mock, "GET", "/health")[0][3]
    await client.block_action("a/b", "lock")
    with pytest.raises(ValueError):
        await client.block_action("x", "delete")


async def test_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """4xx with detail and invalid JSON are typed."""
    client = TempoApiClient(async_get_clientsession(hass), BASE, TOKEN)
    aioclient_mock.post(f"{BASE}/v1/tasks", status=422, json={"detail": "effort too large"})
    aioclient_mock.get(f"{BASE}/v1/tasks", text="not json")
    with pytest.raises(TempoApiError, match="effort too large") as err:
        await client.create_task({})
    assert err.value.status == 422
    with pytest.raises(TempoConnectionError):
        await client.tasks()
