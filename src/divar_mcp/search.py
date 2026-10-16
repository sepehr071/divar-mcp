"""Ad search: listings, counts, cheapest, best for a budget, new ads since a time, export, and map search."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import re
from datetime import timedelta
from typing import Annotated, Any, Literal

from pydantic import Field

from .core import (
    SITE,
    TITLE_AREA,
    Brand,
    Category,
    City,
    Cursor,
    Districts,
    Exclude,
    Filters,
    HasPhoto,
    Keywords,
    MaxAge,
    MaxDeposit,
    MaxPrice,
    MinDeposit,
    MinPrice,
    Pages,
    QueryText,
    Recent,
    build_query,
    dist,
    fetch_cards,
    flag_outliers,
    fold,
    is_sale_real_estate,
    main_price,
    make_keep,
    now,
    parse_time,
    per_m2,
    sane,
    short_toman,
    slim,
)
from .http import ApiError, api
from .post import fetch_post, parse_post
from .registry import tool

Limit = Annotated[int, Field(ge=1, le=100, description="Max ads in the answer.")]


@tool("Search ads")
async def dv_search(
    city: City = "تهران",
    category: Category = None,
    query: QueryText = None,
    districts: Districts = None,
    min_price: MinPrice = None,
    max_price: MaxPrice = None,
    min_deposit: MinDeposit = None,
    max_deposit: MaxDeposit = None,
    brand_model: Brand = None,
    has_photo: HasPhoto = None,
    recent: Recent = None,
    filters: Filters = None,
    max_age_hours: MaxAge = None,
    keywords: Keywords = None,
    exclude: Exclude = None,
    sort: Annotated[
        Literal["newest", "cheapest", "expensive"],
        Field(
            description="'newest' is Divar's own order (default, pages by cursor). 'cheapest' / 'expensive' sort the ads fetched in this call by price (Divar cannot sort by price)."
        ),
    ] = "newest",
    pages: Pages = 1,
    limit: Limit = 20,
    cursor: Cursor = None,
    thumbnails: Annotated[  # read by server.compact, which strips `image` from ad rows unless this is true
        bool, Field(description="Include each ad's thumbnail URL in `image` (about 40 tokens per ad).")
    ] = False,
) -> dict[str, Any]:
    """Search Divar ads in one or more cities by category, text and filters; compact cards, prices in Toman.

    Card fields are described in the server instructions; `age_h` is hours since the last bump, `image` comes only
    with `thumbnails`, and `suspect_price` includes 'outlier' (at or under a tenth of the fetched median, 5+ priced
    ads). Newest first; the first 1-3 rows may be promoted older ads. Continue with `next_cursor` as `cursor`.
    Filters the category lacks come back in `ignored`; an empty Persian query is retried once with letters folded.
    Next: dv_post, dv_compare, dv_cheapest, dv_market.
    """
    q = await build_query(
        city, category, query, districts=districts, min_price=min_price, max_price=max_price,
        min_deposit=min_deposit, max_deposit=max_deposit, brand_model=brand_model, has_photo=has_photo,
        recent=recent, filters=filters,
    )  # fmt: skip
    got = await fetch_cards(
        q, pages=pages, cursor=cursor, want=limit if sort == "newest" else None,
        keep=make_keep(max_age_hours, keywords, exclude),
    )  # fmt: skip
    ads = got["cards"]
    flag_outliers(ads)
    if sort != "newest":
        priced = [c for c in ads if main_price(c)]
        priced.sort(key=lambda c: main_price(c) or 0, reverse=sort == "expensive")
        ads = priced + [c for c in ads if not main_price(c)]
    return slim(
        {
            **q.head(),
            "count": len(ads[:limit]),
            "ads": ads[:limit],
            "next_cursor": got["next_cursor"],
            "pages_fetched": got["pages"],
            "retried_with": got["retried_with"],
            "web_url": q.web_url(),
        }
    )


@tool("Count matching ads")
async def dv_count(
    city: City = "تهران",
    category: Category = None,
    query: QueryText = None,
    districts: Districts = None,
    min_price: MinPrice = None,
    max_price: MaxPrice = None,
    min_deposit: MinDeposit = None,
    max_deposit: MaxDeposit = None,
    brand_model: Brand = None,
    has_photo: HasPhoto = None,
    recent: Recent = None,
    filters: Filters = None,
) -> dict[str, Any]:
    """How many active ads match a search, in one cheap call (capped: 10,000 means '10,000 or more').

    Use to size a market, to test whether a filter combination is too narrow, or to compare cities and districts
    before fetching ads. Same arguments as dv_search.
    """
    q = await build_query(
        city, category, query, districts=districts, min_price=min_price, max_price=max_price,
        min_deposit=min_deposit, max_deposit=max_deposit, brand_model=brand_model, has_photo=has_photo,
        recent=recent, filters=filters,
    )  # fmt: skip
    data = await api(
        "/postlist/w/approximate-post-count", body={"search_data": q.search_data(), "city_ids": q.city_ids}
    )
    n = int(data.get("count") or 0)
    return slim({**q.head(), "count": n, "or_more": bool(data.get("count_exceeds_threshold"))})


def _scan_notes(cards: list[dict[str, Any]]) -> dict[str, int]:
    return slim(
        {
            "suspect_price": sum(1 for c in cards if c.get("suspect_price")),
            "no_price": sum(1 for c in cards if not c.get("suspect_price") and not main_price(c)),
        }
    )


@tool("Find cheapest ads")
async def dv_cheapest(
    city: City = "تهران",
    category: Category = None,
    query: QueryText = None,
    districts: Districts = None,
    min_price: MinPrice = None,
    max_price: MaxPrice = None,
    brand_model: Brand = None,
    has_photo: HasPhoto = None,
    recent: Recent = None,
    filters: Filters = None,
    max_age_hours: MaxAge = None,
    keywords: Keywords = None,
    exclude: Exclude = None,
    min_realistic_price: Annotated[
        int | None,
        Field(
            ge=0,
            description="Drop ads priced below this many Toman (seller junk prices like 1,000 are already flagged and skipped).",
        ),
    ] = None,
    include_outliers: Annotated[
        bool,
        Field(description="Also list ads priced under 10% of the scanned median (usually deposits, parts or bait)."),
    ] = False,
    pages: Pages = 3,
    limit: Limit = 10,
) -> dict[str, Any]:
    """The cheapest ads of a search, cheapest first. Skips negotiable ads, placeholder or absurd prices and far-below-median outliers.

    Divar cannot sort by price, so this scans the newest `pages` pages (about 25 ads each) and ranks them: raise
    `pages` for a wider scan or narrow with filters. Rent categories rank by monthly rent (deposit shown too). Ads
    under 10% of the median (`include_outliers` shows them) are usually not the thing searched for: a 100,000 Toman
    'car' is a transfer slip or a deposit. The answer says how many ads were scanned and skipped. Next: dv_post, dv_compare.
    """
    q = await build_query(
        city, category, query, districts=districts, min_price=min_price, max_price=max_price, brand_model=brand_model,
        has_photo=has_photo, recent=recent, filters=filters,
    )  # fmt: skip
    got = await fetch_cards(q, pages=pages, keep=make_keep(max_age_hours, keywords, exclude))
    cards = got["cards"]
    priced = sorted(p for c in cards if (p := main_price(c)))
    floor = min_realistic_price or 0
    if priced and not include_outliers:
        floor = max(floor, priced[len(priced) // 2] // 10)
    ranked = sorted((c for c in cards if (main_price(c) or 0) > floor), key=lambda c: main_price(c) or 0)
    outliers = sum(1 for p in priced if p <= floor)
    return slim(
        {
            **q.head(),
            "scanned": len(cards),
            "skipped": {**_scan_notes(cards), **slim({"below_floor": outliers})},
            "price_floor": floor or None,
            "count": len(ranked[:limit]),
            "ads": ranked[:limit],
            "next_cursor": got["next_cursor"],
        }
    )


@tool("Best ads for a budget")
async def dv_best_for_budget(
    budget: Annotated[
        int, Field(ge=1, description="Maximum price in Toman (rent categories: monthly rent), e.g. 1500000000.")
    ],
    city: City = "تهران",
    category: Category = None,
    query: QueryText = None,
    districts: Districts = None,
    min_price: MinPrice = None,
    brand_model: Brand = None,
    has_photo: HasPhoto = None,
    recent: Recent = None,
    filters: Filters = None,
    max_age_hours: MaxAge = None,
    keywords: Keywords = None,
    exclude: Exclude = None,
    pages: Pages = 3,
    limit: Annotated[int, Field(ge=1, le=50, description="Ads per list.")] = 8,
) -> dict[str, Any]:
    """Within a budget: the best-value ads (furthest below their peers' median) and the most you can get (closest to the budget).

    Scans the newest `pages` pages capped at `budget`. Each `best_value` ad has a `deal_score` (0-100) with `reasons`
    (distance below `budget_median`, photos, freshness; penalties for prices over 70% under the median and for notice,
    parking or co-op titles). Ads under 20% of the uncapped `market_median` and bait titles (وامدار, قدرالسهم, اقساط,
    پیش\u200cفروش...; kept if the word is in `query` / `keywords`) are dropped into `skipped_implausible`. Property for
    sale ranks by price per m2; the top 6 ad pages are opened for publish time, floor, parking and elevator (the
    `caveat` says the rest are unchecked). An unrealistic budget returns an empty `best_value` with a `note`. Use a
    narrow query or brand: a mixed category's median means nothing. Next: dv_appraise, dv_post.
    """
    q = await build_query(
        city, category, query, districts=districts, min_price=min_price, max_price=budget, brand_model=brand_model,
        has_photo=has_photo, recent=recent, filters=filters,
    )  # fmt: skip
    q_all = await build_query(
        city, category, query, districts=districts, brand_model=brand_model, has_photo=has_photo, recent=recent,
        filters=filters,
    )  # fmt: skip
    keep = make_keep(max_age_hours, keywords, exclude)
    inside, market = await _both(q, q_all, pages, keep)
    prices = sane([p for c in market["cards"] if (p := main_price(c))])
    stats = dist(prices)
    fit = [c for c in inside["cards"] if (main_price(c) or 0) and (main_price(c) or 0) <= budget]
    floor = round(stats["median"] * _IMPLAUSIBLE) if stats and len(prices) >= 10 else 0
    fit_all = len(fit)
    asked = f"{query or ''} {' '.join(keywords or [])}"
    fit = [c for c in fit if (main_price(c) or 0) >= floor and not is_bait(c, asked)]
    peers = dist(sane([main_price(c) for c in fit]))
    median = peers["median"] if peers else None
    by_m2 = await is_sale_real_estate(q.category)  # a 45 m flat is cheap in total, not per metre
    pool = await with_areas(fit) if by_m2 else fit
    unit = dist(sane([c["price_per_m2"] for c in pool])) if by_m2 else peers
    scored = (
        await rank_pool(pool, unit["median"] if unit else None, query)
        if by_m2
        else [deal_score(c, unit["median"] if unit else None, query) for c in pool]
    )
    best = sorted(scored, key=lambda c: (-c["deal_score"], -c.get("photos", 0), c.get("age_h", 1e9)))
    closest = sorted(fit, key=lambda c: -(main_price(c) or 0))
    return slim(
        {
            **q.head(),
            "budget": budget,
            "budget_median": median,
            "ranked_by": "price_per_m2" if by_m2 else "price",
            "median_price_per_m2": unit["median"] if by_m2 and unit else None,
            "no_area": len(fit) - len(pool),
            "market_median": stats["median"] if stats else None,
            "market_ads_priced": len(prices),
            "within_budget": len(fit),
            "skipped_implausible": fit_all - len(fit),
            "implausible_below": floor or None,
            "note": "Every ad in the budget looks like bait or co-op housing: the budget is far below this market."
            if fit_all and not fit
            else "No priced ad at or under the budget in the scanned pages: compare with `market_median`."
            if not fit_all
            else None,
            "best_value": best[:limit],
            "closest_to_budget": closest[:limit],
            "caveat": _CAVEAT,
            "next_cursor": inside["next_cursor"],
        }
    )


_CAVEAT = (
    "Asking prices only. Floor, parking, elevator and the true publication date are read from the ad page of the "
    "top few ads only (`checked`); `age_h` is hours since the last bump, not since publishing. Verify on the ad page."
)
_IMPLAUSIBLE = 0.2  # an ad under this share of the uncapped market median is bait / co-op / a deposit, not a deal
_BAIT = re.compile(
    r"فرصت طلایی|خانه[\s\u200c]?دار[\s\u200c]?(?:شو|شدن)|خانه اولی|فاقد مسکن|از دست نده|اقساط|وامدار|قدر.?السهم|سود.?تضمینی|سرمایه.?گذاری|پیش.?فروش"
)
_NOT_THE_ITEM = re.compile(r"اطلاعیه|پارکینگ|انباری|تعاونی|واگذاری مساکن")


def is_bait(c: dict[str, Any], asked: str = "") -> bool:
    """A bait / co-op / loan title, unless the searcher asked for that very word."""
    m = _BAIT.search(c.get("title", ""))
    return bool(m) and fold(m.group(0)) not in fold(asked)


async def with_areas(cards: list[dict[str, Any]], fetch_cap: int = 6) -> list[dict[str, Any]]:
    """The cards that have an area, with `area`, `area_from` ('title' or 'ad') and `price_per_m2`. Rows carry no area:
    it is read from the title ('۱۲۰ متر'), else from the ad page for at most `fetch_cap` ads (Divar answers 429 to bursts)."""
    need = []
    for c in cards:
        m = TITLE_AREA.search(fold(c.get("title", "")))
        if m and 15 <= int(m.group(1)) <= 5000:
            c["area"], c["area_from"] = int(m.group(1)), "title"
        else:
            need.append(c)

    async def one(c: dict[str, Any]) -> None:
        if (p := await read_ad(c)) and p.get("area"):
            c["area"], c["area_from"] = p["area"], "ad"

    await asyncio.gather(*(one(c) for c in need[:fetch_cap]))
    for c in cards:
        if c.get("area") and c.get("price"):
            c["price_per_m2"] = per_m2(c["price"], c["area"])
    return [c for c in cards if c.get("price_per_m2")]


async def read_ad(c: dict[str, Any]) -> dict[str, Any] | None:
    """Open the ad page once and copy what the list lacks onto the card: `published_h` (hours since the first
    publication, `age_h` is only the last bump), `basement`, `floor`, `no_parking`, `no_elevator`. None if it fails."""
    try:
        p = parse_post(c["token"], await fetch_post(c["token"]), max_images=0)
    except ApiError:
        return None
    floor = fold(p.get("specs", {}).get("طبقه"))
    lack = " ".join(p.get("missing_features", []))
    when = parse_time(p.get("published_at"))
    c.update(
        checked=True,
        published_h=round((now() - when).total_seconds() / 3600, 1) if when else None,
        basement="زیر" in floor,
        floor=int(floor) if floor.isdigit() else None,
        no_parking="پارکینگ ندارد" in lack,
        no_elevator="آسانسور ندارد" in lack,
    )
    return p


async def rank_pool(pool: list[dict[str, Any]], median: int | None, query: str | None, cap: int = 6) -> list[dict]:
    """Score per-m2 property ads, open the pages of the `cap` best (never more: Divar answers 429 to bursts) to learn
    publication time, floor, parking and elevator, then score everything again with that knowledge."""
    first = sorted((deal_score(c, median, query, True) for c in pool), key=lambda c: -c["deal_score"])
    top = {c["token"] for c in first[:cap]}
    await asyncio.gather(*(read_ad(c) for c in pool if c["token"] in top and not c.get("checked")))
    return [deal_score(c, median, query, True) for c in pool]


def deal_score(c: dict[str, Any], median: int | None, query: str | None = None, by_m2: bool = False) -> dict[str, Any]:
    """An ad with `deal_score` 0-100 and `reasons`: price (or price per m2 with `by_m2`) vs its peers' median (up to
    60), photos, freshness and chat (up to 40 together), minus penalties for bait-like prices and notice / parking /
    storage / co-op titles."""
    s = dict(c)
    score, why = 0, []
    if median:
        pct = round((median - (c["price_per_m2"] if by_m2 else main_price(c))) / median * 100)
        s["below_median_pct"] = pct
        if pct > 70:
            s["too_good"] = True
            score -= 30
            why.append(f"{pct}% under the median: probably bait or a wrong price (-30)")
        elif pct > 0:
            pts = min(pct, 30) * 2
            score += pts
            why.append(f"{pct}% below the median (+{pts})")
        else:
            why.append(f"{'at' if pct == 0 else f'{-pct}% above'} the median (+0)")
    if c.get("photos", 0) >= 3:
        score += 15
        why.append("3+ photos (+15)")
    if c.get("published_h") is not None:
        if c["published_h"] <= 24:
            score += 15
            why.append("published in the last 24 h (+15)")
        elif c.get("age_h") is not None and c["age_h"] <= 24:
            why.append(f"only re-bumped: first published {round(c['published_h'] / 24)} days ago (+0)")
    elif c.get("age_h") is not None and c["age_h"] <= 24:
        score += 5
        why.append("last bumped in the last 24 h; the first publication time is unknown (+5)")
    for flag, label, pts in (
        ("basement", "below-ground floor (زیرهمکف / زیرزمین)", 20),
        ("no_parking", "no parking", 10),
    ):
        if c.get(flag):
            score -= pts
            why.append(f"{label} (-{pts})")
    if c.get("no_elevator") and (c.get("floor") or 0) >= 3:
        score -= 5
        why.append(f"no elevator on floor {c['floor']} (-5)")
    if c.get("chat"):
        score += 10
        why.append("chat enabled (+10)")
    if _NOT_THE_ITEM.search(f"{c.get('title', '')} {c.get('seller', '')}") and not _NOT_THE_ITEM.search(query or ""):
        score -= 40
        why.append("looks like a notice, parking, storage or co-op housing transfer, not a normal sale (-40)")
    s["deal_score"], s["reasons"] = max(0, min(100, score)), why
    return s


@tool("Find deals")
async def dv_find_deals(
    city: City = "تهران",
    category: Category = None,
    query: QueryText = None,
    districts: Districts = None,
    min_price: MinPrice = None,
    max_price: MaxPrice = None,
    brand_model: Brand = None,
    has_photo: HasPhoto = None,
    recent: Recent = None,
    filters: Filters = None,
    max_age_hours: MaxAge = None,
    keywords: Keywords = None,
    exclude: Exclude = None,
    min_discount_pct: Annotated[
        int, Field(ge=5, le=60, description="Only ads at least this many percent below the median.")
    ] = 15,
    pages: Pages = 3,
    limit: Annotated[int, Field(ge=1, le=50, description="Max deals.")] = 8,
) -> dict[str, Any]:
    """Ads priced well below the median of the same search, without a budget: scored deals, with bait and junk prices removed.

    Scans the newest `pages` pages and keeps ads `min_discount_pct` to 70% below the median (price per m2 for property
    for sale). Left out: suspect and placeholder prices, ads under 20% of the median (deposits, parts, bait), bait or
    co-op titles (وامدار, قدرالسهم, اقساط, پیش\u200cفروش...; unless you ask for the word) and notice / parking titles.
    Use a narrow query or filters: the median of a mixed category means nothing. Asking prices only. Next: dv_post,
    dv_appraise, dv_compare.
    """
    q = await build_query(
        city, category, query, districts=districts, min_price=min_price, max_price=max_price, brand_model=brand_model,
        has_photo=has_photo, recent=recent, filters=filters,
    )  # fmt: skip
    got = await fetch_cards(q, pages=pages, keep=make_keep(max_age_hours, keywords, exclude))
    cards = got["cards"]
    asked = f"{query or ''} {' '.join(keywords or [])}"
    priced = [c for c in cards if main_price(c)]
    stats = dist(sane([main_price(c) for c in priced]))
    if not stats or len(priced) < 8:
        return slim(
            {**q.head(), "scanned": len(cards), "priced": len(priced), "deals": [],
             "note": "Too few priced ads (need 8+) to tell a deal from the market: widen the search or raise `pages`."}
        )  # fmt: skip
    floor = round(stats["median"] * _IMPLAUSIBLE)
    ok = [c for c in priced if main_price(c) >= floor and not is_bait(c, asked)]
    by_m2 = await is_sale_real_estate(q.category)
    pool = await with_areas(ok) if by_m2 else ok
    unit = dist(sane([c["price_per_m2"] if by_m2 else main_price(c) for c in pool]))
    scored = []
    if unit:
        scored = (
            await rank_pool(pool, unit["median"], query)
            if by_m2
            else [deal_score(c, unit["median"], query) for c in pool]
        )
    deals = [c for c in scored if min_discount_pct <= c.get("below_median_pct", 0) <= 70 and c["deal_score"] > 0]
    deals.sort(key=lambda c: (-c["deal_score"], -c["below_median_pct"]))
    return slim(
        {
            **q.head(),
            "scanned": len(cards),
            "priced": len(priced),
            "skipped": {"implausible_or_bait": len(priced) - len(ok), "no_area": len(ok) - len(pool)},
            "ranked_by": "price_per_m2" if by_m2 else "price",
            "median": unit["median"] if unit else None,
            "count": len(deals[:limit]),
            "deals": deals[:limit],
            "caveat": _CAVEAT,
            "next_cursor": got["next_cursor"],
        }
    )


async def _both(q, q_all, pages, keep):
    return await asyncio.gather(fetch_cards(q, pages=pages, keep=keep), fetch_cards(q_all, pages=pages, keep=keep))


@tool("New ads since a time")
async def dv_new_since(
    city: City = "تهران",
    category: Category = None,
    query: QueryText = None,
    districts: Districts = None,
    min_price: MinPrice = None,
    max_price: MaxPrice = None,
    min_deposit: MinDeposit = None,
    max_deposit: MaxDeposit = None,
    brand_model: Brand = None,
    has_photo: HasPhoto = None,
    filters: Filters = None,
    keywords: Keywords = None,
    exclude: Exclude = None,
    since: Annotated[
        str | None,
        Field(
            description="UTC timestamp like 2026-10-16T01:50:00Z (the `latest` of the previous call). Omit to use `hours`."
        ),
    ] = None,
    hours: Annotated[
        float, Field(gt=0, le=72, description="Used when `since` is omitted: ads from the last N hours.")
    ] = 1,
    pages: Pages = 3,
    limit: Limit = 30,
) -> dict[str, Any]:
    """Ads published after a moment, newest first: poll this to watch a search (a new flat, a cheap car) without repeats.

    Call it, remember `latest`, and pass it as `since` next time: only ads newer than that come back. Promoted old
    ads are excluded by date. `more` is true when the page limit was reached and even more new ads exist. Nothing is
    stored or pushed: the caller (or a scheduler) decides when to poll.
    """
    q = await build_query(
        city, category, query, districts=districts, min_price=min_price, max_price=max_price,
        min_deposit=min_deposit, max_deposit=max_deposit, brand_model=brand_model, has_photo=has_photo, filters=filters,
    )  # fmt: skip
    cutoff = parse_time(since) if since else now() - timedelta(hours=hours)
    if cutoff is None:
        raise ApiError("since must be an ISO UTC timestamp like 2026-10-16T01:50:00Z.")
    kw = make_keep(None, keywords, exclude)
    got = await fetch_cards(
        q, pages=pages, keep=lambda c: kw(c) and parse_time(c["posted"]) > cutoff, stop_before=cutoff
    )
    ads = got["cards"]
    latest = max((c["posted"] for c in ads), default=cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return slim(
        {
            **q.head(),
            "since": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "latest": latest,
            "count": len(ads[:limit]),
            "ads": ads[:limit],
            "more": len(ads) > limit or (got["next_cursor"] is not None and not got["stopped"]),
        }
    )


_COLUMNS = ["token", "title", "price", "deposit", "rent", "district", "posted", "photos", "seller", "info", "url"]


@tool("Export ads")
async def dv_export(
    city: City = "تهران",
    category: Category = None,
    query: QueryText = None,
    districts: Districts = None,
    min_price: MinPrice = None,
    max_price: MaxPrice = None,
    min_deposit: MinDeposit = None,
    max_deposit: MaxDeposit = None,
    brand_model: Brand = None,
    has_photo: HasPhoto = None,
    recent: Recent = None,
    filters: Filters = None,
    max_age_hours: MaxAge = None,
    keywords: Keywords = None,
    exclude: Exclude = None,
    format: Annotated[
        Literal["csv", "jsonl"], Field(description="'csv' (header + rows) or 'jsonl' (one JSON ad per line).")
    ] = "csv",
    pages: Pages = 4,
    limit: Annotated[int, Field(ge=1, le=300, description="Max rows.")] = 100,
) -> dict[str, Any]:
    """The ads of a search as CSV or JSON lines text, ready to save to a spreadsheet or analyse.

    Same arguments as dv_search. Columns: token, title, price, deposit, rent (Toman), district, posted (UTC), photos,
    seller, info, url. Missing prices are empty. The text is in `data`; write it to a file yourself.
    """
    q = await build_query(
        city, category, query, districts=districts, min_price=min_price, max_price=max_price,
        min_deposit=min_deposit, max_deposit=max_deposit, brand_model=brand_model, has_photo=has_photo,
        recent=recent, filters=filters,
    )  # fmt: skip
    got = await fetch_cards(q, pages=pages, want=limit, keep=make_keep(max_age_hours, keywords, exclude))
    rows = got["cards"][:limit]
    if format == "jsonl":
        text = "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    else:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=_COLUMNS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        text = buf.getvalue()
    return slim({**q.head(), "format": format, "rows": len(rows), "next_cursor": got["next_cursor"], "data": text})


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    p = math.pi / 180
    a = (
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    )
    return round(12_742_000 * math.asin(math.sqrt(a)))


def pin(p: dict[str, Any], lat: float, lon: float) -> dict[str, Any]:
    c, f = p.get("map_post_card") or {}, p.get("map_pin_feature") or {}
    out: dict[str, Any] = {"token": c.get("token", ""), "title": c.get("title", "")}
    for pf in c.get("price_fields") or []:
        t = pf.get("title", "")
        key = (
            "deposit"
            if t.startswith("ودیعه")
            else "rent"
            if t.startswith("اجاره")
            else "price_per_m2"  # "متری:" chip; the "قیمت:" chip is the total
            if t.startswith("متری")
            else "price"
        )
        out[key] = short_toman(pf.get("value"))
    out["chips"] = [x["title"] for x in c.get("chips") or [] if x.get("title")]
    area = next((int(m.group(1)) for t in out["chips"] if (m := re.fullmatch(r"(\d+) متر", fold(t)))), None)
    if area and out.get("price") and "deposit" not in out:  # the chip is rounded text ('۲۹۱ میلیون'): recompute
        out["area"], out["price_per_m2"] = area, per_m2(out["price"], area)
    out["lat"], out["lon"] = round(f.get("lat", 0), 5), round(f.get("lon", 0), 5)
    out["approximate_location"] = f.get("approximate_location")
    out["distance_m"] = _haversine(lat, lon, f["lat"], f["lon"]) if "lat" in f and "lon" in f else None
    out["url"] = f"{SITE}/v/{out['token']}"
    return slim(out)


@tool("Ads near a point")
async def dv_nearby(
    lat: Annotated[
        float, Field(ge=24, le=40, description="Latitude, e.g. 35.7. Get one from dv_place_search or dv_districts.")
    ],
    lon: Annotated[float, Field(ge=43, le=64, description="Longitude, e.g. 51.4.")],
    category: Annotated[
        str,
        Field(
            description="Required: the map shows nothing without a category. Slug or Persian name, e.g. 'apartment-sell', 'apartment-rent', 'light'. List them with dv_categories."
        ),
    ],
    radius_m: Annotated[int, Field(ge=100, le=20_000, description="Search radius in metres.")] = 1000,
    query: QueryText = None,
    min_price: MinPrice = None,
    max_price: MaxPrice = None,
    min_deposit: MinDeposit = None,
    max_deposit: MaxDeposit = None,
    brand_model: Brand = None,
    filters: Filters = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max ads in the answer.")] = 20,
) -> dict[str, Any]:
    """Ads on the map around a GPS point, nearest first, with coordinates, distance, price chips and a thumbnail.

    Up to 200 ads per area are available (`total_in_area` is the true count). Pin positions are fuzzed by about 500 m
    for most categories (`approximate_location`), so distances are approximate. A category is required (Divar's map
    returns nothing without one). Map prices are rounded in the site's display (1.5 billion = 1,500,000,000); sale
    pins give the total `price` and, for homes, `price_per_m2`. Next: dv_post for details.
    """
    if not (category or "").strip() or category.strip().upper() == "ROOT":
        raise ApiError("dv_nearby needs a category (Divar's map shows nothing without one): see dv_categories.")
    found = await api(f"/places/find?lat={lat}&long={lon}")
    city_id = (found.get("city") or {}).get("id")
    q = await build_query(
        str(city_id or 1), category, query, min_price=min_price, max_price=max_price, min_deposit=min_deposit,
        max_deposit=max_deposit, brand_model=brand_model, filters=filters,
    )  # fmt: skip
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * max(math.cos(math.radians(lat)), 0.01))
    body = {
        "search_data": q.search_data(),
        "camera_info": {
            "bbox": {
                "min_latitude": lat - dlat,
                "min_longitude": lon - dlon,
                "max_latitude": lat + dlat,
                "max_longitude": lon + dlon,
            },
            "zoom": 14,
        },
        "city_ids": q.city_ids,
    }
    data = await api("/mapview/viewport", body=body)
    pins = [pin(p, lat, lon) for p in data.get("posts") or []]
    pins = [p for p in pins if p.get("distance_m") is None or p["distance_m"] <= radius_m * 1.5]
    pins.sort(key=lambda p: p.get("distance_m", 10**9))
    return slim(
        {
            **q.head(),
            "place": found.get("display_text"),
            "total_in_area": data.get("count"),
            "count": len(pins[:limit]),
            "ads": pins[:limit],
        }
    )
