import json

import httpx
import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio

SEARCH = "/v8/postlist/w/search"
CURSOR_1 = "2026-10-16T00:09:36.104969Z"


def light_routes(ref):
    """Page 1 for a fresh search, page 2 when a cursor is sent."""

    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(
            200, json=fixture("search_light_p2.json" if "pagination_data" in body else "search_light.json")
        )

    ref[SEARCH] = handler
    return ref


def form(api, i=-1):
    return api.body(i)["search_data"]["form_data"]["data"]


async def test_dv_search_returns_compact_cards(client, ref):
    ref[SEARCH] = fixture("search_light.json")
    out = (await client.call_tool("dv_search", {"category": "car", "limit": 20})).structured_content
    assert ref.body()["city_ids"] == ["1"] and form(ref) == {"category": {"str": {"value": "light"}}}
    assert out["category"] == "light" and out["city"] == "تهران" and out["count"] == 8
    promoted = out["ads"][0]
    assert promoted["token"] == "gaPz00RZ" and promoted["tag"] == "پله شده" and promoted["price"] == 6_350_000_000
    car = next(a for a in out["ads"] if a["token"] == "gaRPIP2m")
    assert car["price"] == 998_000_000 and car["info"] == "۱۳۸,۰۰۰ کیلومتر" and car["district"] == "وردآورد"
    assert "posted" not in car and car["age_h"] == 0.1 and car["photos"] == 9
    assert car["url"] == "https://divar.ir/v/gaRPIP2m" and car["chat"] is True and "image" not in car
    assert "tag" not in car and "negotiable" not in car and "suspect_price" not in car
    assert out["next_cursor"] == CURSOR_1 and out["web_url"] == "https://divar.ir/s/tehran/car"


async def test_dv_search_encodes_every_filter_kind(client, ref):
    ref[SEARCH] = fixture("search_light.json")
    args = {
        "category": "light",
        "query": "سمند",
        "districts": ["ونک"],
        "min_price": 200_000_000,
        "max_price": 900_000_000,
        "brand_model": ["سمند"],
        "has_photo": True,
        "recent": "1d",
        "filters": {"gearbox": "automatic", "usage": {"max": 100000}, "bogus": 1, "year": {"min": 1395}},
    }
    out = (await client.call_tool("dv_search", args)).structured_content
    data = form(ref)
    assert ref.body()["search_data"]["query"] == "سمند"
    assert data["price"] == {"number_range": {"minimum": "200000000", "maximum": "900000000"}}
    assert data["districts"] == {"repeated_string": {"value": ["315"]}}
    assert data["brand_model"] == {"repeated_string": {"value": ["Samand"]}}
    assert data["has-photo"] == {"boolean": {"value": True}}
    assert data["recent_ads"] == {"str": {"value": "1d"}}
    assert data["gearbox"] == {"str": {"value": "automatic"}}
    assert data["usage"] == {"number_range": {"maximum": "100000"}}
    assert len(out["ignored"]) == 2 and out["ignored"][0].startswith("bogus")
    assert "did you mean production-year?" in out["ignored"][1]
    assert out["applied"]["districts"] == ["ونک"] and out["applied"]["brand_model"] == ["Samand"]


async def test_dv_search_rejects_bad_input(client, ref):
    ref[SEARCH] = fixture("search_light.json")
    for args in (
        {"category": "nope"},
        {"city": "zzzz"},
        {"recent": "2w"},
        {"districts": ["نامعلوم"]},
        {"min_price": -1},
    ):
        assert (await client.call_tool("dv_search", {"category": "light", **args})).is_error, args


async def test_dv_search_rent_rows_split_deposit_and_rent(client, ref):
    ref[SEARCH] = fixture("search_rent.json")
    out = await client.call_tool("dv_search", {"category": "apartment-rent", "min_price": 5_000_000, "min_deposit": 1})
    data = form(ref)
    assert data["rent"] == {"number_range": {"minimum": "5000000"}} and data["credit"] == {
        "number_range": {"minimum": "1"}
    }
    ads = {a["token"]: a for a in out.structured_content["ads"]}
    full = ads["gaWWawiB"]
    assert (full["deposit"], full["rent"], full["full_deposit"]) == (9_000_000_000, 0, True) and "price" not in full
    assert full["seller"] == "آژانس بام ونك" and ads["gaRPYuZk"]["rent"] == 25_000_000
    assert ads["gaRz3SoL"]["suspect_price"] == ["tiny_rent"]


