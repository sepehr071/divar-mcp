"""Real-estate behaviour: parse_query words, per-metre ranking, bait titles, find_deals, web_url, outlier flags."""

import pytest
from conftest import fixture

from divar_mcp.search import is_bait

pytestmark = pytest.mark.anyio

SEARCH = "/v8/postlist/w/search"
POST = "/v8/posts-v2/web/"
FLAT_TOKENS = {"small": "gaRzh3GL", "big": "gaRPDd4x"}


async def parse(client, text):
    return (await client.call_tool("dv_parse_query", {"text": text})).structured_content


async def test_parse_query_flat_with_a_district_and_a_size(client, ref):
    out = await parse(client, "آپارتمان ۸۰ متری در ونک تا ۵ میلیارد")
    assert out["search"] == {
        "max_price": 5_000_000_000,
        "filters": {"size": {"min": 72, "max": 88}},
        "districts": ["ونک"],
        "category": "apartment-sell",
    }
    assert "query" not in out["search"]
    assert any("'آپارتمان'" in n for n in out["notes"]) and not any("اپارتمان" in n for n in out["notes"])


async def test_parse_query_rent_maps_deposit_and_drops_rent_words(client, ref):
    out = await parse(client, "رهن و اجاره آپارتمان ونک ودیعه ۵۰۰ میلیون")
    assert out["search"] == {"max_deposit": 500_000_000, "districts": ["ونک"], "category": "apartment-rent"}
    both = await parse(client, "اجاره آپارتمان ونک اجاره تا ۲۰ میلیون ودیعه از ۱ تا ۲ میلیارد")
    assert both["search"]["min_deposit"] == 1_000_000_000 and both["search"]["max_deposit"] == 2_000_000_000
    assert both["search"]["max_price"] == 20_000_000 and "query" not in both["search"]


async def test_parse_query_common_nouns_become_categories(client, ref):
    out = await parse(client, "ماشین پراید مشهد زیر ۲۰۰ میلیون")
    assert out["search"] == {"max_price": 200_000_000, "city": "مشهد", "category": "light", "query": "پراید"}
    phone = await parse(client, "گوشی")
    assert phone["search"] == {"category": "mobile-phones"}


def test_bait_words_for_loans_coops_and_investment_are_matched_unless_asked():
    def bait(title, asked=""):
        return is_bait({"title": title}, asked)

    for t in (
        "۱۵۱ متر دارای ۱۴۱مترقدرالسهم",
        "وامدار ۳ میلیارد",
        "سود تضمینی ۳۰٪",
        "مناسب سرمایه گذاری",
        "پیش فروش برج",
    ):
        assert bait(t)
    assert not bait("۱۲۰ متر تکواحد کمالی پونک")
    assert not bait("وامدار ۳ میلیارد", asked="وامدار")


async def flats(client, ref, **args):
    ref[SEARCH] = fixture("search_flats.json")  # 20 real flat ads in Vanak / Pounak, 53 m to 188 m
    return (
        await client.call_tool("dv_best_for_budget", {"category": "apartment-sell", "limit": 5, **args})
    ).structured_content


async def test_best_for_budget_ranks_flats_by_price_per_metre_and_drops_bait(client, ref):
    out = await flats(client, ref, budget=60_000_000_000)
    assert out["ranked_by"] == "price_per_m2" and out["median_price_per_m2"] == 402_500_000
    best = out["best_value"]
    assert best[0]["token"] == "gaOTwfK0" and best[0]["price_per_m2"] == 220_481_000 and best[0]["area"] == 83
    assert [a["deal_score"] for a in best] == sorted((a["deal_score"] for a in best), reverse=True)
    assert all(a["price_per_m2"] and a["area_from"] == "title" for a in best)
    # the 53 m flat (5.95B) is the cheapest in total; it is under 20% of the market median, not a "win"
    assert "gaKZm0w6" not in {a["token"] for a in best}
    # co-op "قدرالسهم" and "سرمایه گذاری" titles are filtered, not just down-scored
    listed = {a["token"] for a in best + out["closest_to_budget"]}
    assert not listed & {"gaRPDd4x", "gaRDyQPH"} and out["skipped_implausible"] == 3
    assert out["no_area"] == 1  # "بلوار عدل ۳خواب": no area in the title, the ad page is not in this test


async def test_best_for_budget_reads_the_area_from_the_ad_page_and_keeps_asked_bait(client, ref):
    ref[POST + "gaPHE4pL"] = fixture("post_flat_small.json")  # any real flat page: area 61
    out = await flats(client, ref, budget=60_000_000_000)
    assert out["no_area"] == 0
    paged = [a for a in out["closest_to_budget"] if a["token"] == "gaPHE4pL"]
    assert not paged or paged[0]["area_from"] == "ad"
    asked = await flats(client, ref, budget=60_000_000_000, keywords=["قدرالسهم"])
    assert [a["token"] for a in asked["closest_to_budget"]] == ["gaRPDd4x"]


