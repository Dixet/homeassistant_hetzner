"""Tests for the subaccount devices and entities of a storage box."""

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_API_KEY,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (  # type: ignore[import-untyped]
    AiohttpClientMocker,
)

from custom_components.hetzner import _size_unique_id, async_remove_config_entry_device
from custom_components.hetzner.binary_sensor import (
    SUBACCOUNT_ACCESS_SETTINGS,
    HetznerSubaccountAccessSettingBinarySensor,
)
from custom_components.hetzner.const import DOMAIN, LEGACY_CONF_UNIT
from custom_components.hetzner.coordinator import (
    StorageBoxData,
    subaccount_device_identifier,
    subaccount_ids_of_device,
)
from custom_components.hetzner.sensor import HetznerSubaccountHomeDirectorySensor

from tests.conftest import BOX_URL, STORAGE_BOX_ID, SUBACCOUNT_ID, SUBACCOUNTS_URL

BINARY_PREFIX = "binary_sensor.storage_box_backup_sub1"
SENSOR_PREFIX = "sensor.storage_box_backup_sub1"
UID = f"{DOMAIN}_storage_box_{STORAGE_BOX_ID}_subaccount_{SUBACCOUNT_ID}"
SUB_IDENTIFIER = (DOMAIN, f"{STORAGE_BOX_ID}_subaccount_{SUBACCOUNT_ID}")

# entity_id -> unique_id of the entities of the fixture subaccount
SUBACCOUNT_ENTITIES = {
    f"{BINARY_PREFIX}_reachable_externally": f"{UID}_reachable_externally",
    f"{BINARY_PREFIX}_samba": f"{UID}_samba_enabled",
    f"{BINARY_PREFIX}_ssh": f"{UID}_ssh_enabled",
    f"{BINARY_PREFIX}_webdav": f"{UID}_webdav_enabled",
    f"{BINARY_PREFIX}_read_only": f"{UID}_readonly",
    f"{SENSOR_PREFIX}_home_directory": f"{UID}_home_directory",
}
# Entities of the storage box itself
BOX_ENTITY_COUNT = 12

# The tests read the states of the protocol sensors, which are disabled by
# default (checked in test_init.py)
pytestmark = pytest.mark.usefixtures("entity_registry_enabled_by_default")


def _mock_api(
    aioclient_mock: AiohttpClientMocker,
    box: dict[str, Any],
    subaccounts: list[Any] | None = None,
    **subaccount_kwargs: Any,
) -> None:
    """Register the responses of the box and its subaccount list."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, json={"storage_box": box})
    if not subaccount_kwargs:
        subaccount_kwargs = {"json": {"subaccounts": subaccounts or []}}
    aioclient_mock.get(SUBACCOUNTS_URL, **subaccount_kwargs)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the config entry."""
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _tick(hass: HomeAssistant) -> None:
    """Advance time past the coordinator update interval."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=601))
    await hass.async_block_till_done()


def _devices(
    hass: HomeAssistant, entry: MockConfigEntry
) -> list[dr.DeviceEntry | dr.ChildDeviceEntry]:
    """Return the devices of the entry, child devices included."""
    dev_reg = dr.async_get(hass)
    return [
        *dr.async_entries_for_config_entry(dev_reg, entry.entry_id),
        *dr.async_child_entries_for_config_entry(dev_reg, entry.entry_id),
    ]


def _entities(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    """Return entity_id -> unique_id of the entry's entities."""
    return {
        e.entity_id: e.unique_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


def _sub_device(
    hass: HomeAssistant, entry: MockConfigEntry
) -> dr.ChildDeviceEntry | None:
    """Return the child device of the fixture subaccount."""
    return dr.async_get(hass).async_get_child_device_by_identifier(
        SUB_IDENTIFIER, entry.entry_id
    )


def _box_device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    """Return the storage box device, which must exist."""
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, STORAGE_BOX_ID), entry.entry_id
    )
    assert device is not None
    return device


