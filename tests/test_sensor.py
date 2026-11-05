"""Tests for the Hetzner Storage Box sensor platform."""

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_API_KEY,
    CONF_HOST,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
    UnitOfInformation,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.icon import async_get_icons
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (  # type: ignore[import-untyped]
    AiohttpClientMocker,
)

from custom_components.hetzner.binary_sensor import (
    ACCESS_SETTINGS,
    HetznerAccessSettingBinarySensor,
)
from custom_components.hetzner.const import (
    CONF_STORAGE_BOX_ID,
    DEFAULT_HOST,
    DOMAIN,
    SIZE_METRICS,
)
from custom_components.hetzner.coordinator import StorageBoxData
from custom_components.hetzner.sensor import (
    SIZE_SENSORS,
    HetznerLocationSensor,
    HetznerSizeSensor,
    HetznerStatusSensor,
    HetznerSubaccountHomeDirectorySensor,
)

from tests.conftest import API_KEY, BOX_URL, STORAGE_BOX_ID, SUBACCOUNTS_URL

PREFIX = "sensor.storage_box_backup"
BINARY_PREFIX = "binary_sensor.storage_box_backup"
UID = f"hetzner_storage_box_{STORAGE_BOX_ID}"

# The tests read the states of the protocol sensors, which are disabled by
# default (checked in test_init.py)
pytestmark = pytest.mark.usefixtures("entity_registry_enabled_by_default")

# entity_id -> unique_id of the binary sensors, one per access setting
BINARY_ENTITIES = {
    f"{BINARY_PREFIX}_reachable_externally": f"{UID}_reachable_externally",
    f"{BINARY_PREFIX}_samba": f"{UID}_samba_enabled",
    f"{BINARY_PREFIX}_ssh": f"{UID}_ssh_enabled",
    f"{BINARY_PREFIX}_webdav": f"{UID}_webdav_enabled",
    f"{BINARY_PREFIX}_zfs_snapshot_folder": f"{UID}_zfs_enabled",
}

# entity_id -> unique_id of all entities of the fixture entry
EXPECTED_ENTITIES = {
    PREFIX: UID,
    f"{PREFIX}_location": f"{UID}_location",
    f"{PREFIX}_total_size": f"{UID}_total",
    f"{PREFIX}_total_used": f"{UID}_used",
    f"{PREFIX}_data_size": f"{UID}_data",
    f"{PREFIX}_snapshot_size": f"{UID}_snapshots",
    f"{PREFIX}_free_space": f"{UID}_free",
    **BINARY_ENTITIES,
}

SIZE_ENTITIES = {
    "total": "total_size",
    "used": "total_used",
    "data": "data_size",
    "snapshots": "snapshot_size",
    "free": "free_space",
}


def _mock_box(
    aioclient_mock: AiohttpClientMocker,
    payload: Any,
    status: int = 200,
    subaccounts: list[dict[str, Any]] | None = None,
) -> None:
    """Register the storage box endpoint responses, by default no subaccounts."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, json={"storage_box": payload}, status=status)
    aioclient_mock.get(SUBACCOUNTS_URL, json={"subaccounts": subaccounts or []})


def _make_entry(hass: HomeAssistant, **data: Any) -> MockConfigEntry:
    """Create and add a current config entry with the given data overrides."""
    entry_data = {
        CONF_API_KEY: API_KEY,
        CONF_HOST: DEFAULT_HOST,
        CONF_STORAGE_BOX_ID: STORAGE_BOX_ID,
    }
    entry_data.update(data)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hetzner backup",
        version=5,
        unique_id=STORAGE_BOX_ID,
        data={k: v for k, v in entry_data.items() if v is not None},
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the config entry."""
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _tick(hass: HomeAssistant) -> None:
    """Advance time past the coordinator update interval."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=601))
    await hass.async_block_till_done()


async def test_entities_created(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """All entities are created under one storage box device."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    # Entities reuse the first refresh (box and subaccounts) instead of fetching
    assert aioclient_mock.call_count == 2
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {API_KEY}"

    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
    assert {e.entity_id: e.unique_id for e in entries} == EXPECTED_ENTITIES

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device_by_identifier(
        (DOMAIN, STORAGE_BOX_ID), config_entry.entry_id
    )
    assert device is not None
    assert device.name == "Storage Box backup"
    assert device.manufacturer == "Hetzner"
    assert device.model == "bx11"
    assert device.configuration_url == "https://console.hetzner.com"
    assert {e.device_id for e in entries} == {device.id}
    assert len(dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id)) == 1


