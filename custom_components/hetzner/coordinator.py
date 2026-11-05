"""Data update coordinator of a Hetzner Storage Box."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    HetznerApiError,
    HetznerAuthError,
    HetznerClient,
    HetznerConnectionError,
    HetznerNotFoundError,
    as_dict,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=600)  # update every 10 minutes

CONFIGURATION_URL = "https://console.hetzner.com"

# The coordinator is the only runtime data of a loaded entry
type HetznerConfigEntry = ConfigEntry[HetznerDataUpdateCoordinator]


@dataclass(frozen=True, slots=True)
class StorageBoxData:
    """Result of one update: the storage box and its subaccounts."""

    box: dict[str, Any]
    # Keyed by the subaccount id as str, the form used in device identifiers
    # and unique_ids
    subaccounts: dict[str, dict[str, Any]] = field(default_factory=dict)


def storage_box_not_found_issue_id(entry_id: str) -> str:
    """Return the repair issue id for a storage box the API cannot find."""
    return f"storage_box_not_found_{entry_id}"


def storage_box_unique_id(storage_box_id: str, *parts: str) -> str:
    """Return the unique_id of an entity of a storage box.

    Without parts it is the status sensor's; the parts are appended with "_".
    """
    return "_".join((f"{DOMAIN}_storage_box_{storage_box_id}", *parts))


def subaccount_device_identifier(storage_box_id: str, subaccount_id: str) -> str:
    """Return the device identifier of a subaccount of a storage box."""
    return f"{storage_box_id}_subaccount_{subaccount_id}"


def storage_box_device_info(storage_box_id: str, box: dict[str, Any]) -> DeviceInfo:
    """Return the device info of the storage box device."""
    box_name = box.get("name") or storage_box_id
    return DeviceInfo(
        identifiers={(DOMAIN, storage_box_id)},
        name=f"Storage Box {box_name}",
        manufacturer="Hetzner",
        model=as_dict(box.get("storage_box_type")).get("name"),
        configuration_url=CONFIGURATION_URL,
    )


def subaccount_device_name(subaccount: dict[str, Any], subaccount_id: str) -> str:
    """Return the name of a subaccount's child device.

    HA puts the box device's name in front of it in entity names and
    entity_ids. The name is required by the API; fall back to the username
    anyway.
    """
    return subaccount.get("name") or subaccount.get("username") or subaccount_id


def subaccount_ids_of_device(
    identifiers: Iterable[tuple[str, str]], storage_box_id: str
) -> set[str]:
    """Return the ids of the box's subaccounts a device stands for.

    Empty for the storage box device itself and for foreign devices.
    """
    prefix = subaccount_device_identifier(storage_box_id, "")
    return {
        identifier[len(prefix) :]
        for domain, identifier in identifiers
        if domain == DOMAIN and identifier.startswith(prefix)
    }


class HetznerDataUpdateCoordinator(DataUpdateCoordinator[StorageBoxData]):
    """Fetch storage box info, stats and subaccounts for one config entry."""

    config_entry: HetznerConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: HetznerConfigEntry,
        api_key: str,
        host: str,
        storage_box_id: str,
    ) -> None:
        """Initialize the coordinator and its API client."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{storage_box_id}",
            update_interval=SCAN_INTERVAL,
        )
        self._client = HetznerClient(async_get_clientsession(hass), api_key, host)
        self._storage_box_id = storage_box_id

    async def _async_update_data(self) -> StorageBoxData:
        """Fetch the box and its subaccounts, then update their devices."""
        issue_id = storage_box_not_found_issue_id(self.config_entry.entry_id)
        try:
            storage_box = await self._client.async_get_storage_box(self._storage_box_id)
            # Two requests per update, far below the API's 3600 requests/hour
            subaccounts = await self._client.async_list_subaccounts(
                self._storage_box_id
            )
        except HetznerAuthError as err:
            # Starts reauth and stops polling: a rejected token will not recover.
            # The reauth notice replaces a not found issue that says HA retries
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_api_key",
                translation_placeholders={"error": str(err)},
            ) from err
        except HetznerNotFoundError as err:
            # A 404 may also mean a token of another project, so keep retrying
            # instead of failing the entry for good and tell the user instead.
            # The subaccount list lives below the box and is empty for a box
            # without subaccounts, so a 404 there means the box itself is gone
            # (deleted between the two requests) and is handled the same way
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="storage_box_not_found",
                translation_placeholders={"storage_box_id": self._storage_box_id},
            )
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="storage_box_not_found",
                translation_placeholders={"storage_box_id": self._storage_box_id},
            ) from err
        except HetznerApiError as err:
            # Includes maintenance (403), rate limits (429) and server errors
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"error": str(err)},
            ) from err
        except HetznerConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

        ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        data = StorageBoxData(
            box=storage_box,
            subaccounts={
                str(subaccount["id"]): subaccount
                for subaccount in subaccounts
                # Without an id there is nothing to identify it by
                if subaccount.get("id") is not None
            },
        )
        self._async_update_devices(data)
        return data

    @callback
    def _async_update_devices(self, data: StorageBoxData) -> None:
        """Keep the devices in step with the API.

        Entities only apply their device info when they are added, so names
        and the box type changed in the Hetzner Console are updated here. A
        name the user set (name_by_user) is a field of its own and stays.
        Devices of subaccounts the API no longer lists are removed, which
        also removes their entities. Only a successful update gets here, so
        an API error never removes one.
        """
        device_registry = dr.async_get(self.hass)
        entry_id = self.config_entry.entry_id

        # Registered by async_setup_entry after the first refresh
        if box_device := device_registry.async_get_device_by_identifier(
            (DOMAIN, self._storage_box_id), entry_id
        ):
            box_info = storage_box_device_info(self._storage_box_id, data.box)
            device_registry.async_update_device(
                box_device.id, name=box_info["name"], model=box_info["model"]
            )

        # Subaccount devices are child devices of the box; devices registered
        # before 1.2 are main devices until their entities are added again
        devices: list[dr.DeviceEntry | dr.ChildDeviceEntry] = [
            *dr.async_entries_for_config_entry(device_registry, entry_id),
            *dr.async_child_entries_for_config_entry(device_registry, entry_id),
        ]
        for device in devices:
            subaccount_ids = subaccount_ids_of_device(
                device.identifiers, self._storage_box_id
            )
            if not subaccount_ids:
                continue
            if listed := sorted(subaccount_ids & set(data.subaccounts)):
                if isinstance(device, dr.ChildDeviceEntry):
                    device_registry.async_update_child_device(
                        device.id,
                        name=subaccount_device_name(
                            data.subaccounts[listed[0]], listed[0]
                        ),
                    )
                continue
            _LOGGER.debug("Removing device %s of a deleted subaccount", device.name)
            device_registry.async_remove_device(device.id)
