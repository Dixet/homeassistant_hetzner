"""Tests for the diagnostics of a storage box config entry."""

from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
)
from pytest_homeassistant_custom_component.components.diagnostics import (  # type: ignore[import-untyped]
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (  # type: ignore[import-untyped]
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import (  # type: ignore[import-untyped]
    ClientSessionGenerator,
)

from tests.conftest import (
    API_KEY,
    BOX_URL,
    STORAGE_BOX_ID,
    SUBACCOUNT_ID,
    SUBACCOUNTS_URL,
)


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    subaccount: dict[str, Any],
) -> None:
    """The entry and the last API data are returned with secrets redacted."""
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box})
    aioclient_mock.get(SUBACCOUNTS_URL, json={"subaccounts": [subaccount]})
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )

    assert API_KEY not in str(diagnostics)
    assert "u123456" not in str(diagnostics)

    entry = diagnostics["entry"]
    assert entry["entry_id"] == config_entry.entry_id
    assert entry["version"] == 5
    assert entry["unique_id"] == STORAGE_BOX_ID
    assert entry["data"] == {
        "api_key": REDACTED,
        "host": "api.hetzner.com",
        "storage_box_id": STORAGE_BOX_ID,
    }
    assert diagnostics["last_update_success"] is True

    box = diagnostics["data"]["box"]
    assert box["username"] == REDACTED
    assert box["server"] == REDACTED
    assert box["location"]["latitude"] == REDACTED
    assert box["location"]["longitude"] == REDACTED
    # Everything else is passed through as the API sent it
    assert box["system"] == storage_box["system"]
    assert box["location"]["name"] == storage_box["location"]["name"]
    assert box["access_settings"] == storage_box["access_settings"]
    assert box["stats"] == storage_box["stats"]

    sub = diagnostics["data"]["subaccounts"][SUBACCOUNT_ID]
    assert sub["username"] == REDACTED
    assert sub["server"] == REDACTED
    assert sub["home_directory"] == subaccount["home_directory"]
    assert sub["access_settings"] == subaccount["access_settings"]


async def test_diagnostics_entry_not_loaded(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Without a successful first refresh there is no API data to return."""
    aioclient_mock.get(BOX_URL, status=500)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )

    assert diagnostics["entry"]["data"]["api_key"] == REDACTED
    assert diagnostics["last_update_success"] is None
    assert diagnostics["data"] is None
