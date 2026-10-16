"""Diagnostics for FairGrow. The installation token never leaves this function."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import FairGrowConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {CONF_TOKEN, "token", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FairGrowConfigEntry
) -> dict[str, Any]:
    """What a support question needs: settings, queue and errors, no secrets."""
    pusher = getattr(entry, "runtime_data", None)
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "pusher": async_redact_data(pusher.diagnostics(), TO_REDACT) if pusher else None,
    }
