"""Tempo integration for Home Assistant.

Home Assistant is the only user interface of Tempo, a self-hosted personal planner. This
integration talks to the private Tempo backend over the internal network with a bearer token.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import TempoApiClient, TempoAuthError, TempoError
from .const import (
    CONF_DASHBOARD,
    CONF_DASHBOARD_REQUIRE_ADMIN,
    CONF_TRACKER_ENTITY_ID,
    DOMAIN,
    LOGGER,
)
from .coordinator import TempoConfigEntry, TempoCoordinator, TempoRuntimeData
from .dashboard import (
    async_register_dashboard,
    async_track_proposals,
    async_unregister_dashboard,
)
from .location import TempoLocationPusher
from .notify_bridge import TempoNotificationBridge
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.CONVERSATION,
    Platform.SENSOR,
    Platform.TODO,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the service actions once."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TempoConfigEntry) -> bool:
    """Set up Tempo from a config entry."""
    session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True))
    client = TempoApiClient(session, entry.data[CONF_URL], entry.data[CONF_API_TOKEN])
    try:
        status = await client.status()
    except TempoAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except TempoError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    coordinator = TempoCoordinator(hass, entry, client)
    entry.runtime_data = TempoRuntimeData(
        client=client, coordinator=coordinator, backend_version=status.get("version")
    )
    await coordinator.async_config_entry_first_refresh()

    tracker = entry.options.get(CONF_TRACKER_ENTITY_ID, entry.data.get(CONF_TRACKER_ENTITY_ID))
    if tracker:
        pusher = TempoLocationPusher(hass, client, tracker)
        entry.runtime_data.location = pusher
        pusher.async_start()
        entry.async_on_unload(pusher.async_stop)

    bridge = TempoNotificationBridge(hass, entry, coordinator)
    entry.runtime_data.bridge = bridge
    bridge.async_start()
    entry.async_on_unload(bridge.async_stop)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if entry.options.get(CONF_DASHBOARD):
        try:
            async_register_dashboard(
                hass,
                entry.entry_id,
                require_admin=bool(entry.options.get(CONF_DASHBOARD_REQUIRE_ADMIN)),
            )
        except HomeAssistantError as err:
            LOGGER.warning("Tempo dashboard not registered: %s", err)
    entry.async_on_unload(async_track_proposals(hass, entry))
    entry.async_on_unload(lambda: async_unregister_dashboard(hass, entry.entry_id))
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config entry (1.1 -> 1.2 only bumps the minor version)."""
    if entry.version > 1:
        # Downgrade from a future major version: refuse rather than guess.
        return False
    if entry.version == 1 and entry.minor_version < 2:
        hass.config_entries.async_update_entry(entry, minor_version=2)
    LOGGER.debug("Tempo entry migrated to %s.%s", entry.version, entry.minor_version)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TempoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
