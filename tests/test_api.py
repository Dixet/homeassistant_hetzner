"""Tests for the Hetzner API client."""

from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (  # type: ignore[import-untyped]
    AiohttpClientMocker,
)

from custom_components.hetzner.api import (
    LIST_PAGE_SIZE,
    HetznerApiError,
    HetznerAuthError,
    HetznerClient,
    HetznerConnectionError,
    HetznerError,
    HetznerNotFoundError,
    HetznerResponseError,
    HetznerTimeoutError,
    as_dict,
)
from custom_components.hetzner.const import DEFAULT_HOST

from tests.conftest import (
    API_KEY,
    BOX_URL,
    LIST_URL,
    STORAGE_BOX_ID,
    SUBACCOUNT,
    SUBACCOUNTS_URL,
)


def _client(hass: HomeAssistant) -> HetznerClient:
    """Return a client using the mocked shared session."""
    return HetznerClient(async_get_clientsession(hass), API_KEY, DEFAULT_HOST)


def _error_body(code: Any) -> dict[str, Any]:
    """Return an API error body with the given code."""
    return {"error": {"code": code, "message": "message", "details": {}}}


async def test_list_storage_boxes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """The list is returned and the token is sent as bearer token."""
    aioclient_mock.get(LIST_URL, json={"storage_boxes": [storage_box]})

    assert await _client(hass).async_list_storage_boxes() == [storage_box]
    assert aioclient_mock.call_count == 1
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert str(url) == f"{LIST_URL}?page=1&per_page={LIST_PAGE_SIZE}"
    assert headers == {"Authorization": f"Bearer {API_KEY}"}


def _page(boxes: list[Any], next_page: Any) -> dict[str, Any]:
    """Return one page of the storage box list."""
    return {
        "storage_boxes": boxes,
        "meta": {"pagination": {"page": 1, "next_page": next_page}},
    }


def _page_url(page: int) -> str:
    """Return the list URL of one page."""
    return f"{LIST_URL}?page={page}&per_page={LIST_PAGE_SIZE}"


async def test_list_storage_boxes_pages(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """All pages are fetched by following next_page."""
    second = {**storage_box, "id": 2}
    third = {**storage_box, "id": 3}
    aioclient_mock.get(_page_url(1), json=_page([storage_box], 2))
    aioclient_mock.get(_page_url(2), json=_page([second], 3))
    aioclient_mock.get(_page_url(3), json=_page([third], None))

    assert await _client(hass).async_list_storage_boxes() == [
        storage_box,
        second,
        third,
    ]
    assert [str(call[1]) for call in aioclient_mock.mock_calls] == [
        _page_url(1),
        _page_url(2),
        _page_url(3),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"meta": None},
        {"meta": {}},
        {"meta": {"pagination": None}},
        {"meta": {"pagination": {}}},
        {"meta": {"pagination": {"next_page": None}}},
        {"meta": {"pagination": {"next_page": "2"}}},
        {"meta": {"pagination": {"next_page": True}}},
        # Not after the current page: would request the same page forever
        {"meta": {"pagination": {"next_page": 1}}},
        {"meta": {"pagination": {"next_page": 0}}},
    ],
)
async def test_list_storage_boxes_last_page(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Without a usable next_page the first page is the only one."""
    aioclient_mock.get(_page_url(1), json={"storage_boxes": [storage_box], **payload})

    assert await _client(hass).async_list_storage_boxes() == [storage_box]
    assert aioclient_mock.call_count == 1


async def test_list_storage_boxes_empty_page(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """An empty page ends the list even if it names a next page."""
    aioclient_mock.get(_page_url(1), json=_page([storage_box], 2))
    aioclient_mock.get(_page_url(2), json=_page([], 3))

    assert await _client(hass).async_list_storage_boxes() == [storage_box]
    assert aioclient_mock.call_count == 2


async def test_list_storage_boxes_malformed_page(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """A page of only malformed elements does not end the list."""
    second = {**storage_box, "id": 2}
    aioclient_mock.get(_page_url(1), json=_page([None, "box"], 2))
    aioclient_mock.get(_page_url(2), json=_page([second], None))

    assert await _client(hass).async_list_storage_boxes() == [second]
    assert aioclient_mock.call_count == 2


async def test_list_storage_boxes_error_on_later_page(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """An error on a later page is raised, not a partial list returned."""
    aioclient_mock.get(_page_url(1), json=_page([storage_box], 2))
    aioclient_mock.get(_page_url(2), status=503)

    with pytest.raises(HetznerApiError):
        await _client(hass).async_list_storage_boxes()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"storage_boxes": None},
        # A dict would give its keys, a string its characters
        {"storage_boxes": {"id": 1}},
        {"storage_boxes": "boxes"},
        {"storage_boxes": [None, "box", 1, ["box"]]},
    ],
)
async def test_list_storage_boxes_missing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, payload: Any
) -> None:
    """A response without usable boxes is an empty list."""
    aioclient_mock.get(LIST_URL, json=payload)

    assert await _client(hass).async_list_storage_boxes() == []


async def test_get_storage_box(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """One box is returned from its own endpoint."""
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box})

    assert await _client(hass).async_get_storage_box(STORAGE_BOX_ID) == storage_box
    assert str(aioclient_mock.mock_calls[0][1]) == BOX_URL


async def test_list_storage_boxes_skips_malformed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Elements that are no objects are left out, the boxes stay."""
    aioclient_mock.get(LIST_URL, json={"storage_boxes": [None, storage_box, "box", 42]})

    assert await _client(hass).async_list_storage_boxes() == [storage_box]


@pytest.mark.parametrize(
    "payload",
    [{}, {"storage_box": None}, {"storage_box": "box"}, {"storage_box": [1]}],
)
async def test_get_storage_box_missing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, payload: Any
) -> None:
    """A response without a box is an empty dict."""
    aioclient_mock.get(BOX_URL, json=payload)

    assert await _client(hass).async_get_storage_box(STORAGE_BOX_ID) == {}


