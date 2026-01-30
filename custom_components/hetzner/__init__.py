from homeassistant import core
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
import logging

from .const import DOMAIN, CONF_API_KEY, CONF_HOST

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

# Configuration schema - this integration only supports config entries
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Set up the Hetzner Storage Box component."""
    
    hass.data[DOMAIN] = {}
    
    return True


async def async_setup_entry(hass: core.HomeAssistant, config_entry) -> bool:
    """Set up Hetzner Storage Box from a config entry."""
    hass.data[DOMAIN][config_entry.entry_id] = {
        CONF_API_KEY: config_entry.data.get(CONF_API_KEY),
        CONF_HOST: config_entry.data.get(CONF_HOST),
    }
    
    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(
        config_entry, PLATFORMS
    )
    
    return True
