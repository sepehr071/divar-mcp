import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_dv_search_filters_apply(client):
    data = await call(
        client,
        "dv_search",
        {"category": "apartment-sell", "min_price": 2_000_000_000, "max_price": 6_000_000_000, "limit": 30, "pages": 2},
    )
    assert data["count"] > 10 and not data.get("ignored")
    tokens = [a["token"] for a in data["ads"]]
    assert len(tokens) == len(set(tokens))
    inside = [a for a in data["ads"] if 2_000_000_000 <= a.get("price", 0) <= 6_000_000_000]
    assert len(inside) >= len(data["ads"]) - 3  # promoted ads can escape the price filter
    assert all(a["url"].startswith("https://divar.ir/v/") and "image" not in a for a in data["ads"][:5])
    with_pics = await call(client, "dv_search", {"category": "light", "limit": 5, "thumbnails": True})
    assert all(a["image"].startswith("https://") for a in with_pics["ads"])


async def test_dv_search_cursor_gives_older_ads(client):
    first = await call(client, "dv_search", {"category": "light", "limit": 5})
    assert first["next_cursor"]
    more = await call(client, "dv_search", {"category": "light", "cursor": first["next_cursor"], "limit": 5})
    assert more["ads"] and not {a["token"] for a in first["ads"]} & {a["token"] for a in more["ads"]}


async def test_dv_search_rent_district_and_brand(client):
    rent = await call(
        client,
        "dv_search",
        {"category": "apartment-rent", "districts": ["ونک"], "max_deposit": 5_000_000_000, "limit": 10, "pages": 2},
    )
    assert rent["applied"]["districts"] == ["ونک"]
    assert rent["count"] > 0 and any("deposit" in a for a in rent["ads"])
    cars = await call(client, "dv_search", {"category": "light", "brand_model": ["پژو 206"], "limit": 5})
    assert cars["applied"]["brand_model"] and cars["count"] > 0
    bad = await client.call_tool("dv_search", {"category": "light", "brand_model": ["zzzz-not-a-car"]})
    assert bad.is_error and "No brand/model" in bad.content[0].text
    exact = await call(client, "dv_search", {"category": "light", "brand_model": ["Peugeot 206"], "limit": 1})
    assert exact["applied"]["brand_model"] == ["Peugeot 206"]


async def test_dv_count(client):
    all_cars = await call(client, "dv_count", {"category": "light"})
    ev = await call(client, "dv_count", {"category": "light", "filters": {"fuel_type": ["ev"]}})
    assert all_cars["count"] >= 1000 and 0 < ev["count"] < all_cars["count"]


async def test_dv_cheapest_and_best_for_budget(client):
    cheap = await call(client, "dv_cheapest", {"category": "mobile-phones", "query": "iphone", "limit": 5, "pages": 2})
    prices = [a["price"] for a in cheap["ads"]]
    assert prices and prices == sorted(prices) and cheap["scanned"] > 20
    by_sort = await call(
        client, "dv_search", {"category": "mobile-phones", "query": "iphone", "sort": "cheapest", "pages": 2}
    )
    ranked = [a for a in by_sort["ads"] if not a.get("suspect_price")]
    assert ranked and ranked[0]["price"] == min(a["price"] for a in ranked)  # junk prices are flagged and last
    assert all(a.get("suspect_price") for a in by_sort["ads"][len(ranked) :])
    best = await call(
        client,
        "dv_best_for_budget",
        {"budget": 300_000_000, "category": "mobile-phones", "query": "iphone", "pages": 2},
    )
    assert best["within_budget"] > 0 and all(a["price"] <= 300_000_000 for a in best["closest_to_budget"])
    assert best["market_median"] > 0 and 0 < best["budget_median"] <= 300_000_000
    scores = [a["deal_score"] for a in best["best_value"]]
    assert scores == sorted(scores, reverse=True) and all(a["reasons"] for a in best["best_value"])


async def test_dv_new_since(client):
    data = await call(client, "dv_new_since", {"category": "mobile-phones", "hours": 3, "limit": 10})
    assert data["count"] > 0 and all(a["age_h"] <= 3.1 for a in data["ads"])
    nxt = await call(client, "dv_new_since", {"category": "mobile-phones", "since": data["latest"], "limit": 10})
    assert not {a["token"] for a in data["ads"]} & {a["token"] for a in nxt["ads"]}


async def test_dv_export(client):
    csv_out = await call(client, "dv_export", {"category": "mobile-phones", "limit": 10, "pages": 1})
    lines = csv_out["data"].splitlines()
    assert lines[0].startswith("token,title,price") and len(lines) == 11
    jl = await call(client, "dv_export", {"category": "mobile-phones", "limit": 3, "format": "jsonl", "pages": 1})
    assert len(jl["data"].splitlines()) == 3


async def test_dv_nearby(client):
    data = await call(
        client,
        "dv_nearby",
        {"lat": 35.7607, "lon": 51.405, "radius_m": 1500, "category": "apartment-rent", "limit": 10},
    )
    assert data["total_in_area"] > 0 and data["ads"]
    dists = [a["distance_m"] for a in data["ads"]]
    assert dists == sorted(dists) and all("lat" in a for a in data["ads"])


async def test_dv_nearby_sale_price_is_the_total(client):
    no_category = await client.call_tool("dv_nearby", {"lat": 35.7607, "lon": 51.405})
    assert no_category.is_error
    data = await call(
        client, "dv_nearby", {"lat": 35.7607, "lon": 51.405, "radius_m": 1500, "category": "apartment-sell", "limit": 5}
    )
    assert data["total_in_area"] > 0 and data["ads"]
    pin = next(a for a in data["ads"] if a.get("price") and a.get("price_per_m2"))
    assert pin["price"] >= 20 * pin["price_per_m2"]  # a total, not the per-metre chip (a flat has 20+ m2)
    post = await call(client, "dv_post", {"token": pin["token"], "max_images": 0})
    assert abs(post["price"] - pin["price"]) <= pin["price"] * 0.05 and post["price"] % 1000 == 0  # no float32 noise


async def test_province_scope_finds_more_than_the_city(client):
    city = await call(client, "dv_count", {"city": "کرج", "category": "light"})
    province = await call(client, "dv_count", {"city": "استان کرج", "category": "light"})
    assert province["count"] >= city["count"] > 0


async def test_dv_watch_and_price_trend(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DIVAR_MCP_STATE", str(tmp_path / "state.json"))
    made = await call(client, "dv_watch", {"action": "create", "category": "mobile-phones", "query": "iphone"})
    checked = await call(client, "dv_watch", {"action": "check", "watch_id": made["id"]})
    assert checked["checked"] == 1 and checked["watches"][0]["name"]
    trend = await call(client, "dv_price_trend", {"category": "mobile-phones", "query": "iphone 13", "pages": 1})
    assert trend["today"]["median"] > 0 and trend["points"] == 1
