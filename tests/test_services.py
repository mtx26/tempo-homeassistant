"""Service action tests: each service hits the right endpoint with the right payload."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util.yaml import load_yaml_dict
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tempo.const import DOMAIN

from .conftest import BASE, calls

COMPONENT = Path(__file__).parents[1] / "custom_components" / DOMAIN
SERVICES_YAML = COMPONENT / "services.yaml"
STRINGS = COMPONENT / "strings.json"


@pytest.mark.parametrize(
    ("service", "data", "method", "path", "payload"),
    [
        (
            "add_task",
            {"subject": "Analyse", "effort_minutes": 120, "target_date": "2026-12-01"},
            "POST",
            "/v1/tasks",
            {
                "subject": "Analyse",
                "effort_minutes": 120,
                "target_date": "2026-12-01",
                "target_kind": "soft",
            },
        ),
        (
            "add_task",
            {
                "subject": "Exam",
                "effort_minutes": 60,
                "target_date": "2026-12-01",
                "hard_deadline": True,
            },
            "POST",
            "/v1/tasks",
            {
                "subject": "Exam",
                "effort_minutes": 60,
                "target_date": "2026-12-01",
                "target_kind": "hard",
            },
        ),
        ("replan", {}, "POST", "/v1/replan", None),
        (
            "approve_proposal",
            {"proposal_id": "prop-1"},
            "POST",
            "/v1/proposals/prop-1/approve",
            None,
        ),
        ("reject_proposal", {"proposal_id": "prop-1"}, "POST", "/v1/proposals/prop-1/reject", None),
        ("lock_block", {"block_id": "block-1"}, "POST", "/v1/blocks/block-1/lock", None),
        ("unlock_block", {"block_id": "block-1"}, "POST", "/v1/blocks/block-1/unlock", None),
        ("complete_block", {"block_id": "block-1"}, "POST", "/v1/blocks/block-1/done", None),
        (
            "add_training",
            {
                "title": "Footing",
                "duration_minutes": 45,
                "start": "2026-11-24T18:00:00",
                "earliest_start": "2026-11-24T16:00:00+01:00",
                "latest_end": "2026-11-24T21:00:00+01:00",
                "notes": "Zone 2",
                "place_id": "park",
            },
            "POST",
            "/v1/activities",
            {
                "kind": "training",
                "title": "Footing",
                "duration_minutes": 45,
                "start": "2026-11-24T18:00:00+01:00",
                "earliest_start": "2026-11-24T16:00:00+01:00",
                "latest_end": "2026-11-24T21:00:00+01:00",
                "notes": "Zone 2",
                "place_id": "park",
            },
        ),
        (
            "add_race",
            {
                "title": "Corrida",
                "start": "2026-12-31T15:00:00+01:00",
                "duration_minutes": 60,
                "place_id": "center",
            },
            "POST",
            "/v1/activities",
            {
                "kind": "race",
                "title": "Corrida",
                "start": "2026-12-31T15:00:00+01:00",
                "duration_minutes": 60,
                "place_id": "center",
            },
        ),
        (
            "set_place",
            {
                "place_id": "campus",
                "name": "Campus",
                "latitude": 50.81,
                "longitude": 4.38,
                "aliases": ["ULB", "Plaine"],
            },
            "PUT",
            "/v1/places/campus",
            {"name": "Campus", "latitude": 50.81, "longitude": 4.38, "aliases": ["ULB", "Plaine"]},
        ),
        (
            "set_availability",
            {
                "windows": [
                    {"weekday": 0, "start": "08:00", "end": "18:00", "place_id": "home"},
                    {"weekday": 6, "start": "20:00", "end": "24:00", "place_id": "home"},
                ]
            },
            "PUT",
            "/v1/availability",
            [
                {"weekday": 0, "start": "08:00", "end": "18:00", "place_id": "home"},
                {"weekday": 6, "start": "20:00", "end": "24:00", "place_id": "home"},
            ],
        ),
        (
            "acknowledge_notification",
            {"notification_id": "n-9"},
            "POST",
            "/v1/notifications/n-9/ack",
            None,
        ),
    ],
)
async def test_service_calls_backend(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    service: str,
    data: dict[str, Any],
    method: str,
    path: str,
    payload: Any,
) -> None:
    """The service sends the expected request."""
    aioclient_mock.request(method, f"{BASE}{path}", status=200, json={})
    await hass.services.async_call(DOMAIN, service, data, blocking=True)
    sent = calls(aioclient_mock, method, path)
    assert sent, f"{method} {path} not called"
    assert sent[-1][2] == payload


async def test_service_with_explicit_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """config_entry_id selects the entry; unknown ids are rejected."""
    aioclient_mock.post(f"{BASE}/v1/replan", status=202, json={"requested_at": "x"})
    await hass.services.async_call(
        DOMAIN, "replan", {"config_entry_id": init_integration.entry_id}, blocking=True
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "replan", {"config_entry_id": "missing"}, blocking=True
        )


async def test_service_without_loaded_entry(hass: HomeAssistant) -> None:
    """Calling a service with no loaded entry fails clearly."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "replan", {}, blocking=True)


