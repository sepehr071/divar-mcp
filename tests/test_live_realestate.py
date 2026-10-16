import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_parse_query_real_estate_text_runs_in_dv_search(client):
    parsed = await call(client, "dv_parse_query", {"text": "آپارتمان ۸۰ متری در ونک تا ۵ میلیارد"})
    s = parsed["search"]
    assert s["category"] == "apartment-sell" and s["districts"] == ["ونک"] and s["max_price"] == 5_000_000_000
    assert s["filters"]["size"] == {"min": 72, "max": 88} and "query" not in s
    found = await call(client, "dv_search", {**s, "limit": 5})
    assert not found.get("ignored") and found["applied"]["districts"] == ["ونک"]
    rent = await call(client, "dv_parse_query", {"text": "رهن و اجاره آپارتمان ونک ودیعه ۵۰۰ میلیون"})
    assert rent["search"]["category"] == "apartment-rent" and rent["search"]["max_deposit"] == 500_000_000
    assert "query" not in rent["search"] and "max_price" not in rent["search"]


async def test_best_for_budget_and_find_deals_rank_flats_per_metre(client):
    args = {"category": "apartment-sell", "districts": ["ونک", "پونک"], "pages": 2}
    best = await call(client, "dv_best_for_budget", {"budget": 60_000_000_000, **args})
    assert best["ranked_by"] == "price_per_m2" and best["median_price_per_m2"] > 0
    assert all(a["price_per_m2"] and a["area"] for a in best["best_value"])
    assert not any("قدرالسهم" in a["title"] or "وامدار" in a["title"] for a in best["best_value"])
    deals = await call(client, "dv_find_deals", args)
    assert deals["ranked_by"] == "price_per_m2" and all(a["below_median_pct"] >= 15 for a in deals["deals"])


async def test_search_web_url_and_outlier_flag(client):
    data = await call(
        client,
        "dv_search",
        {"category": "apartment-sell", "districts": ["ونک"], "max_price": 5_000_000_000, "limit": 3},
    )
    assert data["web_url"].startswith("https://divar.ir/s/tehran/buy-apartment?") and "districts=" in data["web_url"]
    assert "price=-5000000000" in data["web_url"]
    cars = await call(
        client, "dv_search", {"city": "مشهد", "category": "light", "query": "پراید", "pages": 2, "limit": 60}
    )
    prices = sorted(a["price"] for a in cars["ads"] if a.get("price"))
    floor = prices[len(prices) // 2] // 10
    assert all("outlier" in a.get("suspect_price", []) for a in cars["ads"] if a.get("price") and a["price"] <= floor)
