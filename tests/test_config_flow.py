"""Tests for the Hetzner Storage Box config flow."""

import logging
from typing import Any
from unittest.mock import patch

import aiohttp
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import selector
import pytest
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (  # type: ignore[import-untyped]
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.hetzner.api import LIST_PAGE_SIZE, HetznerError
from custom_components.hetzner.config_flow import (
    HetznerConfigFlow,
    StorageBoxInfo,
    _error_key,
)
from custom_components.hetzner.const import CONF_STORAGE_BOX_ID, DEFAULT_HOST, DOMAIN

from tests.conftest import API_KEY, BOX_URL, LIST_URL, STORAGE_BOX_ID, SUBACCOUNTS_URL

# The type name "bx11" repeats its description "BX11", so it is left out
BOX_DESCRIPTION = f"backup (ID: {STORAGE_BOX_ID}, Status: active, Type: BX11)"
BOX_OPTION = {"value": STORAGE_BOX_ID, "label": BOX_DESCRIPTION}


def _queue_list_responses(
    aioclient_mock: AiohttpClientMocker, *responses: dict[str, Any]
) -> None:
    """Register LIST_URL so successive GETs return the given responses in order.

    aioclient_mock always answers with the first matching registration, so a
    side_effect is used to hand out a different response per call.
    """
    queue = list(responses)

    async def _side_effect(
        method: str, url: URL, _data: Any
    ) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(method, url, **queue.pop(0))

    aioclient_mock.get(LIST_URL, side_effect=_side_effect)


def _list_payload(*boxes: dict[str, Any]) -> dict[str, Any]:
    """Return a successful list response containing the given boxes."""
    return {"json": {"storage_boxes": list(boxes)}}


async def _start_flow(hass: HomeAssistant) -> ConfigFlowResult:
    """Start a user config flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _submit_key(
    hass: HomeAssistant, flow_id: str, api_key: str = API_KEY
) -> ConfigFlowResult:
    """Submit the user step."""
    return await hass.config_entries.flow.async_configure(
        flow_id, {CONF_API_KEY: api_key}
    )


async def _finish_after_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    flow_id: str,
    storage_box: dict[str, Any],
) -> None:
    """Fix the API, resubmit the key, pick the box and expect a new entry."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(LIST_URL, **_list_payload(storage_box))

    result = await _submit_key(hass, flow_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "storage_box"
    assert result["errors"] == {}

    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == STORAGE_BOX_ID


# Flows that replace the API key of an entry: step id and abort reason
KEY_FLOWS = {
    "reauth": ("reauth_confirm", "reauth_successful"),
    "reconfigure": ("reconfigure", "reconfigure_successful"),
}


async def _start_key_flow(
    hass: HomeAssistant, entry: MockConfigEntry, flow: str
) -> ConfigFlowResult:
    """Start a reauth or reconfigure flow of the entry."""
    # MockConfigEntry is untyped
    result: ConfigFlowResult
    if flow == "reauth":
        result = await entry.start_reauth_flow(hass)
    else:
        result = await entry.start_reconfigure_flow(hass)
    return result


def _schema(result: ConfigFlowResult) -> dict[Any, Any]:
    """Return the schema dict of a form result."""
    data_schema = result["data_schema"]
    assert data_schema is not None
    schema: dict[Any, Any] = data_schema.schema
    return schema


async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    """Test the initial form is shown."""
    result = await _start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    # Only the API key: the size sensors' unit is set per entity
    assert list(_schema(result)) == [CONF_API_KEY]
    _assert_masked_api_key(result)


def _assert_masked_api_key(result: ConfigFlowResult) -> None:
    """Assert the form asks for the API key in a password field."""
    key_selector = _schema(result)[CONF_API_KEY]
    assert isinstance(key_selector, selector.TextSelector)
    assert key_selector.config["type"] == selector.TextSelectorType.PASSWORD


@pytest.mark.parametrize("api_key", ["", "   "])
async def test_user_step_api_key_required(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
    api_key: str,
) -> None:
    """Test an empty or whitespace API key is rejected without an API call."""
    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"], api_key=api_key)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_API_KEY: "api_key_required"}
    assert aioclient_mock.call_count == 0

    await _finish_after_error(hass, aioclient_mock, result["flow_id"], storage_box)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ({"status": 401}, "invalid_api_key"),
        ({"status": 403}, "invalid_api_key"),
        ({"status": 403, "json": {"error": {"code": "forbidden"}}}, "invalid_api_key"),
        ({"status": 403, "json": {"error": {"code": "maintenance"}}}, "api_error"),
        ({"status": 404}, "invalid_host"),
        ({"status": 429}, "api_error"),
        ({"status": 500}, "api_error"),
        ({"exc": TimeoutError()}, "connection_timeout"),
        ({"exc": aiohttp.ClientError("boom")}, "cannot_connect"),
        ({"exc": ValueError("boom")}, "unknown"),
    ],
)
async def test_user_step_validation_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
    response: dict[str, Any],
    error: str,
) -> None:
    """Test API key validation failures are mapped to form errors."""
    aioclient_mock.get(LIST_URL, **response)

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_API_KEY: error}

    await _finish_after_error(hass, aioclient_mock, result["flow_id"], storage_box)