@pytest.mark.parametrize(
    ("language", "free_space_name", "reachable_name"),
    [
        ("de", "Freier Speicher", "Extern erreichbar (Hauptkonto)"),
        ("nl", "Vrije ruimte", "Extern bereikbaar (hoofdaccount)"),
    ],
)
async def test_entity_ids_stay_english(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    language: str,
    free_space_name: str,
    reachable_name: str,
) -> None:
    """Languages with native entity_ids still get the English entity_ids."""
    hass.config.language = language
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
    assert {e.entity_id: e.unique_id for e in entries} == EXPECTED_ENTITIES
    # Only the entity_ids stay English, the names are translated
    state = hass.states.get(f"{PREFIX}_free_space")
    assert state is not None
    assert state.attributes["friendly_name"] == f"Storage Box backup {free_space_name}"
    state = hass.states.get(f"{BINARY_PREFIX}_reachable_externally")
    assert state is not None
    assert state.attributes["friendly_name"] == f"Storage Box backup {reachable_name}"


async def test_status_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Status sensor exposes status and box details."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    state = hass.states.get(PREFIX)
    assert state is not None
    assert state.state == "active"
    assert state.attributes["friendly_name"] == "Storage Box backup"
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == ["active", "initializing", "locked"]
    for key in ("id", "name", "username", "server", "system", "created"):
        assert state.attributes[key] == storage_box[key]
    assert state.attributes["storage_box_type"] == "bx11"


async def test_location_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Location sensor exposes location name and details."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    state = hass.states.get(f"{PREFIX}_location")
    assert state is not None
    assert state.state == "fsn1"
    location = storage_box["location"]
    for key in ("id", "country", "city", "latitude", "longitude", "description"):
        assert state.attributes[key] == location[key]


async def test_access_setting_binary_sensors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Each access setting is an on/off diagnostic binary sensor."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    expected = {
        "reachable_externally": STATE_ON,
        "samba": STATE_OFF,
        "ssh": STATE_ON,
        "webdav": STATE_OFF,
        "zfs_snapshot_folder": STATE_ON,
    }
    ent_reg = er.async_get(hass)
    for suffix, value in expected.items():
        entity_id = f"{BINARY_PREFIX}_{suffix}"
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == value, suffix
        assert "device_class" not in state.attributes
        reg_entry = ent_reg.async_get(entity_id)
        assert reg_entry is not None
        assert reg_entry.entity_category is EntityCategory.DIAGNOSTIC
        assert reg_entry.translation_key == suffix

    # A setting that changes is picked up on the next update
    storage_box["access_settings"]["samba_enabled"] = True
    _mock_box(aioclient_mock, storage_box)
    await _tick(hass)
    assert hass.states.get(f"{BINARY_PREFIX}_samba").state == STATE_ON  # type: ignore[union-attr]


