import json
import re
from pathlib import Path

import httpx
import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio

CITIES = "/v8/places/cities"


async def test_all_tools_are_read_only(client):
    tools = (await client.list_tools()).tools
    assert len(tools) == 31
    for t in tools:
        assert t.name.startswith("dv_"), t.name
        local = t.name in ("dv_watch", "dv_price_trend")  # these two write only the local state file
        assert t.annotations.read_only_hint is not local and t.annotations.destructive_hint is False, t.name
        assert t.description and t.title, t.name


async def test_readme_tool_tables_match_the_server(client):
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    listed = re.findall(r"^\| `(dv_\w+)` \|", readme, re.M)
    assert sorted(listed) == sorted(t.name for t in (await client.list_tools()).tools)


async def test_dropped_connection_is_retried_once(client, api):
    attempts = []

    def flaky(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request=request)
        return httpx.Response(200, json=fixture("cities.json"))

    api[CITIES] = flaky
    result = await client.call_tool("dv_cities", {"query": "مشهد"})
    assert not result.is_error and result.structured_content["cities"][0]["id"] == 3
    assert len(attempts) == 2


async def test_empty_200_body_is_retried_once(client, api):
    replies = iter([httpx.Response(200), httpx.Response(200, json=fixture("cities.json"))])
    api[CITIES] = lambda r: next(replies)
    result = await client.call_tool("dv_cities", {})
    assert not result.is_error and len(api.calls) == 2


async def test_network_error_after_retry(client, api):
    def down(request):
        raise httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING]", request=request)

    api[CITIES] = down
    result = await client.call_tool("dv_cities", {})
    assert result.is_error and "Could not reach divar.ir (ConnectError)" in result.content[0].text
    assert len(api.calls) == 2


async def test_timeout_is_not_retried(client, api):
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    api[CITIES] = slow
    result = await client.call_tool("dv_cities", {})
    assert result.is_error and "did not answer in time" in result.content[0].text and len(api.calls) == 1


@pytest.mark.parametrize(
    ("status", "text"),
    [(404, "HTTP 404"), (429, "rate limiting"), (500, "server error (HTTP 500)"), (403, "refused"), (400, "rejected")],
)
async def test_http_errors_are_actionable(client, api, status, text):
    api[CITIES] = lambda r: httpx.Response(status)
    result = await client.call_tool("dv_cities", {})
    assert result.is_error and text in result.content[0].text


async def test_api_message_is_passed_on(client, api):
    api[CITIES] = lambda r: httpx.Response(400, json={"code": 3, "message": "invalid category: x"})
    result = await client.call_tool("dv_cities", {})
    assert result.is_error and "invalid category: x" in result.content[0].text


async def test_non_json_reply(client, api):
    api[CITIES] = lambda r: httpx.Response(200, text="<html>oops</html>")
    result = await client.call_tool("dv_cities", {})
    assert result.is_error and "non-JSON" in result.content[0].text


async def test_browser_user_agent_and_no_env_proxy(client, api, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")  # must be ignored (trust_env=False)
    api[CITIES] = fixture("cities.json")
    result = await client.call_tool("dv_cities", {})
    assert not result.is_error and "Chrome" in api.calls[0].headers["User-Agent"]
    assert api.calls[0].method == "GET" and api.calls[0].url.host == "api.divar.ir"


async def test_post_503_is_retried_once(client, api):
    replies = iter([httpx.Response(503), httpx.Response(200, json=fixture("count.json"))])
    api["/v8/postlist/w/approximate-post-count"] = lambda r: next(replies)
    api[CITIES] = fixture("cities.json")
    result = await client.call_tool("dv_count", {})
    assert not result.is_error and result.structured_content["count"] == 2147


async def test_redirects_are_not_followed(client, api):
    api[CITIES] = lambda r: httpx.Response(302, headers={"location": "https://evil.example/x"})
    result = await client.call_tool("dv_cities", {})
    assert result.is_error and len(api.calls) == 1


async def test_raw_client_one_text_block_no_structured_content_no_output_schema(api):
    from mcp import Client

    from divar_mcp.server import mcp

    api[CITIES] = fixture("cities.json")
    async with Client(mcp) as raw:  # the `client` fixture patches call_tool; this one does not
        tools = (await raw.list_tools()).tools
        assert all(t.output_schema is None for t in tools)
        assert all('"title"' not in json.dumps(t.input_schema) for t in tools)
        result = await raw.call_tool("dv_cities", {})
    assert len(result.content) == 1 and result.content[0].type == "text" and result.structured_content is None


def test_rows_trim_ad_cards_only():
    from divar_mcp.server import _rows

    ad = {
        "token": "gaX",
        "title": "t",
        "image": "https://i",
        "posted": "2026-10-16T00:00:00Z",
        "age_h": 1.0,
        "chat": False,
    }
    out = _rows({"ads": [ad], "image": "kept", "empty": []}, thumbnails=False)
    assert out == {"ads": [{"token": "gaX", "title": "t", "age_h": 1.0}], "image": "kept", "empty": []}
    assert _rows({"ads": [ad]}, thumbnails=True)["ads"][0]["image"] == "https://i"


async def test_schemas_are_flat_and_untitled(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    props = tools["dv_search"].input_schema["properties"]
    assert props["min_price"] == {"minimum": 0, "type": "integer", "description": props["min_price"]["description"]}
    assert all("title" not in v and "anyOf" not in v for v in props.values())
    assert not tools["dv_search"].description.startswith(" ") and "\n    " not in tools["dv_search"].description