async def test_subaccount_device_and_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """Each subaccount is a child device of the box with diagnostic entities."""
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert aioclient_mock.call_count == 2
    assert config_entry.runtime_data.data.subaccounts == {SUBACCOUNT_ID: subaccount}

    device = _sub_device(hass, config_entry)
    assert device is not None
    # The box device's name is put in front of it in entity names and ids
    assert device.name == "sub1"
    assert device.parent_device_id == _box_device(hass, config_entry).id
    assert len(_devices(hass, config_entry)) == 2

    entities = _entities(hass, config_entry)
    assert len(entities) == BOX_ENTITY_COUNT + len(SUBACCOUNT_ENTITIES)
    assert SUBACCOUNT_ENTITIES.items() <= entities.items()

    ent_reg = er.async_get(hass)
    for entity_id in SUBACCOUNT_ENTITIES:
        reg_entry = ent_reg.async_get(entity_id)
        assert reg_entry is not None
        assert reg_entry.device_id == device.id, entity_id
        assert reg_entry.entity_category is EntityCategory.DIAGNOSTIC, entity_id

    expected = {
        "reachable_externally": (STATE_OFF, "Reachable externally"),
        "samba": (STATE_ON, "Samba"),
        "ssh": (STATE_ON, "SSH"),
        "webdav": (STATE_OFF, "WebDAV"),
        "read_only": (STATE_ON, "Read-only"),
    }
    for suffix, (value, name) in expected.items():
        state = hass.states.get(f"{BINARY_PREFIX}_{suffix}")
        assert state is not None
        assert state.state == value, suffix
        # The state's friendly_name leaves out the parent device's name
        assert state.attributes["friendly_name"] == f"sub1 {name}"
        reg_entry = ent_reg.async_get(f"{BINARY_PREFIX}_{suffix}")
        assert reg_entry is not None
        assert er.async_get_full_entity_name(hass, reg_entry) == (
            f"Storage Box backup sub1 {name}"
        )

    state = hass.states.get(f"{SENSOR_PREFIX}_home_directory")
    assert state is not None
    assert state.state == "backups/host01"
    assert state.attributes["friendly_name"] == "sub1 Home directory"
    assert state.attributes["username"] == "u123456-sub1"
    assert state.attributes["server"] == "u123456-sub1.your-storagebox.de"
    assert state.attributes["description"] == "host01 backup"
    reg_entry = ent_reg.async_get(f"{SENSOR_PREFIX}_home_directory")
    assert reg_entry is not None
    assert reg_entry.translation_key == "home_directory"


@pytest.mark.parametrize(
    ("language", "readonly_name", "home_name"),
    [
        ("de", "Schreibgeschützt", "Home-Verzeichnis"),
        ("nl", "Alleen-lezen", "Thuismap"),
    ],
)
async def test_subaccount_entity_ids_stay_english(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
    language: str,
    readonly_name: str,
    home_name: str,
) -> None:
    """Languages with native entity_ids still get the English entity_ids."""
    hass.config.language = language
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)

    assert SUBACCOUNT_ENTITIES.items() <= _entities(hass, config_entry).items()
    state = hass.states.get(f"{BINARY_PREFIX}_read_only")
    assert state is not None
    assert state.attributes["friendly_name"] == f"sub1 {readonly_name}"
    state = hass.states.get(f"{SENSOR_PREFIX}_home_directory")
    assert state is not None
    assert state.attributes["friendly_name"] == f"sub1 {home_name}"


@pytest.mark.parametrize(
    ("changes", "device_name", "prefix"),
    [
        ({"name": ""}, "u123456-sub1", "storage_box_backup_u123456_sub1"),
        ({"name": None}, "u123456-sub1", "storage_box_backup_u123456_sub1"),
        (
            {"name": None, "username": None},
            SUBACCOUNT_ID,
            f"storage_box_backup_{SUBACCOUNT_ID}",
        ),
    ],
)
async def test_subaccount_name_fallback(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
    changes: dict[str, Any],
    device_name: str,
    prefix: str,
) -> None:
    """Without a name the device is named after the username, then the id."""
    subaccount.update(changes)
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)

    device = _sub_device(hass, config_entry)
    assert device is not None
    assert device.name == device_name
    assert hass.states.get(f"binary_sensor.{prefix}_ssh") is not None