async def test_access_setting_missing_is_unknown(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """A setting the API leaves out or sends as null is unknown, not off."""
    del storage_box["access_settings"]["webdav_enabled"]
    storage_box["access_settings"]["samba_enabled"] = None
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    for suffix in ("webdav", "samba"):
        state = hass.states.get(f"{BINARY_PREFIX}_{suffix}")
        assert state is not None
        assert state.state == STATE_UNKNOWN, suffix
    assert hass.states.get(f"{BINARY_PREFIX}_ssh").state == STATE_ON  # type: ignore[union-attr]


# Values of the fixture in GiB, the unit of new size sensors
GIB = 1024**3
EXPECTED_GIB = {
    "total": 1024.0,
    "used": 300_000_000_000 / GIB,
    "data": 250_000_000_000 / GIB,
    "snapshots": 50_000_000_000 / GIB,
    "free": 1024.0 - 300_000_000_000 / GIB,
}


async def test_size_sensors_default_to_gib(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """New size sensors show the bytes of the API in GiB with data size metadata."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    ent_reg = er.async_get(hass)
    for metric, suffix in SIZE_ENTITIES.items():
        entity_id = f"{PREFIX}_{suffix}"
        state = hass.states.get(entity_id)
        assert state is not None
        assert float(state.state) == pytest.approx(EXPECTED_GIB[metric]), metric
        assert state.attributes["unit_of_measurement"] == "GiB"
        assert state.attributes["device_class"] == "data_size"
        assert state.attributes["state_class"] == "measurement"
        reg_entry = ent_reg.async_get(entity_id)
        assert reg_entry is not None
        assert reg_entry.unique_id == f"{UID}_{metric}"
        assert reg_entry.options["sensor.private"] == {
            "suggested_unit_of_measurement": "GiB"
        }
        assert reg_entry.options["sensor"] == {"suggested_display_precision": 2}
    # A BX11 is a binary terabyte
    assert hass.states.get(f"{PREFIX}_total_size").state == "1024.0"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("unit", "total", "used"),
    [
        ("B", "1099511627776", "300000000000"),
        ("GB", 1099.511627776, 300.0),
        ("TB", 1.099511627776, 0.3),
        ("TiB", 1.0, 300_000_000_000 / 1024**4),
    ],
)
async def test_size_sensor_unit_from_entity_settings(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    unit: str,
    total: str | float,
    used: str | float,
) -> None:
    """The unit is changed per entity in its settings; HA converts the bytes."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    ent_reg = er.async_get(hass)

    for suffix, expected in (("total_size", total), ("total_used", used)):
        entity_id = f"{PREFIX}_{suffix}"
        ent_reg.async_update_entity_options(
            entity_id, "sensor", {"unit_of_measurement": unit}
        )
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes["unit_of_measurement"] == unit
        if isinstance(expected, str):
            # Bytes are the native value, passed on unconverted
            assert state.state == expected
        else:
            assert float(state.state) == pytest.approx(expected)


async def test_size_sensor_not_rounded(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """The state keeps every byte; the frontend rounds to the display precision."""
    storage_box["stats"]["size"] = 1_234_567_890
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    state = hass.states.get(f"{PREFIX}_total_used")
    assert state is not None
    assert float(state.state) == pytest.approx(1_234_567_890 / GIB, abs=1e-12)


async def test_free_space_clamped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Free space never goes negative."""
    storage_box["stats"]["size"] = 2_000_000_000_000
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    state = hass.states.get(f"{PREFIX}_free_space")
    assert state is not None
    assert float(state.state) == 0


async def test_missing_stats_and_type(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Missing stats and storage box type report unknown sizes, not zero."""
    del storage_box["stats"]
    storage_box["storage_box_type"] = None
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    for suffix in SIZE_ENTITIES.values():
        state = hass.states.get(f"{PREFIX}_{suffix}")
        assert state is not None
        assert state.state == STATE_UNKNOWN, suffix

    state = hass.states.get(PREFIX)
    assert state is not None
    assert state.attributes["storage_box_type"] is None

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, STORAGE_BOX_ID), config_entry.entry_id
    )
    assert device is not None
    assert device.model is None


async def test_malformed_nested_values(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Nested values that are no objects count as missing instead of failing."""
    storage_box["stats"] = "300 GB"
    storage_box["storage_box_type"] = "bx11"
    storage_box["location"] = "fsn1"
    storage_box["access_settings"] = ["ssh_enabled"]
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    for suffix in SIZE_ENTITIES.values():
        state = hass.states.get(f"{PREFIX}_{suffix}")
        assert state is not None
        assert state.state == STATE_UNKNOWN, suffix
    status = hass.states.get(PREFIX)
    assert status is not None
    assert status.state == "active"
    assert status.attributes["storage_box_type"] is None
    location = hass.states.get(f"{PREFIX}_location")
    assert location is not None
    assert location.state == STATE_UNKNOWN
    assert location.attributes["city"] is None
    for entity_id in BINARY_ENTITIES:
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNKNOWN, entity_id
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, STORAGE_BOX_ID), config_entry.entry_id
    )
    assert device is not None
    assert device.model is None


@pytest.mark.parametrize("payload", [None, {}])
async def test_empty_storage_box(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    payload: Any,
) -> None:
    """An empty storage_box response yields default values."""
    _mock_box(aioclient_mock, payload)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED

    # Without a name the device falls back to the storage box id
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, STORAGE_BOX_ID), config_entry.entry_id
    )
    assert device is not None
    assert device.name == f"Storage Box {STORAGE_BOX_ID}"
    assert device.model is None

    prefix = f"sensor.storage_box_{STORAGE_BOX_ID}"
    status = hass.states.get(prefix)
    assert status is not None
    assert status.state == STATE_UNKNOWN
    assert status.attributes["id"] is None
    location = hass.states.get(f"{prefix}_location")
    assert location is not None
    assert location.state == STATE_UNKNOWN
    assert location.attributes["city"] is None
    for entity_id in BINARY_ENTITIES:
        access = hass.states.get(entity_id.replace("backup", STORAGE_BOX_ID))
        assert access is not None
        assert access.state == STATE_UNKNOWN, entity_id
    for suffix in SIZE_ENTITIES.values():
        state = hass.states.get(f"{prefix}_{suffix}")
        assert state is not None
        assert state.state == STATE_UNKNOWN, suffix


async def test_missing_storage_box_key(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A response without a storage_box key is treated as empty."""
    aioclient_mock.get(BOX_URL, json={})
    aioclient_mock.get(SUBACCOUNTS_URL, json={})
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    state = hass.states.get(f"binary_sensor.storage_box_{STORAGE_BOX_ID}_ssh")
    assert state is not None
    assert state.state == STATE_UNKNOWN


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        {"status": 500},
        {"exc": asyncio.TimeoutError},
        {"exc": aiohttp.ClientError},
    ],
)
async def test_first_refresh_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    mock_kwargs: dict[str, Any],
) -> None:
    """API errors on the first refresh retry setup and create no entities.

    Error statuses carry a valid body so only the status check can reject them.
    """
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box}, **mock_kwargs)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert aioclient_mock.call_count == 1
    assert not hasattr(config_entry, "runtime_data")
    assert hass.states.async_entity_ids() == []
    ent_reg = er.async_get(hass)
    assert er.async_entries_for_config_entry(ent_reg, config_entry.entry_id) == []


