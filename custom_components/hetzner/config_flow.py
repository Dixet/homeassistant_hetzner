"""Config flow for Hetzner Storage Boxes."""

from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    HetznerAuthError,
    HetznerClient,
    HetznerConnectionError,
    HetznerError,
    HetznerNotFoundError,
    HetznerTimeoutError,
    as_dict,
)
from .const import CONF_STORAGE_BOX_ID, CONFIG_ENTRY_VERSION, DEFAULT_HOST, DOMAIN

if TYPE_CHECKING:
    import probatio as vol
else:
    # Recent Home Assistant versions install probatio under this name; importing
    # it as voluptuous keeps older Home Assistant versions working
    import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

# Masked like a password: the token grants access to the whole project
DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        )
    }
)

# Appended to the dropdown label of boxes that already have a config entry
ALREADY_CONFIGURED_LABEL = "already configured"


class StorageBoxInfo(TypedDict):
    """Fields of a storage box shown in the selection dropdown."""

    name: str | None
    status: str | None
    box_type: str | None
    description: str | None


def _error_key(err: HetznerError, storage_box_id: str | None = None) -> str:
    """Map an API error to a form error key.

    Expected failures are shown in the form, so they are only logged at
    debug level.

    Args:
        err: Error raised by the API client
        storage_box_id: Box queried, None if the storage box list was requested

    Returns:
        Error key of the form
    """
    if isinstance(err, HetznerAuthError):
        _LOGGER.debug("Hetzner API rejected the API key: %s", err)
        return "invalid_api_key"
    if isinstance(err, HetznerNotFoundError):
        if storage_box_id is not None:
            _LOGGER.debug("Storage box %s not found", storage_box_id)
            return "storage_box_not_found"
        _LOGGER.debug("Hetzner API endpoint not found")
        return "invalid_host"
    if isinstance(err, HetznerTimeoutError):
        _LOGGER.debug("Timeout connecting to Hetzner API")
        return "connection_timeout"
    if isinstance(err, HetznerConnectionError):
        _LOGGER.debug("Error connecting to Hetzner API: %s", err)
        return "cannot_connect"
    # HetznerApiError and any error class the client may add later: an error
    # of the API, not of this integration
    _LOGGER.debug("Unexpected response from Hetzner API: %s", err)
    return "api_error"


def create_storage_box_schema(
    box_options: list[selector.SelectOptionDict],
) -> vol.Schema:
    """Create schema with available storage boxes as options using selector.

    Args:
        box_options: Storage box IDs as values with formatted descriptions as labels
    """
    return vol.Schema(
        {
            # Translated as config.step.storage_box.data.storage_box_id
            vol.Required(CONF_STORAGE_BOX_ID): selector.SelectSelector(
                selector.SelectSelectorConfig(options=box_options)
            )
        }
    )


class HetznerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hetzner Storage Box."""

    VERSION = CONFIG_ENTRY_VERSION

    data: dict[str, Any]
    # Boxes listed while validating the key, shown by the box step right after
    _listed_storage_boxes: dict[str, StorageBoxInfo] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - Ask for API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            host = DEFAULT_HOST

            # Validate the API key is not empty
            if not api_key:
                errors[CONF_API_KEY] = "api_key_required"
            else:
                # Listing the boxes validates the key; the box step shows them
                # without requesting all pages a second time
                try:
                    storage_boxes = await self._fetch_storage_boxes(api_key, host)
                except HetznerError as err:
                    errors[CONF_API_KEY] = _error_key(err)
                except Exception:
                    _LOGGER.exception("Unexpected error listing the storage boxes")
                    errors[CONF_API_KEY] = "unknown"
                else:
                    _LOGGER.debug("API key validated successfully")
                    self.data = {
                        CONF_API_KEY: api_key,
                        CONF_HOST: host,
                    }
                    self._listed_storage_boxes = storage_boxes
                    return await self.async_step_storage_box()

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],  # noqa: ARG002
    ) -> ConfigFlowResult:
        """Start reauthentication after the API rejected the key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key after the API rejected the old one."""
        return await self._async_step_replace_api_key(
            "reauth_confirm", self._get_reauth_entry(), user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user replace the API key of a configured storage box."""
        return await self._async_step_replace_api_key(
            "reconfigure", self._get_reconfigure_entry(), user_input
        )

    async def _async_step_replace_api_key(
        self, step_id: str, entry: ConfigEntry, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Ask for a new API key and check it can still see the entry's box.

        The storage box itself stays: it is the entry's unique_id. Another box
        is added as a new entry.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            host = entry.data[CONF_HOST]

            if not api_key:
                errors[CONF_API_KEY] = "api_key_required"
            # Query the box itself: a key of another project is valid as well
            elif validation_error := await self._validate_api_key(
                api_key, host, entry.data[CONF_STORAGE_BOX_ID]
            ):
                errors[CONF_API_KEY] = validation_error
            else:
                # Reloads a loaded entry and sets up one that failed, e.g. with
                # a rejected key
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id=step_id,
            data_schema=DATA_SCHEMA,
            errors=errors,
            # Only reauth gets the entry title as {name} from HA itself
            description_placeholders={
                CONF_STORAGE_BOX_ID: entry.data[CONF_STORAGE_BOX_ID]
            },
        )

    def _client(self, api_key: str, host: str) -> HetznerClient:
        """Return an API client using Home Assistant's shared session."""
        return HetznerClient(async_get_clientsession(self.hass), api_key, host)

    async def _validate_api_key(
        self, api_key: str, host: str, storage_box_id: str
    ) -> str | None:
        """Check the API key has access to the storage box.

        Args:
            api_key: API key to check
            host: Host of the Hetzner API
            storage_box_id: Storage box the key needs access to

        Returns:
            None if the key has access, else the error key of the form
        """
        try:
            await self._client(api_key, host).async_get_storage_box(storage_box_id)
        except HetznerError as err:
            return _error_key(err, storage_box_id)
        except Exception:
            _LOGGER.exception("Unexpected error checking the API key")
            return "unknown"
        _LOGGER.debug("API key validated successfully")
        return None

    def _build_box_options(
        self, storage_boxes: dict[str, StorageBoxInfo]
    ) -> list[selector.SelectOptionDict]:
        """Build dropdown options for the storage boxes.

        Args:
            storage_boxes: Dict mapping box ID to box info

        Returns:
            Options with the box ID as value and a formatted description as label
        """
        # Select options cannot be disabled, so configured boxes stay in the
        # list, marked and last (the dropdown preselects the first option);
        # selecting one shows an error. The mark is English: option labels
        # built here cannot be translated
        configured = self._async_current_ids(include_ignore=False)
        return [
            selector.SelectOptionDict(
                value=box_id,
                label=self._box_label(box_id, box_info, box_id in configured),
            )
            for box_id, box_info in sorted(
                storage_boxes.items(), key=lambda item: item[0] in configured
            )
        ]

    @staticmethod
    def _box_label(box_id: str, box_info: StorageBoxInfo, configured: bool) -> str:
        """Format a dropdown label, leaving out fields the API did not return."""
        details = [f"ID: {box_id}"]
        if box_info["status"]:
            details.append(f"Status: {box_info['status']}")
        box_type, description = box_info["box_type"], box_info["description"]
        # The API repeats the type name as its description ("bx11", "BX11"):
        # shown once, as Hetzner writes it, so labels are cut off less on
        # narrow screens
        if box_type and description and box_type.casefold() == description.casefold():
            box_type = None
        type_info = ", ".join(part for part in (box_type, description) if part)
        if type_info:
            details.append(f"Type: {type_info}")
        if configured:
            details.append(ALREADY_CONFIGURED_LABEL)

        if box_info["name"]:
            return f"{box_info['name']} ({', '.join(details)})"
        return ", ".join(details)

    async def _fetch_storage_boxes(
        self, api_key: str, host: str
    ) -> dict[str, StorageBoxInfo]:
        """Fetch list of available storage boxes from Hetzner API.

        Returns a dict mapping storage box ID (as string) to its name, status,
        type name and type description. API errors are raised.
        """
        storage_boxes: dict[str, StorageBoxInfo] = {}
        for box in await self._client(api_key, host).async_list_storage_boxes():
            # Without an id the box could not be selected or polled
            if (box_id := box.get("id")) is None:
                continue
            box_type = as_dict(box.get("storage_box_type"))
            storage_boxes[str(box_id)] = {
                "name": box.get("name"),
                "status": box.get("status"),
                "box_type": box_type.get("name"),
                "description": box_type.get("description"),
            }
        return storage_boxes

    async def async_step_storage_box(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the storage box selection step."""
        errors: dict[str, str] = {}

        # Listed by the user step for the first form, fetched again on every
        # submit, so the form without boxes retries and the selection is
        # checked against the current list
        storage_boxes: dict[str, StorageBoxInfo] | None = self._listed_storage_boxes
        self._listed_storage_boxes = None
        if storage_boxes is None:
            try:
                storage_boxes = await self._fetch_storage_boxes(
                    self.data[CONF_API_KEY], self.data[CONF_HOST]
                )
            except HetznerError as err:
                # Not "no_storage_boxes": the key may well have boxes
                errors["base"] = _error_key(err)
                storage_boxes = {}
            except Exception:
                _LOGGER.exception("Unexpected error listing the storage boxes")
                errors["base"] = "unknown"
                storage_boxes = {}
        if not storage_boxes and not errors:
            errors["base"] = "no_storage_boxes"

        if not storage_boxes:
            return self.async_show_form(
                step_id="storage_box",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        # Empty when the form without boxes is submitted to fetch them again
        if user_input:
            # The dropdown submits the box ID, which stays stable while the
            # box's status or name may change between showing and submitting
            selected_box_id = user_input.get(CONF_STORAGE_BOX_ID, "")
            selected_box = storage_boxes.get(selected_box_id)

            if selected_box_id in self._async_current_ids(include_ignore=False):
                # Shown in the form, so another box can be selected. Checked
                # before setting the flow's unique_id: a second flow picking
                # the same box would otherwise abort as already in progress
                errors["base"] = "already_configured"
            elif selected_box:
                # The storage box id as unique ID allows one entry per box
                await self.async_set_unique_id(selected_box_id)
                self._abort_if_unique_id_configured()
                # Create config entry with selected storage box
                return self.async_create_entry(
                    title=f"Hetzner {selected_box['name'] or selected_box_id}",
                    data={
                        CONF_API_KEY: self.data[CONF_API_KEY],
                        CONF_HOST: self.data[CONF_HOST],
                        CONF_STORAGE_BOX_ID: selected_box_id,
                    },
                )
            else:
                errors["base"] = "invalid_selection"

        return self.async_show_form(
            step_id="storage_box",
            data_schema=create_storage_box_schema(
                self._build_box_options(storage_boxes)
            ),
            errors=errors,
        )
