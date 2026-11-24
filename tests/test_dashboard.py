"""Dashboard generation and the install/remove services."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.tempo.const import CONF_DASHBOARD, DOMAIN
from custom_components.tempo.dashboard import (
    DASHBOARD_URL_PATH,
    ENTITY_KEYS,
    async_resolve_entities,
    build_dashboard_config,
)

from .conftest import mock_backend, setup_entry

ALL_ENTITIES = {key: f"{domain}.tempo_{key}" for key, (domain, _) in ENTITY_KEYS.items()}
CORE_CARDS = {
    "heading",
    "calendar",
    "tile",
    "markdown",
    "todo-list",
    "conditional",
    "vertical-stack",
    "grid",
    "button",
}


def _cards(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten every card of the dashboard (including nested ones)."""
    found: list[dict[str, Any]] = []

    def walk(card: dict[str, Any]) -> None:
        found.append(card)
        if "card" in card:
            walk(card["card"])
        for child in card.get("cards", []):
            walk(child)

    for view in config["views"]:
        for section in view["sections"]:
            for card in section["cards"]:
                walk(card)
    return found


def test_build_dashboard_config_uses_only_core_cards() -> None:
    """The generated dashboard references every entity and only built-in cards."""
    config = build_dashboard_config(ALL_ENTITIES, "fr")
    view = config["views"][0]
    assert view["type"] == "sections"
    cards = _cards(config)
    assert {card["type"] for card in cards} <= CORE_CARDS
    text = repr(config)
    for key, entity_id in ALL_ENTITIES.items():
        if key not in ("approve_oldest_proposal", "reject_oldest_proposal"):
            assert entity_id in text, entity_id
    headings = [c["heading"] for c in cards if c["type"] == "heading"]
    assert headings == [
        "Agenda",
        "Prochain départ",
        "Tâches d'étude",
        "Entraînements à confirmer",
        "Approbations",
        "État du planning",
        "Configuration",
    ]
    calendar = next(c for c in cards if c["type"] == "calendar")
    assert calendar["entities"] == ["calendar.tempo_calendar"]
    todo = next(c for c in cards if c["type"] == "todo-list")
    assert todo["entity"] == "todo.tempo_tasks"
    # Buttons are pressed through perform-action (works on phones without more-info).
    pressed = [
        c["tap_action"]["target"]["entity_id"]
        for c in cards
        if c.get("tap_action", {}).get("perform_action") == "button.press"
    ]
    # The "oldest proposal" buttons are not on the dashboard (their target may change
    # after the page was loaded); explicit per-proposal actions are used instead.
    assert pressed == ["button.tempo_replan"]
    # Approvals and trainings-to-confirm details are hidden when the count is 0.
    conditionals = [c for c in cards if c["type"] == "conditional"]
    assert {c["conditions"][0]["entity"] for c in conditionals} == {
        ALL_ENTITIES["pending_proposals"],
        ALL_ENTITIES["trainings_to_confirm"],
    }
    assert "tempo.confirm_activity" in text


def test_build_dashboard_config_english_and_partial() -> None:
    """English texts; sections without entities are dropped (configuration help stays)."""
    config = build_dashboard_config({"calendar": "calendar.tempo"}, "en")
    sections = config["views"][0]["sections"]
    assert [s["cards"][0]["heading"] for s in sections] == ["Agenda", "Configuration"]
    empty = build_dashboard_config({}, None)["views"][0]["sections"]
    assert [s["cards"][0]["heading"] for s in empty] == ["Configuration"]