async def test_subaccount_of_box_without_name(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """Entities of a box without a name get its id from the parent device."""
    storage_box["name"] = None
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)

    device = _sub_device(hass, config_entry)
    assert device is not None
    assert device.name == "sub1"
    assert _box_device(hass, config_entry).name == f"Storage Box {STORAGE_BOX_ID}"
    assert (
        hass.states.get(f"binary_sensor.storage_box_{STORAGE_BOX_ID}_sub1_ssh")
        is not None
    )


async def test_subaccount_missing_values_unknown(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """Settings or a home directory the API leaves out are unknown, not off."""
    del subaccount["access_settings"]["readonly"]
    subaccount["access_settings"]["ssh_enabled"] = None
    del subaccount["home_directory"]
    other = {**subaccount, "id": 43, "name": "sub2", "access_settings": None}
    _mock_api(aioclient_mock, storage_box, [subaccount, other])
    await _setup(hass, config_entry)

    for entity_id in (
        f"{BINARY_PREFIX}_read_only",
        f"{BINARY_PREFIX}_ssh",
        f"{SENSOR_PREFIX}_home_directory",
        "binary_sensor.storage_box_backup_sub2_samba",
    ):
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNKNOWN, entity_id
    assert hass.states.get(f"{BINARY_PREFIX}_samba").state == STATE_ON  # type: ignore[union-attr]


async def test_home_directory_too_long(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A home directory longer than a state allows is unknown without errors."""
    subaccount["home_directory"] = "a" * 256
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)

    state = hass.states.get(f"{SENSOR_PREFIX}_home_directory")
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes["username"] == "u123456-sub1"
    assert "is longer than" not in caplog.text

    subaccount["home_directory"] = "a" * 255
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _tick(hass)
    assert hass.states.get(f"{SENSOR_PREFIX}_home_directory").state == "a" * 255  # type: ignore[union-attr]


async def test_invalid_subaccounts_skipped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """Entries without an id or that are no objects get no device."""
    no_id = {**subaccount, "id": None, "name": "noid"}
    _mock_api(aioclient_mock, storage_box, [subaccount, no_id, "bogus"])
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert list(config_entry.runtime_data.data.subaccounts) == [SUBACCOUNT_ID]
    assert len(_devices(hass, config_entry)) == 2


async def test_new_subaccount_added_later(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """A subaccount appearing in a later update gets its device and entities."""
    _mock_api(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    assert len(_devices(hass, config_entry)) == 1
    assert len(_entities(hass, config_entry)) == BOX_ENTITY_COUNT

    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _tick(hass)

    device = _sub_device(hass, config_entry)
    assert device is not None
    assert device.parent_device_id == _box_device(hass, config_entry).id
    assert SUBACCOUNT_ENTITIES.items() <= _entities(hass, config_entry).items()
    assert hass.states.get(f"{BINARY_PREFIX}_ssh").state == STATE_ON  # type: ignore[union-attr]

    # Known subaccounts are not added again
    second = {**subaccount, "id": 43, "name": "sub2"}
    _mock_api(aioclient_mock, storage_box, [subaccount, second])
    await _tick(hass)

    entities = _entities(hass, config_entry)
    assert len(entities) == BOX_ENTITY_COUNT + 2 * len(SUBACCOUNT_ENTITIES)
    assert "binary_sensor.storage_box_backup_sub2_ssh" in entities
    assert not any(entity_id.endswith("_2") for entity_id in entities)


async def test_removed_subaccount_removes_device(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """A subaccount the API no longer lists loses its device and entities."""
    second = {**subaccount, "id": 43, "name": "sub2"}
    _mock_api(aioclient_mock, storage_box, [subaccount, second])
    await _setup(hass, config_entry)
    assert len(_devices(hass, config_entry)) == 3

    _mock_api(aioclient_mock, storage_box, [second])
    await _tick(hass)

    assert _sub_device(hass, config_entry) is None
    assert len(_devices(hass, config_entry)) == 2
    entities = _entities(hass, config_entry)
    assert not set(SUBACCOUNT_ENTITIES) & set(entities)
    for entity_id in SUBACCOUNT_ENTITIES:
        assert hass.states.get(entity_id) is None, entity_id
    assert hass.states.get("binary_sensor.storage_box_backup_sub2_ssh") is not None
    # The box keeps its device and entities
    assert _box_device(hass, config_entry) is not None
    assert len(entities) == BOX_ENTITY_COUNT + len(SUBACCOUNT_ENTITIES)

    # Should it come back, it is added again
    _mock_api(aioclient_mock, storage_box, [subaccount, second])
    await _tick(hass)

    assert _sub_device(hass, config_entry) is not None
    assert SUBACCOUNT_ENTITIES.items() <= _entities(hass, config_entry).items()
    assert hass.states.get(f"{BINARY_PREFIX}_ssh").state == STATE_ON  # type: ignore[union-attr]


async def test_stale_device_removed_on_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """A subaccount deleted while HA was not running is removed on setup.

    Devices of other identifiers in the entry are left alone. The stale device
    is a main device, as registered before subaccounts became child devices.
    """
    dev_reg = dr.async_get(hass)
    stale = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, subaccount_device_identifier(STORAGE_BOX_ID, "7"))},
    )
    stale_entity = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_storage_box_{STORAGE_BOX_ID}_subaccount_7_home_directory",
        config_entry=config_entry,
        device_id=stale.id,
    )
    foreign = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("other", subaccount_device_identifier(STORAGE_BOX_ID, "7"))},
    )
    _mock_api(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    assert dev_reg.async_get(stale.id) is None
    assert er.async_get(hass).async_get(stale_entity.entity_id) is None
    assert dev_reg.async_get(foreign.id) is not None
    assert _box_device(hass, config_entry) is not None


async def test_stale_child_device_removed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """A child device of a subaccount the API does not list is removed."""
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)
    dev_reg = dr.async_get(hass)
    stale = dev_reg.async_get_or_create_child(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, subaccount_device_identifier(STORAGE_BOX_ID, "7"))},
        name="gone",
        parent_device_id=_box_device(hass, config_entry).id,
    )

    await _tick(hass)

    assert dev_reg.async_get(stale.id) is None
    assert _sub_device(hass, config_entry) is not None


async def test_device_names_follow_the_api(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """Names and the box type changed in the Hetzner Console are updated.

    Names the user gave the devices stay; entity_ids do not change.
    """
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)
    dev_reg = dr.async_get(hass)
    box = _box_device(hass, config_entry)
    sub = _sub_device(hass, config_entry)
    assert sub is not None

    storage_box["name"] = "archive"
    storage_box["storage_box_type"]["name"] = "bx21"
    subaccount["name"] = "host02"
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _tick(hass)

    box = _box_device(hass, config_entry)
    assert box.name == "Storage Box archive"
    assert box.model == "bx21"
    sub = _sub_device(hass, config_entry)
    assert sub is not None
    assert sub.name == "host02"
    reg_entry = er.async_get(hass).async_get(f"{BINARY_PREFIX}_ssh")
    assert reg_entry is not None
    assert er.async_get_full_entity_name(hass, reg_entry) == (
        "Storage Box archive host02 SSH"
    )

    dev_reg.async_update_device(box.id, name_by_user="My box")
    dev_reg.async_update_child_device(sub.id, name_by_user="My sub")
    subaccount["name"] = "host03"
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _tick(hass)

    box = _box_device(hass, config_entry)
    assert (box.name, box.name_by_user) == ("Storage Box archive", "My box")
    sub = _sub_device(hass, config_entry)
    assert sub is not None
    assert (sub.name, sub.name_by_user) == ("host03", "My sub")


async def test_main_subaccount_device_becomes_child(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A subaccount device of an earlier version keeps its id and user settings.

    Up to 1.1 subaccounts were main devices linked with via_device_id.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    box = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, STORAGE_BOX_ID)},
        name="Storage Box backup",
    )
    legacy = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={SUB_IDENTIFIER},
        name="Storage Box backup sub1",
        manufacturer="Hetzner",
        model="Storage Box subaccount",
        via_device_id=box.id,
    )
    dev_reg.async_update_device(
        legacy.id, name_by_user="My sub", area_id="office", labels={"backup"}
    )
    # Registered by an earlier version under the same entity_ids
    for entity_id, unique_id in SUBACCOUNT_ENTITIES.items():
        domain, object_id = entity_id.split(".")
        ent_reg.async_get_or_create(
            domain,
            DOMAIN,
            unique_id,
            config_entry=config_entry,
            device_id=legacy.id,
            suggested_object_id=object_id,
        )
    # As after a restart: the registrations above belong to no running setup
    dev_reg.async_config_entry_unloaded(config_entry.entry_id)

    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)

    device = _sub_device(hass, config_entry)
    assert device is not None
    assert device.id == legacy.id
    assert device.parent_device_id == box.id
    assert device.name == "sub1"
    assert device.name_by_user == "My sub"
    assert device.area_id == "office"
    assert device.labels == {"backup"}
    assert (
        dev_reg.async_get_device_by_identifier(SUB_IDENTIFIER, config_entry.entry_id)
        is None
    )
    assert SUBACCOUNT_ENTITIES.items() <= _entities(hass, config_entry).items()
    for entity_id in SUBACCOUNT_ENTITIES:
        reg_entry = ent_reg.async_get(entity_id)
        assert reg_entry is not None
        assert reg_entry.device_id == legacy.id, entity_id
        assert hass.states.get(entity_id) is not None, entity_id
    assert "error adding device" not in caplog.text


async def test_api_error_keeps_subaccounts(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """A failed subaccount request makes everything unavailable, removes nothing."""
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)

    _mock_api(aioclient_mock, storage_box, status=500)
    await _tick(hass)

    assert _sub_device(hass, config_entry) is not None
    for entity_id in [*SUBACCOUNT_ENTITIES, "sensor.storage_box_backup"]:
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNAVAILABLE, entity_id

    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _tick(hass)

    for entity_id in SUBACCOUNT_ENTITIES:
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


@pytest.mark.parametrize("status", [401, 403])
async def test_subaccount_auth_error_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    status: int,
) -> None:
    """A token rejected on the subaccount list fails setup and starts reauth."""
    _mock_api(aioclient_mock, storage_box, status=status)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1


async def test_subaccount_not_found_is_box_not_found(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """A 404 on the subaccount list means the box is gone: same repair issue."""
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)

    _mock_api(aioclient_mock, storage_box, status=404)
    await _tick(hass)

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"storage_box_not_found_{config_entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_placeholders == {"storage_box_id": STORAGE_BOX_ID}
    assert _reauth_flows(hass) == []
    # Nothing is removed while the box cannot be read
    assert _sub_device(hass, config_entry) is not None

    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _tick(hass)
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"storage_box_not_found_{config_entry.entry_id}"
        )
        is None
    )


def _entity_mock(subaccounts: dict[str, Any] | None) -> MagicMock:
    """Return a coordinator mock with the given subaccounts, None for no data."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = (
        None if subaccounts is None else StorageBoxData(box={}, subaccounts=subaccounts)
    )
    return coordinator


@pytest.mark.parametrize(
    ("subaccounts", "available"),
    [
        ({SUBACCOUNT_ID: {"access_settings": {"readonly": True}}}, True),
        # Gone from the API: unavailable until the coordinator removes it
        ({}, False),
        (None, False),
    ],
)
def test_subaccount_entity_availability(
    subaccounts: dict[str, Any] | None, available: bool
) -> None:
    """Subaccount entities are only available while the API lists them."""
    coordinator = _entity_mock(subaccounts)
    binary = HetznerSubaccountAccessSettingBinarySensor(
        coordinator, STORAGE_BOX_ID, SUBACCOUNT_ID, SUBACCOUNT_ACCESS_SETTINGS[-1]
    )
    sensor = HetznerSubaccountHomeDirectorySensor(
        coordinator, STORAGE_BOX_ID, SUBACCOUNT_ID
    )

    assert binary.available is available
    assert sensor.available is available
    assert binary.is_on is (True if available else None)
    assert sensor.native_value is None
    assert binary.unique_id == f"{UID}_readonly"

    coordinator.last_update_success = False
    assert binary.available is False


async def test_remove_config_entry_device(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """Only devices of subaccounts the API no longer lists can be deleted."""
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)
    dev_reg = dr.async_get(hass)
    stale = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, subaccount_device_identifier(STORAGE_BOX_ID, "7"))},
    )
    foreign = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "999_subaccount_7")},
    )
    box = _box_device(hass, config_entry)
    current = _sub_device(hass, config_entry)
    assert current is not None

    assert not await async_remove_config_entry_device(hass, config_entry, box)
    assert not await async_remove_config_entry_device(hass, config_entry, current)
    assert not await async_remove_config_entry_device(hass, config_entry, foreign)
    assert await async_remove_config_entry_device(hass, config_entry, stale)

    # Not loaded: a subaccount device that still exists comes back on setup
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await async_remove_config_entry_device(hass, config_entry, current)
    assert not await async_remove_config_entry_device(hass, config_entry, box)


