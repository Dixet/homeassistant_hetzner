import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector
import logging
import asyncio

from .const import DOMAIN, CONF_API_KEY, CONF_HOST, CONF_STORAGE_BOX_ID, DEFAULT_HOST

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_API_KEY): str,
})

# Field key used for storage box selection; using the config constant
# allows Home Assistant to look up a translation for this key under
# `config.step.storage_box.data.storage_box_id`.
STORAGE_BOX_SELECT_FIELD = CONF_STORAGE_BOX_ID

def create_storage_box_schema(descriptions_list: list) -> vol.Schema:
    """Create schema with available storage boxes as options using selector.
    
    Args:
        descriptions_list: List of formatted descriptions for each storage box
    """
    return vol.Schema({
        vol.Required(STORAGE_BOX_SELECT_FIELD): selector.SelectSelector(
            selector.SelectSelectorConfig(options=descriptions_list)
        )
    })


class HetznerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hetzner Storage Box."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step - Ask for API key."""
        errors = {}

        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY, "").strip()
            host = DEFAULT_HOST

            # Validate the API key is not empty
            if not api_key:
                errors[CONF_API_KEY] = "api_key_required"
            else:
                # Attempt to validate the API key by testing connection
                validation_error = await self._validate_api_key(api_key, host)
                if validation_error:
                    errors[CONF_API_KEY] = validation_error
                else:
                    # API key is valid, proceed to next step
                    self.data = {
                        CONF_API_KEY: api_key,
                        CONF_HOST: host,
                    }
                    return await self.async_step_storage_box()

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "api_key_info": "Your Hetzner Storage Box API key",
            },
        )

    async def _validate_api_key(self, api_key: str, host: str) -> str | None:
        """Validate API key by testing connection to Hetzner API.

        Returns None if valid, or error string if invalid.
        """
        try:
            session = async_get_clientsession(self.hass)
            headers = {"Authorization": f"Bearer {api_key}"}
            
            # Attempt a test API call to validate the key against Hetzner API
            async with session.get(
                f"https://{host}/v1/storage_boxes",
                headers=headers,
                timeout=10,
            ) as resp:
                if resp.status == 401:
                    _LOGGER.error("Invalid API key for Hetzner Storage Box")
                    return "invalid_api_key"
                elif resp.status == 200:
                    _LOGGER.debug("API key validated successfully")
                    return None
                elif resp.status == 404:
                    _LOGGER.error("Hetzner API endpoint not found")
                    return "invalid_host"
                else:
                    _LOGGER.error(f"Unexpected response from Hetzner API: {resp.status}")
                    return "api_error"
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout connecting to Hetzner API")
            return "connection_timeout"
        except Exception as e:
            _LOGGER.error(f"Error validating API key: {e}")
            return "validation_error"

    async def _build_description_map(self, storage_boxes: dict) -> dict:
        """Build a mapping of formatted descriptions to storage box IDs.
        
        Args:
            storage_boxes: Dict mapping box ID to box info
            
        Returns:
            Dict mapping formatted description strings to box IDs
        """
        descriptions_to_ids = {}
        for box_id, box_info in storage_boxes.items():
            desc = f"{box_info['name']} (ID: {box_id}, Status: {box_info['status']}, Type: {box_info['box_type']}, {box_info['description']})"
            descriptions_to_ids[desc] = box_id
        return descriptions_to_ids

    async def _fetch_storage_boxes(self, api_key: str, host: str) -> dict:
        """Fetch list of available storage boxes from Hetzner API.
        
        Returns a dict mapping storage box ID (as string) to info dict with:
        - name: storage box name
        - status: storage box status
        - box_type: storage box type name
        - description: storage box type description
        """
        try:
            session = async_get_clientsession(self.hass)
            headers = {"Authorization": f"Bearer {api_key}"}
            
            async with session.get(
                f"https://{host}/v1/storage_boxes",
                headers=headers,
                timeout=10,
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error("Error fetching storage boxes: %s", resp.status)
                    return {}
                
                data = await resp.json()
                storage_boxes = {}
                
                for box in data.get("storage_boxes", []):
                    box_id = str(box.get("id"))
                    box_type = box.get("storage_box_type") or {}
                    storage_boxes[box_id] = {
                        "name": box.get("name"),
                        "status": box.get("status"),
                        "box_type": box_type.get("name"),
                        "description": box_type.get("description"),
                    }
                
                return storage_boxes
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout fetching storage boxes")
            return {}
        except Exception as e:
            _LOGGER.error("Error fetching storage boxes: %s", e)
            return {}

    async def async_step_storage_box(self, user_input=None):
        """Handle the storage box selection step."""
        errors = {}
        
        # Fetch available storage boxes from API
        storage_boxes = await self._fetch_storage_boxes(
            self.data[CONF_API_KEY],
            self.data[CONF_HOST]
        )
        
        if not storage_boxes:
            errors["base"] = "no_storage_boxes"
            return self.async_show_form(
                step_id="storage_box",
                data_schema=vol.Schema({}),
                errors=errors,
                description_placeholders={
                    "storage_box_info": "No storage boxes found for this account",
                },
            )

        if user_input is not None:
            # user_input[STORAGE_BOX_SELECT_FIELD] is the formatted description string
            selected_description = user_input.get(STORAGE_BOX_SELECT_FIELD)
            # Map description back to ID
            descriptions_to_ids = await self._build_description_map(storage_boxes)
            selected_box_id = descriptions_to_ids.get(selected_description)
            
            if selected_box_id:
                selected_box = storage_boxes.get(selected_box_id)
                # Set unique ID for the config entry (storage box id) to allow single instance per box
                await self.async_set_unique_id(selected_box_id)
                self._abort_if_unique_id_configured()
                # Create config entry with selected storage box
                return self.async_create_entry(
                    title=f"Hetzner {selected_box['name']}",
                    data={
                        CONF_API_KEY: self.data[CONF_API_KEY],
                        CONF_HOST: self.data[CONF_HOST],
                        CONF_STORAGE_BOX_ID: selected_box_id,
                    },
                )
            else:
                errors["base"] = "invalid_selection"

        # Build mapping of descriptions to IDs for dropdown
        descriptions_to_ids = await self._build_description_map(storage_boxes)
        descriptions_list = list(descriptions_to_ids.keys())
        
        return self.async_show_form(
            step_id="storage_box",
            data_schema=create_storage_box_schema(descriptions_list),
            errors=errors,
        )