async def test_find_deals_lists_ads_below_the_per_metre_median(client, ref):
    ref[SEARCH] = fixture("search_flats.json")
    out = (await client.call_tool("dv_find_deals", {"category": "apartment-sell"})).structured_content
    assert out["ranked_by"] == "price_per_m2" and out["median"] == 402_500_000 and out["scanned"] == 20
    assert [a["token"] for a in out["deals"]] == ["gaOTwfK0", "ga--HzzJ", "gaRPCaTG"]
    assert all(15 <= a["below_median_pct"] <= 70 for a in out["deals"])
    assert out["skipped"]["implausible_or_bait"] == 3
    strict = (
        await client.call_tool("dv_find_deals", {"category": "apartment-sell", "min_discount_pct": 40})
    ).structured_content
    assert [a["token"] for a in strict["deals"]] == ["gaOTwfK0"]
    ref[SEARCH] = {"list_widgets": fixture("search_flats.json")["list_widgets"][:4], "pagination": {}}
    few = (await client.call_tool("dv_find_deals", {"category": "apartment-sell"})).structured_content
    assert few["deals"] == [] and "Too few" in few["note"]


async def test_compare_two_flats_by_price_per_metre(client, api):
    api[POST + FLAT_TOKENS["small"]] = fixture("post_flat_small.json")
    api[POST + FLAT_TOKENS["big"]] = fixture("post_flat_big.json")
    out = (await client.call_tool("dv_compare", {"tokens": list(FLAT_TOKENS.values())})).structured_content
    small, big = out["ads"]
    assert small["area"] == 61 and small["price"] == 23_000_000_000 and small["price_per_m2"] == 377_049_000
    assert big["area"] == 151 and big["price_per_m2"] == 298_013_000
    # 23B < 45B in total, but the 151 m flat is cheaper per metre: it is the one to call cheapest
    assert out["compared_by"] == "price_per_m2" and out["cheapest"] == FLAT_TOKENS["big"]
    assert small["cheapest_saves_pct"] == 21 and "cheapest_saves_pct" not in big


async def test_dv_post_gives_price_per_metre_for_a_flat(client, api):
    api[POST + FLAT_TOKENS["big"]] = fixture("post_flat_big.json")
    out = (await client.call_tool("dv_post", {"token": FLAT_TOKENS["big"], "max_images": 0})).structured_content
    assert out["area"] == 151 and out["price_per_m2"] == 298_013_000


async def test_search_flags_a_price_far_below_the_sample_median(client, ref):
    ref[SEARCH] = fixture("search_pride.json")  # 24 real Pride ads in Mashhad, one at 180,000 Toman
    out = (
        await client.call_tool("dv_search", {"city": "مشهد", "category": "light", "query": "پراید", "limit": 30})
    ).structured_content
    flagged = {a["price"]: a["suspect_price"] for a in out["ads"] if a.get("suspect_price")}
    assert flagged == {180_000: ["outlier"]}
    few = fixture("search_pride.json")
    few["list_widgets"] = few["list_widgets"][-3:]
    ref[SEARCH] = few
    out = (await client.call_tool("dv_search", {"city": "مشهد", "category": "light"})).structured_content
    assert not any(a.get("suspect_price") for a in out["ads"])  # 3 ads are too few to call an outlier


async def test_web_url_carries_category_districts_price_size_and_text(client, ref):
    ref[SEARCH] = fixture("search_flats.json")
    out = (
        await client.call_tool(
            "dv_search",
            {
                "category": "apartment-sell",
                "districts": ["ونک"],
                "max_price": 5_000_000_000,
                "query": "نوساز",
                "limit": 1,
            },
        )
    ).structured_content
    # checked in Chrome: this address applies the district, the price range and the text (size=72-88 too)
    assert out["web_url"] == (
        "https://divar.ir/s/tehran/buy-apartment?price=-5000000000&districts=315&q=%D9%86%D9%88%D8%B3%D8%A7%D8%B2"
    )


async def test_web_url_is_left_out_when_the_site_cannot_express_the_search(client, ref):
    ref[SEARCH] = fixture("search_flats.json")
    photo = (
        await client.call_tool("dv_search", {"category": "apartment-sell", "has_photo": True, "limit": 1})
    ).structured_content
    assert "web_url" not in photo
    two = (
        await client.call_tool("dv_search", {"city": "تهران,کرج", "category": "light", "limit": 1})
    ).structured_content
    assert "web_url" not in two
    plain = (await client.call_tool("dv_search", {"category": "light", "limit": 1})).structured_content
    assert plain["web_url"] == "https://divar.ir/s/tehran/car"


