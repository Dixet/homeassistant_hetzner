"""Binary sensors for the access settings of a Hetzner Storage Box."""

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import as_dict
from .const import CONF_STORAGE_BOX_ID
from .coordinator import (
    HetznerConfigEntry,
    HetznerDataUpdateCoordinator,
    storage_box_unique_id,
)
from .entity import (
    HetznerEntity,
    HetznerSubaccountEntity,
    async_add_subaccount_entities,
)

# Entities only read coordinator data and never call the API themselves
PARALLEL_UPDATES = 0

# One binary sensor per boolean of access_settings in the API; the key is the
# API field and the end of the unique_id. These are the settings of the main
# account only, subaccounts have their own (below), so the names say
# "(main account)"
ACCESS_SETTINGS: tuple[BinarySensorEntityDescription, ...] = tuple(
    BinarySensorEntityDescription(
        key=key,
        translation_key=translation_key,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=enabled_default,
    )
    for key, translation_key, enabled_default in (
        ("reachable_externally", "reachable_externally", True),
        ("samba_enabled", "samba", False),
        ("ssh_enabled", "ssh", False),
        ("webdav_enabled", "webdav", False),
        ("zfs_enabled", "zfs_snapshot_folder", False),
    )
)

# The access settings of each subaccount; the key is the API field and the end
# of the unique_id like above. Own translation keys: no "(main account)" suffix.
# The protocols are disabled by default: with many subaccounts they add up, and
# only new registry entries are affected, existing ones stay enabled
SUBACCOUNT_ACCESS_SETTINGS: tuple[BinarySensorEntityDescription, ...] = tuple(
    BinarySensorEntityDescription(
        key=key,
        translation_key=translation_key,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=enabled_default,
    )
    for key, translation_key, enabled_default in (
        ("reachable_externally", "subaccount_reachable_externally", True),
        ("samba_enabled", "subaccount_samba", False),
        ("ssh_enabled", "subaccount_ssh", False),
        ("webdav_enabled", "subaccount_webdav", False),
        ("readonly", "readonly", True),
    )
)

# English entity names by translation key, the base of new entity_ids in every
# language; must match translations/en.json, for the main account without the
# " (main account)" suffix, which entity_ids leave out (checked by the tests)
OBJECT_ID_NAMES = {
    "reachable_externally": "Reachable externally",
    "samba": "Samba",
    "ssh": "SSH",
    "webdav": "WebDAV",
    "zfs_snapshot_folder": "ZFS snapshot folder",
    "subaccount_reachable_externally": "Reachable externally",
    "subaccount_samba": "Samba",
    "subaccount_ssh": "SSH",
    "subaccount_webdav": "WebDAV",
    "readonly": "Read-only",
}


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    config_entry: HetznerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the access setting binary sensors of a box and its subaccounts."""
    storage_box_id = config_entry.data[CONF_STORAGE_BOX_ID]
    # Already refreshed in __init__.async_setup_entry
    coordinator = config_entry.runtime_data

    async_add_entities(
        HetznerAccessSettingBinarySensor(coordinator, storage_box_id, description)
        for description in ACCESS_SETTINGS
    )
    async_add_subaccount_entities(
        config_entry,
        async_add_entities,
        lambda subaccount_id: (
            HetznerSubaccountAccessSettingBinarySensor(
                coordinator, storage_box_id, subaccount_id, description
            )
            for description in SUBACCOUNT_ACCESS_SETTINGS
        ),
    )


def _access_setting(access_settings: dict[str, Any], key: str) -> bool | None:
    """Return one access setting, None if the API leaves it out or sends null."""
    # Unknown rather than off when the API leaves a setting out
    value = access_settings.get(key)
    return None if value is None else bool(value)


class HetznerAccessSettingBinarySensor(HetznerEntity, BinarySensorEntity):
    """Whether one access method of the storage box is enabled."""

    def __init__(
        self,
        coordinator: HetznerDataUpdateCoordinator,
        storage_box_id: str,
        description: BinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor of one access setting."""
        super().__init__(coordinator, storage_box_id)
        self.entity_description = description
        self._attr_unique_id = storage_box_unique_id(
            self._storage_box_id, description.key
        )
        self._object_id_name = OBJECT_ID_NAMES[str(description.translation_key)]

    @property
    def is_on(self) -> bool | None:
        """Return whether the access method is enabled."""
        return _access_setting(
            as_dict(self._box.get("access_settings")), self.entity_description.key
        )


class HetznerSubaccountAccessSettingBinarySensor(
    HetznerSubaccountEntity, BinarySensorEntity
):
    """Whether one access method is enabled for a subaccount, or it is read-only."""

    def __init__(
        self,
        coordinator: HetznerDataUpdateCoordinator,
        storage_box_id: str,
        subaccount_id: str,
        description: BinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor of one subaccount setting."""
        super().__init__(coordinator, storage_box_id, subaccount_id, description.key)
        self.entity_description = description
        self._object_id_name = OBJECT_ID_NAMES[str(description.translation_key)]

    @property
    def is_on(self) -> bool | None:
        """Return whether the setting is enabled for the subaccount."""
        return _access_setting(
            as_dict(self._subaccount.get("access_settings")),
            self.entity_description.key,
        )