async def test_full_flow_creates_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test a valid key and box selection create a config entry."""
    payload = _list_payload(storage_box)
    _queue_list_responses(aioclient_mock, payload, payload)

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"], api_key=f"  {API_KEY} ")

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "storage_box"
    assert result["errors"] == {}
    box_selector = _schema(result)[CONF_STORAGE_BOX_ID]
    assert box_selector.config["options"] == [BOX_OPTION]
    # The list that validated the key is shown, not requested again
    assert aioclient_mock.call_count == 1
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers == {"Authorization": f"Bearer {API_KEY}"}

    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hetzner backup"
    assert result["data"] == {
        CONF_API_KEY: API_KEY,
        CONF_HOST: DEFAULT_HOST,
        CONF_STORAGE_BOX_ID: STORAGE_BOX_ID,
    }
    assert result["options"] == {}
    entry = result["result"]
    assert entry.unique_id == STORAGE_BOX_ID
    assert entry.version == 5


async def test_storage_box_step_lists_multiple_boxes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test every box is offered and missing fields are left out of the label."""
    other = {"id": 42, "name": "other", "status": "initializing"}
    unnamed = {"id": 43, "name": None, "storage_box_type": {"name": "bx21"}}
    _queue_list_responses(aioclient_mock, _list_payload(storage_box, other, unnamed))

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])

    box_selector = _schema(result)[CONF_STORAGE_BOX_ID]
    assert box_selector.config["options"] == [
        BOX_OPTION,
        {"value": "42", "label": "other (ID: 42, Status: initializing)"},
        {"value": "43", "label": "ID: 43, Type: bx21"},
    ]


async def test_storage_box_step_skips_malformed_boxes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Boxes without id are left out, a malformed type is left out of the label."""
    no_id = {"name": "no id", "status": "active"}
    bad_type = {"id": 42, "name": "other", "storage_box_type": "bx11"}
    _queue_list_responses(
        aioclient_mock,
        {"json": {"storage_boxes": [no_id, "box", storage_box, None, bad_type]}},
    )

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])

    box_selector = _schema(result)[CONF_STORAGE_BOX_ID]
    assert box_selector.config["options"] == [
        BOX_OPTION,
        {"value": "42", "label": "other (ID: 42)"},
    ]


async def test_storage_box_step_only_boxes_without_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A list of boxes without id has nothing to select."""
    _queue_list_responses(aioclient_mock, _list_payload({"name": "no id"}))

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "storage_box"
    assert result["errors"] == {"base": "no_storage_boxes"}


