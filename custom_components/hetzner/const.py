"""Constants of the Hetzner Storage Box integration."""

from homeassistant.const import UnitOfInformation

DOMAIN = "hetzner"

# Configuration keys (CONF_API_KEY and CONF_HOST come from homeassistant.const)
CONF_STORAGE_BOX_ID = "storage_box_id"

# Default values
# Hetzner public API host (fixed)
DEFAULT_HOST = "api.hetzner.com"

# Kept in step with async_migrate_entry, which refuses newer entries
CONFIG_ENTRY_VERSION = 5

# Size sensors in creation order. Their unique_ids end in _<metric> and are
# parsed by splitting on "_", so metrics must not contain one
SIZE_METRICS = ("total", "used", "data", "snapshots", "free")
# Display unit of new size sensors; their native unit is bytes
SIZE_SUGGESTED_UNIT = UnitOfInformation.GIBIBYTES

# Values of the status field of a storage box in the API
STORAGE_BOX_STATUSES = ("active", "initializing", "locked")

# Display unit setting of config entries before version 5, only read by the
# migration: stored in data (< v4) or options (v4), default bytes
LEGACY_CONF_UNIT = "unit"
LEGACY_DEFAULT_UNIT = UnitOfInformation.BYTES
# Units that setting offered, keyed by the suffix they gave size unique_ids
LEGACY_UNITS = {
    unit.lower(): unit
    for unit in (
        UnitOfInformation.BYTES,
        UnitOfInformation.MEGABYTES,
        UnitOfInformation.GIGABYTES,
        UnitOfInformation.TERABYTES,
    )
}
