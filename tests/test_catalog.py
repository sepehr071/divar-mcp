import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio

OPTIONS = "/v8/w/lazy-multi-select-hierarchy-options"


async def test_dv_categories_search_children_and_top_level(client, ref):
    by_name = (await client.call_tool("dv_categories", {"query": "موبایل"})).structured_content
    assert by_name["categories"][0]["slug"] == "mobile-phones" and by_name["categories"][0]["leaf"] is True
    kids = (await client.call_tool("dv_categories", {"parent": "cars"})).structured_content
    assert [c["slug"] for c in kids["categories"]] == ["light"]
    top = (await client.call_tool("dv_categories", {})).structured_content
    assert [c["slug"] for c in top["categories"]] == ["real-estate", "vehicles", "electronic-devices"]


async def test_dv_filters_for_a_rent_category(client, ref):
    out = (await client.call_tool("dv_filters", {"category": "apartment-rent"})).structured_content
    by_key = {f["key"]: f for f in out["filters"]}
    assert {"credit", "rent", "districts", "rooms", "parking"} <= set(by_key)
    assert by_key["rent"]["type"] == "number_range" and by_key["rent"]["unit"] == "تومان"
    assert by_key["districts"]["lazy"] is True and by_key["parking"]["type"] == "boolean"
    assert "دو=۲" in by_key["rooms"]["options"]
    assert ref.body(-1)["data"]["form_data"]["data"]["category"] == {"str": {"value": "apartment-rent"}}


async def test_dv_filters_needs_a_known_category(client, ref):
    result = await client.call_tool("dv_filters", {"category": "no-such-thing"})
    assert (
        result.is_error and "Unknown category" in result.content[0].text and "dv_categories" in result.content[0].text
    )
    nothing = await client.call_tool("dv_filters", {})
    assert nothing.is_error


async def test_web_slug_alias_and_persian_name_resolve(client, ref):
    out = (await client.call_tool("dv_filters", {"category": "car"})).structured_content
    assert out["category"] == "light"
    out = (await client.call_tool("dv_filters", {"category": "اجارهٔ آپارتمان"})).structured_content
    assert out["category"] == "apartment-rent"


async def test_dv_filter_options_typed_search_uses_the_filters_search_key(client, ref):
    out = (
        await client.call_tool("dv_filter_options", {"category": "light", "query": "سمند", "limit": 2})
    ).structured_content
    body = ref.body(-1)
    assert body["search_key"] == "car_brand_model" and body["field"] == "brand_model" and body["q"] == "سمند"
    assert out["options"] == [{"value": "Samand", "label": "سمند"}, {"value": "Samand SE", "label": "سمند SE"}]


async def test_dv_filter_options_brand_tree_and_models(client, ref):
    ref[OPTIONS] = fixture("options_brand.json")
    brands = (await client.call_tool("dv_filter_options", {"category": "light"})).structured_content
    assert brands["total"] == 4 and {"value": "Peugeot", "label": "پژو", "models_count": 5} in brands["options"]
    assert ref.body(-1)["payload"]["filter_name"] == "brand_model"
    models = (await client.call_tool("dv_filter_options", {"category": "light", "brand": "peugeot"})).structured_content
    assert models["count"] == 5 and models["options"][0]["value"].startswith("Peugeot")
    assert ref.paths().count(OPTIONS) == 1  # the tree is cached
    missing = await client.call_tool("dv_filter_options", {"category": "light", "brand": "Nope"})
    assert missing.is_error


async def test_dv_filter_options_plain_filter_and_unknown_filter(client, ref):
    out = (await client.call_tool("dv_filter_options", {"category": "light", "filter": "gearbox"})).structured_content
    assert out["options"] and all("value" in o for o in out["options"])
    bad = await client.call_tool("dv_filter_options", {"category": "light", "filter": "nope"})
    assert bad.is_error and "dv_filters" in bad.content[0].text


async def test_dv_suggest_pairs_suggestions_with_category_guesses(client, ref):
    ref["/v8/prediction/w/query"] = fixture("suggest.json")
    ref["/v8/prediction/cat-component-search"] = fixture("category_suggest.json")
    out = (await client.call_tool("dv_suggest", {"query": "پراید", "city": "تهران"})).structured_content
    first = out["suggestions"][0]
    assert first["text"] == "پراید" and first["category"] == "light" and first["probability"] > 0.5
    assert out["categories"][0]["category"] == "light"
    sent = [c for c in ref.calls if c.url.path.endswith("/query")][0]
    assert b'"cities":["1"]' in sent.content.replace(b" ", b"")


async def test_brand_model_keys_are_validated_against_the_option_tree(client, ref):
    ref["/v8/postlist/w/search"] = fixture("search_light.json")
    ok = (await client.call_tool("dv_search", {"category": "light", "brand_model": ["samand"]})).structured_content
    assert ok["applied"]["brand_model"] == ["Samand"] and "/v8/fields-search" not in ref.paths()
    ref["/v8/fields-search"] = {"results": []}
    bad = await client.call_tool("dv_search", {"category": "light", "brand_model": ["peugeot-2066"]})
    assert bad.is_error and "No brand/model matches" in bad.content[0].text


async def test_dv_parse_query_extracts_city_district_price_and_category(client, ref):
    ref["/v8/prediction/cat-component-search"] = fixture("category_suggest.json")
    out = (await client.call_tool("dv_parse_query", {"text": "پراید ونک تهران زیر ۸٫۵ میلیارد"})).structured_content
    assert out["search"] == {
        "max_price": 8_500_000_000,
        "city": "تهران",
        "districts": ["ونک"],
        "category": "light",
        "query": "پراید",
    }
    ranged = (await client.call_tool("dv_parse_query", {"text": "از ۲ تا ۵ میلیارد"})).structured_content
    assert ranged["search"] == {"min_price": 2_000_000_000, "max_price": 5_000_000_000}
    assert "No city" in ranged["notes"][0]
    area = (await client.call_tool("dv_parse_query", {"text": "آپارتمان ۸۰ متری ونک تهران"})).structured_content
    assert area["search"]["filters"] == {"size": {"min": 72, "max": 88}} and "متری" not in area["search"].get(
        "query", ""
    )
    above = (await client.call_tool("dv_parse_query", {"text": "بالای ۱۰۰ میلیون"})).structured_content
    assert above["search"] == {"min_price": 100_000_000}
