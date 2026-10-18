import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from mcp import Client

from divar_mcp import core, http, search
from divar_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 16, 2, 0, 0, tzinfo=timezone.utc)  # fixtures were recorded just before this moment


def fixture(name: str):
    """A recorded (trimmed) response, parsed JSON."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def without_rows(data: dict, *titles: str) -> dict:
    """An ad response with the spec rows of these titles removed (a seller who typed no price has no price row)."""
    for sec in data["sections"]:
        sec["widgets"] = [w for w in sec["widgets"] if w.get("data", {}).get("title") not in titles]
    return data


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch, request):
    """The shared httpx client is bound to one event loop; each test gets a new loop, caches and a frozen clock."""
    http.set_transport(None)
    core._CACHE.clear()
    http.RETRY_DELAY = 0
    monkeypatch.setattr(http, "JITTER", (0, 0))
    if "live" not in request.keywords:
        monkeypatch.setattr(core, "now", lambda: NOW)
        monkeypatch.setattr(search, "now", lambda: NOW)
    yield
    http.set_transport(None)


@pytest.fixture
def api():
    """Route table for a fake Divar API.

    Keys are decoded URL paths ("/v8/postlist/w/search"). Values: a dict/list (JSON) or a callable(request) ->
    httpx.Response. Unknown paths return 403 with the gateway's text, like the real one. Every request is appended
    to api.calls.
    """

    class Routes(dict):
        calls: list[httpx.Request]

        def body(self, i: int = -1) -> dict:
            return json.loads(self.calls[i].content)

        def paths(self) -> list[str]:
            return [c.url.path for c in self.calls]

    routes = Routes()
    routes.calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.calls.append(request)
        target = routes.get(request.url.path)
        if target is None:
            return httpx.Response(403, text="RBAC: access denied")
        if callable(target):
            return target(request)
        return httpx.Response(200, json=target)

    http.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
def ref(api):
    """Routes for the reference data every search needs: cities, categories, filter schemas, districts, brands."""
    api["/v8/places/cities"] = fixture("cities.json")
    api["/v8/postlist/w/categories"] = fixture("categories.json")
    api["/v8/places/cities/1/districts"] = fixture("districts.json")

    def filters(request):
        slug = json.loads(request.content)["data"]["form_data"]["data"]["category"]["str"]["value"]
        name = {"light": "filters_light.json", "apartment-rent": "filters_rent.json"}.get(slug, "filters_light.json")
        return httpx.Response(200, json=fixture(name))

    api["/v8/postlist/w/filters"] = filters
    api["/v8/w/lazy-multi-select-hierarchy-options"] = fixture("options_brand.json")
    api["/v8/fields-search"] = lambda r: httpx.Response(
        200,
        json=fixture(
            "fields_search_districts.json"
            if json.loads(r.content)["field"] == "districts"
            else "fields_search_brand.json"
        ),
    )
    return api


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        call_tool = c.call_tool

        async def parsed(*args, **kwargs):
            result = await call_tool(*args, **kwargs)
            if not result.is_error:
                result.structured_content = json.loads(result.content[0].text)
            return result

        c.call_tool = parsed
        yield c
