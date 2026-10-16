"""FairGrow: share chosen Home Assistant sensors with fairgrow.eu.

No devices of its own and no entities of its own: the user picks entities that
already exist in Home Assistant (from ZHA, Zigbee2MQTT or any other integration)
and their readings are pushed to the FairGrow account that made the pairing code.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FairGrowApi, FairGrowAuthError, FairGrowError
from .const import CONF_TOKEN, CONF_URL, DEFAULT_URL
from .coordinator import FairGrowPusher

_LOGGER = logging.getLogger(__name__)

type FairGrowConfigEntry = ConfigEntry[FairGrowPusher]


async def async_setup_entry(hass: HomeAssistant, entry: FairGrowConfigEntry) -> bool:
    """Start pushing the chosen entities."""
    pusher = FairGrowPusher(hass, entry)
    try:
        await pusher.async_start()
    except FairGrowAuthError as err:
        await pusher.async_stop()
        raise ConfigEntryAuthFailed(
            "FairGrow no longer accepts this installation; pair again"
        ) from err
    entry.runtime_data = pusher
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: FairGrowConfigEntry) -> None:
    """The user changed which entities are shared: start over with the new list."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FairGrowConfigEntry) -> bool:
    """Stop listening; queued readings are saved for the next start."""
    await entry.runtime_data.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Removing the integration also revokes it on FairGrow.

    Best effort: if FairGrow cannot be reached, the installation stays listed on
    FairGrow, where the user can disconnect it under Your coordinators."""
    api = FairGrowApi(
        async_get_clientsession(hass),
        entry.data.get(CONF_URL, DEFAULT_URL),
        entry.data.get(CONF_TOKEN),
    )
    try:
        await api.unpair()
    except FairGrowError as err:
        _LOGGER.warning("Could not revoke the installation on FairGrow: %s", err)
