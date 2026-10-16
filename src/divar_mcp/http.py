"""Shared async HTTP client for the Divar API.

Divar serves everything from one JSON gateway, `https://api.divar.ir/v8`. No token, cookie or header is needed for
public reads. Every tool goes through `api`. `_send` caps concurrency, retries once (after a fixed 0.5 s) when the
server drops the connection or answers 200 with an empty body, or 502/503/504, and turns HTTP failures into
`ToolError` messages the model can act on. Divar answers an unknown path or a wrong HTTP method with
`403 RBAC: access denied` (not 404). Every call here is a read-only query, even the POSTs.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

API = "https://api.divar.ir/v8"
SITE = "https://divar.ir"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0",
    "Accept": "application/json",
}

MAX_CONCURRENCY = 4
RETRY_DELAY = 0.5  # seconds, fixed
RETRY_STATUS = (502, 503, 504)

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None


class ApiError(ToolError):
    """A failed upstream call."""


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit
    _transport, _client, _limit = transport, None, None


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Divar answers direct connections fine; a proxy is only used when the user opts in with DIVAR_MCP_PROXY.
        _client = httpx.AsyncClient(
            transport=_transport,
            headers=HEADERS,
            timeout=40,
            follow_redirects=False,  # the API never redirects; do not follow one to another host
            trust_env=False,
            proxy=os.environ.get("DIVAR_MCP_PROXY") or None,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


async def api(path: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET `path` (or POST the JSON `body`; every Divar POST used here is a read-only query) and return the parsed
    JSON object."""
    r = await _send("POST" if body is not None else "GET", f"{API}{path}", json=body)
    try:
        data = r.json()
    except ValueError as e:
        raise ApiError(f"divar.ir returned a non-JSON response for {path} (HTTP {r.status_code}).") from e
    return data if isinstance(data, dict) else {}


async def _send(method: str, url: str, **kwargs: Any) -> httpx.Response:
    client, limit = _get_client()
    for attempt in (1, 2):
        try:
            async with limit:
                r = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            raise ApiError("divar.ir did not answer in time. Try again in a moment.") from e
        except httpx.TransportError as e:
            if attempt == 2:
                raise ApiError(
                    f"Could not reach divar.ir ({type(e).__name__}). Check the internet connection, "
                    "or set DIVAR_MCP_PROXY."
                ) from e
            await asyncio.sleep(RETRY_DELAY)
            continue
        if attempt == 1 and ((r.status_code == 200 and not r.content) or r.status_code in RETRY_STATUS):
            await asyncio.sleep(RETRY_DELAY)
            continue
        break
    if r.status_code >= 400:
        raise ApiError(_status_message(r))
    return r


def _status_message(r: httpx.Response) -> str:
    code = r.status_code
    try:
        said = str(r.json().get("message") or "")[:200]
    except (ValueError, AttributeError):
        said = ""
    if code == 400:
        return f"divar.ir rejected the parameters (HTTP 400){': ' + said if said else ''}. Check the category slug and filters."
    if code == 403:
        return "divar.ir refused the request (HTTP 403). Turn off VPN/proxy or set DIVAR_MCP_PROXY."
    if code == 404:
        return f"Not found on divar.ir (HTTP 404){': ' + said if said else ''}. The ad may have been removed."
    if code == 429:
        return "divar.ir is rate limiting requests (HTTP 429). Wait a minute before retrying."
    if code >= 500:
        return f"divar.ir had a server error (HTTP {code}). Try again later."
    return f"divar.ir rejected the request (HTTP {code})."