async def test_list_subaccounts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """All subaccounts come from one request below the box, without paging."""
    other = {**SUBACCOUNT, "id": 43, "name": "sub2"}
    aioclient_mock.get(SUBACCOUNTS_URL, json={"subaccounts": [SUBACCOUNT, other]})

    assert await _client(hass).async_list_subaccounts(STORAGE_BOX_ID) == [
        SUBACCOUNT,
        other,
    ]
    assert aioclient_mock.call_count == 1
    _, url, _, headers = aioclient_mock.mock_calls[0]
    # No page or per_page parameter: the endpoint is not paginated
    assert str(url) == SUBACCOUNTS_URL
    assert headers == {"Authorization": f"Bearer {API_KEY}"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"subaccounts": None},
        {"subaccounts": []},
        {"subaccounts": {"id": 42}},
        {"subaccounts": "sub1"},
        {"subaccounts": [None, "sub1", 42]},
    ],
)
async def test_list_subaccounts_missing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, payload: Any
) -> None:
    """A box without (usable) subaccounts is an empty list."""
    aioclient_mock.get(SUBACCOUNTS_URL, json=payload)

    assert await _client(hass).async_list_subaccounts(STORAGE_BOX_ID) == []


async def test_list_subaccounts_skips_malformed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Elements that are no objects are left out, the subaccounts stay."""
    aioclient_mock.get(SUBACCOUNTS_URL, json={"subaccounts": ["sub", SUBACCOUNT, None]})

    assert await _client(hass).async_list_subaccounts(STORAGE_BOX_ID) == [SUBACCOUNT]


@pytest.mark.parametrize("value", [None, "text", 1, True, ["a"]])
def test_as_dict_non_object(value: Any) -> None:
    """Anything but a JSON object becomes an empty dict."""
    assert as_dict(value) == {}


def test_as_dict_object() -> None:
    """A JSON object is returned as it is."""
    value = {"name": "bx11"}
    assert as_dict(value) is value


@pytest.mark.parametrize(
    ("response", "exc_type"),
    [
        ({"status": 401}, HetznerAuthError),
        ({"status": 403, "json": _error_body("forbidden")}, HetznerAuthError),
        ({"status": 403, "json": _error_body("maintenance")}, HetznerApiError),
        ({"status": 404, "json": _error_body("not_found")}, HetznerNotFoundError),
        ({"status": 500}, HetznerApiError),
        ({"text": "not json"}, HetznerApiError),
        ({"exc": TimeoutError()}, HetznerTimeoutError),
        ({"exc": aiohttp.ClientError("boom")}, HetznerConnectionError),
    ],
)
async def test_list_subaccounts_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    exc_type: type[HetznerError],
) -> None:
    """Listing subaccounts maps errors like the other requests."""
    aioclient_mock.get(SUBACCOUNTS_URL, **response)

    with pytest.raises(exc_type):
        await _client(hass).async_list_subaccounts(STORAGE_BOX_ID)


@pytest.mark.parametrize(
    ("response", "exc_type", "status", "code"),
    [
        (
            {"status": 401, "json": _error_body("unauthorized")},
            HetznerAuthError,
            401,
            "unauthorized",
        ),
        ({"status": 401}, HetznerAuthError, 401, None),
        (
            {"status": 403, "json": _error_body("forbidden")},
            HetznerAuthError,
            403,
            "forbidden",
        ),
        # Without a readable code a 403 is taken as missing permissions
        ({"status": 403}, HetznerAuthError, 403, None),
        (
            {"status": 403, "text": "<html>Forbidden</html>"},
            HetznerAuthError,
            403,
            None,
        ),
        ({"status": 403, "json": ["forbidden"]}, HetznerAuthError, 403, None),
        ({"status": 403, "json": {"error": "forbidden"}}, HetznerAuthError, 403, None),
        ({"status": 403, "json": _error_body(42)}, HetznerAuthError, 403, None),
        # Neither is fixed by a new token
        (
            {"status": 403, "json": _error_body("maintenance")},
            HetznerApiError,
            403,
            "maintenance",
        ),
        (
            {"status": 403, "json": _error_body("resource_limit_exceeded")},
            HetznerApiError,
            403,
            "resource_limit_exceeded",
        ),
        (
            {"status": 404, "json": _error_body("not_found")},
            HetznerNotFoundError,
            404,
            "not_found",
        ),
        (
            {"status": 429, "json": _error_body("rate_limit_exceeded")},
            HetznerApiError,
            429,
            "rate_limit_exceeded",
        ),
        ({"status": 500}, HetznerApiError, 500, None),
        ({"status": 503, "text": "Service Unavailable"}, HetznerApiError, 503, None),
        # Successful status, unusable body
        ({"text": "not json"}, HetznerApiError, 200, "invalid_json"),
        ({"json": ["not", "an", "object"]}, HetznerApiError, 200, "invalid_json"),
    ],
)
async def test_error_responses(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    exc_type: type[HetznerResponseError],
    status: int,
    code: str | None,
) -> None:
    """HTTP errors are mapped by status and error code."""
    aioclient_mock.get(BOX_URL, **response)

    with pytest.raises(exc_type) as exc_info:
        await _client(hass).async_get_storage_box(STORAGE_BOX_ID)

    assert exc_info.value.status == status
    assert exc_info.value.code == code
    expected = f"HTTP {status}" if code is None else f"HTTP {status} ({code})"
    assert str(exc_info.value) == expected


async def test_auth_error_on_list(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Listing maps errors the same way."""
    aioclient_mock.get(LIST_URL, status=401)

    with pytest.raises(HetznerAuthError):
        await _client(hass).async_list_storage_boxes()


@pytest.mark.parametrize(
    ("exc", "exc_type"),
    [
        (TimeoutError(), HetznerTimeoutError),
        (aiohttp.ClientError("boom"), HetznerConnectionError),
        (aiohttp.ClientConnectionError("refused"), HetznerConnectionError),
    ],
)
async def test_connection_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    exc: Exception,
    exc_type: type[HetznerConnectionError],
) -> None:
    """Timeouts and aiohttp errors become connection errors."""
    aioclient_mock.get(BOX_URL, exc=exc)

    with pytest.raises(exc_type) as exc_info:
        await _client(hass).async_get_storage_box(STORAGE_BOX_ID)

    assert exc_info.value.__cause__ is exc


def test_exception_hierarchy() -> None:
    """Callers can catch every client error through the base class."""
    for exc_type in (
        HetznerAuthError,
        HetznerNotFoundError,
        HetznerApiError,
        HetznerConnectionError,
        HetznerTimeoutError,
    ):
        assert issubclass(exc_type, HetznerError)
    assert issubclass(HetznerTimeoutError, HetznerConnectionError)
    assert not issubclass(HetznerAuthError, HetznerApiError)
