"""Saved watches and price history. Divar has neither, so both live in a small local file (see `store.py`): a watch
remembers a search and the time it was last checked, the price trend remembers one median per search per day."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import Field

from . import core, store
from .core import (
    Brand,
    Category,
    City,
    Districts,
    Exclude,
    Filters,
    Keywords,
    MaxPrice,
    MinPrice,
    Pages,
    QueryText,
    build_query,
    fetch_cards,
    slim,
)
from .http import ApiError
from .market import _stats
from .registry import tool
from .search import dv_new_since

MAX_WATCHES = 50
MAX_SNAPSHOTS = 90


def _stamp() -> str:
    return core.now().strftime("%Y-%m-%dT%H:%M:%SZ")


@tool("Saved watches", local=True)
async def dv_watch(
    action: Annotated[
        Literal["create", "check", "list", "delete"],
        Field(
            description="'create' saves a search, 'check' returns the ads newer than the last check and moves the mark, 'list' shows the saved searches, 'delete' removes one."
        ),
    ],
    watch_id: Annotated[
        str | None,
        Field(
            description="check / delete: the id from 'create' or 'list'. check without it checks every watch.",
            max_length=16,
        ),
    ] = None,
    name: Annotated[
        str | None, Field(description="create: a label for you, e.g. 'cheap Pride in Karaj'.", max_length=80)
    ] = None,
    city: City = "تهران",
    category: Category = None,
    query: QueryText = None,
    districts: Districts = None,
    min_price: MinPrice = None,
    max_price: MaxPrice = None,
    brand_model: Brand = None,
    filters: Filters = None,
    keywords: Keywords = None,
    exclude: Exclude = None,
    limit: Annotated[int, Field(ge=1, le=50, description="check: max new ads per watch.")] = 10,
) -> dict[str, Any]:
    """Save a search and later get only the ads that appeared since the last check; stored in a local file, nothing is pushed.

    `create` takes the search arguments of dv_search (validated once, then stored with the current time as the mark).
    `check` runs dv_new_since for the watch and moves the mark to the newest ad it returned, so the next check shows only
    newer ads; it does not run by itself: call it from a schedule or when asked. The file is `~/.divar-mcp/state.json`
    (override with DIVAR_MCP_STATE); it holds only your searches. Next: dv_post for a new ad.
    """
    state = store.load()
    watches = state["watches"]
    if action == "list":
        return {"count": len(watches), "watches": [{"id": i, **w} for i, w in watches.items()]}
    if action == "create":
        if len(watches) >= MAX_WATCHES:
            raise ApiError(f"At most {MAX_WATCHES} watches; delete some with action='delete'.")
        search = slim(
            {
                "city": city,
                "category": category,
                "query": query,
                "districts": districts,
                "min_price": min_price,
                "max_price": max_price,
                "brand_model": brand_model,
                "filters": filters,
                "keywords": keywords,
                "exclude": exclude,
            }
        )
        q = await build_query(  # fail now on an unknown city / category / brand, not at the first check
            city, category, query, districts=districts, min_price=min_price, max_price=max_price,
            brand_model=brand_model, filters=filters,
        )  # fmt: skip
        wid = hashlib.sha1(f"{json.dumps(search, sort_keys=True)}{_stamp()}".encode()).hexdigest()[:8]
        watches[wid] = {
            "name": name or q.head().get("query") or category or "search",
            "search": search,
            "mark": _stamp(),
        }
        store.save(state)
        return slim({"id": wid, **watches[wid], "applied": q.applied, "ignored": q.ignored})
    ids = [watch_id] if watch_id else list(watches)
    missing = [i for i in ids if i not in watches]
    if missing:
        raise ApiError(f"No watch '{missing[0]}'. List them with action='list'.")
    if action == "delete":
        if not watch_id:
            raise ApiError("Give the watch_id to delete.")
        gone = watches.pop(watch_id)
        store.save(state)
        return {"deleted": watch_id, "name": gone["name"]}
    results = []
    for i in ids:
        w = watches[i]
        got = await dv_new_since(**w["search"], since=w["mark"], pages=3, limit=limit)
        w["mark"] = got["latest"]
        results.append(
            slim(
                {
                    "id": i,
                    "name": w["name"],
                    "since": got["since"],
                    "count": got["count"],
                    "ads": got["ads"],
                    "more": got.get("more"),
                }
            )
        )
    store.save(state)
    return {"checked": len(results), "new_ads": sum(r["count"] for r in results), "watches": results}


@tool("Price trend", local=True)
async def dv_price_trend(
    city: City = "تهران",
    category: Category = None,
    query: QueryText = None,
    districts: Districts = None,
    brand_model: Brand = None,
    filters: Filters = None,
    pages: Pages = 3,
    record: Annotated[bool, Field(description="Store today's median so later calls can show the trend.")] = True,
) -> dict[str, Any]:
    """The median asking price of a search over the days you have checked it: records one point per day in a local file.

    Divar keeps no price history, so the trend starts with your first call and grows each day you call it again
    (a scheduler helps). Each call scans the newest `pages` pages, stores median / quartiles / count for today (a second
    call the same UTC day replaces it) and returns the last 90 points with `change_pct` since the previous day. Use a
    narrow query: a mixed category's median is noise. For a one-off distribution use dv_market.
    """
    q = await build_query(city, category, query, districts=districts, brand_model=brand_model, filters=filters)
    cards = (await fetch_cards(q, pages=pages))["cards"]
    stats = _stats(cards)
    d = stats.get("price") or stats.get("rent")
    if not d:
        raise ApiError("No priced ads in this search, so there is no median to record.")
    key = hashlib.sha1(json.dumps([q.city_ids, q.category, q.query, q.applied], sort_keys=True).encode()).hexdigest()[
        :10
    ]
    today = _stamp()[:10]
    point = {"date": today, "n": d["n"], "median": d["median"], "p25": d["p25"], "p75": d["p75"]}
    state = store.load()
    entry = state["history"].setdefault(key, {"search": q.head(), "points": []})
    points = [p for p in entry["points"] if p["date"] != today]
    prev = points[-1] if points else None
    if record:
        entry["points"] = (points + [point])[-MAX_SNAPSHOTS:]
        store.save(state)
    return slim(
        {
            **q.head(),
            "metric": "price" if stats.get("price") else "monthly rent",
            "today": point,
            "change_pct": round((point["median"] - prev["median"]) / prev["median"] * 100, 1) if prev else None,
            "points": len(entry["points"]) if record else len(points),
            "history": (entry["points"] if record else points)[-30:],
            "caveat": "Medians of the newest asking prices; the sample changes between days, so small moves are noise.",
        }
    )