async def test_unexpected_error_logged_with_traceback(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Errors outside the API client are bugs: logged with their traceback."""
    aioclient_mock.get(LIST_URL, exc=ValueError("boom"))

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])

    assert result["errors"] == {CONF_API_KEY: "unknown"}
    assert "Unexpected error listing the storage boxes" in caplog.text
    assert "Traceback" in caplog.text


def test_error_key_unknown_client_error(caplog: pytest.LogCaptureFixture) -> None:
    """An error class the client may add later is an API error, not a bug."""
    with caplog.at_level(logging.DEBUG):
        assert _error_key(HetznerError("new")) == "api_error"

    assert "Traceback" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_storage_box_step_lists_all_pages(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test boxes beyond the first page of the list are offered too."""
    other = {"id": 42, "name": "other", "status": "initializing"}
    for page, boxes, next_page in ((1, [storage_box], 2), (2, [other], None)):
        aioclient_mock.get(
            f"{LIST_URL}?page={page}&per_page={LIST_PAGE_SIZE}",
            json={
                "storage_boxes": boxes,
                "meta": {"pagination": {"page": page, "next_page": next_page}},
            },
        )

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])

    assert _schema(result)[CONF_STORAGE_BOX_ID].config["options"] == [
        BOX_OPTION,
        {"value": "42", "label": "other (ID: 42, Status: initializing)"},
    ]


async def test_storage_box_step_box_changed_before_submit(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test a box whose status changes before submitting can still be selected."""
    changed = {**storage_box, "status": "locked"}
    _queue_list_responses(
        aioclient_mock, _list_payload(storage_box), _list_payload(changed)
    )

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])
    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STORAGE_BOX_ID] == STORAGE_BOX_ID


async def test_storage_box_without_name_uses_id_in_title(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test a box without a name gets its ID in the entry title."""
    storage_box["name"] = None
    aioclient_mock.get(LIST_URL, **_list_payload(storage_box))

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])
    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Hetzner {STORAGE_BOX_ID}"


@pytest.mark.parametrize(
    ("list_response", "error"),
    [
        # Only a successful, empty list means there are no boxes
        ({"json": {"storage_boxes": []}}, "no_storage_boxes"),
        ({"json": {}}, "no_storage_boxes"),
        ({"status": 500}, "api_error"),
        ({"status": 429}, "api_error"),
        (
            {"status": 403, "json": {"error": {"code": "maintenance"}}},
            "api_error",
        ),
        ({"status": 401}, "invalid_api_key"),
        ({"status": 403, "json": {"error": {"code": "forbidden"}}}, "invalid_api_key"),
        ({"status": 404}, "invalid_host"),
        ({"text": "not json"}, "api_error"),
        ({"exc": TimeoutError()}, "connection_timeout"),
        ({"exc": aiohttp.ClientError("boom")}, "cannot_connect"),
        ({"exc": ValueError("boom")}, "unknown"),
    ],
)
async def test_storage_box_step_list_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
    list_response: dict[str, Any],
    error: str,
) -> None:
    """Test a failed or empty box list shows its error until boxes are found."""
    payload = _list_payload(storage_box)
    _queue_list_responses(aioclient_mock, {"json": {}}, list_response, payload, payload)

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])

    # A key without boxes is valid; the box step says there are none
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "storage_box"
    assert result["errors"] == {"base": "no_storage_boxes"}
    assert _schema(result) == {}

    # Submitting the empty form fetches the boxes again
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "storage_box"
    assert result["errors"] == {"base": error}
    assert _schema(result) == {}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "storage_box"
    assert result["errors"] == {}
    assert _schema(result)[CONF_STORAGE_BOX_ID].config["options"] == [BOX_OPTION]

    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == STORAGE_BOX_ID


