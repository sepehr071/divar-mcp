import json

import httpx
import pytest
from conftest import fixture, without_rows

pytestmark = pytest.mark.anyio

POST = "/v8/posts-v2/web/"


def posts(api):
    api[POST + "gaRPIP2m"] = fixture("post_car.json")
    api[POST + "gaLXtvpz"] = fixture("post_car2.json")
    api[POST + "gaRPYuZk"] = fixture("post_rent.json")
    return api


async def test_dv_post_sale_ad(client, api):
    posts(api)
    out = (await client.call_tool("dv_post", {"token": "gaRPIP2m", "max_images": 1})).structured_content
    assert out["price"] == 998_000_000 and "deposit" not in out and "negotiable" not in out
    assert "business_ref" not in out  # 17 characters: dv_dealer rejects it; the usable token comes from dv_seller
    assert (
        out["title"] == "206تیپ3 مدل 96 لوازمی" and out["brand_model"] == "Peugeot 206 3" and out["category"] == "light"
    )
    assert out["category_path"][0] == "وسایل نقلیه" and out["category_path"][-1] == "پژو 206 تیپ ۳"
    assert (
        out["specs"]["کارکرد"] == "۱۳۸۰۰۰"
        and out["specs"]["گیربکس"] == "دنده\u200cای"
        and "قیمت پایه" not in out["specs"]
    )
    assert out["city"] == "تهران" and out["city_id"] == 1 and out["district"] == "وردآورد"
    assert "published" not in out and out["published_at"] and out["expires"] == "2026-11-16"
    assert out["images"] == [
        "https://s100.divarcdn.com/static/photo/neda/webp_post/UyWr_6Azd5i1wV0CN99qLw/e8c870af-fdad-4671-99cd-517a92224070.webp"
    ]
    assert out["photos"] == 9 and out["approximate_location"] is True and 35 < out["lat"] < 36 and 51 < out["lon"] < 52
    assert out["seller_type"] == "personal" and out["chat"] is True and out["url"] == "https://divar.ir/v/gaRPIP2m"
    assert len(out["description"]) <= 1501 and out["description"].startswith("سلام علیکم")
    full = (
        await client.call_tool("dv_post", {"token": "gaRPIP2m", "full_text": True, "max_images": 0})
    ).structured_content
    assert "images" not in full and len(full["description"]) >= len(out["description"])


async def test_dv_post_rental_ad_has_deposit_rent_features_and_shared_housing_flag(client, api):
    posts(api)
    out = (await client.call_tool("dv_post", {"token": "gaRPYuZk"})).structured_content
    assert (out["deposit"], out["rent"]) == (100_000_000, 25_000_000) and "price" not in out
    assert "full_deposit" not in out and out["shared_housing"] is True
    assert out["features"] == ["آسانسور", "پارکینگ", "انباری"]
    assert "ودیعه" not in out["specs"] and out["specs"]["اتاق"] == "۲"


async def test_dv_post_negotiable_and_suspect_prices(client, api):
    data = without_rows(fixture("post_car.json"), "قیمت پایه")
    data["webengage"]["price"] = 0
    api[POST + "aaaaaaaa"] = data
    out = (await client.call_tool("dv_post", {"token": "aaaaaaaa"})).structured_content
    assert out["negotiable"] is True and "price" not in out
    data = fixture("post_car.json")
    for sec in data["sections"]:
        for w in sec["widgets"]:
            if w.get("data", {}).get("title") == "قیمت پایه":
                w["data"]["value"] = "‏۱,۱۱۱,۱۱۱ تومان"
    api[POST + "bbbbbbbb"] = data
    out = (await client.call_tool("dv_post", {"token": "bbbbbbbb"})).structured_content
    assert out["price"] == 1111111 and out["suspect_price"] == ["placeholder"]
    data = without_rows(fixture("post_rent.json"), "اجارهٔ ماهانه")  # a full-deposit ad shows no monthly-rent row
    data["webengage"]["rent"] = 0
    api[POST + "cccccccc"] = data
    out = (await client.call_tool("dv_post", {"token": "cccccccc"})).structured_content
    assert out["full_deposit"] is True and out["rent"] == 0 and out["deposit"] == 100_000_000


