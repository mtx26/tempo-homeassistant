"""Read-only remaining-work list tests."""

from __future__ import annotations

import pytest
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tempo.todo import task_description

ENTITY = "todo.tempo_tasks"


async def test_items(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Only non-archived tasks are listed, with soft/hard and remaining minutes."""
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "2"
    assert state.attributes["supported_features"] == 0
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
    assert "souple" in items[0]["description"]
    assert "stricte" in items[1]["description"]


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("add_item", {"item": "Chimie"}),
        ("update_item", {"item": "Analyse", "status": "completed"}),
        ("remove_item", {"item": ["Analyse"]}),
    ],
)
async def test_list_is_read_only(
    hass: HomeAssistant, init_integration: MockConfigEntry, service: str, data: dict[str, object]
) -> None:
    """Changes go through the conversation, never through the to-do list."""
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            TODO_DOMAIN, service, data, target={"entity_id": ENTITY}, blocking=True
        )


def test_unknown_duration_and_no_deadline() -> None:
    assert "Durée inconnue" in task_description({"effort_minutes": None})
    assert "sans échéance" in task_description(
        {"effort_minutes": 60, "remaining_minutes": 60, "completed_minutes": 0}
    )
