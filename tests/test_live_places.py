import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_dv_cities(client):
    data = await call(client, "dv_cities", {"query": "تهران"})
    assert data["total_cities"] > 1000 and data["cities"][0]["id"] == 1 and data["cities"][0]["slug"] == "tehran"
    top = await call(client, "dv_cities", {})
    assert [c["slug"] for c in top["cities"]][:2] == ["tehran", "mashhad"]


async def test_dv_districts_and_neighbours(client):
    data = await call(client, "dv_districts", {"city": "تهران", "query": "ونک"})
    assert data["total_districts"] > 300 and data["districts"][0]["name"] == "ونک"
    near = await call(client, "dv_neighbours", {"district_id": data["districts"][0]["id"]})
    assert near["count"] > 0 and all(d["id"] for d in near["districts"])


async def test_dv_locate(client):
    data = await call(client, "dv_locate", {"lat": 35.7, "lon": 51.4})
    assert data["city"]["id"] == 1 and data["district"]["id"] and data["place"]


async def test_dv_place_search(client):
    data = await call(client, "dv_place_search", {"query": "ونک"})
    assert data["places"] and 35 < data["places"][0]["lat"] < 36 and 51 < data["places"][0]["lon"] < 52


async def test_dv_place_mapping(client):
    data = await call(client, "dv_place_mapping", {"ids": [209, 1, 99999999]})
    assert data["mapping"] == {"209": 655} and data["unchanged"] == [1] and data["unverified"] == [99999999]
