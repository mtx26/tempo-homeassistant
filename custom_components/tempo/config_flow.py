"""Config, reauth and options flows for Tempo."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_TOKEN, CONF_SCAN_INTERVAL, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    TempoApiClient,
    TempoAuthError,
    TempoConnectionError,
    TempoError,
    TempoRateLimitedError,
    normalize_url,
)
from .const import (
    CONF_HOME_FROM_ZONE,
    CONF_NOTIFY_SERVICE,
    CONF_TRACKER_ENTITY_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MIN_SCAN_INTERVAL,
)
from .coordinator import TempoConfigEntry

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_VERIFY_SSL, default=True): bool,
    }
)
REAUTH_SCHEMA = vol.Schema(
    {vol.Required(CONF_API_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)
TRACKER_SELECTOR = EntitySelector(EntitySelectorConfig(domain="device_tracker"))


def _client(hass: HomeAssistant, url: str, token: str, verify_ssl: bool) -> TempoApiClient:
    return TempoApiClient(async_get_clientsession(hass, verify_ssl=verify_ssl), url, token)


async def _validate(client: TempoApiClient) -> tuple[dict[str, str], dict[str, Any]]:
    """Call /v1/status and map failures to form errors."""
    try:
        return {}, await client.status()
    except TempoAuthError:
        return {"base": "invalid_auth"}, {}
    except TempoRateLimitedError:
        return {"base": "rate_limited"}, {}
    except TempoConnectionError:
        return {"base": "cannot_connect"}, {}
    except TempoError:
        LOGGER.exception("Unexpected Tempo backend answer")
        return {"base": "unknown"}, {}


async def _push_tracker(client: TempoApiClient, entity_id: str) -> dict[str, str]:
    return await _patch_settings(client, {CONF_TRACKER_ENTITY_ID: entity_id})


async def _patch_settings(client: TempoApiClient, payload: dict[str, Any]) -> dict[str, str]:
    try:
        await client.patch_settings(payload)
    except TempoAuthError:
        return {"base": "invalid_auth"}
    except TempoRateLimitedError:
        return {"base": "rate_limited"}
    except TempoConnectionError:
        return {"base": "cannot_connect"}
    except TempoError:
        LOGGER.exception("Could not store Tempo settings")
        return {"base": "unknown"}
    return {}


class TempoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Tempo config flow."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: TempoConfigEntry) -> TempoOptionsFlow:
        """Return the options flow."""
        return TempoOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the backend URL and token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalize_url(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                errors, _status = await _validate(
                    _client(self.hass, url, user_input[CONF_API_TOKEN], user_input[CONF_VERIFY_SSL])
                )
                if not errors:
                    self._data = {
                        CONF_URL: url,
                        CONF_API_TOKEN: user_input[CONF_API_TOKEN],
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    }
                    return await self.async_step_tracker()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_tracker(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the phone device_tracker used as the actual location."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input[CONF_TRACKER_ENTITY_ID]
            client = _client(
                self.hass,
                self._data[CONF_URL],
                self._data[CONF_API_TOKEN],
                self._data[CONF_VERIFY_SSL],
            )
            errors = await _push_tracker(client, entity_id)
            if not errors:
                return self.async_create_entry(
                    title="Tempo", data={**self._data, CONF_TRACKER_ENTITY_ID: entity_id}
                )
        return self.async_show_form(
            step_id="tracker",
            data_schema=vol.Schema({vol.Required(CONF_TRACKER_ENTITY_ID): TRACKER_SELECTOR}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after the token was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            errors, _status = await _validate(
                _client(
                    self.hass,
                    entry.data[CONF_URL],
                    user_input[CONF_API_TOKEN],
                    entry.data.get(CONF_VERIFY_SSL, True),
                )
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"url": entry.data[CONF_URL]},
        )


class TempoOptionsFlow(OptionsFlowWithReload):
    """Technical options: phone tracker, polling interval, notify service.

    ``home_from_zone`` (one-shot) registers Home Assistant's home zone as the Tempo place
    ``home`` and makes it the home place. Everything else (places, rules, tasks, events) is
    managed by conversation with the Tempo assistant.
    """

    def _client(self) -> TempoApiClient:
        entry = self.config_entry
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None:
            return runtime.client  # type: ignore[no-any-return]
        return _client(
            self.hass,
            entry.data[CONF_URL],
            entry.data[CONF_API_TOKEN],
            entry.data.get(CONF_VERIFY_SSL, True),
        )

    async def _home_from_zone(self, client: TempoApiClient) -> dict[str, str]:
        config = self.hass.config
        try:
            await client.operation(
                "upsert_place",
                {
                    "place_id": "home",
                    "name": config.location_name or "Domicile",
                    "latitude": config.latitude,
                    "longitude": config.longitude,
                },
            )
            await client.operation("update_settings", {"home_place_id": "home"})
        except TempoAuthError:
            return {"base": "invalid_auth"}
        except TempoError as err:
            LOGGER.warning("Could not register the home zone in Tempo: %s", err)
            return {"base": "cannot_connect"}
        return {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        entry = self.config_entry
        errors: dict[str, str] = {}
        client = self._client()
        current_tracker = entry.options.get(
            CONF_TRACKER_ENTITY_ID, entry.data.get(CONF_TRACKER_ENTITY_ID)
        )
        if user_input is not None:
            notify = (user_input.get(CONF_NOTIFY_SERVICE) or "").strip()
            if notify and (
                not notify.startswith("notify.")
                or not self.hass.services.has_service("notify", notify.removeprefix("notify."))
            ):
                errors[CONF_NOTIFY_SERVICE] = "invalid_notify_service"
            tracker = user_input[CONF_TRACKER_ENTITY_ID]
            if not errors and tracker != current_tracker:
                errors = await _push_tracker(client, tracker)
            if not errors and user_input.get(CONF_HOME_FROM_ZONE):
                errors = await self._home_from_zone(client)
            if not errors:
                options: dict[str, Any] = {
                    CONF_TRACKER_ENTITY_ID: tracker,
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                }
                if notify:
                    options[CONF_NOTIFY_SERVICE] = notify
                return self.async_create_entry(data=options)

        fields: dict[vol.Marker, Any] = {
            vol.Required(CONF_TRACKER_ENTITY_ID): TRACKER_SELECTOR,
            vol.Required(CONF_SCAN_INTERVAL): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=3600,
                    step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(CONF_NOTIFY_SERVICE): str,
            vol.Optional(CONF_HOME_FROM_ZONE, default=False): bool,
        }
        suggested = {
            CONF_TRACKER_ENTITY_ID: current_tracker,
            CONF_SCAN_INTERVAL: entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            CONF_NOTIFY_SERVICE: entry.options.get(CONF_NOTIFY_SERVICE, ""),
            CONF_HOME_FROM_ZONE: False,
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), user_input or suggested
            ),
            errors=errors,
        )
