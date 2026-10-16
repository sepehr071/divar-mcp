"""Places: cities, neighbourhoods, GPS lookups and the map location picker."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from pydantic import Field

from .core import HOUR, City, cached, cities, district_list, fold, match_cities, rank, resolve_cities, slim
from .http import ApiError, api
from .registry import tool
from .search import _haversine

TOP_CITIES = ("tehran", "mashhad", "karaj", "shiraz", "isfahan", "ahvaz", "tabriz")


def _city(c: dict[str, Any]) -> dict[str, Any]:
    return slim({k: c[k] for k in ("id", "name", "slug", "province_id", "lat", "lon")})


@tool("Find cities")
async def dv_cities(
    query: Annotated[
        str | None,
        Field(
            description="City name or slug, Persian or Latin, e.g. 'مشهد', 'kar'. Omit for the 7 biggest cities.",
            max_length=60,
        ),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=50, description="Max cities to return.")] = 10,
) -> dict[str, Any]:
    """City ids for a name (1,177 cities; Tehran is 1, Mashhad 3). Returns id, name, URL slug, province id and centre.

    Use first when you need a `city` for another tool (they also accept the name directly). Persian letter variants
    (ي/ی, ك/ک) are matched. Next: dv_districts for the neighbourhoods of a city, dv_search to look for ads.
    """
    allc = await cities()
    if query:
        hits = match_cities(query, allc, limit)
    else:
        hits = [c for s in TOP_CITIES for c in allc if c["slug"] == s][:limit]
    return {"total_cities": len(allc), "count": len(hits), "cities": [_city(c) for c in hits]}


@tool("City neighbourhoods")
async def dv_districts(
    city: City = "تهران",
    query: Annotated[str | None, Field(description="Part of a neighbourhood name, e.g. 'ونک'.", max_length=60)] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max neighbourhoods to return.")] = 30,
) -> dict[str, Any]:
    """Neighbourhoods (محله) of one city with ids, centre point and the main streets inside (Tehran has about 450).

    Use to get the district ids for a `districts` filter, or to pick coordinates for dv_nearby. dv_search and the other
    search tools take district names directly. Next: dv_neighbours for adjacent areas.
    """
    c = (await resolve_cities(city))[0]
    rows = await district_list(c["id"])
    if query:
        q = fold(query)
        rows = sorted((r for r in rows if rank(q, [fold(r["name"])]) < 9), key=lambda r: rank(q, [fold(r["name"])]))
    return {
        "city": c["name"],
        "city_id": c["id"],
        "total_districts": len(await district_list(c["id"])),
        "districts": [slim(r) for r in rows[:limit]],
    }


@tool("Adjacent neighbourhoods")
async def dv_neighbours(
    district_id: Annotated[int, Field(ge=1, le=10_000_000, description="District id from dv_districts, e.g. 655.")],
    city: City = "تهران",
) -> dict[str, Any]:
    """Neighbourhoods adjacent to a district, to widen a neighbourhood search to the surrounding area.

    `districts` is Divar's own adjacency list, which is short for some areas (Vanak lists one). `nearest` adds the
    closest districts of `city` by centroid distance (within 3 km, up to 6) so a widened search is not too narrow.
    Pass the ids (or names) as `districts` to dv_search.
    """
    data = await api(f"/places/districts/{district_id}/neighbours")
    rows = [
        {
            "id": d["id"],
            "name": d.get("name", ""),
            "lat": (d.get("centroid") or {}).get("latitude"),
            "lon": (d.get("centroid") or {}).get("longitude"),
        }
        for d in data.get("districts") or []
    ]
    all_rows = await district_list((await resolve_cities(city))[0]["id"])
    me = next((d for d in all_rows if d["id"] == district_id), None)
    listed = {r["id"] for r in rows} | {district_id}
    near = (
        sorted(
            (
                {"id": d["id"], "name": d["name"], "distance_m": _haversine(me["lat"], me["lon"], d["lat"], d["lon"])}
                for d in all_rows
                if d["id"] not in listed and d["lat"] and me["lat"]
            ),
            key=lambda d: d["distance_m"],
        )
        if me
        else []
    )
    return slim(
        {
            "district_id": district_id,
            "count": len(rows),
            "districts": rows,
            "nearest": [d for d in near if d["distance_m"] <= 3000][:6],
        }
    )


@tool("Locate a GPS point")
async def dv_locate(
    lat: Annotated[float, Field(ge=24, le=40, description="Latitude inside Iran, e.g. 35.7.")],
    lon: Annotated[float, Field(ge=43, le=64, description="Longitude inside Iran, e.g. 51.4.")],
) -> dict[str, Any]:
    """The city, neighbourhood and street address of a GPS point.

    Gives the city id and district id to use as filters. The street address is only known inside covered areas.
    Next: dv_nearby for ads around the point.
    """

    async def address() -> dict[str, Any]:
        try:
            return await api("/map-discovery/reverse-geocode", body={"location": {"latitude": lat, "longitude": lon}})
        except ApiError:
            return {}

    found, addr = await asyncio.gather(api(f"/places/find?lat={lat}&long={lon}"), address())
    c, d = found.get("city") or {}, found.get("district") or {}
    return slim(
        {
            "place": found.get("display_text"),
            "city": slim({"id": c.get("id"), "name": c.get("name"), "slug": c.get("slug")}),
            "district": slim({"id": d.get("id"), "name": d.get("name")}),
            "address": addr.get("address"),
            "street": addr.get("street"),
            "address_detailed": addr.get("address_detailed"),
            "restricted_area": found.get("is_restricted_area"),
        }
    )


@tool("Search a place on the map")
async def dv_place_search(
    query: Annotated[
        str, Field(min_length=2, max_length=80, description="Landmark, mall, street or area, e.g. 'برج میلاد', 'ونک'.")
    ],
    lat: Annotated[float, Field(ge=24, le=40, description="Map centre latitude to bias results.")] = 35.7,
    lon: Annotated[float, Field(ge=43, le=64, description="Map centre longitude to bias results.")] = 51.4,
) -> dict[str, Any]:
    """Map autocomplete: turn a landmark or street name into coordinates and a bounding box.

    Use the returned lat/lon with dv_nearby (ads around a place) or dv_locate. Persian text works best.
    """
    body = {
        "query": query,
        "point": {"longitude": lon, "latitude": lat},
        "zoom": 10,
        "history": [],
        "hide_my_location_row": True,
    }
    items = (await api("/map-discovery/map-view/get-search-results", body=body)).get("items") or []
    out = []
    for it in items:
        p = it.get("place") or {}
        b = p.get("bbox") or {}
        if not b:
            continue
        out.append(
            {
                "title": p.get("title", ""),
                "subtitle": p.get("subtitle", ""),
                "lat": round((b["min_latitude"] + b["max_latitude"]) / 2, 6),
                "lon": round((b["min_longitude"] + b["max_longitude"]) / 2, 6),
                "radius_m": p.get("radius"),
            }
        )
    return {"query": query, "count": len(out), "places": out}


@tool("Map old place ids")
async def dv_place_mapping(
    ids: Annotated[
        list[int],
        Field(min_length=1, max_length=50, description="Old district/city ids saved from earlier Divar data."),
    ],
) -> dict[str, Any]:
    """Convert old Divar place ids to the current ones (Divar merged and renumbered places).

    Use when an id from an old link or saved data returns nothing. `mapping` has the renumbered ids only; `unchanged`
    are current city ids; `unverified` ids are not in the renumber table and are not cities (districts cannot be
    checked here: see dv_districts).
    """
    table = (await api("/places/mapping")).get("ids") or {}
    known = {c["id"] for c in await cities()}
    return slim(
        {
            "mapping": {str(i): table[str(i)] for i in ids if str(i) in table},
            "unchanged": [i for i in ids if str(i) not in table and i in known],
            "unverified": [i for i in ids if str(i) not in table and i not in known],
        }
    )


def _bbox(coords: Any) -> list[float]:
    pts: list[list[float]] = []

    def walk(c: Any) -> None:
        if c and isinstance(c[0], (int, float)):
            pts.append(c)
        else:
            for x in c:
                walk(x)

    walk(coords)
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    return [round(min(lons), 5), round(min(lats), 5), round(max(lons), 5), round(max(lats), 5)] if pts else []


def _thin(coords: Any, max_points: int) -> Any:
    """Round to 5 decimals and keep at most `max_points` vertices per ring."""
    if coords and isinstance(coords[0], (int, float)):
        return [round(x, 5) for x in coords]
    if coords and isinstance(coords[0][0], (int, float)):
        step = max(1, -(-len(coords) // max_points))
        ring = [_thin(p, max_points) for p in coords[::step]]
        return ring if ring[0] == ring[-1] else [*ring, ring[0]]
    return [_thin(c, max_points) for c in coords]


@tool("City boundary")
async def dv_boundary(
    city: City = "تهران",
    district: Annotated[
        str | None,
        Field(
            description="A neighbourhood name or id (e.g. 'ونک', '315') to get its polygon. Omit for the city's bounding box and its neighbourhoods.",
            max_length=60,
        ),
    ] = None,
    max_points: Annotated[
        int, Field(ge=8, le=500, description="Max vertices per polygon ring (the full outline is thinned).")
    ] = 60,
    limit: Annotated[int, Field(ge=1, le=100, description="Max neighbourhoods listed in the overview.")] = 30,
) -> dict[str, Any]:
    """Neighbourhood borders of a city from Divar's GeoJSON: the city's bounding box, or one neighbourhood's polygon.

    Without `district`: the city bounding box [min_lon, min_lat, max_lon, max_lat] and the neighbourhoods with id, centre
    and box. With `district`: a GeoJSON Feature of that neighbourhood (coordinates [lon, lat], thinned to `max_points`
    per ring, so the shape is approximate). Use to draw an area or test a point against it; for a point's own
    neighbourhood use dv_locate. Next: dv_nearby with the centre.
    """
    c = (await resolve_cities(city))[0]
    if c.get("province"):
        raise ApiError("Boundaries exist per city, not per province.")

    async def load() -> list[dict[str, Any]]:
        return ((await api(f"/places/cities/{c['id']}/geojson")).get("geojson") or {}).get("features") or []

    feats = await cached(f"geojson:{c['id']}", HOUR, load)
    if not district:
        rows = [
            {
                "id": f["properties"].get("id"),
                "name": f["properties"].get("name", ""),
                "centroid": [
                    round((f["properties"].get("centroid") or {}).get("longitude", 0), 5),
                    round((f["properties"].get("centroid") or {}).get("latitude", 0), 5),
                ],
                "bbox": _bbox((f.get("geometry") or {}).get("coordinates")),
            }
            for f in feats
        ]
        boxes = [r["bbox"] for r in rows if r["bbox"]]
        city_box = (
            [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
            if boxes
            else []
        )
        return slim(
            {
                "city": c["name"],
                "bbox": city_box,
                "total_districts": len(rows),
                "count": len(rows[:limit]),
                "districts": rows[:limit],
            }
        )
    q = fold(district)
    hit = next((f for f in feats if q.isdigit() and str(f["properties"].get("id")) == q), None) or min(
        (f for f in feats if rank(q, [fold(f["properties"].get("name", ""))]) < 9),
        key=lambda f: rank(q, [fold(f["properties"].get("name", ""))]),
        default=None,
    )
    if hit is None:
        raise ApiError(f"No district '{district}' in the boundaries of {c['name']}. List them with dv_districts.")
    geom = hit.get("geometry") or {}
    return {
        "city": c["name"],
        "feature": {
            "type": "Feature",
            "properties": {k: hit["properties"].get(k) for k in ("id", "name", "slug")},
            "geometry": {"type": geom.get("type"), "coordinates": _thin(geom.get("coordinates") or [], max_points)},
        },
        "bbox": _bbox(geom.get("coordinates")),
        "approximate": True,
    }