async def test_first_refresh_error_recovers_on_retry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """A failed first refresh is retried and loads once the API recovers."""
    aioclient_mock.get(BOX_URL, status=500)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY

    _mock_box(aioclient_mock, storage_box)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    state = hass.states.get(PREFIX)
    assert state is not None
    assert state.state == "active"


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        {"status": 500},
        {"exc": asyncio.TimeoutError},
        {"exc": aiohttp.ClientError},
    ],
)
async def test_later_refresh_error_and_recovery(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    mock_kwargs: dict[str, Any],
) -> None:
    """Entities become unavailable on API errors and recover afterwards."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    assert hass.states.get(PREFIX).state == "active"  # type: ignore[union-attr]

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box}, **mock_kwargs)
    await _tick(hass)

    for entity_id in EXPECTED_ENTITIES:
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNAVAILABLE, entity_id

    storage_box["status"] = "locked"
    _mock_box(aioclient_mock, storage_box)
    await _tick(hass)

    assert hass.states.get(PREFIX).state == "locked"  # type: ignore[union-attr]
    for entity_id in EXPECTED_ENTITIES:
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state != STATE_UNAVAILABLE, entity_id


def _reauth_flows(hass: HomeAssistant) -> list[Any]:
    """Return the reauth flows in progress for the integration."""
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == "reauth"
    ]


@pytest.mark.parametrize(
    ("status", "body", "detail"),
    [
        (401, {"error": {"code": "unauthorized"}}, "HTTP 401 (unauthorized)"),
        (403, {"error": {"code": "forbidden"}}, "HTTP 403 (forbidden)"),
        # No readable error code: assume missing permissions
        (403, {"storage_box": {}}, "HTTP 403"),
    ],
)
async def test_first_refresh_auth_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    status: int,
    body: dict[str, Any],
    detail: str,
) -> None:
    """A rejected API key fails setup without retrying and starts reauth."""
    aioclient_mock.get(BOX_URL, json=body, status=status)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert config_entry.reason == f"The Hetzner API rejected the API token: {detail}"
    assert aioclient_mock.call_count == 1
    assert hass.states.async_entity_ids() == []
    flows = _reauth_flows(hass)
    assert len(flows) == 1
    assert flows[0]["step_id"] == "reauth_confirm"
    assert flows[0]["context"]["entry_id"] == config_entry.entry_id


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        {"status": 403, "json": {"error": {"code": "maintenance"}}},
        {"status": 403, "json": {"error": {"code": "resource_limit_exceeded"}}},
        {"status": 429, "json": {"error": {"code": "rate_limit_exceeded"}}},
    ],
)
async def test_first_refresh_temporary_api_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_kwargs: dict[str, Any],
) -> None:
    """Maintenance and rate limits retry setup instead of asking for a new key."""
    aioclient_mock.get(BOX_URL, **mock_kwargs)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    code = mock_kwargs["json"]["error"]["code"]
    assert config_entry.reason == (
        f"The Hetzner API returned an error: HTTP {mock_kwargs['status']} ({code})"
    )
    assert _reauth_flows(hass) == []


async def test_first_refresh_connection_error_reason(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A timeout is shown with a translated reason."""
    aioclient_mock.get(BOX_URL, exc=asyncio.TimeoutError)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert config_entry.reason == (
        "Could not connect to the Hetzner API: Timeout connecting to the Hetzner API"
    )


