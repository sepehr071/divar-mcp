import json

import httpx
import pytest
from conftest import fixture, without_rows

pytestmark = pytest.mark.anyio

SEARCH = "/v8/postlist/w/search"


def light_routes(ref):
    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(
            200, json=fixture("search_light_p2.json" if "pagination_data" in body else "search_light.json")
        )

    ref[SEARCH] = handler


async def test_dv_market_distribution_by_district_and_day(client, ref):
    light_routes(ref)
    out = (await client.call_tool("dv_market", {"category": "light", "pages": 2})).structured_content
    assert out["scanned"] == 14 and out["priced"] == 14
    s = out["stats"]["price"]
    assert s["n"] == 11 and s["min"] == 250_000_000 and s["median"] == 2_840_000_000 and s["max"] == 11_200_000_000
    assert s["p25"] < s["median"] < s["p75"] <= s["p90"]
    assert {"district": "وردآورد", "n": 1, "median": 998_000_000} in out["by_district"]
    days = {d["date"]: d["n"] for d in out["per_day"]}
    assert days["2026-10-16"] == 9 and days["2026-10-13"] == 1 and list(days) == sorted(days)
    assert out["newest_age_h"] == 0.1 and out["oldest_age_h"] == 63.6


async def test_dv_market_for_rentals_reports_rent_and_deposit(client, ref):
    ref[SEARCH] = fixture("search_rent.json")
    out = (await client.call_tool("dv_market", {"category": "apartment-rent"})).structured_content
    stats = out["stats"]
    assert "price" not in stats and stats["rent"]["n"] == 5 and stats["deposit"]["n"] == 7
    assert out["excluded"]["suspect_price"] == 1  # the 10,000 Toman rent


async def test_dv_appraise_ranks_an_ad_against_its_comparables(client, ref):
    light_routes(ref)
    ref["/v8/posts-v2/web/gaRPIP2m"] = fixture("post_car.json")
    out = (await client.call_tool("dv_appraise", {"token": "gaRPIP2m", "pages": 1})).structured_content
    assert out["subject"]["price"] == 998_000_000 and out["basis"][0] == "پژو 206 تیپ ۳" and len(out["basis"]) > 1
    assert out["comparable_ads"] == 7 and out["stats"]["median"] == 3_500_000_000  # the ad itself is not a comparable
    assert out["percentile"] == 0 and out["verdict"] == "insufficient comparables" and out["vs_median_pct"] == -71
    assert out["fair_band"] == [out["stats"]["p25"], out["stats"]["p75"]] and out["confidence"] == "low"
    assert "leaning" not in out  # no lean either way when the comparables are thin


async def test_dv_appraise_a_price_without_an_ad(client, ref):
    light_routes(ref)
    out = (
        await client.call_tool("dv_appraise", {"price": 3_100_000_000, "category": "light", "pages": 2})
    ).structured_content
    assert out["subject"] == {"price": 3_100_000_000} and out["comparable_ads"] == 11
    assert out["verdict"] == "fair" and 40 <= out["percentile"] <= 60


async def test_dv_appraise_needs_enough_comparables(client, ref):
    page = fixture("search_light.json")
    page["list_widgets"] = page["list_widgets"][:3]
    ref[SEARCH] = page
    out = (await client.call_tool("dv_appraise", {"price": 1_000_000_000, "category": "light"})).structured_content
    assert out["comparable_ads"] == 3 and "not enough" in out["verdict"] and "percentile" not in out


async def test_dv_appraise_input_errors(client, ref):
    nothing = await client.call_tool("dv_appraise", {})
    assert nothing.is_error and "token" in nothing.content[0].text
    data = without_rows(fixture("post_car.json"), "قیمت پایه")
    data["webengage"]["price"] = 0
    ref["/v8/posts-v2/web/aaaaaaaa"] = data
    free = await client.call_tool("dv_appraise", {"token": "aaaaaaaa"})
    assert free.is_error and "negotiable" in free.content[0].text


async def test_dv_appraise_never_widens_past_the_brand(client, ref):
    light_routes(ref)  # every level returns the same 14 ads, so the loop would walk the whole breadcrumb
    ref["/v8/posts-v2/web/gaRPIP2m"] = fixture("post_car.json")
    out = (await client.call_tool("dv_appraise", {"token": "gaRPIP2m", "pages": 1})).structured_content
    assert out["basis"] == ["پژو 206 تیپ ۳", "پژو 206", "پژو"]  # not "خودرو سواری و وانت" or wider


async def test_dv_appraise_far_off_price_is_low_confidence(client, ref):
    light_routes(ref)
    out = (
        await client.call_tool("dv_appraise", {"price": 150_000_000, "category": "light", "pages": 2})
    ).structured_content
    assert out["comparable_ads"] == 11 and out["confidence"] == "low" and "10x" in out["caveat"]
