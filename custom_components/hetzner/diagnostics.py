"""Diagnostics of a Hetzner Storage Box config entry."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .coordinator import HetznerConfigEntry

# Users attach diagnostics to public issues: the token grants access to the
# project, username and server contain the Hetzner customer number. Location
# coordinates are only the data center's, but never belong in a public file
TO_REDACT = {CONF_API_KEY, "username", "server", "latitude", "longitude"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,  # noqa: ARG001
    config_entry: HetznerConfigEntry,
) -> dict[str, Any]:
    """Return the config entry and the API data of the last update."""
    # runtime_data is only set once the first refresh succeeded
    loaded = config_entry.state is ConfigEntryState.LOADED
    coordinator = config_entry.runtime_data if loaded else None
    return {
        "entry": async_redact_data(config_entry.as_dict(), TO_REDACT),
        "last_update_success": (
            coordinator.last_update_success if coordinator else None
        ),
        "data": (
            async_redact_data(asdict(coordinator.data), TO_REDACT)
            if coordinator
            else None
        ),
    }
