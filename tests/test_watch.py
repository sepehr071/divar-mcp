import json
from datetime import datetime, timezone

import httpx
import pytest
from conftest import fixture

from divar_mcp import core
from divar_mcp.search import deal_score

pytestmark = pytest.mark.anyio

SEARCH = "/v8/postlist/w/search"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("DIVAR_MCP_STATE", str(tmp_path / "state.json"))
    return tmp_path / "state.json"


def stored(state):
    return json.loads(state.read_text(encoding="utf-8"))


async def test_watch_create_check_list_delete(client, ref, state):
    ref[SEARCH] = fixture("search_light.json")
    made = (
        await client.call_tool("dv_watch", {"action": "create", "name": "cars", "category": "light"})
    ).structured_content
    wid = made["id"]
    assert made["mark"] == "2026-10-16T02:00:00Z" and stored(state)["watches"][wid]
    listed = (await client.call_tool("dv_watch", {"action": "list"})).structured_content
    assert listed["count"] == 1 and listed["watches"][0]["name"] == "cars"
    data = stored(state)
    data["watches"][wid]["mark"] = "2026-10-16T01:40:00Z"  # as if created earlier, so the fixture ads are new
    state.write_text(json.dumps(data), encoding="utf-8")
    checked = (await client.call_tool("dv_watch", {"action": "check", "watch_id": wid})).structured_content
    assert checked["new_ads"] == 4 and checked["watches"][0]["ads"][0]["token"] == "gaRPIP2m"
    again = (await client.call_tool("dv_watch", {"action": "check"})).structured_content
    assert again["new_ads"] == 0  # the mark moved to the newest ad
    gone = (await client.call_tool("dv_watch", {"action": "delete", "watch_id": wid})).structured_content
    assert gone["deleted"] == wid and stored(state)["watches"] == {}
    missing = await client.call_tool("dv_watch", {"action": "check", "watch_id": "nope"})
    assert missing.is_error and "No watch" in missing.content[0].text


async def test_watch_create_rejects_unknown_category(client, ref, state):
    result = await client.call_tool("dv_watch", {"action": "create", "category": "nope"})
    assert result.is_error and not state.exists()


async def test_price_trend_records_one_point_per_day(client, ref, state, monkeypatch):
    ref[SEARCH] = fixture("search_light.json")
    first = (await client.call_tool("dv_price_trend", {"category": "light", "pages": 1})).structured_content
    assert first["today"]["date"] == "2026-10-16" and first["points"] == 1 and "change_pct" not in first
    again = (await client.call_tool("dv_price_trend", {"category": "light", "pages": 1})).structured_content
    assert again["points"] == 1  # the same UTC day replaces the point
    monkeypatch.setattr(core, "now", lambda: datetime(2026, 10, 17, 2, 0, 0, tzinfo=timezone.utc))
    tomorrow = (await client.call_tool("dv_price_trend", {"category": "light", "pages": 1})).structured_content
    assert tomorrow["points"] == 2 and tomorrow["change_pct"] == 0.0
    dry = (
        await client.call_tool("dv_price_trend", {"category": "light", "pages": 1, "record": False})
    ).structured_content
    assert dry["points"] == 1 and len(stored(state)["history"]) == 1


def test_deal_score_explains_itself():
    ad = {"title": "فروش", "price": 100, "photos": 5, "age_h": 2, "chat": True}
    good = deal_score(ad, 200)
    assert good["deal_score"] == 90 and good["below_median_pct"] == 50 and len(good["reasons"]) == 4
    assert "last bumped" in good["reasons"][2] and "(+5)" in good["reasons"][2]
    old = deal_score({**ad, "published_h": 2900}, 200)  # bumped 2 h ago, first published four months ago
    assert old["deal_score"] == 85 and "re-bumped" in old["reasons"][2]
    bait = deal_score({**ad, "price": 10}, 200)
    assert bait["too_good"] is True and bait["deal_score"] < 40 and "bait" in bait["reasons"][0]
    coop = deal_score({**ad, "seller": "کارمند تعاونی مسکن"}, 200)
    assert coop["deal_score"] == 50 and "co-op" in coop["reasons"][-1]
    assert deal_score({**ad, "seller": "تعاونی"}, 200, "تعاونی")["deal_score"] == 90  # the user asked for it


async def test_status_resources_and_prompts(client, api, state):
    api["/v8/places/find"] = fixture("find.json")
    api["/v8/places/cities"] = fixture("cities.json")
    api["/v8/postlist/w/categories"] = fixture("categories.json")
    status = (await client.call_tool("dv_status", {})).structured_content
    assert status["divar"]["ok"] is True and status["watches"] == 0
    api["/v8/places/find"] = lambda r: httpx.Response(403, text="RBAC: access denied")
    bad = (await client.call_tool("dv_status", {})).structured_content
    assert bad["divar"]["ok"] is False and "error" in bad["divar"]
    uris = {str(r.uri) for r in (await client.list_resources()).resources}
    assert {"divar://cities", "divar://categories"} <= uris
    cities = json.loads((await client.read_resource("divar://cities")).contents[0].text)
    assert cities["columns"][0] == "id" and cities["rows"][0][1] == "تهران"
    prompts = {p.name for p in (await client.list_prompts()).prompts}
    assert {"compare_ads", "best_under_budget", "appraise_listing", "watch_market"} <= prompts
