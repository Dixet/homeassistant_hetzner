"""Minimal async client for the Hetzner Storage Box API.

Kept free of Home Assistant imports: callers pass in their aiohttp session
and map the exceptions below to config flow errors or coordinator failures.
"""

from typing import Any

import aiohttp

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
# Largest page size the API accepts (default 25)
LIST_PAGE_SIZE = 50


class HetznerError(Exception):
    """Base class of all errors raised by the client."""


class HetznerConnectionError(HetznerError):
    """The API could not be reached."""


class HetznerTimeoutError(HetznerConnectionError):
    """The API did not answer in time."""


class HetznerResponseError(HetznerError):
    """The API answered with an error or an unusable response."""

    def __init__(self, status: int, code: str | None = None) -> None:
        """Store the HTTP status and the API's error code, if any."""
        detail = f"HTTP {status}"
        if code:
            detail = f"{detail} ({code})"
        super().__init__(detail)
        self.status = status
        self.code = code


class HetznerAuthError(HetznerResponseError):
    """The API token was rejected or lacks permissions."""


class HetznerNotFoundError(HetznerResponseError):
    """The resource does not exist or is not visible to this token."""


class HetznerApiError(HetznerResponseError):
    """Any other error, e.g. maintenance, rate limit or server errors."""


class HetznerClient:
    """Read-only access to the storage boxes of one Hetzner project."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str, host: str) -> None:
        """Use the caller's session for requests to host with api_key."""
        self._session = session
        self._api_key = api_key
        self._host = host

    async def async_list_storage_boxes(self) -> list[dict[str, Any]]:
        """Return the storage boxes visible to the API token.

        The list is paginated: pages are requested until meta.pagination has
        no next_page. A response without meta counts as the only page.
        """
        storage_boxes: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self._async_get(
                "storage_boxes", {"page": page, "per_page": LIST_PAGE_SIZE}
            )
            page_items = data.get("storage_boxes")
            storage_boxes.extend(_dict_items(page_items))
            next_page = _next_page(data)
            # An empty page or a next_page not after this one would never end.
            # A page of only malformed elements is not empty: the next one
            # may still hold boxes.
            if not page_items or next_page is None or next_page <= page:
                return storage_boxes
            page = next_page

    async def async_get_storage_box(self, storage_box_id: str) -> dict[str, Any]:
        """Return one storage box."""
        data = await self._async_get(f"storage_boxes/{storage_box_id}")
        return as_dict(data.get("storage_box"))

    async def async_list_subaccounts(self, storage_box_id: str) -> list[dict[str, Any]]:
        """Return all subaccounts of one storage box.

        Unlike the storage box list, this endpoint has no page/per_page
        parameters and no meta.pagination: one response holds all of them.
        """
        data = await self._async_get(f"storage_boxes/{storage_box_id}/subaccounts")
        return _dict_items(data.get("subaccounts"))

    async def _async_get(
        self, path: str, params: dict[str, int] | None = None
    ) -> dict[str, Any]:
        """GET a path below /v1 and return the decoded JSON object."""
        try:
            async with self._session.get(
                f"https://{self._host}/v1/{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    raise _error_for_status(resp.status, await _async_error_code(resp))
                try:
                    data = await resp.json()
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise HetznerApiError(resp.status, "invalid_json") from err
        except TimeoutError as err:
            raise HetznerTimeoutError("Timeout connecting to the Hetzner API") from err
        except aiohttp.ClientError as err:
            raise HetznerConnectionError(
                f"Error connecting to the Hetzner API: {err}"
            ) from err

        if not isinstance(data, dict):
            raise HetznerApiError(200, "invalid_json")
        return data


def as_dict(value: Any) -> dict[str, Any]:
    """Return a JSON object of an API response, empty for anything else.

    Guards nested objects (e.g. storage_box_type, location) against null or
    malformed values, so callers can use .get() on the result.
    """
    return value if isinstance(value, dict) else {}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """Return the JSON objects of a list in an API response.

    Anything but a list counts as empty, elements that are no objects are
    left out: callers read every element with .get().
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _next_page(data: dict[str, Any]) -> int | None:
    """Return meta.pagination.next_page of a list response, if there is one."""
    meta = data.get("meta")
    pagination = meta.get("pagination") if isinstance(meta, dict) else None
    if not isinstance(pagination, dict):
        return None
    next_page = pagination.get("next_page")
    # bool is an int subclass but never a page number
    if isinstance(next_page, bool) or not isinstance(next_page, int):
        return None
    return next_page


async def _async_error_code(resp: aiohttp.ClientResponse) -> str | None:
    """Return error.code of an error response, or None if there is none.

    Proxies and load balancers may answer with HTML or an empty body.
    """
    try:
        body = await resp.json(content_type=None)
    except aiohttp.ClientError, ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def _error_for_status(status: int, code: str | None) -> HetznerResponseError:
    """Map an HTTP error status and error code to an exception."""
    if status == 401:
        return HetznerAuthError(status, code)
    # 403 also covers maintenance and resource limits, which a new token
    # would not fix; without a readable code assume missing permissions
    if status == 403 and code in (None, "forbidden"):
        return HetznerAuthError(status, code)
    # The API cannot tell a deleted box from one of another project
    if status == 404:
        return HetznerNotFoundError(status, code)
    return HetznerApiError(status, code)
