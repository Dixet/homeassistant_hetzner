from datetime import timedelta
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_STORAGE_BOX_ID,
    CONF_UNIT,
    DEFAULT_HOST,
    DEFAULT_UNIT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=600)  # update every 10 minutes


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    discovery_info=None,
) -> None:
    """Set up sensors for Hetzner Storage Box using a shared coordinator."""
    api_key = hass.data[DOMAIN][config_entry.entry_id].get(CONF_API_KEY)
    host = hass.data[DOMAIN][config_entry.entry_id].get(CONF_HOST, DEFAULT_HOST)
    storage_box_id = config_entry.data.get(CONF_STORAGE_BOX_ID)
    display_unit = hass.data[DOMAIN][config_entry.entry_id].get(CONF_UNIT, DEFAULT_UNIT)

    if storage_box_id is None:
        _LOGGER.error("No storage box id in config entry")
        return

    # Create a coordinator that fetches the storage box info and stats
    async def async_update_data():
        session = async_get_clientsession(hass)
        headers = {"Authorization": f"Bearer {api_key}"}
        url = f"https://{host}/v1/storage_boxes/{storage_box_id}"

        async with session.get(url, headers=headers, timeout=10) as resp:
            if resp.status != 200:
                _LOGGER.error("Error fetching storage box data: %s", resp.status)
                return {}
            data = await resp.json()
            return data.get("storage_box") or {}

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{storage_box_id}",
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    # Do the first refresh so entities have data immediately
    await coordinator.async_config_entry_first_refresh()

    # Create entities: status, location, access options, total size, used size, data size, snapshot size, free space
    entities = [
        HetznerStatusSensor(coordinator, storage_box_id),
        HetznerLocationSensor(coordinator, storage_box_id),
        HetznerAccessOptionsSensor(coordinator, storage_box_id),
        HetznerSizeSensor(coordinator, storage_box_id, "total", display_unit),
        HetznerSizeSensor(coordinator, storage_box_id, "used", display_unit),
        HetznerSizeSensor(coordinator, storage_box_id, "data", display_unit),
        HetznerSizeSensor(coordinator, storage_box_id, "snapshots", display_unit),
        HetznerSizeSensor(coordinator, storage_box_id, "free", display_unit),
    ]

    async_add_entities(entities, True)


class HetznerStatusSensor(CoordinatorEntity, SensorEntity):
    """Sensor representing the storage box status and details."""

    def __init__(self, coordinator: DataUpdateCoordinator, storage_box_id: str):
        super().__init__(coordinator)
        self._storage_box_id = storage_box_id
        self._attr_unique_id = f"{DOMAIN}_storage_box_{self._storage_box_id}"

    @property
    def name(self) -> str:
        data = self.coordinator.data or {}
        box_name = data.get("name")
        if box_name:
            return f"Storage Box {box_name}"
        return f"Storage Box {self._storage_box_id}"

    @property
    def icon(self) -> str:
        return "mdi:database"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        return data.get("status")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        s_type = data.get("storage_box_type") or {}
        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "username": data.get("username"),
            "server": data.get("server"),
            "system": data.get("system"),
            "storage_box_type": s_type.get("name"),
            "created": data.get("created"),
        }


class HetznerLocationSensor(CoordinatorEntity, SensorEntity):
    """Sensor representing the storage box location."""

    def __init__(self, coordinator: DataUpdateCoordinator, storage_box_id: str):
        super().__init__(coordinator)
        self._storage_box_id = storage_box_id
        self._attr_unique_id = f"{DOMAIN}_storage_box_{self._storage_box_id}_location"

    @property
    def name(self) -> str:
        data = self.coordinator.data or {}
        box_name = data.get("name")
        if box_name:
            return f"Storage Box {box_name} Location"
        return f"Storage Box {self._storage_box_id} Location"

    @property
    def icon(self) -> str:
        return "mdi:map-marker"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        location = data.get("location") or {}
        return location.get("name")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        location = data.get("location") or {}
        return {
            "id": location.get("id"),
            "country": location.get("country"),
            "city": location.get("city"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "description": location.get("description"),
        }


class HetznerSizeSensor(CoordinatorEntity, SensorEntity):
    """Sensor for various storage size metrics."""

    VALID_TYPES = {"total", "used", "data", "snapshots", "free"}

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        storage_box_id: str,
        metric: str,
        display_unit: str,
    ):
        super().__init__(coordinator)

        if metric not in self.VALID_TYPES:
            raise ValueError("Invalid metric for HetznerSizeSensor")

        self._metric = metric
        self._storage_box_id = storage_box_id
        self._display_unit = display_unit

        # Use bytes as the unit (native value is bytes from API)
        self._attr_native_unit_of_measurement = self._display_unit
        self._attr_suggested_display_precision = 2

        # Report as a measurement (for long term statistics) and set device class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_class = SensorDeviceClass.DATA_SIZE
        self._attr_unique_id = (
            f"{DOMAIN}_storage_box_{self._storage_box_id}_{self._metric}_"
            f"{self._display_unit.lower()}"
        )

    @property
    def name(self) -> str:
        data = self.coordinator.data or {}
        box_name = data.get("name")
        suffix = {
            "total": "Total Size",
            "used": "Total Used",
            "data": "Data Size",
            "snapshots": "Snapshot Size",
            "free": "Free Space",
        }[self._metric]
        if box_name:
            return f"Storage Box {box_name} {suffix}"
        return f"Storage Box {self._storage_box_id} {suffix}"

    @property
    def icon(self) -> str:
        return "mdi:harddisk"

    @property
    def native_value(self) -> float:
        data = self.coordinator.data or {}
        stats = data.get("stats") or {}
        sb = data.get("storage_box_type") or {}

        total = sb.get("size") or 0
        used = stats.get("size") or 0
        data_size = stats.get("size_data") or 0
        snapshots = stats.get("size_snapshots") or 0

        if self._metric == "total":
            value = total
        elif self._metric == "used":
            value = used
        elif self._metric == "data":
            value = data_size
        elif self._metric == "snapshots":
            value = snapshots
        else:
            # free space (clamp to 0 to avoid negative values)
            value = max(0, total - data_size - snapshots)

        return self._convert_from_bytes(value)

    def _convert_from_bytes(self, value: float | int) -> float:
        factors = {
            "B": 1,
            "MB": 1000**2,
            "GB": 1000**3,
            "TB": 1000**4,
        }
        factor = factors.get(self._display_unit, 1)
        return round(float(value) / factor, 2)


class HetznerAccessOptionsSensor(CoordinatorEntity, SensorEntity):
    """Sensor that counts enabled access options and exposes each option as an attribute."""

    def __init__(self, coordinator: DataUpdateCoordinator, storage_box_id: str):
        super().__init__(coordinator)
        self._storage_box_id = storage_box_id
        self._attr_unique_id = (
            f"{DOMAIN}_storage_box_{self._storage_box_id}_access_options"
        )
        self._attr_icon = "mdi:network"
        # Expose as a numeric measurement (count of enabled access methods)
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def name(self) -> str:
        data = self.coordinator.data or {}
        box_name = data.get("name")
        if box_name:
            return f"Storage Box {box_name} Access Options"
        return f"Storage Box {self._storage_box_id} Access Options"

    @property
    def native_value(self) -> int:
        data = self.coordinator.data or {}
        access = data.get("access_settings") or {}
        # Count only values that are True
        return sum(1 for v in access.values() if v is True)

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        access = data.get("access_settings") or {}
        # Mirror each access flag as an attribute and provide a convenience list
        attrs = {}
        for k, v in access.items():
            attrs[k] = bool(v)

        return attrs
