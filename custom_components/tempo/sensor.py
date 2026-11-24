"""Sensors summarizing the Tempo plan."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DEPARTURE_STATUSES, PLAN_STATUSES
from .coordinator import TempoConfigEntry, TempoCoordinator, TempoData, pending_proposals
from .entity import TempoEntity, parse_dt

PARALLEL_UPDATES = 0

type StateValue = str | int | datetime | None


@dataclass(frozen=True, kw_only=True)
class TempoSensorEntityDescription(SensorEntityDescription):
    """Describe a Tempo sensor."""

    value_fn: Callable[[TempoData], StateValue]
    attrs_fn: Callable[[TempoData], dict[str, Any]] | None = None


def _summary(data: TempoData) -> dict[str, Any]:
    return data.summary or {}


def _next_event(data: TempoData) -> dict[str, Any] | None:
    event = _summary(data).get("next_event")
    return event if isinstance(event, dict) else None


def _next_departure(data: TempoData) -> dict[str, Any] | None:
    travel = _summary(data).get("next_departure")
    return travel if isinstance(travel, dict) else None


def _departure_check(data: TempoData) -> dict[str, Any] | None:
    check = _summary(data).get("departure_check")
    return check if isinstance(check, dict) else None


def _next_event_value(data: TempoData) -> str | None:
    event = _next_event(data)
    return str(event.get("title") or "")[:255] if event else None


def _next_event_attrs(data: TempoData) -> dict[str, Any]:
    event = _next_event(data) or {}
    return {
        "start": event.get("start"),
        "end": event.get("end"),
        "type": event.get("type"),
        "location": event.get("location"),
        "fixed": event.get("fixed"),
        "event_id": event.get("id"),
    }


def _departure_value(data: TempoData) -> datetime | None:
    check = _departure_check(data)
    if check and (leave_at := parse_dt(check.get("leave_at"))):
        return leave_at
    travel = _next_departure(data)
    if travel:
        return parse_dt(travel.get("departure")) or parse_dt(travel.get("start"))
    return None


def _departure_attrs(data: TempoData) -> dict[str, Any]:
    travel = _next_departure(data) or {}
    legs = list(travel.get("legs") or [])
    return {
        "title": travel.get("title"),
        "origin": legs[0].get("from") if legs else None,
        "destination": legs[-1].get("to") if legs else travel.get("location"),
        "departure": travel.get("departure") or travel.get("start"),
        "arrival": travel.get("arrival") or travel.get("end"),
        "status": travel.get("status"),
        "realtime": any(bool(leg.get("realtime")) for leg in legs),
        "legs": legs,
    }


def _departure_status(data: TempoData) -> str | None:
    check = _departure_check(data)
    if check is None:
        return "none"
    status = check.get("status")
    return status if status in DEPARTURE_STATUSES else None


def _departure_status_attrs(data: TempoData) -> dict[str, Any]:
    check = _departure_check(data) or {}
    return {
        key: check.get(key)
        for key in ("leave_at", "minutes_late", "reason", "warnings", "checked_at", "event_title")
    }


def _plan_status(data: TempoData) -> str | None:
    status = _summary(data).get("plan_status")
    return status if status in PLAN_STATUSES else None


def _plan_attrs(data: TempoData) -> dict[str, Any]:
    summary = _summary(data)
    return {
        "planned_at": summary.get("planned_at"),
        "unplanned_minutes": summary.get("unplanned_minutes"),
        "missed_blocks": summary.get("missed_blocks", 0),
        "warnings": list(summary.get("warnings") or []),
    }


def _int(key: str) -> Callable[[TempoData], int | None]:
    def value(data: TempoData) -> int | None:
        raw = _summary(data).get(key)
        return int(raw) if isinstance(raw, int | float) else None

    return value


def _proposal_attrs(data: TempoData) -> dict[str, Any]:
    """Pending proposals, oldest first."""
    return {
        "proposals": [
            {
                "id": p.get("id"),
                "kind": p.get("kind"),
                "title": p.get("title"),
                "created_at": p.get("created_at"),
            }
            for p in pending_proposals(data)
        ]
    }


def _place_name(data: TempoData, place_id: Any) -> str | None:
    for place in data.places:
        if place_id is not None and place.get("id") == place_id:
            return str(place.get("name") or place_id)
    return None


def _home_value(data: TempoData) -> str | None:
    home = (data.settings or {}).get("home_place_id")
    if not home:
        return None
    return (_place_name(data, home) or str(home))[:255]


def _settings_attrs(data: TempoData) -> dict[str, Any]:
    settings = data.settings or {}
    home = settings.get("home_place_id")
    return {
        "home_place_id": home,
        "home_place_name": _place_name(data, home),
        "horizon_days": settings.get("horizon_days"),
        "buffer_minutes": settings.get("buffer_minutes"),
        "stays": [
            {**stay, "place_name": _place_name(data, stay.get("place_id"))}
            for stay in settings.get("planned_locations") or []
            if isinstance(stay, dict)
        ],
        "study_preferences": dict(settings.get("study_preferences") or {}),
        "places": [{"id": place.get("id"), "name": place.get("name")} for place in data.places],
    }


def _to_confirm(data: TempoData) -> list[dict[str, Any]]:
    return list(_summary(data).get("to_confirm") or [])


def _to_confirm_attrs(data: TempoData) -> dict[str, Any]:
    return {
        "activities": [
            {
                "id": a.get("id"),
                "title": a.get("title"),
                "preferred_day": a.get("preferred_day"),
                "kind": a.get("kind"),
            }
            for a in _to_confirm(data)
        ]
    }


def _issues_attrs(data: TempoData) -> dict[str, Any]:
    summary = _summary(data)
    return {
        "issues": list(summary.get("issues") or []),
        "warnings": list(summary.get("warnings") or []),
    }


SENSORS: tuple[TempoSensorEntityDescription, ...] = (
    TempoSensorEntityDescription(
        key="next_event",
        translation_key="next_event",
        value_fn=_next_event_value,
        attrs_fn=_next_event_attrs,
    ),
    TempoSensorEntityDescription(
        key="next_departure",
        translation_key="next_departure",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_departure_value,
        attrs_fn=_departure_attrs,
    ),
    TempoSensorEntityDescription(
        key="departure_status",
        translation_key="departure_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(DEPARTURE_STATUSES),
        value_fn=_departure_status,
        attrs_fn=_departure_status_attrs,
    ),
    TempoSensorEntityDescription(
        key="remaining_study",
        translation_key="remaining_study",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_int("remaining_study_minutes"),
    ),
    TempoSensorEntityDescription(
        key="today_study",
        translation_key="today_study",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_int("today_study_minutes"),
    ),
    TempoSensorEntityDescription(
        key="plan_status",
        translation_key="plan_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(PLAN_STATUSES),
        value_fn=_plan_status,
        attrs_fn=_plan_attrs,
    ),
    TempoSensorEntityDescription(
        key="pending_proposals",
        translation_key="pending_proposals",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_int("pending_proposals"),
        attrs_fn=_proposal_attrs,
    ),
    TempoSensorEntityDescription(
        key="unread_notifications",
        translation_key="unread_notifications",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_int("unread_notifications"),
    ),
    TempoSensorEntityDescription(
        key="trainings_to_confirm",
        translation_key="trainings_to_confirm",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(_to_confirm(data)),
        attrs_fn=_to_confirm_attrs,
    ),
    TempoSensorEntityDescription(
        key="issues",
        translation_key="issues",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(_summary(data).get("issues") or []),
        attrs_fn=_issues_attrs,
    ),
    TempoSensorEntityDescription(
        key="settings",
        translation_key="settings",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_home_value,
        attrs_fn=_settings_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TempoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Tempo sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(TempoSensor(coordinator, description) for description in SENSORS)


class TempoSensor(TempoEntity, SensorEntity):
    """A sensor computed from the coordinator snapshot."""

    entity_description: TempoSensorEntityDescription

    def __init__(
        self, coordinator: TempoCoordinator, description: TempoSensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateValue:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
