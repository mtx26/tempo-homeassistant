"""To-do list entity tests."""

from __future__ import annotations

import pytest
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tempo.todo import parse_effort

from .conftest import BASE, TASKS, calls

ENTITY = "todo.tempo_tasks"


async def test_items(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Only non-archived tasks are listed, with soft/hard and remaining minutes."""
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "2"
    response = await hass.services.async_call(
        TODO_DOMAIN,
        "get_items",
        {},
        target={"entity_id": ENTITY},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    items = response[ENTITY]["items"]  # type: ignore[index, call-overload]
    assert [i["summary"] for i in items] == ["Analyse", "Physique"]
    assert items[0]["due"] == "2026-12-05"
    assert "Reste 510 min" in items[0]["description"]
    assert "souple (soft)" in items[0]["description"]
    assert "stricte (hard)" in items[1]["description"]


async def test_create_item(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Creating an item posts a soft-target task; effort parsed from description."""
    aioclient_mock.post(f"{BASE}/v1/tasks", status=201, json=TASKS[0])
    await hass.services.async_call(
        TODO_DOMAIN,
        "add_item",
        {"item": "Chimie", "due_date": "2026-12-20", "description": "environ 120 min"},
        target={"entity_id": ENTITY},
        blocking=True,
    )
    posted = calls(aioclient_mock, "POST", "/v1/tasks")
    assert posted[-1][2] == {
        "subject": "Chimie",
        "effort_minutes": 120,
        "target_date": "2026-12-20",
        "target_kind": "soft",
    }

    await hass.services.async_call(
        TODO_DOMAIN,
        "add_item",
        {"item": "Exam", "due_date": "2026-12-21", "description": "hard"},
        target={"entity_id": ENTITY},
        blocking=True,
    )
    assert calls(aioclient_mock, "POST", "/v1/tasks")[-1][2] == {
        "subject": "Exam",
        "effort_minutes": 60,
        "target_date": "2026-12-21",
        "target_kind": "hard",
    }


async def test_create_requires_due(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The backend needs a target date."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            TODO_DOMAIN, "add_item", {"item": "X"}, target={"entity_id": ENTITY}, blocking=True
        )


async def test_complete_item_archives(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Completing an item archives the task."""
    aioclient_mock.patch(f"{BASE}/v1/tasks/task-1", json={**TASKS[0], "archived": True})
    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {"item": "task-1", "status": "completed"},
        target={"entity_id": ENTITY},
        blocking=True,
    )
    assert calls(aioclient_mock, "PATCH", "/v1/tasks/task-1")[-1][2] == {"archived": True}


async def test_update_due_date(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Changing the due date patches target_date."""
    aioclient_mock.patch(f"{BASE}/v1/tasks/task-2", json=TASKS[1])
    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {"item": "Physique", "due_date": "2026-12-15"},
        target={"entity_id": ENTITY},
        blocking=True,
    )
    assert calls(aioclient_mock, "PATCH", "/v1/tasks/task-2")[-1][2] == {
        "target_date": "2026-12-15"
    }


async def test_delete_items_archives(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Deleting archives every selected task."""
    aioclient_mock.patch(f"{BASE}/v1/tasks/task-1", json=TASKS[0])
    aioclient_mock.patch(f"{BASE}/v1/tasks/task-2", json=TASKS[1])
    await hass.services.async_call(
        TODO_DOMAIN,
        "remove_item",
        {"item": ["task-1", "task-2"]},
        target={"entity_id": ENTITY},
        blocking=True,
    )
    assert calls(aioclient_mock, "PATCH", "/v1/tasks/task-1")[-1][2] == {"archived": True}
    assert calls(aioclient_mock, "PATCH", "/v1/tasks/task-2")[-1][2] == {"archived": True}


async def test_backend_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Backend failures are reported as HomeAssistantError."""
    aioclient_mock.patch(f"{BASE}/v1/tasks/task-1", status=500)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            TODO_DOMAIN,
            "update_item",
            {"item": "task-1", "status": "completed"},
            target={"entity_id": ENTITY},
            blocking=True,
        )


def test_parse_effort() -> None:
    """Effort parsing falls back to 60 minutes."""
    assert parse_effort(None) == 60
    assert parse_effort("rien") == 60
    assert parse_effort("90min") == 90
    assert parse_effort("0 min") == 60
