"""Shared fixtures for Tempo tests (the backend HTTP API is always mocked)."""

from __future__ import annotations

import re
from collections.abc import Generator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tempo.const import CONF_TRACKER_ENTITY_ID, DOMAIN

BASE = "http://tempo-api:8000"
TOKEN = "super-secret-token"
TRACKER = "device_tracker.phone"
NOW = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)  # 08:00 Europe/Brussels

SUMMARY: dict[str, Any] = {
    "now": "2026-11-23T08:00:00+01:00",
    "plan_status": "partial",
    "planned_at": "2026-11-23T07:55:00+01:00",
    "issues": ["Analyse: 30 min could not be planned before the soft target"],
    "warnings": ["Realtime data unavailable for STIB"],
    "unplanned_minutes": 30,
    "missed_blocks": 2,
    "next_event": {
        "type": "class",
        "id": "class-1",
        "title": "Algèbre",
        "start": "2026-11-23T10:00:00+01:00",
        "end": "2026-11-23T12:00:00+01:00",
        "location": "Campus Plaine",
        "fixed": True,
    },
    "next_departure": {
        "type": "travel",
        "id": "travel-1",
        "title": "Home → Campus Plaine",
        "start": "2026-11-23T09:20:00+01:00",
        "end": "2026-11-23T09:55:00+01:00",
        "fixed": False,
        "status": "routed",
        "departure": "2026-11-23T09:20:00+01:00",
        "arrival": "2026-11-23T09:55:00+01:00",
        "legs": [
            {
                "mode": "WALK",
                "line": None,
                "agency": None,
                "from": "Home",
                "to": "Gare Centrale",
                "start": "2026-11-23T09:20:00+01:00",
                "end": "2026-11-23T09:30:00+01:00",
                "realtime": False,
            },
            {
                "mode": "BUS",
                "line": "71",
                "agency": "STIB",
                "from": "Gare Centrale",
                "to": "Campus Plaine",
                "start": "2026-11-23T09:32:00+01:00",
                "end": "2026-11-23T09:55:00+01:00",
                "realtime": True,
            },
        ],
    },
    "departure_check": {
        "status": "leave_earlier",
        "leave_at": "2026-11-23T09:10:00+01:00",
        "minutes_late": 0,
        "reason": "Bus 71 delayed",
        "warnings": [],
        "checked_at": "2026-11-23T07:59:00+01:00",
        "event_title": "Algèbre",
    },
    "remaining_study_minutes": 600,
    "today_study_minutes": 90,
    "pending_proposals": 1,
    "unread_notifications": 1,
}

SCHEDULE: list[dict[str, Any]] = [
    SUMMARY["next_departure"],
    SUMMARY["next_event"],
    {
        "type": "study",
        "id": "study-1",
        "title": "Étude : Analyse",
        "start": "2026-11-23T13:00:00+01:00",
        "end": "2026-11-23T14:30:00+01:00",
        "location": "Bibliothèque",
        "fixed": False,
        "locked": True,
        "block_id": "block-1",
        "task_id": "task-1",
        "state": "planned",
    },
    {
        "type": "training",
        "id": "training-1",
        "title": "Footing",
        "start": "2026-11-23T18:00:00+01:00",
        "end": "2026-11-23T19:00:00+01:00",
        "fixed": False,
        "activity_id": "act-1",
        "notes": "Zone 2",
    },
]

TASKS: list[dict[str, Any]] = [
    {
        "id": "task-1",
        "subject": "Analyse",
        "effort_minutes": 600,
        "completed_minutes": 90,
        "remaining_minutes": 510,
        "target_date": "2026-12-05",
        "target_kind": "soft",
        "archived": False,
    },
    {
        "id": "task-2",
        "subject": "Physique",
        "effort_minutes": 120,
        "completed_minutes": 0,
        "remaining_minutes": 120,
        "target_date": "2026-12-10",
        "target_kind": "hard",
        "archived": False,
    },
    {
        "id": "task-3",
        "subject": "Archivée",
        "effort_minutes": 60,
        "completed_minutes": 60,
        "remaining_minutes": 0,
        "target_date": "2026-11-01",
        "target_kind": "soft",
        "archived": True,
    },
]