def test_web_url_range_format_for_size_and_rent_filters():
    from divar_mcp.core import Q

    q = Q(
        [{"id": 1, "slug": "tehran"}],
        "apartment-rent",
        "",
        {
            "category": {"str": {"value": "apartment-rent"}},
            "credit": {"number_range": {"maximum": "5000000000"}},
            "rent": {"number_range": {"minimum": "1000000", "maximum": "20000000"}},
            "size": {"number_range": {"minimum": "72", "maximum": "88"}},
        },
    )
    # each of these ranges was checked in Chrome (credit / rent / size inputs filled from the address)
    assert q.web_url() == (
        "https://divar.ir/s/tehran/rent-apartment?credit=-5000000000&rent=1000000-20000000&size=72-88"
    )


# ---- verify round 4 fixes: publish time vs bump, floor / parking, similar outliers


def test_jalali_time_converts_tehran_local_to_utc():
    from divar_mcp.core import jalali_time

    assert jalali_time("۲۶ خرداد ۱۴۰۵، ۱۷:۴۳").isoformat() == "2026-06-16T14:13:00+00:00"
    assert jalali_time("۱ فروردین ۱۴۰۵، ۰۰:۰۰").isoformat() == "2026-03-20T20:30:00+00:00"
    assert jalali_time("۳۰ اسفند ۱۴۰۳، ۱۲:۰۰").isoformat() == "2025-03-20T08:30:00+00:00"  # leap year end
    assert jalali_time("دقایقی پیش") is None


async def test_dv_post_gives_the_real_publish_and_bump_times(client, api):
    api[POST + "gaxYV8NL"] = fixture("post_flat_bumped.json")  # divar.ir shows "4 months ago" for this ad
    out = (await client.call_tool("dv_post", {"token": "gaxYV8NL", "max_images": 0})).structured_content
    assert out["published_at"] == "2026-06-16T14:13:00Z" and out["bumped_at"] == "2026-10-15T08:01:00Z"


async def test_a_rebumped_old_ad_gets_no_freshness_bonus(client, ref):
    ref[SEARCH] = fixture("search_flats.json")
    out = await flats(client, ref, budget=60_000_000_000)  # no ad page routes: publish time unknown
    top = out["best_value"][0]
    assert top["age_h"] > 24 or any("bumped" in r for r in top["reasons"])
    assert not any("published in the last 24 h" in r for a in out["best_value"] for r in a["reasons"])
    fresh = fixture("search_flats.json")
    ref[SEARCH] = fresh
    tok = out["best_value"][0]["token"]
    ref[POST + tok] = fixture("post_flat_bumped.json")  # published 4 months ago, bumped yesterday
    again = await flats(client, ref, budget=60_000_000_000)
    ad = next(a for a in again["best_value"] if a["token"] == tok)
    assert ad["published_h"] > 2900 and not any("+15" in r and "24 h" in r for r in ad["reasons"])
    assert out["caveat"] == again["caveat"] and "last bump" in out["caveat"]


async def test_basement_and_no_parking_flats_lose_points_and_say_why(client, ref):
    ref[SEARCH] = fixture("search_flats.json")
    first = await flats(client, ref, budget=60_000_000_000)
    tok = first["best_value"][0]["token"]
    ref[POST + tok] = fixture("post_flat_basement.json")  # real زیرهمکف flat, "پارکینگ ندارد"
    out = await flats(client, ref, budget=60_000_000_000)
    ad = next(a for a in out["best_value"] + out["closest_to_budget"] if a["token"] == tok)
    assert ad["basement"] is True and ad["no_parking"] is True and ad["checked"] is True
    assert any("below-ground" in r for r in ad["reasons"]) and any("no parking" in r for r in ad["reasons"])
    before = next(a for a in first["best_value"] if a["token"] == tok)
    assert ad["deal_score"] <= before["deal_score"] - 30
    assert out["best_value"][0]["token"] != tok
    deals = (await client.call_tool("dv_find_deals", {"category": "apartment-sell"})).structured_content
    assert "caveat" in deals


async def test_similar_drops_bulk_listings_and_other_sized_flats(client, ref):
    ref[POST + "gaL_XoC1"] = fixture("post_flat_basement.json")  # 120 m, 24B
    ref[SEARCH] = fixture("search_similar_flats.json")  # 24 real ads around it, from 47.9B flats to 254B bulk sales
    out = (await client.call_tool("dv_similar", {"token": "gaL_XoC1", "limit": 50})).structured_content
    assert out["excluded_outliers"] == 6 and out["count"] == 18
    assert all(a["price"] <= 5 * 24_000_000_000 for a in out["ads"])  # bulk sales far above 5x are gone
