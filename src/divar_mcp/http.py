"""Shared async HTTP client for the Divar API.

Divar serves everything from one JSON gateway, `https://api.divar.ir/v8`. No token, cookie or header is needed for
public reads. Every tool goes through `api`. `_send` caps concurrency, retries (up to MAX_RETRIES, jittered backoff,
Retry-After honoured) when the server drops the connection, times out, answers 200 with an empty body, 429 or 5xx, and
turns HTTP failures into `ToolError` messages the model can act on. A 403 is never retried. Divar answers an unknown
path or a wrong HTTP method with `403 RBAC: access denied` (not 404). Every call here is a read-only query, even the POSTs.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import time
from typing import Any

import httpx
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

API = "https://api.divar.ir/v8"
SITE = "https://divar.ir"

HEADERS = {
    # User-Agent, sec-ch-ua and TLS come from the impersonated Chrome profile (see _get_client).
    "Accept": "application/json",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Origin": SITE,
    "Referer": f"{SITE}/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "priority": "u=1, i",
}

MAX_CONCURRENCY = 2
JITTER = (0.15, 0.6)  # seconds before each call
RETRY_DELAY = 0.5
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10  # cap for Retry-After and backoff, seconds

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_browser_lock: asyncio.Lock | None = None
_cleared: tuple[httpx.AsyncClient, float] | None = None


class _XhrTransport(AsyncCurlTransport):
    """The Chrome profile sends navigation-only headers; a None value makes curl drop them (API calls are XHR)."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        p = super()._create_request_params(req)
        p["headers"] = [*p["headers"], (b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        return p


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, or None for network errors."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit, _proxy_index, _browser_lock, _cleared
    _transport, _client, _limit, _proxy_index, _browser_lock, _cleared = transport, None, None, 0, None, None


def _proxies() -> list[str]:
    """DIVAR_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("DIVAR_MCP_PROXY", "").split(",") if p.strip()]


# The gateway's plain 403 ("RBAC: access denied") is not a challenge; only a bot-check page is.
_CHALLENGE = re.compile(r"just a moment|captcha|challenge|arvan|attention required|enable javascript", re.I)


def _is_challenge(r: httpx.Response) -> bool:
    """A bot-check page: 403/429/503 with an HTML body that carries a known challenge marker."""
    return (
        r.status_code in (403, 429, 503)
        and "html" in r.headers.get("content-type", "") + r.text[:20].lower()
        and bool(_CHALLENGE.search(r.text[:5000]))
    )


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client.

    Only the client that failed may rotate: a slower request that failed on an old client must not skip a good proxy.
    The old client is closed late because other requests may still be using it.
    """
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(60)  # longer than the 40 s request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Divar answers direct connections fine; a proxy is only used when the user opts in with DIVAR_MCP_PROXY.
        # curl_cffi presents Chrome's TLS/HTTP2 fingerprint and default headers; cookies stay with httpx. The proxy
        # goes to the transport (a client-level proxy would be ignored once a transport is given).
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _XhrTransport(
                impersonate="chrome",
                default_headers=True,
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=40,
            follow_redirects=False,  # the API never redirects; do not follow one to another host
            trust_env=False,
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
    """Timeout, network error, empty 200, 5xx and 429 are retried with jittered backoff or Retry-After; 403 never."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await _get(method, url, **kwargs)
        except ApiError as e:
            if attempt == MAX_RETRIES or (e.status is not None and e.status < 500 and e.status != 429):
                raise
            wait = e.retry_after if e.retry_after is not None else RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
    raise AssertionError("unreachable")


async def _once(
    client: httpx.AsyncClient, limit: asyncio.Semaphore, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    try:
        async with limit:
            await asyncio.sleep(random.uniform(*JITTER))
            return await client.request(method, url, **kwargs)
    except httpx.TimeoutException as e:
        raise ApiError("divar.ir did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach divar.ir ({type(e).__name__}). Check the internet connection, or set DIVAR_MCP_PROXY."
        ) from e


async def _browser_cookies(proxy: str | None) -> list[dict[str, Any]]:
    """Open the home page in headless patchright, wait for the challenge to clear, return the cookies."""
    try:
        from patchright.async_api import async_playwright
    except ImportError as e:
        raise ApiError(
            "Divar served a bot-check page. Install the browser extra (`pip install divar-mcp[browser]`, or "
            "`uvx --with patchright==1.63.0 divar-mcp`) and run `patchright install chromium`.",
            403,
        ) from e
    u = httpx.URL(proxy) if proxy else None
    opts = u and {"server": f"{u.scheme}://{u.host}:{u.port}", "username": u.username, "password": u.password}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=opts)
        try:
            page = await browser.new_page()
            await page.goto(SITE, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_function(
                "!/just a moment|access denied|attention required|arvancloud/i.test(document.title)", timeout=30_000
            )
            return await browser.contexts[0].cookies()
        finally:
            await browser.close()


async def _clear_challenge(client: httpx.AsyncClient) -> None:
    """Rung 2: let a real browser pass the bot check once and share its cookies with the httpx jar."""
    global _browser_lock, _cleared
    _browser_lock = _browser_lock or asyncio.Lock()
    async with _browser_lock:  # one browser at a time; callers that waited reuse the cookies just fetched
        if _cleared and _cleared[0] is client and time.monotonic() - _cleared[1] < 30:
            return
        proxies = _proxies()
        try:
            cookies = await _browser_cookies(proxies[_proxy_index % len(proxies)] if proxies else None)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(f"Divar bot check was not cleared by the browser ({type(e).__name__}).", 403) from e
        for c in cookies:
            client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        _cleared = (client, time.monotonic())


async def _get(method: str, url: str, **kwargs: Any) -> httpx.Response:
    client, limit = _get_client()
    r = await _once(client, limit, method, url, **kwargs)
    if _is_challenge(r):
        _next_proxy(client)
        client, limit = _get_client()
        await _clear_challenge(client)
        r = await _once(client, limit, method, url, **kwargs)  # one retry; a second block is final
        if _is_challenge(r):
            _next_proxy(client)
    if r.status_code == 200 and not r.content:
        raise ApiError("divar.ir returned an empty response. Try again in a moment.", 502)
    if r.status_code >= 400:
        if r.status_code == 429:
            _next_proxy(client)
        raise ApiError(_status_message(r), r.status_code, _retry_after(r))
    return r


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After on a 429/503, else None (HTTP-date form is ignored)."""
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


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
