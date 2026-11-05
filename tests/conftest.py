"""Shared fixtures for the Hetzner Storage Box tests."""

from collections.abc import Generator
import copy
from typing import Any
from unittest.mock import PropertyMock, patch

from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
)

from custom_components.hetzner.const import CONF_STORAGE_BOX_ID, DEFAULT_HOST, DOMAIN

API_KEY = "test-api-key"
STORAGE_BOX_ID = "123456"
LIST_URL = f"https://{DEFAULT_HOST}/v1/storage_boxes"
BOX_URL = f"{LIST_URL}/{STORAGE_BOX_ID}"
SUBACCOUNTS_URL = f"{BOX_URL}/subaccounts"
SUBACCOUNT_ID = "42"

STORAGE_BOX: dict[str, Any] = {
    "id": int(STORAGE_BOX_ID),
    "name": "backup",
    "username": "u123456",
    "status": "active",
    "server": "u123456.your-storagebox.de",
    "system": "FSN1-BX123",
    "created": "2024-01-01T00:00:00+00:00",
    "storage_box_type": {
        "name": "bx11",
        "description": "BX11",
        # A BX11 ("1 TB") reports a binary terabyte
        "size": 2**40,
    },
    "location": {
        "id": 1,
        "name": "fsn1",
        "country": "DE",
        "city": "Falkenstein",
        "latitude": 50.47612,
        "longitude": 12.370071,
        "description": "Falkenstein DC Park 1",
    },
    "access_settings": {
        "reachable_externally": True,
        "samba_enabled": False,
        "ssh_enabled": True,
        "webdav_enabled": False,
        "zfs_enabled": True,
    },
    "stats": {
        "size": 300_000_000_000,
        "size_data": 250_000_000_000,
        "size_snapshots": 50_000_000_000,
    },
}


SUBACCOUNT: dict[str, Any] = {
    "id": int(SUBACCOUNT_ID),
    "storage_box": int(STORAGE_BOX_ID),
    "name": "sub1",
    "username": "u123456-sub1",
    "server": "u123456-sub1.your-storagebox.de",
    "home_directory": "backups/host01",
    "description": "host01 backup",
    "labels": {"environment": "prod"},
    "created": "2025-02-22T00:02:00+00:00",
    "access_settings": {
        "reachable_externally": False,
        "samba_enabled": True,
        "ssh_enabled": True,
        "webdav_enabled": False,
        "readonly": True,
    },
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,  # noqa: ARG001
) -> Generator[None]:
    """Enable loading the custom integration in every test."""
    yield


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Register every entity as enabled, also those disabled by default."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        return_value=True,
        new_callable=PropertyMock,
    ):
        yield


@pytest.fixture
def storage_box() -> dict[str, Any]:
    """Return a fresh copy of a storage box API payload."""
    return copy.deepcopy(STORAGE_BOX)


@pytest.fixture
def subaccount() -> dict[str, Any]:
    """Return a fresh copy of a subaccount API payload."""
    return copy.deepcopy(SUBACCOUNT)


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a current-version config entry added to hass (not set up)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hetzner backup",
        version=5,
        unique_id=STORAGE_BOX_ID,
        data={
            CONF_API_KEY: API_KEY,
            CONF_HOST: DEFAULT_HOST,
            CONF_STORAGE_BOX_ID: STORAGE_BOX_ID,
        },
    )
    entry.add_to_hass(hass)
    return entry