async def test_dv_post_unknown_token_and_bad_token(client, api):
    api[POST + "zzzzzzzz"] = lambda r: httpx.Response(404, json={"code": 5, "message": "آگهی یافت نشد"})
    result = await client.call_tool("dv_post", {"token": "zzzzzzzz"})
    assert result.is_error and "آگهی یافت نشد" in result.content[0].text
    bad = await client.call_tool("dv_post", {"token": "no spaces/allowed"})
    assert bad.is_error and not [c for c in api.calls if "spaces" in str(c.url)]


async def test_dv_posts_batch_reports_missing_ads_without_failing(client, api):
    posts(api)
    api[POST + "zzzzzzzz"] = lambda r: httpx.Response(404, json={"code": 5, "message": "آگهی یافت نشد"})
    out = (
        await client.call_tool("dv_posts", {"tokens": ["gaRPIP2m", "zzzzzzzz", "gaRPIP2m", "gaRPYuZk"]})
    ).structured_content
    assert out["count"] == 3 and [a["token"] for a in out["ads"]] == ["gaRPIP2m", "zzzzzzzz", "gaRPYuZk"]
    assert "error" in out["ads"][1] and len(out["ads"][0]["images"]) == 1 and len(out["ads"][0]["description"]) <= 301


async def test_dv_compare_lists_only_the_differing_specs(client, api):
    posts(api)
    out = (await client.call_tool("dv_compare", {"tokens": ["gaRPIP2m", "gaLXtvpz"]})).structured_content
    assert out["cheapest"] == "gaLXtvpz"
    first, second = out["ads"]
    assert first["price"] == 998_000_000 and first["cheapest_saves_pct"] == 2 and "cheapest_saves_pct" not in second
    assert out["differences"]["کارکرد"] == {"gaRPIP2m": "۱۳۸۰۰۰", "gaLXtvpz": "۲۲۰۰۰۰"}
    assert out["shared_specs"]["گیربکس"] == "دنده\u200cای" and "کارکرد" not in out["shared_specs"]
    same = await client.call_tool("dv_compare", {"tokens": ["gaRPIP2m", "gaRPIP2m"]})
    assert same.is_error and "two different" in same.content[0].text


async def test_dv_compare_across_a_rental_and_a_sale_still_works(client, api):
    posts(api)
    out = (await client.call_tool("dv_compare", {"tokens": ["gaRPIP2m", "gaRPYuZk"]})).structured_content
    assert out["ads"][1]["deposit"] == 100_000_000 and out["features_only_in"]["پارکینگ"] == ["gaRPYuZk"]


async def test_dv_seller_counts_business_card_and_other_ads(client, api):
    api["/v8/premium-user/post-page/Qa5zZOOm/owner-posts-list"] = fixture("owner_posts.json")
    api["/v8/premium-user/post-page/web/business-data/Qa5zZOOm/lazy"] = fixture("business.json")
    out = (await client.call_tool("dv_seller", {"token": "Qa5zZOOm", "limit": 2})).structured_content
    assert api.body(0) == {} and api.body(1) == {}
    assert (out["personal_ads"], out["business_ads"]) == (0, 6)
    assert out["business"] == {"name": "رادین خودرو", "active_ads": 11, "brand_token": "ZbeypyTr"}
    assert out["count"] == 2 and out["ads"][0]["token"] == "Qa5zZOOm" and out["ads"][0]["price"] == 1_500_000_000


async def test_dv_seller_of_a_private_person_has_no_business(client, api):
    api["/v8/premium-user/post-page/gaRPIP2m/owner-posts-list"] = {"page": {"widget_list": []}}
    api["/v8/premium-user/post-page/web/business-data/gaRPIP2m/lazy"] = {"widget_list": []}
    out = (await client.call_tool("dv_seller", {"token": "gaRPIP2m"})).structured_content
    assert "business" not in out and out["count"] == 0


async def test_dv_seller_of_a_personal_ad_survives_business_404(client, api):
    api["/v8/premium-user/post-page/gaR7RNq_/owner-posts-list"] = fixture("owner_posts.json")
    api["/v8/premium-user/post-page/web/business-data/gaR7RNq_/lazy"] = lambda r: httpx.Response(
        404, json={"message": {"title": "business not found"}, "error_code": "374000"}
    )
    out = (await client.call_tool("dv_seller", {"token": "gaR7RNq_", "limit": 1})).structured_content
    assert "business" not in out and out["seller_kind"].startswith("personal") and out["count"] == 1