async def test_dv_search_sorts_fetched_ads_by_price_and_puts_unpriced_last(client, ref):
    ref[SEARCH] = fixture("search_phones.json")
    out = (await client.call_tool("dv_search", {"category": "mobile-phones", "sort": "cheapest"})).structured_content
    prices = [a.get("price") for a in out["ads"]]
    assert prices[:3] == [70_000_000, 176_000_000, 300_000_000]  # under a tenth of the median (7M) goes last, flagged
    assert {a["price"] for a in out["ads"] if a.get("suspect_price") == ["outlier"]} == {1_500_000, 5_000_000}
    with_pics = (
        await client.call_tool("dv_search", {"category": "mobile-phones", "thumbnails": True})
    ).structured_content
    assert all(a["image"].startswith("https://") for a in with_pics["ads"] if a["token"])
    dear = (
        await client.call_tool("dv_search", {"category": "mobile-phones", "sort": "expensive", "limit": 2})
    ).structured_content
    assert [a["price"] for a in dear["ads"]] == [300_000_000, 176_000_000]


async def test_dv_search_merges_pages_with_the_time_cursor_and_dedupes(client, ref):
    def handler(request):
        body = json.loads(request.content)
        if "pagination_data" not in body:
            return httpx.Response(200, json=fixture("search_light.json"))
        page = fixture("search_light_p2.json")
        page["list_widgets"].insert(0, fixture("search_light.json")["list_widgets"][2])  # a repeated ad
        return httpx.Response(200, json=page)

    ref[SEARCH] = handler
    out = (await client.call_tool("dv_search", {"category": "light", "pages": 2, "limit": 100})).structured_content
    second = ref.body(-1)["pagination_data"]
    assert second["last_post_date"] == CURSOR_1 and second["@type"].endswith("PaginationData")
    tokens = [a["token"] for a in out["ads"]]
    assert len(tokens) == len(set(tokens)) == 14 and out["pages_fetched"] == 2
    assert out["next_cursor"] == "2026-10-15T23:06:24.181981Z"
    again = (await client.call_tool("dv_search", {"category": "light", "cursor": CURSOR_1})).structured_content
    assert (
        "gaR7hoS1" in [a["token"] for a in again["ads"]] and ref.body()["pagination_data"]["last_post_date"] == CURSOR_1
    )


async def test_dv_search_stops_fetching_once_the_limit_is_met(client, ref):
    light_routes(ref)
    out = (await client.call_tool("dv_search", {"category": "light", "pages": 3, "limit": 5})).structured_content
    assert out["count"] == 5 and out["pages_fetched"] == 1 and ref.paths().count(SEARCH) == 1


async def test_dv_search_retries_an_empty_arabic_query_with_folded_letters(client, ref):
    def handler(request):
        q = json.loads(request.content)["search_data"].get("query")
        return httpx.Response(200, json=fixture("search_light.json" if q == "کیا" else "search_empty.json"))

    ref[SEARCH] = handler
    out = (await client.call_tool("dv_search", {"category": "light", "query": "كيا"})).structured_content
    assert out["retried_with"] == "کیا" and out["count"] == 8 and out["query"] == "کیا"
    empty = (await client.call_tool("dv_search", {"category": "light", "query": "zzz"})).structured_content
    assert empty["count"] == 0 and empty["ads"] == []


async def test_dv_search_client_side_filters(client, ref):
    ref[SEARCH] = fixture("search_light.json")
    fresh = (await client.call_tool("dv_search", {"category": "light", "max_age_hours": 1})).structured_content
    assert [a["token"] for a in fresh["ads"]][:2] == ["gaRPIP2m", "gaRL4vtx"] and fresh["count"] == 6
    assert all(a["age_h"] <= 1 for a in fresh["ads"])
    only = (
        await client.call_tool("dv_search", {"category": "light", "max_age_hours": 1, "keywords": ["206"]})
    ).structured_content
    assert [a["token"] for a in only["ads"]] == ["gaRPIP2m"]
    no = (await client.call_tool("dv_search", {"category": "light", "exclude": ["206"]})).structured_content
    assert "gaRPIP2m" not in [a["token"] for a in no["ads"]]


async def test_dv_search_without_category_has_no_category_filter(client, ref):
    ref[SEARCH] = fixture("search_light.json")
    out = (
        await client.call_tool("dv_search", {"query": "پراید", "max_price": 5, "city": "تهران,کرج"})
    ).structured_content
    assert form(ref) == {"price": {"number_range": {"maximum": "5"}}}
    assert ref.body()["city_ids"] == [
        "1",
        str(next(c["id"] for c in fixture("cities.json")["cities"] if c["slug"] == "karaj")),
    ]
    assert out["city"] == "تهران, کرج" and "category" not in out