async def test_resolve_entities(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Every dashboard entity is created by the integration and resolved by unique id."""
    resolved = async_resolve_entities(hass, init_integration.entry_id)
    assert set(resolved) == set(ENTITY_KEYS)
    assert resolved["calendar"] == "calendar.tempo"
    assert resolved["replan"] == "button.tempo_replan"
    for entity_id in resolved.values():
        assert hass.states.get(entity_id) is not None, entity_id


@pytest.fixture
async def lovelace(hass: HomeAssistant) -> None:
    """Set up the Lovelace component (frontend panels are registered in hass.data)."""
    assert await async_setup_component(hass, "lovelace", {})


async def test_install_and_remove_dashboard(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    lovelace: None,
    init_integration: MockConfigEntry,
) -> None:
    """install_dashboard registers a panel served by the Lovelace websocket API."""
    response = await hass.services.async_call(
        DOMAIN, "install_dashboard", {}, blocking=True, return_response=True
    )
    assert response == {"url_path": DASHBOARD_URL_PATH}
    assert init_integration.options[CONF_DASHBOARD] is True
    panel = hass.data[DATA_PANELS][DASHBOARD_URL_PATH]
    assert panel.component_name == "lovelace"
    assert panel.sidebar_title == "Tempo"

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "lovelace/config", "url_path": DASHBOARD_URL_PATH})
    msg = await client.receive_json()
    assert msg["success"], msg
    assert msg["result"]["views"][0]["type"] == "sections"
    assert "todo.tempo_tasks" in repr(msg["result"])

    # Read-only: saving from the UI is refused.
    await client.send_json_auto_id(
        {"type": "lovelace/config/save", "url_path": DASHBOARD_URL_PATH, "config": {}}
    )
    msg = await client.receive_json()
    assert not msg["success"]

    # Installing again only updates the panel.
    await hass.services.async_call(DOMAIN, "install_dashboard", {}, blocking=True)

    await hass.services.async_call(DOMAIN, "remove_dashboard", {}, blocking=True)
    assert init_integration.options[CONF_DASHBOARD] is False
    assert DASHBOARD_URL_PATH not in hass.data[DATA_PANELS]
    assert DASHBOARD_URL_PATH not in hass.data[LOVELACE_DATA].dashboards


async def test_dashboard_restored_on_setup_and_removed_on_unload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    lovelace: None,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The persisted option re-registers the dashboard; unloading removes it."""
    mock_backend(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=mock_config_entry.unique_id,
        data=dict(mock_config_entry.data),
        options={CONF_DASHBOARD: True},
    )
    await setup_entry(hass, entry)
    assert DASHBOARD_URL_PATH in hass.data[DATA_PANELS]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DASHBOARD_URL_PATH not in hass.data[DATA_PANELS]


async def test_dashboard_url_taken(
    hass: HomeAssistant, lovelace: None, init_integration: MockConfigEntry
) -> None:
    """An existing panel with the same URL is never overwritten."""
    from homeassistant.components import frontend

    frontend.async_register_built_in_panel(
        hass, "lovelace", frontend_url_path=DASHBOARD_URL_PATH, config={"mode": "storage"}
    )
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, "install_dashboard", {}, blocking=True)
    assert err.value.translation_key == "dashboard_url_taken"
    assert init_integration.options.get(CONF_DASHBOARD) is not True