async def test_dv_seller_still_fails_when_the_ad_is_gone(client, api):
    api["/v8/premium-user/post-page/zzzzzzzz/owner-posts-list"] = lambda r: httpx.Response(404, json={"message": "x"})
    api["/v8/premium-user/post-page/web/business-data/zzzzzzzz/lazy"] = lambda r: httpx.Response(404, json={})
    result = await client.call_tool("dv_seller", {"token": "zzzzzzzz"})
    assert result.is_error and "404" in result.content[0].text


async def test_dv_dealer(client, api):
    api["/v8/premium-user/web/business/brand-landing/ZbeypyTr"] = fixture("brand_landing.json")
    out = (await client.call_tool("dv_dealer", {"brand_token": "ZbeypyTr"})).structured_content
    assert api.body() == {}
    assert out["name"] == "رادین خودرو" and out["active_ads"] == 11 and out["member_since"] == "۲۴ دی ۱۳۹۸"
    assert out["count"] == 3 and out["ads"][0]["price"] == 1_450_000_000
    assert out["url_prefix"] == "https://divar.ir/v/"
    for ad in out["ads"]:  # compact rows: no image / url / seller / posted / chat
        assert not {"image", "url", "seller", "posted", "chat"} & ad.keys() and ad["token"] and ad["title"]
    assert len(json.dumps(out, ensure_ascii=False)) < 1500


async def test_dv_similar_searches_the_ads_own_breadcrumb_and_excludes_it(client, ref):
    posts(ref)
    ref["/v8/postlist/w/search"] = fixture("search_light.json")
    out = (await client.call_tool("dv_similar", {"token": "gaRPIP2m", "limit": 5})).structured_content
    sent = ref.body()
    data = sent["search_data"]["form_data"]["data"]
    assert sent["city_ids"] == ["1"] and data["category"] == {"str": {"value": "light"}}
    assert data["brand_model"] == {"repeated_string": {"value": ["Peugeot 206 3"]}}
    assert data["districts"] == {"repeated_string": {"value": ["374"]}}
    assert out["basis"] == ["پژو 206 تیپ ۳"] and out["count"] == 5
    assert "gaRPIP2m" not in [a["token"] for a in out["ads"]]


async def test_dv_similar_widens_until_enough(client, ref):
    posts(ref)
    ref["/v8/postlist/w/search"] = fixture("search_light.json")
    out = (await client.call_tool("dv_similar", {"token": "gaRPIP2m", "limit": 20})).structured_content
    assert len(out["basis"]) > 1 and out["basis"][0] == "پژو 206 تیپ ۳" and out["count"] == 7
    last = ref.body()["search_data"]["form_data"]["data"]
    assert "brand_model" not in last  # the widest level is the plain category


async def test_dv_post_price_comes_from_the_exact_row_not_the_rounded_webengage_number(client, api):
    data = fixture("post_car.json")
    data["webengage"]["price"] = 998_000_128  # Divar's raw number is float32-rounded for big prices
    api[POST + "dddddddd"] = data
    out = (await client.call_tool("dv_post", {"token": "dddddddd"})).structured_content
    assert out["price"] == 998_000_000


async def test_dv_post_sale_price_uses_the_total_row_of_the_apartment_layout(client, api):
    data = without_rows(fixture("post_car.json"), "قیمت پایه")
    data["webengage"]["price"] = 52_500_000_768  # float32 rounding of 52,500,000,000
    rows = next(s for s in data["sections"] if s["section_name"] == "LIST_DATA")["widgets"]
    rows.append({"widget_type": "UNEXPANDABLE_ROW", "data": {"title": "قیمت کل", "value": "‏۵۲,۵۰۰,۰۰۰,۰۰۰ تومان"}})
    api[POST + "eeeeeeee"] = data
    out = (await client.call_tool("dv_post", {"token": "eeeeeeee"})).structured_content
    assert out["price"] == 52_500_000_000
