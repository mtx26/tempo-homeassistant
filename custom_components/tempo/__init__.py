"""Tempo integration for Home Assistant.

Home Assistant is the only user interface of Tempo, a self-hosted personal planner. This
integration talks to the private Tempo backend over the internal network with a bearer token.
"""

from __future__ import annotations

from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import TempoApiClient, TempoAuthError, TempoError
from .const import CONF_TRACKER_ENTITY_ID, DOMAIN
from .coordinator import TempoConfigEntry, TempoCoordinator, TempoRuntimeData
from .location import TempoLocationPusher
from .notify_bridge import TempoNotificationBridge
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR, Platform.TODO]

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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TempoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