async def test_remove_device_through_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """The UI can delete a stale subaccount device, but not the box device."""
    assert await async_setup_component(hass, "config", {})
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, config_entry)
    dev_reg = dr.async_get(hass)
    stale = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, subaccount_device_identifier(STORAGE_BOX_ID, "7"))},
    )
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "config/device_registry/remove", "device_id": stale.id}
    )
    response = await client.receive_json()
    assert response["success"]
    assert dev_reg.async_get(stale.id) is None

    box = _box_device(hass, config_entry)
    await client.send_json_auto_id(
        {"type": "config/device_registry/remove", "device_id": box.id}
    )
    response = await client.receive_json()
    assert not response["success"]
    assert dev_reg.async_get(box.id) is not None


def test_subaccount_ids_of_device() -> None:
    """Only this box's subaccount identifiers of the domain are recognized."""
    assert subaccount_ids_of_device(
        {SUB_IDENTIFIER, (DOMAIN, STORAGE_BOX_ID)}, STORAGE_BOX_ID
    ) == {SUBACCOUNT_ID}
    assert subaccount_ids_of_device({(DOMAIN, STORAGE_BOX_ID)}, STORAGE_BOX_ID) == set()
    assert subaccount_ids_of_device({("other", SUB_IDENTIFIER[1])}, STORAGE_BOX_ID) == (
        set()
    )
    # Another box whose id ends like this one's
    assert (
        subaccount_ids_of_device(
            {(DOMAIN, f"9{STORAGE_BOX_ID}_subaccount_1")}, STORAGE_BOX_ID
        )
        == set()
    )