def _not_found_issue(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Return the storage box not found repair issue of an entry, if any."""
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"storage_box_not_found_{entry.entry_id}"
    )


async def test_first_refresh_storage_box_not_found(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """A box the API cannot find retries setup and raises a repair issue."""
    aioclient_mock.get(BOX_URL, status=404, json={"error": {"code": "not_found"}})
    await _setup(hass, config_entry)

    # Not a setup error: the token may only lack access for now
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert config_entry.reason == (
        f"Storage box {STORAGE_BOX_ID} not found — deleted, or not accessible "
        "with this API token"
    )
    assert aioclient_mock.call_count == 1
    assert _reauth_flows(hass) == []
    assert hass.states.async_entity_ids() == []

    issue = _not_found_issue(hass, config_entry)
    assert issue is not None
    assert issue.domain == DOMAIN
    assert issue.is_fixable is False
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_key == "storage_box_not_found"
    assert issue.translation_placeholders == {"storage_box_id": STORAGE_BOX_ID}

    _mock_box(aioclient_mock, storage_box)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert _not_found_issue(hass, config_entry) is None


async def test_later_refresh_storage_box_not_found(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """A box that disappears raises an issue, keeps polling and recovers."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    assert _not_found_issue(hass, config_entry) is None

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, status=404)
    await _tick(hass)

    assert config_entry.state is ConfigEntryState.LOADED
    assert _not_found_issue(hass, config_entry) is not None
    assert _reauth_flows(hass) == []
    for entity_id in EXPECTED_ENTITIES:
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNAVAILABLE, entity_id

    # Polling goes on, so the box is found again without a reload
    _mock_box(aioclient_mock, storage_box)
    await _tick(hass)

    assert _not_found_issue(hass, config_entry) is None
    assert hass.states.get(PREFIX).state == "active"  # type: ignore[union-attr]