async def test_storage_box_step_invalid_selection(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test a box removed before submitting shows invalid_selection.

    The selector only accepts boxes of the shown form, so the error needs a
    box that disappears between showing and submitting the form.
    """
    other = {"id": 42, "name": "other", "status": "active"}
    only_box = _list_payload(storage_box)
    _queue_list_responses(
        aioclient_mock, _list_payload(storage_box, other), only_box, only_box
    )

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STORAGE_BOX_ID: "42"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "storage_box"
    assert result["errors"] == {"base": "invalid_selection"}
    box_selector = _schema(result)[CONF_STORAGE_BOX_ID]
    assert box_selector.config["options"] == [BOX_OPTION]

    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == STORAGE_BOX_ID


@pytest.mark.usefixtures("config_entry")
async def test_storage_box_step_already_configured(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test a configured box is marked and selecting it shows an error."""
    other = {"id": 42, "name": "other", "status": "active"}
    aioclient_mock.get(LIST_URL, **_list_payload(storage_box, other))
    # Listed first by the API, offered last: the dropdown preselects the first
    options = [
        {"value": "42", "label": "other (ID: 42, Status: active)"},
        {
            "value": STORAGE_BOX_ID,
            "label": f"{BOX_DESCRIPTION[:-1]}, already configured)",
        },
    ]

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])
    assert _schema(result)[CONF_STORAGE_BOX_ID].config["options"] == options

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "storage_box"
    assert result["errors"] == {"base": "already_configured"}
    assert _schema(result)[CONF_STORAGE_BOX_ID].config["options"] == options
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1

    # Another box of the same key can still be added
    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STORAGE_BOX_ID: "42"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "42"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


@pytest.mark.usefixtures("config_entry")
async def test_storage_box_already_configured_in_two_flows(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test a second flow picking a configured box gets the same form error.

    The first flow must not keep the box as its unique_id, or the second
    would abort as already in progress.
    """
    aioclient_mock.get(LIST_URL, **_list_payload(storage_box))
    flows = []
    for _ in range(2):
        result = await _start_flow(hass)
        flows.append((await _submit_key(hass, result["flow_id"]))["flow_id"])

    for flow_id in flows:
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "already_configured"}


async def test_storage_box_ignored_entry_not_marked(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test a box with an ignored entry is offered unmarked and can be added."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=STORAGE_BOX_ID,
        source=config_entries.SOURCE_IGNORE,
    ).add_to_hass(hass)
    aioclient_mock.get(LIST_URL, **_list_payload(storage_box))

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])
    assert _schema(result)[CONF_STORAGE_BOX_ID].config["options"] == [BOX_OPTION]

    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == STORAGE_BOX_ID


@pytest.mark.parametrize("flow", KEY_FLOWS)
async def test_key_flow_updates_api_key(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    flow: str,
) -> None:
    """Test a valid new key is stored and the not loaded entry reloaded."""
    step_id, reason = KEY_FLOWS[flow]
    aioclient_mock.get(BOX_URL, json={"storage_box": {}})

    result = await _start_key_flow(hass, config_entry, flow)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step_id
    assert result["errors"] == {}
    assert list(_schema(result)) == [CONF_API_KEY]
    _assert_masked_api_key(result)
    placeholders = result["description_placeholders"]
    assert placeholders is not None
    assert placeholders[CONF_STORAGE_BOX_ID] == STORAGE_BOX_ID

    with patch(
        "custom_components.hetzner.async_setup_entry", return_value=True
    ) as setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: " new-key "}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert config_entry.data[CONF_API_KEY] == "new-key"
    assert config_entry.data[CONF_STORAGE_BOX_ID] == STORAGE_BOX_ID
    assert config_entry.options == {}
    assert len(setup.mock_calls) == 1
    assert aioclient_mock.call_count == 1
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert str(url) == BOX_URL
    assert headers == {"Authorization": "Bearer new-key"}


async def test_reconfigure_loaded_entry_reloads_with_new_key(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Test reconfiguring a running entry reloads it with the new key."""
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box})
    aioclient_mock.get(SUBACCOUNTS_URL, json={"subaccounts": []})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box})
    aioclient_mock.get(SUBACCOUNTS_URL, json={"subaccounts": []})
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "new-key"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.state is config_entries.ConfigEntryState.LOADED
    assert config_entry.runtime_data is not coordinator
    # Key check plus the first refresh after the reload, all with the new key
    assert aioclient_mock.call_count == 3
    for _, _, _, headers in aioclient_mock.mock_calls:
        assert headers == {"Authorization": "Bearer new-key"}


