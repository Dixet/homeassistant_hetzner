"""Sensors of a Hetzner Storage Box."""

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    MAX_LENGTH_STATE_STATE,
    EntityCategory,
    UnitOfInformation,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import as_dict
from .const import CONF_STORAGE_BOX_ID, SIZE_SUGGESTED_UNIT, STORAGE_BOX_STATUSES
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

_LOGGER = logging.getLogger(__name__)

# Entities only read coordinator data and never call the API themselves
PARALLEL_UPDATES = 0

# English entity names by translation key, the base of new entity_ids in every
# language; must match translations/en.json (checked by the tests)
OBJECT_ID_NAMES = {
    "location": "Location",
    "total_size": "Total size",
    "total_used": "Total used",
    "data_size": "Data size",
    "snapshot_size": "Snapshot size",
    "free_space": "Free space",
    "home_directory": "Home directory",
}


def _bytes(values: dict[str, Any], key: str) -> int | None:
    """Return one size in bytes, None if the API leaves it out or sends no int."""
    value = values.get(key)
    # bool is an int subclass but never a size
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


# Unknown rather than 0 when the API leaves a value out: a 0 would end up in
# the long-term statistics as a real measurement
def _total_size(box: dict[str, Any]) -> int | None:
    """Return the size of the storage box type in bytes."""
    return _bytes(as_dict(box.get("storage_box_type")), "size")


def _stats_size(key: str) -> Callable[[dict[str, Any]], int | None]:
    """Return a function reading one size of the box's stats in bytes."""
    return lambda box: _bytes(as_dict(box.get("stats")), key)


def _free_space(box: dict[str, Any]) -> int | None:
    """Return the total size minus the total disk usage in bytes."""
    total = _total_size(box)
    used = _bytes(as_dict(box.get("stats")), "size")
    if total is None or used is None:
        return None
    # The total disk usage (data and snapshots, as the API counts it)
    # subtracted, so used and free add up to the total. Clamped to 0 to avoid
    # negative values
    return max(0, total - used)


@dataclass(frozen=True, kw_only=True)
class HetznerSizeSensorEntityDescription(SensorEntityDescription):
    """Describes one size of a storage box."""

    # Reads the size in bytes from the box as the API sent it
    value_fn: Callable[[dict[str, Any]], int | None]