async def test_auth_error_after_not_found_deletes_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Reauth stops polling, so the issue saying HA retries goes away."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, status=404)
    await _tick(hass)
    assert _not_found_issue(hass, config_entry) is not None

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, status=401)
    await _tick(hass)

    assert _not_found_issue(hass, config_entry) is None
    assert len(_reauth_flows(hass)) == 1


async def test_unload_entry_deletes_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Nothing retries after unloading, so the issue goes away."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, status=404)
    await _tick(hass)
    assert _not_found_issue(hass, config_entry) is not None

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert _not_found_issue(hass, config_entry) is None


async def test_remove_entry_deletes_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Deleting the entry removes its repair issue."""
    aioclient_mock.get(BOX_URL, status=404)
    await _setup(hass, config_entry)
    assert _not_found_issue(hass, config_entry) is not None

    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()

    assert _not_found_issue(hass, config_entry) is None


async def test_later_refresh_maintenance_keeps_key(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Maintenance after setup makes entities unavailable without reauth."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, status=403, json={"error": {"code": "maintenance"}})
    await _tick(hass)

    assert _reauth_flows(hass) == []
    assert hass.states.get(PREFIX).state == STATE_UNAVAILABLE  # type: ignore[union-attr]

    _mock_box(aioclient_mock, storage_box)
    await _tick(hass)

    assert hass.states.get(PREFIX).state == "active"  # type: ignore[union-attr]


async def test_later_refresh_auth_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """A key rejected after setup makes entities unavailable and starts reauth."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    assert _reauth_flows(hass) == []

    _mock_box(aioclient_mock, storage_box, status=401)
    await _tick(hass)

    for entity_id in EXPECTED_ENTITIES:
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNAVAILABLE, entity_id
    assert len(_reauth_flows(hass)) == 1


async def test_unload_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Unloading the entry removes its data and makes entities unavailable."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    assert config_entry.runtime_data.data.box == storage_box

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert not hasattr(config_entry, "runtime_data")
    state = hass.states.get(PREFIX)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


def _size_sensor(coordinator: MagicMock, metric: str) -> HetznerSizeSensor:
    """Create the size sensor of one metric."""
    description = next(d for d in SIZE_SENSORS if d.key == metric)
    return HetznerSizeSensor(coordinator, STORAGE_BOX_ID, description)


def test_size_sensor_descriptions_match_metrics() -> None:
    """The size sensors are the metrics the migration knows, in that order."""
    assert tuple(description.key for description in SIZE_SENSORS) == SIZE_METRICS


def test_size_sensor_native_bytes() -> None:
    """Size sensors report the API's bytes as integers in bytes."""
    coordinator = MagicMock()
    coordinator.data = StorageBoxData(box={"storage_box_type": {"size": 2**40}})
    sensor = _size_sensor(coordinator, "total")
    assert sensor.native_value == 2**40
    assert isinstance(sensor.native_value, int)
    assert sensor.native_unit_of_measurement == UnitOfInformation.BYTES
    assert sensor.suggested_unit_of_measurement == UnitOfInformation.GIBIBYTES
    assert sensor.suggested_display_precision == 2
    assert sensor.unique_id == f"{UID}_total"


