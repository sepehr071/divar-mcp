import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def _ads(client, category="light", n=4, **extra):
    """Ads come and go: take current ones that have a price."""
    data = await call(client, "dv_search", {"category": category, "limit": 30, **extra})
    return [a for a in data["ads"] if a.get("price") and not a.get("suspect_price")][:n]


async def test_dv_post_and_dv_posts(client):
    ads = await _ads(client)
    post = await call(client, "dv_post", {"token": ads[0]["token"]})
    assert post["title"] and post["specs"] and post["images"] and post["category"] == "light" and "lat" in post
    assert post["price"] == ads[0]["price"]
    many = await call(client, "dv_posts", {"tokens": [a["token"] for a in ads[:3]]})
    assert many["count"] == 3 and all("error" not in a for a in many["ads"])


async def test_dv_post_rental(client):
    ads = (await call(client, "dv_search", {"category": "apartment-rent", "limit": 10}))["ads"]
    post = await call(client, "dv_post", {"token": ads[0]["token"]})
    assert "deposit" in post and "rent" in post and post["specs"]


async def test_dv_compare(client):
    ads = await _ads(client, n=2)
    data = await call(client, "dv_compare", {"tokens": [a["token"] for a in ads]})
    assert data["cheapest"] in {a["token"] for a in ads} and len(data["ads"]) == 2
    assert data["differences"] or data["shared_specs"]


async def test_dv_similar_and_appraise(client):
    ads = await _ads(client, n=1, brand_model=["Peugeot 206"])
    token = ads[0]["token"]
    similar = await call(client, "dv_similar", {"token": token, "limit": 6})
    assert similar["ads"] and token not in [a["token"] for a in similar["ads"]] and similar["basis"]
    apr = await call(client, "dv_appraise", {"token": token})
    assert apr["comparable_ads"] >= 5 and apr["verdict"] and 0 <= apr["percentile"] <= 100
    assert "خودرو سواری و وانت" not in apr["basis"]  # never widened to the whole category
    by_price = await call(
        client, "dv_appraise", {"price": 1_000_000_000, "category": "light", "brand_model": ["Peugeot 206"]}
    )
    assert by_price["stats"]["median"] > 0


async def test_dv_market(client):
    data = await call(client, "dv_market", {"category": "mobile-phones", "query": "iphone", "pages": 2})
    s = data["stats"]["price"]
    assert data["scanned"] > 20 and s["min"] <= s["median"] <= s["max"] and data["per_day"] and data["by_district"]
    rent = await call(client, "dv_market", {"category": "apartment-rent", "districts": ["ونک"], "pages": 2})
    assert rent["stats"]["rent"]["n"] > 0 and rent["stats"]["deposit"]["n"] > 0


async def test_dv_seller_of_a_personal_ad(client):
    ads = await call(client, "dv_search", {"category": "mobile-phones", "limit": 15})
    sellers = [await call(client, "dv_seller", {"token": a["token"], "limit": 2}) for a in ads["ads"]]
    personal = next(s for s in sellers if "business" not in s)  # a personal ad must not fail on the business 404
    assert personal["seller_kind"].startswith("personal")


async def test_dv_seller_and_dv_dealer(client):
    dealers = await call(
        client, "dv_search", {"category": "light", "filters": {"business-type": ["car-business"]}, "limit": 10}
    )
    business = None
    for a in dealers["ads"]:
        seller = await call(client, "dv_seller", {"token": a["token"], "limit": 3})
        if seller.get("business"):
            business = seller
            break
    assert business and business["business"]["brand_token"]
    page = await call(client, "dv_dealer", {"brand_token": business["business"]["brand_token"]})
    assert page["name"] and page["ads"] and page["active_ads"] >= 1
