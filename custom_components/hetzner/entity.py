"""Base entities of a Hetzner Storage Box."""

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    HetznerConfigEntry,
    HetznerDataUpdateCoordinator,
    storage_box_device_info,
    storage_box_unique_id,
    subaccount_device_identifier,
    subaccount_device_name,
)


@callback
def async_add_subaccount_entities(
    config_entry: HetznerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    entity_factory: Callable[[str], Iterable[Entity]],
) -> None:
    """Add the entities of every subaccount, now and when new ones appear.

    entity_factory returns the entities of one subaccount id.
    """
    coordinator = config_entry.runtime_data
    known: set[str] = set()

    @callback
    def _async_add_new_subaccounts() -> None:
        """Add the entities of subaccounts not seen before."""
        current = set(coordinator.data.subaccounts)
        # Removed subaccounts lost their device and entities in the coordinator
        known.intersection_update(current)
        if new := current - known:
            known.update(new)
            async_add_entities(
                entity
                for subaccount_id in sorted(new)
                for entity in entity_factory(subaccount_id)
            )

    _async_add_new_subaccounts()
    config_entry.async_on_unload(
        coordinator.async_add_listener(_async_add_new_subaccounts)
    )


class HetznerEntity(CoordinatorEntity[HetznerDataUpdateCoordinator]):
    """Base entity grouping all entities under one storage box device."""

    _attr_has_entity_name = True
    # English name for new entity_ids; None means the device name alone
    _object_id_name: str | None = None

    def __init__(
        self, coordinator: HetznerDataUpdateCoordinator, storage_box_id: str
    ) -> None:
        """Initialize an entity of the storage box storage_box_id."""
        super().__init__(coordinator)
        self._storage_box_id = storage_box_id

    @property
    def _box(self) -> dict[str, Any]:
        """Return the storage box of the last update, empty without data."""
        data = self.coordinator.data
        return data.box if data is not None else {}

    @property
    def suggested_object_id(self) -> str | None:
        """Return the English name to base new entity_ids on.

        HA would derive them from the translated name in languages with
        native entity_ids (e.g. de, nl); keep them English as documented.
        """
        return self._object_id_name

    @property
    def device_info(self) -> DeviceInfo | ChildDeviceInfo:
        """Return the storage box device."""
        return storage_box_device_info(self._storage_box_id, self._box)


class HetznerSubaccountEntity(HetznerEntity):
    """Base entity grouping the entities of a subaccount under its own device.

    The device is a child device of the box: a subaccount is a part of the
    storage box (its own login, access settings and home directory on the
    same box), not a device HA reaches through it.
    """

    def __init__(
        self,
        coordinator: HetznerDataUpdateCoordinator,
        storage_box_id: str,
        subaccount_id: str,
        key: str,
    ) -> None:
        """Initialize the entity key of one subaccount."""
        super().__init__(coordinator, storage_box_id)
        self._subaccount_id = subaccount_id
        self._attr_unique_id = storage_box_unique_id(
            storage_box_id, "subaccount", subaccount_id, key
        )

    @property
    def _subaccount(self) -> dict[str, Any]:
        """Return the subaccount of the last update, empty if it is gone."""
        data = self.coordinator.data
        if data is None:
            return {}
        return data.subaccounts.get(self._subaccount_id) or {}

    @property
    def available(self) -> bool:
        """Unavailable once the API no longer lists the subaccount.

        Its device and entities are removed by the coordinator right after.
        """
        data = self.coordinator.data
        return (
            super().available
            and data is not None
            and self._subaccount_id in data.subaccounts
        )

    @property
    def device_info(self) -> ChildDeviceInfo:
        """Return the subaccount's device, a child device of the box."""
        # The parent's name comes first in entity names and entity_ids, which
        # keeps them apart from the box's own (binary_sensor.storage_box_backup
        # _sub1_ssh vs ..._backup_ssh) and from equally named subaccounts of
        # other boxes
        return ChildDeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    subaccount_device_identifier(
                        self._storage_box_id, self._subaccount_id
                    ),
                )
            },
            name=subaccount_device_name(self._subaccount, self._subaccount_id),
            # Registered in __init__.async_setup_entry before the platforms
            parent_device_id=dr.async_get_device_id_by_identifier(
                self.coordinator.hass,
                (DOMAIN, self._storage_box_id),
                config_entry_id=self.coordinator.config_entry.entry_id,
            ),
        )
