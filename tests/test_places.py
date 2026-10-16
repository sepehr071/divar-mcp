import httpx
import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio


async def test_dv_cities_by_name_and_slug(client, api):
    api["/v8/places/cities"] = fixture("cities.json")
    out = (await client.call_tool("dv_cities", {"query": "مشهد", "limit": 3})).structured_content
    assert out["cities"][0] == {
        "id": 3,
        "name": "مشهد",
        "slug": "mashhad",
        "province_id": 880,
        "lat": 36.5660934,
        "lon": 59.6603813,
    }
    by_slug = (await client.call_tool("dv_cities", {"query": "karaj"})).structured_content
    assert by_slug["cities"][0]["name"] == "کرج"
    arabic = (await client.call_tool("dv_cities", {"query": "كرج"})).structured_content  # Arabic kaf
    assert arabic["cities"][0]["id"] == by_slug["cities"][0]["id"]


async def test_dv_cities_without_query_lists_top_cities(client, api):
    api["/v8/places/cities"] = fixture("cities.json")
    out = (await client.call_tool("dv_cities", {})).structured_content
    assert out["cities"][0]["slug"] == "tehran" and out["total_cities"] == len(fixture("cities.json")["cities"])


async def test_dv_districts_search_and_cache(client, ref):
    out = (await client.call_tool("dv_districts", {"city": "تهران", "query": "ونک"})).structured_content
    assert out["city_id"] == 1 and out["districts"][0]["id"] == 315 and out["districts"][0]["name"] == "ونک"
    assert {d["name"] for d in out["districts"]} >= {"ونک", "ده\u200cونک"}  # prefix and partial matches too
    await client.call_tool("dv_districts", {"city": "1"})
    assert ref.paths().count("/v8/places/cities/1/districts") == 1  # cached


async def test_dv_districts_unknown_city_suggests(client, ref):
    result = await client.call_tool("dv_districts", {"city": "zzzz"})
    assert result.is_error and "Unknown city" in result.content[0].text


async def test_dv_neighbours(client, ref):
    ref["/v8/places/districts/315/neighbours"] = fixture("neighbours.json")
    out = (await client.call_tool("dv_neighbours", {"district_id": 315})).structured_content
    assert out["count"] == 4 and out["districts"][0]["id"] == 204 and out["districts"][0]["name"] == "جمال\u200cزاده"
    listed = {d["id"] for d in out["districts"]}
    near = out["nearest"]  # districts Divar does not list as neighbours, closest first, within 3 km
    assert near and not listed & {d["id"] for d in near} and 315 not in {d["id"] for d in near}
    assert [d["distance_m"] for d in near] == sorted(d["distance_m"] for d in near) and near[-1]["distance_m"] <= 3000


async def test_dv_locate_merges_find_and_address(client, api):
    api["/v8/places/find"] = fixture("find.json")
    api["/v8/map-discovery/reverse-geocode"] = fixture("reverse_geocode.json")
    out = (await client.call_tool("dv_locate", {"lat": 35.7, "lon": 51.4})).structured_content
    assert api.body() == {"location": {"latitude": 35.7, "longitude": 51.4}}
    assert out["city"] == {"id": 1, "name": "تهران", "slug": "tehran"} and out["district"]["id"] == 655
    assert out["street"] == "خ شهدای ژاندارمری" and out["place"].startswith("تهران")


async def test_dv_locate_without_address_outside_covered_area(client, api):
    api["/v8/places/find"] = fixture("find.json")
    api["/v8/map-discovery/reverse-geocode"] = lambda r: httpx.Response(
        404, json={"code": 5, "message": "district does not exist"}
    )
    out = (await client.call_tool("dv_locate", {"lat": 35.7, "lon": 51.4})).structured_content
    assert "address" not in out and out["city"]["id"] == 1


async def test_dv_place_search(client, api):
    api["/v8/map-discovery/map-view/get-search-results"] = fixture("location_search.json")
    out = (await client.call_tool("dv_place_search", {"query": "ونک"})).structured_content
    body = api.body()
    assert body["query"] == "ونک" and body["point"] == {"longitude": 51.4, "latitude": 35.7}
    first = out["places"][0]
    assert first["title"] == "برج آسمان ونک" and 35.75 < first["lat"] < 35.77 and 51.40 < first["lon"] < 51.42


async def test_dv_place_mapping(client, api):
    api["/v8/places/mapping"] = fixture("mapping.json")
    api["/v8/places/cities"] = fixture("cities.json")
    out = (await client.call_tool("dv_place_mapping", {"ids": [209, 99999]})).structured_content
    assert out["mapping"] == {"209": 655} and out["unverified"] == [99999] and "unchanged" not in out
    known = (await client.call_tool("dv_place_mapping", {"ids": [1]})).structured_content
    assert known["unchanged"] == [1]


async def test_province_scope_uses_the_province_id_of_a_city(client, ref):
    ref["/v8/postlist/w/approximate-post-count"] = fixture("count.json")
    out = (await client.call_tool("dv_count", {"city": "استان مشهد", "category": "light"})).structured_content
    assert ref.body()["city_ids"] == ["880"] and out["city"] == "استان مشهد"
    ref["/v8/postlist/w/search"] = fixture("search_light.json")
    found = await client.call_tool("dv_search", {"city": "استان مشهد", "category": "light"})
    assert "web_url" not in found.structured_content  # the site has no province address to point at
    bad = await client.call_tool("dv_search", {"city": "province:مشهد", "districts": ["ونک"]})
    assert bad.is_error and "not a province" in bad.content[0].text
    nope = await client.call_tool("dv_boundary", {"city": "استان مشهد"})
    assert nope.is_error and "per city" in nope.content[0].text


async def test_dv_boundary_overview_and_one_district_polygon(client, api):
    api["/v8/places/cities"] = fixture("cities.json")
    api["/v8/places/cities/1/geojson"] = fixture("geojson.json")
    feature = fixture("geojson.json")["geojson"]["features"][0]["properties"]
    overview = (await client.call_tool("dv_boundary", {})).structured_content
    assert overview["total_districts"] == 1 and overview["districts"][0]["id"] == feature["id"]
    box = overview["bbox"]
    assert len(box) == 4 and box[0] < box[2] and box[1] < box[3]
    one = (await client.call_tool("dv_boundary", {"district": str(feature["id"]), "max_points": 8})).structured_content
    ring = one["feature"]["geometry"]["coordinates"][0][0]
    assert one["feature"]["properties"]["name"] == feature["name"] and ring[0] == ring[-1] and len(ring) >= 3
    missing = await client.call_tool("dv_boundary", {"district": "zzzz"})
    assert missing.is_error