async def test_dashboard_without_lovelace(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Without Lovelace loaded the service fails clearly."""
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, "install_dashboard", {}, blocking=True)
    assert err.value.translation_key == "dashboard_unavailable"


async def test_markdown_templates_render(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Every markdown template renders against the real entity states."""
    from homeassistant.helpers.template import Template

    config = build_dashboard_config(async_resolve_entities(hass, init_integration.entry_id), "fr")
    rendered = [
        Template(card["content"], hass).async_render(parse_result=False)
        for card in _cards(config)
        if card["type"] == "markdown"
    ]
    text = "\n".join(rendered)
    assert "Partir à 09:10" in text
    assert "Home → Campus Plaine" in text
    assert "BUS 71: Gare Centrale → Campus Plaine" in text
    assert "Bus 71 delayed" in text
    assert "Fractionné 10x400" in text
    assert "`act-3`" in text
    assert "Domicile : Kot" in text
    assert "Horizon : 14 jours" in text
    assert "Marge : 10 min" in text
    assert "Parents : 2026-12-20T10:00:00+01:00 → 2026-12-27T18:00:00+01:00" in text
    assert "Kot (`home`)" in text
    assert "Déplacer l'entraînement accepté" in text
    assert "Séances d'étude manquées : 2" in text
    assert "Realtime data unavailable for STIB" in text


def test_per_proposal_actions_with_confirmation() -> None:
    """Each pending proposal gets approve/reject actions with its id and a confirmation."""
    proposals = [
        {"id": "prop-1", "title": "Déplacer\nFooting", "created_at": "2026-11-23T07:00"},
        {"id": "prop-2", "title": "Course", "created_at": "2026-11-23T08:00"},
    ]
    config = build_dashboard_config(ALL_ENTITIES, "fr", proposals, "entry-1")
    actions = [
        c["tap_action"]
        for c in _cards(config)
        if c.get("tap_action", {}).get("perform_action", "").endswith("_proposal")
    ]
    assert [(a["perform_action"], a["data"]["proposal_id"]) for a in actions] == [
        ("tempo.approve_proposal", "prop-1"),
        ("tempo.reject_proposal", "prop-1"),
        ("tempo.approve_proposal", "prop-2"),
        ("tempo.reject_proposal", "prop-2"),
    ]
    assert all(a["data"]["config_entry_id"] == "entry-1" for a in actions)
    assert actions[0]["confirmation"]["text"] == "Approuver la proposition « Déplacer Footing » ?"
    assert actions[3]["confirmation"]["text"] == "Refuser la proposition « Course » ?"
    # Every Tempo action of the dashboard asks for confirmation.
    for card in _cards(config):
        action = card.get("tap_action", {})
        if action.get("perform_action", "").startswith("tempo."):
            assert action["confirmation"]["text"]


async def test_backend_strings_are_escaped(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Links, images, HTML and headings from backend strings are neutralized."""
    from copy import deepcopy

    from homeassistant.helpers.template import Template

    from .conftest import PROPOSALS, SUMMARY

    evil = "![x](http://evil/i.png) [click](http://evil) <img src=x> # h *b* `c`"
    summary = deepcopy(SUMMARY)
    summary["issues"] = [evil]
    summary["warnings"] = []
    summary["departure_check"]["reason"] = evil
    summary["departure_check"]["warnings"] = [f"STIB: {evil}"]
    summary["next_departure"]["legs"][1]["to"] = evil
    proposals = [{**PROPOSALS[0], "title": evil, "id": "p`1"}]
    mock_backend(aioclient_mock, summary=summary, proposals=proposals)
    await setup_entry(hass, mock_config_entry)

    config = build_dashboard_config(async_resolve_entities(hass, mock_config_entry.entry_id), "en")
    rendered = [
        Template(card["content"], hass).async_render(parse_result=False)
        for card in _cards(config)
        if card["type"] == "markdown"
    ]
    text = "\n".join(rendered)
    escaped = (
        r"\!\[x\]\(http://evil/i.png\) \[click\]\(http://evil\) \<img src=x\> \# h \*b\* \`c\`"
    )
    # issue, reason, STIB warning, leg destination (twice: header + leg), proposal title
    assert text.count(escaped) == 6, text
    assert "![x]" not in text.replace(r"\!\[x\]", "")
    assert "<img" not in text.replace(r"\<img", "")
    assert "`p1`" in text  # identifiers cannot break out of their code span


async def test_dashboard_require_admin_option(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    lovelace: None,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The panel is public by default and admin-only when the option is set."""
    from custom_components.tempo.const import CONF_DASHBOARD_REQUIRE_ADMIN

    mock_backend(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=mock_config_entry.unique_id,
        data=dict(mock_config_entry.data),
        options={CONF_DASHBOARD: True, CONF_DASHBOARD_REQUIRE_ADMIN: True},
    )
    await setup_entry(hass, entry)
    assert hass.data[DATA_PANELS][DASHBOARD_URL_PATH].require_admin is True
    assert hass.data[LOVELACE_DATA].dashboards[DASHBOARD_URL_PATH].config["require_admin"]

    hass.config_entries.async_update_entry(entry, options={CONF_DASHBOARD: True})
    await hass.services.async_call(DOMAIN, "install_dashboard", {}, blocking=True)
    assert hass.data[DATA_PANELS][DASHBOARD_URL_PATH].require_admin is False


async def test_lovelace_updated_when_proposals_change(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    lovelace: None,
    init_integration: MockConfigEntry,
) -> None:
    """Open dashboards are told to reload when the pending proposals change."""
    from pytest_homeassistant_custom_component.common import async_capture_events

    from .conftest import PROPOSALS, poll

    await hass.services.async_call(DOMAIN, "install_dashboard", {}, blocking=True)
    events = async_capture_events(hass, "lovelace_updated")
    await poll(hass)
    assert events == []
    aioclient_mock.clear_requests()
    mock_backend(
        aioclient_mock,
        proposals=[*PROPOSALS, {**PROPOSALS[0], "id": "prop-2", "title": "Autre"}],
    )
    await poll(hass)
    assert [e.data for e in events] == [{"url_path": DASHBOARD_URL_PATH}]