@pytest.mark.parametrize(
    ("missing", "unknown"),
    [
        # Free space needs the total size and the total disk usage
        ("size", {"used", "free"}),
        ("size_data", {"data"}),
        ("size_snapshots", {"snapshots"}),
    ],
)
def test_size_sensor_single_value_missing(missing: str, unknown: set[str]) -> None:
    """Only the sensors that need a missing or non-int value are unknown.

    Free space is the total minus the total disk usage, even where that is
    more than data and snapshots together, so used and free add up.
    """
    stats: dict[str, Any] = {"size": 30, "size_data": 20, "size_snapshots": 5}
    box = {"storage_box_type": {"size": 100}, "stats": stats}
    expected = {"total": 100, "used": 30, "data": 20, "snapshots": 5, "free": 70}
    coordinator = MagicMock()
    for value in (None, "20", 1.5, True):
        stats[missing] = value
        coordinator.data = StorageBoxData(box=box)
        for metric in SIZE_METRICS:
            sensor = _size_sensor(coordinator, metric)
            assert sensor.native_value == (
                None if metric in unknown else expected[metric]
            ), (metric, value)


@pytest.mark.parametrize("box_type", [{}, {"size": "100"}, None])
def test_size_sensor_total_missing(box_type: Any) -> None:
    """Without the total size, total and free space are unknown."""
    stats = {"size": 30, "size_data": 20, "size_snapshots": 5}
    coordinator = MagicMock()
    coordinator.data = StorageBoxData(
        box={"storage_box_type": box_type, "stats": stats}
    )
    expected = {"total": None, "used": 30, "data": 20, "snapshots": 5, "free": None}
    for metric in SIZE_METRICS:
        sensor = _size_sensor(coordinator, metric)
        assert sensor.native_value == expected[metric], metric


def test_size_sensor_invalid_stats() -> None:
    """Stats or a type that are no objects make every size unknown."""
    coordinator = MagicMock()
    coordinator.data = StorageBoxData(box={"stats": [1], "storage_box_type": "bx11"})
    for metric in SIZE_METRICS:
        sensor = _size_sensor(coordinator, metric)
        assert sensor.native_value is None, metric


def test_size_sensor_without_coordinator_data() -> None:
    """A coordinator without data reports unknown."""
    coordinator = MagicMock()
    coordinator.data = None
    sensor = _size_sensor(coordinator, "free")
    assert sensor.native_value is None
    assert sensor.device_info["name"] == f"Storage Box {STORAGE_BOX_ID}"


def test_binary_sensor_without_coordinator_data() -> None:
    """A coordinator without data reports every access setting as unknown."""
    coordinator = MagicMock()
    coordinator.data = None
    sensor = HetznerAccessSettingBinarySensor(
        coordinator, STORAGE_BOX_ID, ACCESS_SETTINGS[0]
    )
    assert sensor.is_on is None


# translation_key -> English name, which also makes up the entity_id (without
# the binary sensors' " (main account)")
TRANSLATED_ENTITIES = {
    f"{PREFIX}_location": ("location", "Location"),
    f"{PREFIX}_total_size": ("total_size", "Total size"),
    f"{PREFIX}_total_used": ("total_used", "Total used"),
    f"{PREFIX}_data_size": ("data_size", "Data size"),
    f"{PREFIX}_snapshot_size": ("snapshot_size", "Snapshot size"),
    f"{PREFIX}_free_space": ("free_space", "Free space"),
    f"{BINARY_PREFIX}_reachable_externally": (
        "reachable_externally",
        "Reachable externally (main account)",
    ),
    f"{BINARY_PREFIX}_samba": ("samba", "Samba (main account)"),
    f"{BINARY_PREFIX}_ssh": ("ssh", "SSH (main account)"),
    f"{BINARY_PREFIX}_webdav": ("webdav", "WebDAV (main account)"),
    f"{BINARY_PREFIX}_zfs_snapshot_folder": (
        "zfs_snapshot_folder",
        "ZFS snapshot folder (main account)",
    ),
}


