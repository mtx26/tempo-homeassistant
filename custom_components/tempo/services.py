"""Service actions of the Tempo integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .api import TempoApiError, TempoConflictError, TempoError, TempoNotFoundError
from .const import CONF_DASHBOARD, DOMAIN
from .coordinator import TempoConfigEntry
from .dashboard import (
    DASHBOARD_URL_PATH,
    async_register_dashboard,
    async_unregister_dashboard,
)

ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SERVICE_ADD_TASK = "add_task"
SERVICE_REPLAN = "replan"
SERVICE_APPROVE_PROPOSAL = "approve_proposal"
SERVICE_REJECT_PROPOSAL = "reject_proposal"
SERVICE_LOCK_BLOCK = "lock_block"
SERVICE_UNLOCK_BLOCK = "unlock_block"
SERVICE_COMPLETE_BLOCK = "complete_block"
SERVICE_ADD_TRAINING = "add_training"
SERVICE_ADD_RACE = "add_race"
SERVICE_SET_PLACE = "set_place"
SERVICE_SET_AVAILABILITY = "set_availability"
SERVICE_ACKNOWLEDGE_NOTIFICATION = "acknowledge_notification"
SERVICE_INTERPRET = "interpret"
SERVICE_CONFIRM_ACTIVITY = "confirm_activity"
SERVICE_INSTALL_DASHBOARD = "install_dashboard"
SERVICE_REMOVE_DASHBOARD = "remove_dashboard"

_ENTRY: dict[vol.Marker, Any] = {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}
_ID = vol.All(cv.string, vol.Length(min=1, max=200))
_MINUTES = vol.All(vol.Coerce(int), vol.Range(min=1, max=24 * 60 * 30))
_PLACE_ID = vol.All(cv.string, vol.Match(r"^[a-z0-9_]{1,50}$"))
_HHMM = vol.Match(r"^([01]\d|2[0-3]):[0-5]\d$")
_END = vol.Any(_HHMM, "24:00")

ADD_TASK_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required("subject"): vol.All(cv.string, vol.Length(min=1, max=200)),
        vol.Required("effort_minutes"): _MINUTES,
        vol.Required("target_date"): cv.date,
        vol.Optional("hard_deadline", default=False): cv.boolean,
    }
)
ENTRY_ONLY_SCHEMA = vol.Schema(_ENTRY)
PROPOSAL_SCHEMA = vol.Schema({**_ENTRY, vol.Required("proposal_id"): _ID})
BLOCK_SCHEMA = vol.Schema({**_ENTRY, vol.Required("block_id"): _ID})
NOTIFICATION_SCHEMA = vol.Schema({**_ENTRY, vol.Required("notification_id"): _ID})
ADD_TRAINING_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required("title"): vol.All(cv.string, vol.Length(min=1, max=200)),
        vol.Required("duration_minutes"): _MINUTES,
        vol.Optional("start"): cv.datetime,
        vol.Optional("notes"): cv.string,
        vol.Optional("place_id"): _PLACE_ID,
        vol.Optional("earliest_start"): cv.datetime,
        vol.Optional("latest_end"): cv.datetime,
    }
)
ADD_RACE_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required("title"): vol.All(cv.string, vol.Length(min=1, max=200)),
        vol.Required("start"): cv.datetime,
        vol.Required("duration_minutes"): _MINUTES,
        vol.Optional("place_id"): _PLACE_ID,
        vol.Optional("notes"): cv.string,
    }
)
SET_PLACE_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required("place_id"): _PLACE_ID,
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=200)),
        vol.Required("latitude"): cv.latitude,
        vol.Required("longitude"): cv.longitude,
        vol.Optional("aliases", default=list): vol.All(cv.ensure_list, [cv.string]),
    }
)
WINDOW_SCHEMA = vol.Schema(
    {
        vol.Required("weekday"): vol.All(vol.Coerce(int), vol.Range(min=0, max=6)),
        vol.Required("start"): _HHMM,
        vol.Required("end"): _END,
        vol.Required("place_id"): _PLACE_ID,
    }
)
SET_AVAILABILITY_SCHEMA = vol.Schema(
    {**_ENTRY, vol.Required("windows"): vol.All(cv.ensure_list, [WINDOW_SCHEMA])}
)
CONFIRM_ACTIVITY_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required("activity_id"): _ID,
        vol.Required("duration_minutes"): _MINUTES,
        vol.Optional("start"): cv.datetime,
    }
)
INTERPRET_SCHEMA = vol.Schema(
    {**_ENTRY, vol.Required("text"): vol.All(cv.string, vol.Length(min=1, max=2000))}
)


def _iso(value: datetime) -> str:
    """Serialize a datetime with an offset (naive values use the HA time zone)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.get_default_time_zone())
    return value.isoformat()