@pytest.mark.parametrize(
    "key",
    [
        "ssh_enabled",
        "readonly",
        "home_directory",
        # Not used, but must not be taken for size sensors either
        "total",
        "free",
        "total_gb",
        "snapshots_tb",
    ],
)
def test_subaccount_unique_ids_are_no_size_sensors(key: str) -> None:
    """The size sensor parsing of the migration ignores subaccount unique_ids."""
    assert _size_unique_id(f"{UID}_{key}") is None
    # The size sensors of the box itself are still recognized
    assert _size_unique_id(f"{DOMAIN}_storage_box_{STORAGE_BOX_ID}_total") is not None
    assert _size_unique_id(f"{DOMAIN}_storage_box_{STORAGE_BOX_ID}_free_gb") == (
        f"{DOMAIN}_storage_box_{STORAGE_BOX_ID}_free"
    )


async def test_migration_leaves_subaccount_entities_alone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """Subaccount entities ending in metric or unit names survive the migration."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hetzner backup",
        version=4,
        unique_id=STORAGE_BOX_ID,
        data={
            CONF_API_KEY: "key",
            "host": "api.hetzner.com",
            "storage_box_id": STORAGE_BOX_ID,
        },
        options={LEGACY_CONF_UNIT: "GB"},
    )
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    odd = [
        ent_reg.async_get_or_create(
            "sensor", DOMAIN, f"{UID}_{key}", config_entry=entry
        )
        for key in ("total_gb", "free", "home_directory")
    ]
    _mock_api(aioclient_mock, storage_box, [subaccount])
    await _setup(hass, entry)

    assert entry.version == 5
    for reg in odd:
        updated = ent_reg.async_get(reg.entity_id)
        assert updated is not None
        assert updated.unique_id == reg.unique_id
        assert "sensor" not in updated.options