async def test_entity_names_from_translations(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Entities are named through translation keys; the status takes the device's."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    ent_reg = er.async_get(hass)

    status = ent_reg.async_get(PREFIX)
    assert status is not None
    assert status.translation_key == "status"
    assert status.original_name is None

    for entity_id, (key, name) in TRANSLATED_ENTITIES.items():
        reg_entry = ent_reg.async_get(entity_id)
        assert reg_entry is not None
        assert reg_entry.translation_key == key
        assert reg_entry.original_name == name
        assert reg_entry.original_icon is None
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes["friendly_name"] == f"Storage Box backup {name}"
        # The frontend picks the icon from icons.json
        assert "icon" not in state.attributes


async def test_icons_cover_translation_keys(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """icons.json has an icon for every entity translation key."""
    _mock_box(aioclient_mock, storage_box, subaccounts=[subaccount])
    await _setup(hass, config_entry)

    all_icons = (await async_get_icons(hass, "entity", [DOMAIN]))[DOMAIN]
    reg_entries = er.async_entries_for_config_entry(
        er.async_get(hass), config_entry.entry_id
    )
    for platform in ("sensor", "binary_sensor"):
        assert set(all_icons[platform]) == {
            e.translation_key for e in reg_entries if e.domain == platform
        }
    icons = all_icons["sensor"]
    assert icons["status"] == {
        "default": "mdi:database",
        "state": {
            "initializing": "mdi:database-sync",
            "locked": "mdi:database-lock",
        },
    }
    assert icons["location"] == {"default": "mdi:map-marker"}
    assert icons["home_directory"] == {"default": "mdi:folder-home"}
    for description in SIZE_SENSORS:
        assert icons[str(description.translation_key)] == {"default": "mdi:harddisk"}
    # Binary sensors switch their icon with the state
    for key, value in all_icons["binary_sensor"].items():
        assert set(value["state"]) == {"on", "off"}, key
        assert value["state"]["on"] != value["state"]["off"], key


@pytest.mark.parametrize("status", ["active", "initializing", "locked"])
async def test_status_sensor_known_states(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    status: str,
) -> None:
    """Every documented status is a valid state of the enum sensor."""
    storage_box["status"] = status
    # Nullable while the box is initializing
    storage_box["username"] = None
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    state = hass.states.get(PREFIX)
    assert state is not None
    assert state.state == status
    assert state.attributes["username"] is None


async def test_status_sensor_unknown_status(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A status the integration does not know is reported as unknown, logged once."""
    storage_box["status"] = "migrating"
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    state = hass.states.get(PREFIX)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    # The details stay available
    assert state.attributes["name"] == "backup"

    await _tick(hass)
    await _tick(hass)
    assert caplog.text.count("Unknown status 'migrating'") == 1

    storage_box["status"] = "active"
    _mock_box(aioclient_mock, storage_box)
    await _tick(hass)
    assert hass.states.get(PREFIX).state == "active"  # type: ignore[union-attr]


def test_unrecorded_attributes_exist() -> None:
    """The attributes left out of the recorder are ones the sensors report."""
    coordinator = MagicMock()
    coordinator.data = StorageBoxData(box={}, subaccounts={"42": {}})
    sensors = [
        HetznerStatusSensor(coordinator, STORAGE_BOX_ID),
        HetznerLocationSensor(coordinator, STORAGE_BOX_ID),
        HetznerSubaccountHomeDirectorySensor(coordinator, STORAGE_BOX_ID, "42"),
    ]
    for sensor in sensors:
        # All of them are fixed details, see the sensor classes
        assert sensor.extra_state_attributes is not None
        attributes = set(sensor.extra_state_attributes)
        assert sensor._unrecorded_attributes == attributes, type(sensor).__name__