def _entry(hass: HomeAssistant, call: ServiceCall) -> TempoConfigEntry:
    """Return the targeted (or the single) loaded Tempo config entry."""
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_loaded"
            )
        return entry
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if len(entries) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_ambiguous" if entries else "entry_not_loaded",
        )
    return entries[0]


async def _run[T](entry: TempoConfigEntry, call: Awaitable[T], refresh: bool = True) -> T:
    """Run a backend call, translate errors and refresh the coordinator."""
    try:
        result = await call
    except TempoNotFoundError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_found",
            translation_placeholders={"error": str(err)},
        ) from err
    except TempoConflictError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="conflict",
            translation_placeholders={"error": str(err)},
        ) from err
    except TempoApiError as err:
        if err.status != 422:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="backend_error",
                translation_placeholders={"error": str(err)},
            ) from err
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_request",
            translation_placeholders={"error": str(err)},
        ) from err
    except TempoError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="backend_error",
            translation_placeholders={"error": str(err)},
        ) from err
    if refresh:
        await entry.runtime_data.coordinator.async_request_refresh()
    return result


async def _add_task(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    target: date = call.data["target_date"]
    payload = {
        "subject": call.data["subject"],
        "effort_minutes": call.data["effort_minutes"],
        "target_date": target.isoformat(),
        "target_kind": "hard" if call.data["hard_deadline"] else "soft",
    }
    await _run(entry, entry.runtime_data.client.create_task(payload))


async def _replan(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    await _run(entry, entry.runtime_data.client.replan())


def _decide(approve: bool) -> Callable[[HomeAssistant, ServiceCall], Awaitable[None]]:
    async def handler(hass: HomeAssistant, call: ServiceCall) -> None:
        entry = _entry(hass, call)
        await _run(
            entry, entry.runtime_data.client.decide_proposal(call.data["proposal_id"], approve)
        )

    return handler


def _block(action: str) -> Callable[[HomeAssistant, ServiceCall], Awaitable[None]]:
    async def handler(hass: HomeAssistant, call: ServiceCall) -> None:
        entry = _entry(hass, call)
        await _run(entry, entry.runtime_data.client.block_action(call.data["block_id"], action))

    return handler


async def _add_training(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    payload: dict[str, Any] = {
        "kind": "training",
        "title": call.data["title"],
        "duration_minutes": call.data["duration_minutes"],
    }
    for key in ("start", "earliest_start", "latest_end"):
        if key in call.data:
            payload[key] = _iso(call.data[key])
    if (
        "earliest_start" in call.data
        and "latest_end" in call.data
        and call.data["latest_end"] <= call.data["earliest_start"]
    ):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_window")
    for key in ("notes", "place_id"):
        if key in call.data:
            payload[key] = call.data[key]
    await _run(entry, entry.runtime_data.client.create_activity(payload))


async def _add_race(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    payload: dict[str, Any] = {
        "kind": "race",
        "title": call.data["title"],
        "start": _iso(call.data["start"]),
        "duration_minutes": call.data["duration_minutes"],
    }
    for key in ("notes", "place_id"):
        if key in call.data:
            payload[key] = call.data[key]
    await _run(entry, entry.runtime_data.client.create_activity(payload))


async def _confirm_activity(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    activity_id: str = call.data["activity_id"]
    data = entry.runtime_data.coordinator.data
    known = next(
        (a for a in (data.activities if data else []) if str(a.get("id")) == activity_id),
        None,
    )
    if known is not None and known.get("kind") == "race" and "start" not in call.data:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="race_start_required"
        )
    payload: dict[str, Any] = {"duration_minutes": call.data["duration_minutes"]}
    if "start" in call.data:
        payload["start"] = _iso(call.data["start"])
    await _run(entry, entry.runtime_data.client.confirm_activity(activity_id, payload))


async def _set_place(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    payload = {
        "name": call.data["name"],
        "latitude": call.data["latitude"],
        "longitude": call.data["longitude"],
        "aliases": list(call.data["aliases"]),
    }
    await _run(entry, entry.runtime_data.client.put_place(call.data["place_id"], payload))


async def _set_availability(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    windows = [dict(window) for window in call.data["windows"]]
    for window in windows:
        if window["end"] != "24:00" and window["end"] <= window["start"]:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_window"
            )
    await _run(entry, entry.runtime_data.client.put_availability(windows))


async def _acknowledge(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    await _run(entry, entry.runtime_data.client.ack_notification(call.data["notification_id"]))


async def _interpret(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call)
    try:
        result = await entry.runtime_data.client.interpret(call.data["text"])
    except TempoNotFoundError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="interpret_unavailable"
        ) from err
    except TempoError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="backend_error",
            translation_placeholders={"error": str(err)},
        ) from err
    if result.get("applied"):
        await entry.runtime_data.coordinator.async_request_refresh()
    return {
        "understood": bool(result.get("understood")),
        "action": result.get("action"),
        "summary": str(result.get("summary") or ""),
        "applied": bool(result.get("applied")),
        "used_llm": bool(result.get("used_llm")),
    }


async def _install_dashboard(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call)
    async_register_dashboard(hass, entry.entry_id)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_DASHBOARD: True})
    return {"url_path": DASHBOARD_URL_PATH}


async def _remove_dashboard(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    async_unregister_dashboard(hass, entry.entry_id)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_DASHBOARD: False})


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Tempo service actions."""

    def register(
        name: str,
        handler: Callable[[HomeAssistant, ServiceCall], Awaitable[Any]],
        schema: vol.Schema,
        supports_response: SupportsResponse = SupportsResponse.NONE,
    ) -> None:
        async def _handle(call: ServiceCall) -> Any:
            return await handler(hass, call)

        hass.services.async_register(
            DOMAIN, name, _handle, schema=schema, supports_response=supports_response
        )

    register(SERVICE_ADD_TASK, _add_task, ADD_TASK_SCHEMA)
    register(SERVICE_REPLAN, _replan, ENTRY_ONLY_SCHEMA)
    register(SERVICE_APPROVE_PROPOSAL, _decide(True), PROPOSAL_SCHEMA)
    register(SERVICE_REJECT_PROPOSAL, _decide(False), PROPOSAL_SCHEMA)
    register(SERVICE_LOCK_BLOCK, _block("lock"), BLOCK_SCHEMA)
    register(SERVICE_UNLOCK_BLOCK, _block("unlock"), BLOCK_SCHEMA)
    register(SERVICE_COMPLETE_BLOCK, _block("done"), BLOCK_SCHEMA)
    register(SERVICE_ADD_TRAINING, _add_training, ADD_TRAINING_SCHEMA)
    register(SERVICE_ADD_RACE, _add_race, ADD_RACE_SCHEMA)
    register(SERVICE_SET_PLACE, _set_place, SET_PLACE_SCHEMA)
    register(SERVICE_SET_AVAILABILITY, _set_availability, SET_AVAILABILITY_SCHEMA)
    register(SERVICE_ACKNOWLEDGE_NOTIFICATION, _acknowledge, NOTIFICATION_SCHEMA)
    register(SERVICE_CONFIRM_ACTIVITY, _confirm_activity, CONFIRM_ACTIVITY_SCHEMA)
    register(
        SERVICE_INSTALL_DASHBOARD,
        _install_dashboard,
        ENTRY_ONLY_SCHEMA,
        SupportsResponse.OPTIONAL,
    )
    register(SERVICE_REMOVE_DASHBOARD, _remove_dashboard, ENTRY_ONLY_SCHEMA)
    register(SERVICE_INTERPRET, _interpret, INTERPRET_SCHEMA, SupportsResponse.OPTIONAL)