# Size sensors in creation order; the key is the metric of SIZE_METRICS and
# the end of the unique_id (checked by the tests)
SIZE_SENSORS: tuple[HetznerSizeSensorEntityDescription, ...] = (
    HetznerSizeSensorEntityDescription(
        key="total", translation_key="total_size", value_fn=_total_size
    ),
    HetznerSizeSensorEntityDescription(
        key="used", translation_key="total_used", value_fn=_stats_size("size")
    ),
    HetznerSizeSensorEntityDescription(
        key="data", translation_key="data_size", value_fn=_stats_size("size_data")
    ),
    HetznerSizeSensorEntityDescription(
        key="snapshots",
        translation_key="snapshot_size",
        value_fn=_stats_size("size_snapshots"),
    ),
    HetznerSizeSensorEntityDescription(
        key="free", translation_key="free_space", value_fn=_free_space
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    config_entry: HetznerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for Hetzner Storage Box using the entry's coordinator."""
    storage_box_id = config_entry.data[CONF_STORAGE_BOX_ID]
    # Already refreshed in __init__.async_setup_entry
    coordinator = config_entry.runtime_data

    entities: list[SensorEntity] = [
        HetznerStatusSensor(coordinator, storage_box_id),
        HetznerLocationSensor(coordinator, storage_box_id),
    ]
    entities.extend(
        HetznerSizeSensor(coordinator, storage_box_id, description)
        for description in SIZE_SENSORS
    )

    # No update_before_add: the coordinator already holds fresh data
    async_add_entities(entities)
    async_add_subaccount_entities(
        config_entry,
        async_add_entities,
        lambda subaccount_id: (
            HetznerSubaccountHomeDirectorySensor(
                coordinator, storage_box_id, subaccount_id
            ),
        ),
    )


class HetznerStatusSensor(HetznerEntity, SensorEntity):
    """Sensor representing the storage box status and details."""

    _attr_device_class = SensorDeviceClass.ENUM
    # Translates the states and picks the icon; the name stays the device's
    _attr_translation_key = "status"
    # Details of the box that rarely change (name, type on an up- or
    # downgrade): not worth storing with every state change
    _unrecorded_attributes = frozenset(
        {"id", "name", "username", "server", "system", "storage_box_type", "created"}
    )

    def __init__(
        self, coordinator: HetznerDataUpdateCoordinator, storage_box_id: str
    ) -> None:
        """Initialize the status sensor."""
        super().__init__(coordinator, storage_box_id)
        self._attr_options = list(STORAGE_BOX_STATUSES)
        self._attr_unique_id = storage_box_unique_id(self._storage_box_id)
        # Main feature of the device: entity takes the device name
        self._attr_name = None
        self._logged_unknown_status: str | None = None

    @property
    def native_value(self) -> str | None:
        """Return the status, None if the API sent an unknown one."""
        data = self._box
        status = data.get("status")
        if status is None or status in STORAGE_BOX_STATUSES:
            return status
        # An ENUM sensor must not report a value outside its options, so a
        # status added to the API later shows as unknown instead of failing
        if status != self._logged_unknown_status:
            _LOGGER.warning(
                "Unknown status %r of storage box %s", status, self._storage_box_id
            )
            self._logged_unknown_status = status
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details of the storage box."""
        data = self._box
        s_type = as_dict(data.get("storage_box_type"))
        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "username": data.get("username"),
            "server": data.get("server"),
            "system": data.get("system"),
            "storage_box_type": s_type.get("name"),
            "created": data.get("created"),
        }


class HetznerLocationSensor(HetznerEntity, SensorEntity):
    """Sensor representing the storage box location."""

    _attr_translation_key = "location"
    _object_id_name = OBJECT_ID_NAMES["location"]
    # Details of the location, which only change along with the state
    _unrecorded_attributes = frozenset(
        {"id", "country", "city", "latitude", "longitude", "description"}
    )

    def __init__(
        self, coordinator: HetznerDataUpdateCoordinator, storage_box_id: str
    ) -> None:
        """Initialize the location sensor."""
        super().__init__(coordinator, storage_box_id)
        self._attr_unique_id = storage_box_unique_id(self._storage_box_id, "location")

    @property
    def native_value(self) -> str | None:
        """Return the name of the location."""
        return as_dict(self._box.get("location")).get("name")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details of the location."""
        location = as_dict(self._box.get("location"))
        return {
            "id": location.get("id"),
            "country": location.get("country"),
            "city": location.get("city"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "description": location.get("description"),
        }


class HetznerSizeSensor(HetznerEntity, SensorEntity):
    """One size of the storage box, described by SIZE_SENSORS."""

    # The API reports bytes; HA converts them to the unit chosen in the entity
    # settings. New entities default to GiB: Hetzner sizes are binary, a BX11
    # ("1 TB") reports 2^40 bytes
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = SIZE_SUGGESTED_UNIT
    _attr_suggested_display_precision = 2
    # Report as a measurement for long-term statistics
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DATA_SIZE

    entity_description: HetznerSizeSensorEntityDescription

    def __init__(
        self,
        coordinator: HetznerDataUpdateCoordinator,
        storage_box_id: str,
        description: HetznerSizeSensorEntityDescription,
    ) -> None:
        """Initialize the sensor of one size."""
        super().__init__(coordinator, storage_box_id)
        self.entity_description = description
        self._object_id_name = OBJECT_ID_NAMES[str(description.translation_key)]
        self._attr_unique_id = storage_box_unique_id(
            self._storage_box_id, description.key
        )

    @property
    def native_value(self) -> int | None:
        """Return the size in bytes, None if the API data is missing."""
        return self.entity_description.value_fn(self._box)


class HetznerSubaccountHomeDirectorySensor(HetznerSubaccountEntity, SensorEntity):
    """Directory of the storage box a subaccount is limited to."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "home_directory"
    _object_id_name = OBJECT_ID_NAMES["home_directory"]
    # Rarely changed details of the subaccount, not worth keeping in history
    _unrecorded_attributes = frozenset({"username", "server", "description"})

    def __init__(
        self,
        coordinator: HetznerDataUpdateCoordinator,
        storage_box_id: str,
        subaccount_id: str,
    ) -> None:
        """Initialize the home directory sensor of one subaccount."""
        super().__init__(coordinator, storage_box_id, subaccount_id, "home_directory")

    @property
    def native_value(self) -> str | None:
        """Return the home directory, None if it does not fit in a state."""
        home_directory = self._subaccount.get("home_directory")
        # The API allows 999 characters, a state 255; HA would log an error on
        # every update and fall back to unknown anyway
        if home_directory is None or len(str(home_directory)) > MAX_LENGTH_STATE_STATE:
            return None
        return str(home_directory)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the login details and description of the subaccount."""
        # What is needed to connect, and the subaccount's own description
        subaccount = self._subaccount
        return {
            "username": subaccount.get("username"),
            "server": subaccount.get("server"),
            "description": subaccount.get("description"),
        }
