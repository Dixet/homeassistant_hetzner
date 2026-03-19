from __future__ import annotations

import logging

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_UNIT,
    DEFAULT_HOST,
    DEFAULT_UNIT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]
SIZE_METRICS = {"total", "used", "data", "snapshots", "free"}
VALID_UNITS = {"b", "mb", "gb", "tb", "kib", "mib", "gib", "tib"}


def _update_sensor_unit_options(
    entity_registry: er.EntityRegistry,
    entity_id: str,
    unit: str,
) -> None:
    """Update stored sensor unit options in the entity registry."""
    # This is the option SensorEntity actually reads back on startup
    entity_registry.async_update_entity_options(
        entity_id,
        f"{SENSOR_DOMAIN}.private",
        {"suggested_unit_of_measurement": unit},
    )

    # Clear any explicit user override if one exists
    entity_registry.async_update_entity(
        entity_id,
        unit_of_measurement=None,
    )


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up Hetzner from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    hass.data[DOMAIN][config_entry.entry_id] = {
        CONF_API_KEY: config_entry.data.get(CONF_API_KEY),
        CONF_HOST: config_entry.data.get(CONF_HOST, DEFAULT_HOST),
        CONF_UNIT: config_entry.options.get(
            CONF_UNIT,
            config_entry.data.get(CONF_UNIT, DEFAULT_UNIT),
        ),
    }

    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_update_listener)
    )

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry,
        PLATFORMS,
    )

    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(config_entry.entry_id, None)

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries and entity unique IDs."""
    entity_registry = er.async_get(hass)

    current_unit = config_entry.options.get(
        CONF_UNIT,
        config_entry.data.get(CONF_UNIT, DEFAULT_UNIT),
    ).lower()

    _LOGGER.debug(
        "Migrating config entry %s with version %s to version 2",
        config_entry.entry_id,
        config_entry.version,
    )

    if config_entry.version < 2:
        for entry in er.async_entries_for_config_entry(
            entity_registry, config_entry.entry_id
        ):
            unique_id = str(entry.unique_id)

            if not unique_id.startswith(f"{DOMAIN}_storage_box_"):
                continue

            parts = unique_id.split("_")

            # Legacy format: hetzner_storage_box_<id>_<metric>
            if len(parts) >= 5 and parts[-1] in SIZE_METRICS:
                new_unique_id = f"{unique_id}_{current_unit}"
                await _safe_update_unique_id(
                    hass=hass,
                    entity_registry=entity_registry,
                    entity_entry=entry,
                    new_unique_id=new_unique_id,
                )

        hass.config_entries.async_update_entry(config_entry, version=3)

    return True


async def _async_update_listener(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Rename size sensor unique_ids when unit changes, update sensor options, then reload."""
    new_unit = config_entry.options.get(
        CONF_UNIT,
        config_entry.data.get(CONF_UNIT, DEFAULT_UNIT),
    ).upper()

    entity_registry = er.async_get(hass)

    for entry in er.async_entries_for_config_entry(
        entity_registry, config_entry.entry_id
    ):
        unique_id = str(entry.unique_id)

        if not unique_id.startswith(f"{DOMAIN}_storage_box_"):
            continue

        parts = unique_id.split("_")

        # Current format: hetzner_storage_box_<id>_<metric>_<unit>
        if len(parts) >= 6 and parts[-2] in SIZE_METRICS and parts[-1] in VALID_UNITS:
            current_unit = parts[-1].lower()

            if current_unit != new_unit.lower():
                new_unique_id = "_".join(parts[:-1] + [new_unit.lower()])

                try:
                    entity_registry.async_update_entity(
                        entry.entity_id,
                        new_unique_id=new_unique_id,
                    )
                except ValueError as err:
                    _LOGGER.error(
                        "Could not rename %s from %s to %s: %s",
                        entry.entity_id,
                        unique_id,
                        new_unique_id,
                        err,
                    )

            _update_sensor_unit_options(entity_registry, entry.entity_id, new_unit)
            continue

        # Legacy format: hetzner_storage_box_<id>_<metric>
        if len(parts) >= 5 and parts[-1] in SIZE_METRICS:
            new_unique_id = f"{unique_id}_{new_unit.lower()}"

            try:
                entity_registry.async_update_entity(
                    entry.entity_id,
                    new_unique_id=new_unique_id,
                )
            except ValueError as err:
                _LOGGER.error(
                    "Could not rename legacy %s from %s to %s: %s",
                    entry.entity_id,
                    unique_id,
                    new_unique_id,
                    err,
                )

            _update_sensor_unit_options(entity_registry, entry.entity_id, new_unit)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][config_entry.entry_id] = {
        CONF_API_KEY: config_entry.data.get(CONF_API_KEY),
        CONF_HOST: config_entry.data.get(CONF_HOST, DEFAULT_HOST),
        CONF_UNIT: new_unit,
    }

    await hass.config_entries.async_reload(config_entry.entry_id)


async def _safe_update_unique_id(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    entity_entry: er.RegistryEntry,
    new_unique_id: str,
) -> None:
    """Safely update a unique_id, removing conflicting stale entries if needed."""
    old_unique_id = str(entity_entry.unique_id)

    if old_unique_id == new_unique_id:
        return

    existing_entity_id = entity_registry.async_get_entity_id(
        "sensor",
        DOMAIN,
        new_unique_id,
    )

    if existing_entity_id and existing_entity_id != entity_entry.entity_id:
        _LOGGER.warning(
            "Removing conflicting stale entity %s before renaming %s -> %s",
            existing_entity_id,
            old_unique_id,
            new_unique_id,
        )
        entity_registry.async_remove(existing_entity_id)

    try:
        _LOGGER.debug(
            "Updating unique_id for %s: %s -> %s",
            entity_entry.entity_id,
            old_unique_id,
            new_unique_id,
        )
        entity_registry.async_update_entity(
            entity_entry.entity_id,
            new_unique_id=new_unique_id,
        )
    except ValueError as err:
        _LOGGER.error(
            "Could not update unique_id for %s: %s -> %s (%s)",
            entity_entry.entity_id,
            old_unique_id,
            new_unique_id,
            err,
        )