async def test_dv_count_and_cap(client, ref):
    ref["/v8/postlist/w/approximate-post-count"] = fixture("count.json")
    out = (
        await client.call_tool("dv_count", {"category": "light", "filters": {"fuel_type": ["ev"]}})
    ).structured_content
    assert out["count"] == 2147 and "or_more" not in out
    sent = ref.body()
    assert sent["city_ids"] == ["1"] and sent["search_data"]["form_data"]["data"]["fuel_type"] == {
        "repeated_string": {"value": ["ev"]}
    }
    ref["/v8/postlist/w/approximate-post-count"] = {"count": "10000", "count_exceeds_threshold": True}
    capped = (await client.call_tool("dv_count", {"category": "light"})).structured_content
    assert capped["count"] == 10000 and capped["or_more"] is True


async def test_dv_cheapest_skips_outliers_and_reports_the_floor(client, ref):
    light_routes(ref)
    out = (await client.call_tool("dv_cheapest", {"category": "light", "pages": 2, "limit": 3})).structured_content
    assert out["scanned"] == 14 and out["price_floor"] == 235_000_000
    assert [a["token"] for a in out["ads"]] == ["gaRfW3zP", "gaRPIP2m", "gaQ7BK3f"]
    assert out["skipped"]["below_floor"] == 3
    wide = (
        await client.call_tool("dv_cheapest", {"category": "light", "pages": 2, "limit": 1, "include_outliers": True})
    ).structured_content
    assert wide["ads"][0]["token"] == "gaRj2JbA" and wide["ads"][0]["price"] == 10_000
    floor = (
        await client.call_tool(
            "dv_cheapest", {"category": "light", "pages": 2, "limit": 1, "min_realistic_price": 500_000_000}
        )
    ).structured_content
    assert floor["ads"][0]["token"] == "gaRPIP2m"


async def test_dv_cheapest_in_rent_ranks_by_rent_and_skips_tiny_rent(client, ref):
    ref[SEARCH] = fixture("search_rent.json")
    out = (
        await client.call_tool("dv_cheapest", {"category": "apartment-rent", "limit": 2, "include_outliers": True})
    ).structured_content
    assert out["ads"][0]["token"] == "gaKjdvJr" and out["ads"][0]["rent"] == 1_000_000
    assert out["skipped"]["suspect_price"] == 1


async def test_dv_best_for_budget_skips_implausible_prices_and_returns_value_and_closest_lists(client, ref):
    light_routes(ref)
    out = (
        await client.call_tool(
            "dv_best_for_budget", {"budget": 1_500_000_000, "category": "light", "pages": 2, "limit": 3}
        )
    ).structured_content
    capped = [json.loads(c.content) for c in ref.calls if c.url.path == SEARCH and b"maximum" in c.content]
    assert capped and capped[0]["search_data"]["form_data"]["data"]["price"] == {
        "number_range": {"maximum": "1500000000"}
    }
    # ads under 20% of the uncapped market median are bait / not the item: skipped and counted, not scored
    assert out["market_median"] == 2_840_000_000 and out["implausible_below"] == 568_000_000
    assert out["within_budget"] == 2 and out["skipped_implausible"] == 4 and out["budget_median"] == 1_161_500_000
    best = out["best_value"]
    assert [a["token"] for a in best] == ["gaRPIP2m", "gaQ7BK3f"] and best[0]["below_median_pct"] == 14
    assert [a["deal_score"] for a in best] == sorted((a["deal_score"] for a in best), reverse=True)
    assert [a["token"] for a in out["closest_to_budget"]] == ["gaQ7BK3f", "gaRPIP2m"]
    assert all(a["price"] <= 1_500_000_000 for a in out["closest_to_budget"])
    tiny = (
        await client.call_tool("dv_best_for_budget", {"budget": 300_000_000, "category": "light", "pages": 2})
    ).structured_content
    assert not tiny.get("best_value")
    assert tiny["skipped_implausible"] >= 1 and "budget is far below" in tiny["note"]


def test_bait_titles_are_matched():
    from divar_mcp.search import _BAIT

    assert _BAIT.search("فقط با مبلغ 700 میلیون خانه\u200cدار شوید") and _BAIT.search("فرصت طلایی برای خانه اولی ها")
    assert not _BAIT.search("آپارتمان ۸۰ متری ونک")