@pytest.mark.parametrize(
    ("status", "error"),
    [(404, ServiceValidationError), (409, ServiceValidationError), (500, HomeAssistantError)],
)
async def test_service_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    status: int,
    error: type[Exception],
) -> None:
    """Backend errors are translated."""
    aioclient_mock.post(f"{BASE}/v1/proposals/p/approve", status=status, json={"detail": "x"})
    with pytest.raises(error):
        await hass.services.async_call(
            DOMAIN, "approve_proposal", {"proposal_id": "p"}, blocking=True
        )


async def test_invalid_payloads(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Schemas reject invalid place ids, windows and empty texts."""
    import voluptuous as vol

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "set_place",
            {"place_id": "Bad Id", "name": "x", "latitude": 1, "longitude": 1},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_availability",
            {"windows": [{"weekday": 0, "start": "18:00", "end": "08:00", "place_id": "home"}]},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "add_training",
            {
                "title": "x",
                "duration_minutes": 30,
                "earliest_start": "2026-11-24T18:00:00+01:00",
                "latest_end": "2026-11-24T17:00:00+01:00",
            },
            blocking=True,
        )


async def test_interpret(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """interpret returns the backend response data."""
    aioclient_mock.post(
        f"{BASE}/v1/interpret",
        json={
            "understood": True,
            "action": "add_task",
            "summary": "Tâche ajoutée",
            "applied": True,
        },
    )
    response = await hass.services.async_call(
        DOMAIN, "interpret", {"text": "Ajoute 2 h d'analyse"}, blocking=True, return_response=True
    )
    assert response == {
        "understood": True,
        "action": "add_task",
        "summary": "Tâche ajoutée",
        "applied": True,
    }
    assert calls(aioclient_mock, "POST", "/v1/interpret")[-1][2] == {"text": "Ajoute 2 h d'analyse"}


async def test_interpret_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Older backends without /v1/interpret produce a clear error."""
    aioclient_mock.post(f"{BASE}/v1/interpret", status=404)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN, "interpret", {"text": "x"}, blocking=True, return_response=True
        )
    assert err.value.translation_key == "interpret_unavailable"


async def test_service_descriptions_load(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """services.yaml is valid and describes every registered service."""
    descriptions = await hass.async_add_executor_job(load_yaml_dict, str(SERVICES_YAML))
    assert set(descriptions) == set(hass.services.async_services_for_domain(DOMAIN))
    assert "proposal_id" in descriptions["reject_proposal"]["fields"]
    assert "config_entry_id" in descriptions["replan"]["fields"]
    strings = json.loads(STRINGS.read_text(encoding="utf-8"))
    for name, desc in descriptions.items():
        assert set(desc["fields"]) == set(strings["services"][name]["fields"]), name
