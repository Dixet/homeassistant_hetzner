"""Tests for the Hetzner Storage Box integration setup and migrations."""

from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_HOST, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (  # type: ignore[import-untyped]
    AiohttpClientMocker,
)

from custom_components.hetzner import (
    _safe_update_unique_id,
    async_migrate_entry,
    async_remove_entry,
    async_unload_entry,
)
from custom_components.hetzner.const import (
    CONF_STORAGE_BOX_ID,
    DEFAULT_HOST,
    DOMAIN,
    LEGACY_CONF_UNIT,
    SIZE_METRICS,
)
from custom_components.hetzner.coordinator import HetznerDataUpdateCoordinator

from tests.conftest import (
    API_KEY,
    BOX_URL,
    STORAGE_BOX,
    STORAGE_BOX_ID,
    SUBACCOUNTS_URL,
)

PREFIX = f"{DOMAIN}_storage_box_{STORAGE_BOX_ID}"
METRICS = list(SIZE_METRICS)
ACCESS_OPTIONS_ID = f"{PREFIX}_access_options"
ENTRY_DATA = {
    CONF_API_KEY: API_KEY,
    CONF_HOST: DEFAULT_HOST,
    CONF_STORAGE_BOX_ID: STORAGE_BOX_ID,
}
# Byte values of the fixture by metric
BYTES = {
    "total": 2**40,
    "used": 300_000_000_000,
    "data": 250_000_000_000,
    "snapshots": 50_000_000_000,
    "free": 2**40 - 300_000_000_000,
}
assert BYTES["total"] == STORAGE_BOX["storage_box_type"]["size"]
# Bytes per unit of the removed unit setting, which converted with these
UNIT_FACTORS = {"B": 1, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
GIB_PRIVATE = {"suggested_unit_of_measurement": "GiB"}


def _mock_box(aioclient_mock: AiohttpClientMocker, box: dict[str, Any]) -> None:
    """Register the storage box API response, a box without subaccounts."""
    aioclient_mock.get(BOX_URL, json={"storage_box": box})
    aioclient_mock.get(SUBACCOUNTS_URL, json={"subaccounts": []})


def _make_entry(
    hass: HomeAssistant,
    version: int,
    unit: str | None = "GB",
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Create and add a config entry with the given version.

    Before version 4 the unit chosen during setup is stored in data, in
    version 4 in options; version 5 has no unit.
    """
    data: dict[str, Any] = dict(ENTRY_DATA)
    options = dict(options or {})
    if unit is not None and version < 4:
        data[LEGACY_CONF_UNIT] = unit
    elif unit is not None and version == 4:
        options = {LEGACY_CONF_UNIT: unit, **options}
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hetzner backup",
        version=version,
        unique_id=STORAGE_BOX_ID,
        data=data,
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


def _register(
    hass: HomeAssistant,
    unique_id: str,
    entry: MockConfigEntry | None,
    unit: str | None = None,
    domain: str = "sensor",
) -> er.RegistryEntry:
    """Pre-populate the entity registry with a hetzner entity."""
    return er.async_get(hass).async_get_or_create(
        domain, DOMAIN, unique_id, config_entry=entry, unit_of_measurement=unit
    )


def _old_size_ids(unit: str) -> dict[str, str]:
    """Return the size sensor unique_ids an entry of version 2 to 4 used."""
    return {metric: f"{PREFIX}_{metric}_{unit.lower()}" for metric in METRICS}


def _reg(hass: HomeAssistant, entity_id: str) -> er.RegistryEntry:
    """Return the registry entry of an entity that must exist."""
    reg = er.async_get(hass).async_get(entity_id)
    assert reg is not None
    return reg


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the entry and wait for it."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Setup stores the coordinator on the entry, unload removes it."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    coordinator = config_entry.runtime_data
    assert isinstance(coordinator, HetznerDataUpdateCoordinator)
    assert coordinator.config_entry is not None
    assert coordinator.config_entry.entry_id == config_entry.entry_id
    assert coordinator.data.box == storage_box
    assert coordinator.data.subaccounts == {}
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert str(url).startswith(f"https://{DEFAULT_HOST}/")
    assert headers["Authorization"] == f"Bearer {API_KEY}"
    # Nothing to reload on entry changes: reauth reloads by itself
    assert config_entry.update_listeners == []

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert not hasattr(config_entry, "runtime_data")


async def test_unload_failure_keeps_data(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Data is kept when unloading the platforms fails."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ):
        assert not await async_unload_entry(hass, config_entry)

    assert hasattr(config_entry, "runtime_data")

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("unit", ["B", "MB", "GB", "TB"])
@pytest.mark.parametrize("version", [2, 3, 4])
async def test_migrate_to_v5(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
    version: int,
    unit: str,
) -> None:
    """Size unique_ids lose the unit, which stays the displayed unit.

    The registry looks like the old platform left it: the size sensors were
    registered with the configured unit as their unit.
    """
    _mock_box(aioclient_mock, storage_box)
    entry = _make_entry(hass, version, unit=unit)
    old = {
        metric: _register(hass, unique_id, entry, unit)
        for metric, unique_id in _old_size_ids(unit).items()
    }
    status = _register(hass, PREFIX, entry)
    location = _register(hass, f"{PREFIX}_location", entry)
    access_options = _register(hass, ACCESS_OPTIONS_ID, entry)
    foreign = _register(hass, "some_other_unique_id", entry)

    with patch(
        "custom_components.hetzner.async_migrate_entry",
        wraps=async_migrate_entry,
    ) as migrate:
        await _setup(hass, entry)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert migrate.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 5
    assert entry.data == ENTRY_DATA
    assert entry.options == {}

    registry = er.async_get(hass)
    for metric, reg in old.items():
        updated = _reg(hass, reg.entity_id)
        assert updated.unique_id == f"{PREFIX}_{metric}"
        assert updated.options["sensor"]["unit_of_measurement"] == unit
        assert updated.options["sensor.private"] == GIB_PRIVATE

        state = hass.states.get(reg.entity_id)
        assert state is not None
        assert state.attributes["unit_of_measurement"] == unit
        assert float(state.state) == pytest.approx(BYTES[metric] / UNIT_FACTORS[unit])
    if unit == "GB":
        # The BX11 total the old code showed as 1,099.51 GB
        assert float(hass.states.get(old["total"].entity_id).state) == (  # type: ignore[union-attr]
            pytest.approx(1099.511627776)
        )

    assert registry.async_get(access_options.entity_id) is None
    for reg in (status, location, foreign):
        assert _reg(hass, reg.entity_id).unique_id == reg.unique_id
    # Reused by the platform: no duplicates, plus the five binary sensors
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len([e for e in entries if e.domain == "sensor"]) == 8
    assert len([e for e in entries if e.domain == "binary_sensor"]) == 5


async def test_migrate_v1_bytes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Version 1 had no unit setting and registered the size sensors in "bytes".

    HA has no converter for "bytes", so they report the equivalent B; the
    statistics unit has to be fixed once by the user (see README).
    """
    _mock_box(aioclient_mock, storage_box)
    entry = _make_entry(hass, 1, unit=None)
    old = {
        metric: _register(hass, f"{PREFIX}_{metric}", entry, "bytes")
        for metric in METRICS
    }
    access_options = _register(hass, ACCESS_OPTIONS_ID, entry)

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 5
    assert entry.data == ENTRY_DATA
    assert entry.options == {}
    assert er.async_get(hass).async_get(access_options.entity_id) is None
    for metric, reg in old.items():
        updated = _reg(hass, reg.entity_id)
        assert updated.unique_id == f"{PREFIX}_{metric}"
        assert updated.options["sensor"]["unit_of_measurement"] == "B"
        assert updated.options["sensor.private"] == GIB_PRIVATE

        state = hass.states.get(reg.entity_id)
        assert state is not None
        assert state.attributes["unit_of_measurement"] == UnitOfInformation.BYTES
        assert int(state.state) == BYTES[metric]


@pytest.mark.parametrize(
    ("data_unit", "options_unit", "expected"),
    [
        ("GB", "TB", "TB"),
        ("GB", None, "GB"),
        (None, None, "B"),
        # Not offered by the setting; the old code showed raw bytes for it
        ("PB", None, "B"),
    ],
)
async def test_migrate_unit_source(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
    data_unit: str | None,
    options_unit: str | None,
    expected: str,
) -> None:
    """A unit in options wins over data; without any the old default bytes."""
    _mock_box(aioclient_mock, storage_box)
    options = {} if options_unit is None else {LEGACY_CONF_UNIT: options_unit}
    entry = _make_entry(hass, 3, unit=data_unit, options=options)
    reg = _register(hass, f"{PREFIX}_total", entry)

    await _setup(hass, entry)

    assert entry.data == ENTRY_DATA
    assert entry.options == {}
    updated = _reg(hass, reg.entity_id)
    assert updated.unique_id == f"{PREFIX}_total"
    assert updated.options["sensor"]["unit_of_measurement"] == expected
    state = hass.states.get(reg.entity_id)
    assert state is not None
    assert state.attributes["unit_of_measurement"] == expected


async def test_migrate_keeps_user_unit_override(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """A unit set in the entity settings stays, and so do other sensor options."""
    _mock_box(aioclient_mock, storage_box)
    entry = _make_entry(hass, 4, unit="GB")
    registry = er.async_get(hass)
    total = _register(hass, f"{PREFIX}_total_gb", entry, "GB")
    registry.async_update_entity_options(
        total.entity_id,
        "sensor",
        {"unit_of_measurement": "MiB", "display_precision": 1},
    )
    used = _register(hass, f"{PREFIX}_used_gb", entry, "GB")
    registry.async_update_entity_options(
        used.entity_id, "sensor", {"display_precision": 0}
    )

    await _setup(hass, entry)

    sensor_options = _reg(hass, total.entity_id).options["sensor"]
    assert sensor_options["unit_of_measurement"] == "MiB"
    assert sensor_options["display_precision"] == 1
    state = hass.states.get(total.entity_id)
    assert state is not None
    assert state.attributes["unit_of_measurement"] == "MiB"
    assert float(state.state) == pytest.approx(2**20)

    sensor_options = _reg(hass, used.entity_id).options["sensor"]
    assert sensor_options["unit_of_measurement"] == "GB"
    assert sensor_options["display_precision"] == 0


async def test_migrate_replaces_old_private_unit(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """The suggested unit the old code stored in sensor.private is replaced.

    Also drops a pending refresh from a unit system change, which would
    otherwise recompute the private options while the entity is added.
    """
    _mock_box(aioclient_mock, storage_box)
    entry = _make_entry(hass, 4, unit="TB")
    reg = _register(hass, f"{PREFIX}_free_tb", entry, "TB")
    er.async_get(hass).async_update_entity_options(
        reg.entity_id,
        "sensor.private",
        {"suggested_unit_of_measurement": "TB", "refresh_initial_entity_options": True},
    )

    await _setup(hass, entry)

    updated = _reg(hass, reg.entity_id)
    assert updated.options["sensor.private"] == GIB_PRIVATE
    assert updated.options["sensor"]["unit_of_measurement"] == "TB"
    state = hass.states.get(reg.entity_id)
    assert state is not None
    assert state.attributes["unit_of_measurement"] == "TB"
    assert float(state.state) == pytest.approx(BYTES["free"] / 1000**4)


async def test_migrate_removes_conflicting_stale_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stale entities holding a target unique_id are removed before renaming.

    Leftovers of the entry itself lose against the size sensor of the
    configured unit, whatever their order in the registry.
    """
    _mock_box(aioclient_mock, storage_box)
    entry = _make_entry(hass, 4, unit="GB")
    # Unrelated entity already holding the new unique_id
    stale = _register(hass, f"{PREFIX}_total", None)
    total = _register(hass, f"{PREFIX}_total_gb", entry, "GB")
    # Leftovers of the entry registered before the live entity
    legacy_used = _register(hass, f"{PREFIX}_used", entry)
    used = _register(hass, f"{PREFIX}_used_gb", entry, "GB")
    free = _register(hass, f"{PREFIX}_free_gb", entry, "GB")
    # ... and after it
    old_free = _register(hass, f"{PREFIX}_free_tb", entry, "TB")
    # Two leftovers: the one renamed first removes the other before its turn
    old_data = _register(hass, f"{PREFIX}_data_tb", entry, "TB")
    legacy_data = _register(hass, f"{PREFIX}_data", entry)
    data = _register(hass, f"{PREFIX}_data_gb", entry, "GB")

    await _setup(hass, entry)

    registry = er.async_get(hass)
    for removed in (stale, legacy_used, old_free, old_data, legacy_data):
        assert registry.async_get(removed.entity_id) is None
        assert f"Removing conflicting stale entity {removed.entity_id}" in caplog.text
    for metric, reg in (
        ("total", total),
        ("used", used),
        ("data", data),
        ("free", free),
    ):
        updated = _reg(hass, reg.entity_id)
        assert updated.unique_id == f"{PREFIX}_{metric}"
        state = hass.states.get(reg.entity_id)
        assert state is not None
        assert state.attributes["unit_of_measurement"] == "GB"
    assert entry.version == 5


async def test_migrate_removes_only_own_access_options_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Only this box's access options sensor goes; ids sharing its prefix stay."""
    _mock_box(aioclient_mock, storage_box)
    entry = _make_entry(hass, 4)
    access_options = _register(hass, ACCESS_OPTIONS_ID, entry)
    other_box = _register(hass, f"{DOMAIN}_storage_box_999_access_options", None)
    # A binary sensor with the same unique_id is no access options sensor
    binary = _register(hass, ACCESS_OPTIONS_ID, entry, domain="binary_sensor")
    status = _register(hass, PREFIX, entry)

    await _setup(hass, entry)

    registry = er.async_get(hass)
    assert registry.async_get(access_options.entity_id) is None
    for reg in (other_box, binary, status):
        assert _reg(hass, reg.entity_id).unique_id == reg.unique_id
    assert hass.states.get(access_options.entity_id) is None


async def test_migrate_current_entry_is_noop(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Migrating an entry that is already v5 changes nothing."""
    reg = _register(hass, f"{PREFIX}_total_gb", config_entry)
    access_options = _register(hass, ACCESS_OPTIONS_ID, config_entry)

    with patch.object(hass.config_entries, "async_update_entry") as update:
        assert await async_migrate_entry(hass, config_entry)

    update.assert_not_called()
    assert _reg(hass, reg.entity_id).unique_id == f"{PREFIX}_total_gb"
    assert _reg(hass, access_options.entity_id).unique_id == ACCESS_OPTIONS_ID


async def test_safe_update_unique_id_same_id_noop(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Renaming to the same unique_id does nothing."""
    registry = er.async_get(hass)
    reg = _register(hass, f"{PREFIX}_total", config_entry)

    with patch.object(registry, "async_update_entity") as update:
        _safe_update_unique_id(registry, reg, f"{PREFIX}_total")

    update.assert_not_called()


async def test_safe_update_unique_id_value_error_logged(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ValueError while renaming is logged, not raised."""
    registry = er.async_get(hass)
    reg = _register(hass, f"{PREFIX}_total_gb", config_entry)

    with patch.object(registry, "async_update_entity", side_effect=ValueError("boom")):
        _safe_update_unique_id(registry, reg, f"{PREFIX}_total")

    assert "Could not update unique_id" in caplog.text
    assert "boom" in caplog.text
    assert _reg(hass, reg.entity_id).unique_id == f"{PREFIX}_total_gb"


async def test_migrate_downgrade_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An entry written by a newer version is left untouched and not set up."""
    entry = _make_entry(hass, 6, options={"future": True})
    reg = _register(hass, f"{PREFIX}_total_gb", entry)
    data, options = dict(entry.data), dict(entry.options)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert entry.version == 6
    assert entry.data == data
    assert entry.options == options
    assert _reg(hass, reg.entity_id).unique_id == f"{PREFIX}_total_gb"
    assert aioclient_mock.call_count == 0


async def test_migrate_entry_refuses_newer_version(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """async_migrate_entry itself refuses an entry from a newer version.

    Home Assistant already stops before calling it; the guard keeps a direct
    call from rewriting data of an unknown layout.
    """
    entry = _make_entry(hass, 6)
    reg = _register(hass, f"{PREFIX}_total_gb", entry)
    access_options = _register(hass, ACCESS_OPTIONS_ID, entry)

    with patch.object(hass.config_entries, "async_update_entry") as update:
        assert not await async_migrate_entry(hass, entry)

    update.assert_not_called()
    assert _reg(hass, reg.entity_id).unique_id == f"{PREFIX}_total_gb"
    assert _reg(hass, access_options.entity_id).unique_id == ACCESS_OPTIONS_ID
    assert "Cannot downgrade config entry" in caplog.text


def test_legacy_units_are_information_units() -> None:
    """The stored units are exactly the values of HA's information units."""
    assert {UnitOfInformation(unit) for unit in UNIT_FACTORS} == {
        UnitOfInformation.BYTES,
        UnitOfInformation.MEGABYTES,
        UnitOfInformation.GIGABYTES,
        UnitOfInformation.TERABYTES,
    }


async def test_remove_entry_without_issue(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Removing an entry without a repair issue does nothing else."""
    other = "storage_box_not_found_other"
    ir.async_create_issue(
        hass,
        DOMAIN,
        other,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="storage_box_not_found",
    )

    await async_remove_entry(hass, config_entry)

    issue_registry = ir.async_get(hass)
    assert issue_registry.async_get_issue(DOMAIN, other) is not None
    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"storage_box_not_found_{config_entry.entry_id}"
        )
        is None
    )


async def test_protocol_sensors_disabled_by_default(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """The protocol and ZFS binary sensors are registered disabled."""
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box})
    aioclient_mock.get(SUBACCOUNTS_URL, json={"subaccounts": [subaccount]})
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    box = "binary_sensor.storage_box_backup"
    sub = f"{box}_sub1"
    for entity_id, enabled in (
        (f"{box}_reachable_externally", True),
        (f"{box}_samba", False),
        (f"{box}_ssh", False),
        (f"{box}_webdav", False),
        (f"{box}_zfs_snapshot_folder", False),
        (f"{sub}_reachable_externally", True),
        (f"{sub}_samba", False),
        (f"{sub}_ssh", False),
        (f"{sub}_webdav", False),
        (f"{sub}_read_only", True),
        ("sensor.storage_box_backup", True),
        ("sensor.storage_box_backup_free_space", True),
        ("sensor.storage_box_backup_sub1_home_directory", True),
    ):
        reg_entry = ent_reg.async_get(entity_id)
        assert reg_entry is not None, entity_id
        if enabled:
            assert reg_entry.disabled_by is None, entity_id
            assert hass.states.get(entity_id) is not None, entity_id
        else:
            assert reg_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            assert hass.states.get(entity_id) is None, entity_id