async def test_dv_new_since_returns_only_newer_ads_and_a_resume_point(client, ref):
    light_routes(ref)
    out = (
        await client.call_tool("dv_new_since", {"category": "light", "since": "2026-10-16T01:40:00Z"})
    ).structured_content
    assert [a["token"] for a in out["ads"]] == ["gaRPIP2m", "gaRL4vtx", "gaQN9ODs", "gaRHIFND"]
    assert out["latest"] == "2026-10-16T01:52:06Z" and out["since"] == "2026-10-16T01:40:00Z" and "more" not in out
    assert ref.paths().count(SEARCH) == 2  # page 2 was all older, so it stopped there
    again = (await client.call_tool("dv_new_since", {"category": "light", "since": out["latest"]})).structured_content
    assert again["count"] == 0 and again["latest"] == out["latest"]
    by_hours = (await client.call_tool("dv_new_since", {"category": "light", "hours": 0.25})).structured_content
    assert [a["token"] for a in by_hours["ads"]] == ["gaRPIP2m", "gaRL4vtx", "gaQN9ODs"]
    bad = await client.call_tool("dv_new_since", {"category": "light", "since": "yesterday"})
    assert bad.is_error and "ISO" in bad.content[0].text


async def test_dv_export_csv_and_jsonl(client, ref):
    ref[SEARCH] = fixture("search_rent.json")
    out = (await client.call_tool("dv_export", {"category": "apartment-rent", "limit": 3})).structured_content
    lines = out["data"].splitlines()
    assert lines[0] == "token,title,price,deposit,rent,district,posted,photos,seller,info,url" and len(lines) == 4
    assert out["rows"] == 3 and "gaWWawiB" in lines[1] and "9000000000" in lines[1]
    jl = (
        await client.call_tool("dv_export", {"category": "apartment-rent", "limit": 2, "format": "jsonl"})
    ).structured_content
    rows = [json.loads(line) for line in jl["data"].splitlines()]
    assert len(rows) == 2 and rows[0]["token"] == "gaWWawiB" and rows[1]["deposit"] == 700_000_000


async def test_dv_nearby_sorts_pins_by_distance_and_converts_prices(client, ref):
    ref["/v8/places/find"] = fixture("find.json")
    ref["/v8/mapview/viewport"] = fixture("viewport.json")
    pins = fixture("viewport.json")["posts"]
    lat, lon = pins[0]["map_pin_feature"]["lat"], pins[0]["map_pin_feature"]["lon"]
    out = (
        await client.call_tool(
            "dv_nearby", {"lat": lat, "lon": lon, "radius_m": 1500, "category": "apartment-rent", "limit": 3}
        )
    ).structured_content
    sent = ref.body()
    box = sent["camera_info"]["bbox"]
    assert box["min_latitude"] < lat < box["max_latitude"] and box["min_longitude"] < lon < box["max_longitude"]
    assert (
        sent["city_ids"] == ["1"]
        and sent["search_data"]["form_data"]["data"]["category"]["str"]["value"] == "apartment-rent"
    )
    assert out["total_in_area"] == 216 and out["count"] == 3
    first = out["ads"][0]
    assert first["distance_m"] == 0 and first["deposit"] == 2_000_000_000 and first["rent"] == 0
    assert first["chips"][:2] == ["۷۵ متر", "۲ اتاق"] and first["approximate_location"] is True
    dists = [a["distance_m"] for a in out["ads"]]
    assert dists == sorted(dists)


async def test_dv_nearby_sale_pins_give_the_total_price_not_the_per_metre_chip(client, ref):
    ref["/v8/places/find"] = fixture("find.json")
    pins = fixture("viewport.json")
    pins["posts"][0]["map_post_card"]["price_fields"] = [
        {"title": "قیمت:", "value": "۵۲٫۵ میلیارد"},
        {"title": "متری:", "value": "۲۹۱ میلیون"},
    ]
    pins["posts"][0]["map_post_card"]["chips"] = [{"title": "۱۸۰ متر"}, {"title": "۳ اتاق"}]
    ref["/v8/mapview/viewport"] = pins
    f = pins["posts"][0]["map_pin_feature"]
    out = (
        await client.call_tool("dv_nearby", {"lat": f["lat"], "lon": f["lon"], "category": "apartment-rent"})
    ).structured_content
    first = out["ads"][0]
    assert (
        first["price"] == 52_500_000_000 and first["price_per_m2"] == 291_666_000 and first["area"] == 180
    )  # site: 291,666,000


async def test_dv_nearby_requires_a_category(client, ref):
    for args in ({"lat": 35.7, "lon": 51.4}, {"lat": 35.7, "lon": 51.4, "category": "ROOT"}):
        assert (await client.call_tool("dv_nearby", args)).is_error
    assert "/v8/mapview/viewport" not in ref.paths()
