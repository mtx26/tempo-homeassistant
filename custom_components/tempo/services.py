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
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.util import dt as dt_util

from .api import TempoApiError, TempoConflictError, TempoError, TempoNotFoundError
from .const import CONF_DASHBOARD, CONF_DASHBOARD_REQUIRE_ADMIN, DOMAIN
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
SERVICE_SET_HOME = "set_home"
SERVICE_SET_SETTINGS = "set_settings"
SERVICE_ADD_STAY = "add_stay"
SERVICE_CLEAR_STAYS = "clear_stays"

ADMIN_SERVICES = frozenset({SERVICE_INSTALL_DASHBOARD, SERVICE_REMOVE_DASHBOARD})

# Study preferences accepted by the backend (tempo.scheduler.model.Preferences); the backend
# still validates the combination (min <= preferred <= max, multiples of the granularity).
STUDY_PREFERENCES: dict[str, Any] = {
    "preferred_block_minutes": vol.All(vol.Coerce(int), vol.Range(min=15, max=240)),
    "min_block_minutes": vol.All(vol.Coerce(int), vol.Range(min=15, max=240)),
    "max_block_minutes": vol.All(vol.Coerce(int), vol.Range(min=15, max=240)),
    "granularity_minutes": vol.All(vol.Coerce(int), vol.In([5, 10, 15, 30])),
    "min_gap_same_subject_minutes": vol.All(vol.Coerce(int), vol.Range(min=0, max=240)),
    "max_daily_study_minutes": vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
    "max_blocks_per_subject_per_day": vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
}

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
SET_HOME_SCHEMA = vol.Schema({**_ENTRY, vol.Required("place_id"): _PLACE_ID})
_SETTINGS_FIELDS: dict[vol.Marker, Any] = {
    **_ENTRY,
    vol.Optional("horizon_days"): vol.All(vol.Coerce(int), vol.Range(min=1, max=42)),
    vol.Optional("buffer_minutes"): vol.All(vol.Coerce(int), vol.Range(min=0, max=120)),
}
for _key, _validator in STUDY_PREFERENCES.items():
    _SETTINGS_FIELDS[vol.Optional(_key)] = _validator
SET_SETTINGS_SCHEMA = vol.All(
    vol.Schema(_SETTINGS_FIELDS),
    cv.has_at_least_one_key("horizon_days", "buffer_minutes", *STUDY_PREFERENCES),
)
ADD_STAY_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required("place_id"): _PLACE_ID,
        vol.Required("start"): cv.datetime,
        vol.Required("end"): cv.datetime,
    }
)
INTERPRET_SCHEMA = vol.Schema(
    {**_ENTRY, vol.Required("text"): vol.All(cv.string, vol.Length(min=1, max=2000))}
)


def _aware(value: datetime) -> datetime:
    """Return an aware datetime (naive values use the HA time zone)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt_util.get_default_time_zone())
    return value


def _iso(value: datetime) -> str:
    """Serialize a datetime with an offset (naive values use the HA time zone)."""
    return _aware(value).isoformat()


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


async def _require_place(entry: TempoConfigEntry, place_id: str) -> None:
    """Refuse clearly a place that was not created with tempo.set_place."""
    places = await _run(entry, entry.runtime_data.client.places(), refresh=False)
    if not any(str(place.get("id")) == place_id for place in places or []):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_place",
            translation_placeholders={"place_id": place_id},
        )


async def _set_home(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    await _require_place(entry, call.data["place_id"])
    payload = {"home_place_id": call.data["place_id"]}
    await _run(entry, entry.runtime_data.client.patch_settings(payload))


async def _current_settings(entry: TempoConfigEntry) -> dict[str, Any]:
    """Read the settings from the backend (never from a possibly stale snapshot)."""
    return dict(await _run(entry, entry.runtime_data.client.settings(), refresh=False) or {})


async def _set_settings(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    payload: dict[str, Any] = {
        key: call.data[key] for key in ("horizon_days", "buffer_minutes") if key in call.data
    }
    preferences = {key: call.data[key] for key in STUDY_PREFERENCES if key in call.data}
    if preferences:
        # PATCH replaces the whole study_preferences object: merge with the stored one.
        current = (await _current_settings(entry)).get("study_preferences") or {}
        payload["study_preferences"] = {**dict(current), **preferences}
    await _run(entry, entry.runtime_data.client.patch_settings(payload))


async def _add_stay(hass: HomeAssistant, call: ServiceCall) -> None:
    """Declare a future stay; Tempo never switches home automatically."""
    entry = _entry(hass, call)
    start, end = _aware(call.data["start"]), _aware(call.data["end"])
    if end <= start:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_window")
    await _require_place(entry, call.data["place_id"])
    stays = list((await _current_settings(entry)).get("stays") or [])
    stays.append(
        {"place_id": call.data["place_id"], "start": start.isoformat(), "end": end.isoformat()}
    )
    await _run(entry, entry.runtime_data.client.patch_settings({"stays": stays}))


async def _clear_stays(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    await _run(entry, entry.runtime_data.client.patch_settings({"stays": []}))


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
    async_register_dashboard(
        hass, entry.entry_id, require_admin=bool(entry.options.get(CONF_DASHBOARD_REQUIRE_ADMIN))
    )
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
        schema: vol.Schema | vol.All,
        supports_response: SupportsResponse = SupportsResponse.NONE,
    ) -> None:
        async def _handle(call: ServiceCall) -> Any:
            return await handler(hass, call)

        if name in ADMIN_SERVICES:
            async_register_admin_service(
                hass, DOMAIN, name, _handle, schema, supports_response=supports_response
            )
            return
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
    register(SERVICE_SET_HOME, _set_home, SET_HOME_SCHEMA)
    register(SERVICE_SET_SETTINGS, _set_settings, SET_SETTINGS_SCHEMA)
    register(SERVICE_ADD_STAY, _add_stay, ADD_STAY_SCHEMA)
    register(SERVICE_CLEAR_STAYS, _clear_stays, ENTRY_ONLY_SCHEMA)
    register(SERVICE_INTERPRET, _interpret, INTERPRET_SCHEMA, SupportsResponse.OPTIONAL)