PROPOSALS: list[dict[str, Any]] = [
    {
        "id": "prop-1",
        "kind": "move_fixed",
        "title": "Déplacer l'entraînement accepté",
        "details": "Le cours a changé, déplacer Footing à 19:00 ?",
        "status": "pending",
        "created_at": "2026-11-23T07:00:00+01:00",
    }
]

NOTIFICATIONS: list[dict[str, Any]] = [
    {
        "id": "notif-1",
        "kind": "ical_change",
        "title": "Cours modifié",
        "message": "Algèbre commence maintenant à 10:00.",
        "created_at": "2026-11-23T06:00:00+01:00",
        "acknowledged": False,
    }
]

ACTIVITIES: list[dict[str, Any]] = [
    {
        "id": "act-1",
        "kind": "training",
        "title": "Footing",
        "status": "active",
        "preferred_day": None,
        "user_modified": False,
        "duration_minutes": 60,
    },
    {
        "id": "act-2",
        "kind": "training",
        "title": "Fractionné 10x400",
        "status": "to_confirm",
        "preferred_day": "2026-11-25",
        "user_modified": False,
        "duration_minutes": None,
    },
    {
        "id": "act-3",
        "kind": "race",
        "title": "Cross de Bruxelles",
        "status": "to_confirm",
        "preferred_day": "2026-11-29",
        "user_modified": False,
        "duration_minutes": None,
    },
    {
        "id": "act-4",
        "kind": "training",
        "title": "Annulé",
        "status": "cancelled",
        "preferred_day": None,
        "user_modified": True,
        "duration_minutes": 45,
    },
]


def mock_backend(
    aioclient_mock: AiohttpClientMocker,
    *,
    summary: dict[str, Any] | None = None,
    schedule: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    proposals: list[dict[str, Any]] | None = None,
    notifications: list[dict[str, Any]] | None = None,
    activities: list[dict[str, Any]] | None = None,
) -> None:
    """Register the read endpoints plus generic write endpoints."""
    aioclient_mock.get(
        f"{BASE}/v1/status", json={"version": "0.6.0", "timezone": "Europe/Brussels"}
    )
    aioclient_mock.get(f"{BASE}/v1/summary", json=deepcopy(summary or SUMMARY))
    aioclient_mock.get(
        f"{BASE}/v1/schedule", json=deepcopy(SCHEDULE if schedule is None else schedule)
    )
    aioclient_mock.get(f"{BASE}/v1/tasks", json=deepcopy(TASKS if tasks is None else tasks))
    aioclient_mock.get(
        f"{BASE}/v1/proposals", json=deepcopy(PROPOSALS if proposals is None else proposals)
    )
    aioclient_mock.get(
        f"{BASE}/v1/notifications",
        json=deepcopy(NOTIFICATIONS if notifications is None else notifications),
    )
    aioclient_mock.get(
        f"{BASE}/v1/activities",
        json=deepcopy(ACTIVITIES if activities is None else activities),
    )
    aioclient_mock.post(re.compile(r".*/v1/notifications/[^/]+/ack$"), status=204)
    aioclient_mock.patch(f"{BASE}/v1/settings", status=204)
    aioclient_mock.post(f"{BASE}/v1/location", status=204)


def calls(aioclient_mock: AiohttpClientMocker, method: str, path: str) -> list[tuple[Any, ...]]:
    """Return the recorded calls for a method and exact path."""
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[0].lower() == method.lower() and call[1].path == path
    ]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in all tests."""


@pytest.fixture(autouse=True)
def frozen_now(freezer: FrozenDateTimeFactory) -> Generator[FrozenDateTimeFactory]:
    """Freeze time at a known instant."""
    freezer.move_to(NOW)
    yield freezer


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a Tempo config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Tempo",
        unique_id=BASE,
        data={
            CONF_URL: BASE,
            CONF_API_TOKEN: TOKEN,
            CONF_VERIFY_SSL: True,
            CONF_TRACKER_ENTITY_ID: TRACKER,
        },
    )


async def poll(hass: HomeAssistant) -> None:
    """Fire the coordinator's next scheduled refresh."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up an entry."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration with the default mocked backend."""
    mock_backend(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    return mock_config_entry