async def test_reauth_flow_loaded_entry_reloads_with_new_key(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test reauth of a running entry reloads it with the new key."""
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box})
    aioclient_mock.get(SUBACCOUNTS_URL, json={"subaccounts": []})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, status=401)
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert config_entry.state is config_entries.ConfigEntryState.LOADED
    coordinator = config_entry.runtime_data

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box})
    aioclient_mock.get(SUBACCOUNTS_URL, json={"subaccounts": []})
    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {CONF_API_KEY: "new-key"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.state is config_entries.ConfigEntryState.LOADED
    assert config_entry.runtime_data is not coordinator
    # Reauth check plus the first refresh after the reload (box and subaccounts),
    # all with the new key
    assert aioclient_mock.call_count == 3
    for _, _, _, headers in aioclient_mock.mock_calls:
        assert headers == {"Authorization": "Bearer new-key"}
    # HA warns when an entry with an update listener is reloaded by the flow
    assert "update listener" not in caplog.text


async def test_reauth_flow_failed_entry_set_up_with_new_key(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Test reauth of an entry whose setup failed with the old key loads it."""
    aioclient_mock.get(BOX_URL, status=401)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box})
    aioclient_mock.get(SUBACCOUNTS_URL, json={"subaccounts": []})
    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {CONF_API_KEY: "new-key"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.state is config_entries.ConfigEntryState.LOADED
    assert config_entry.runtime_data.data.box == storage_box


async def test_no_options_flow(config_entry: MockConfigEntry) -> None:
    """Test there are no options: units are set in the entity settings."""
    assert not HetznerConfigFlow.async_supports_options_flow(config_entry)


@pytest.mark.parametrize(
    ("api_key", "response", "error"),
    [
        ("  ", None, "api_key_required"),
        ("new-key", {"status": 401}, "invalid_api_key"),
        ("new-key", {"status": 403}, "invalid_api_key"),
        (
            "new-key",
            {"status": 403, "json": {"error": {"code": "maintenance"}}},
            "api_error",
        ),
        ("new-key", {"status": 404}, "storage_box_not_found"),
        ("new-key", {"status": 500}, "api_error"),
        ("new-key", {"exc": TimeoutError()}, "connection_timeout"),
        ("new-key", {"exc": aiohttp.ClientError("boom")}, "cannot_connect"),
        ("new-key", {"exc": ValueError("boom")}, "unknown"),
    ],
)
@pytest.mark.parametrize("flow", KEY_FLOWS)
async def test_key_flow_errors_recover(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    api_key: str,
    response: dict[str, Any] | None,
    error: str,
    flow: str,
) -> None:
    """Test a rejected key shows an error and a valid key then finishes."""
    step_id, reason = KEY_FLOWS[flow]
    if response is not None:
        aioclient_mock.get(BOX_URL, **response)

    result = await _start_key_flow(hass, config_entry, flow)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: api_key}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step_id
    assert result["errors"] == {CONF_API_KEY: error}
    assert config_entry.data[CONF_API_KEY] == API_KEY

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, json={"storage_box": {}})
    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "new-key"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert config_entry.data[CONF_API_KEY] == "new-key"


@pytest.mark.parametrize(
    ("box_type", "description", "type_label"),
    [
        ("bx11", "BX11", "BX11"),
        ("bx11", "BX11 (legacy)", "bx11, BX11 (legacy)"),
        ("bx11", None, "bx11"),
        (None, "BX11", "BX11"),
    ],
)
def test_box_label_type(
    box_type: str | None, description: str | None, type_label: str
) -> None:
    """The type name is only left out when it repeats the description."""
    box_info: StorageBoxInfo = {
        "name": None,
        "status": None,
        "box_type": box_type,
        "description": description,
    }
    assert HetznerConfigFlow._box_label("1", box_info, False) == (
        f"ID: 1, Type: {type_label}"
    )
