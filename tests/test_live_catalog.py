import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_dv_categories(client):
    top = await call(client, "dv_categories", {})
    assert top["total_categories"] > 200 and "real-estate" in [c["slug"] for c in top["categories"]]
    found = await call(client, "dv_categories", {"query": "اجاره آپارتمان"})
    assert "apartment-rent" in [c["slug"] for c in found["categories"]]


async def test_dv_filters(client):
    data = await call(client, "dv_filters", {"category": "apartment-sell"})
    keys = {f["key"] for f in data["filters"]}
    assert {"price", "size", "rooms", "districts"} <= keys
    cars = await call(client, "dv_filters", {"category": "car"})
    assert cars["category"] == "light" and "brand_model" in {f["key"] for f in cars["filters"]}


async def test_dv_filter_options(client):
    data = await call(client, "dv_filter_options", {"category": "light", "query": "سمند"})
    assert data["options"] and data["options"][0]["value"].startswith("Samand")
    brands = await call(client, "dv_filter_options", {"category": "light", "limit": 5})
    assert brands["total"] > 100 and brands["options"][0]["value"] and brands["options"][0]["label"]
    models = await call(client, "dv_filter_options", {"category": "light", "brand": "Peugeot"})
    assert models["count"] > 3


async def test_dv_suggest(client):
    data = await call(client, "dv_suggest", {"query": "پژو"})
    assert data["suggestions"] and data["suggestions"][0]["category"] == "light"
    assert "light" in [c["category"] for c in data["categories"]]


async def test_dv_parse_query_and_boundary(client):
    data = await call(client, "dv_parse_query", {"text": "آپارتمان اجاره ونک تهران زیر ۸ میلیارد"})
    assert data["search"]["city"] == "تهران" and data["search"]["districts"] == ["ونک"]
    assert data["search"]["max_price"] == 8_000_000_000 and data["search"]["category"] == "apartment-rent"
    found = await call(client, "dv_search", {**data["search"], "limit": 3})
    assert found["count"] > 0 and not found.get("ignored")
    area = await call(client, "dv_boundary", {"city": "تهران", "district": "ونک"})
    lon0, lat0, lon1, lat1 = area["bbox"]
    assert 51 < lon0 < lon1 < 52 and 35 < lat0 < lat1 < 36
    assert (await call(client, "dv_boundary", {"city": "تهران"}))["total_districts"] > 300


async def test_dv_status(client):
    data = await call(client, "dv_status", {})
    assert data["divar"]["ok"] is True and data["divar"]["latency_ms"] > 0
