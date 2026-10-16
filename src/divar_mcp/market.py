"""Market intelligence from asking prices: distribution by district and day, and a fair-price check for one ad."""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Annotated, Any

from pydantic import Field

from .core import (
    Brand,
    Category,
    City,
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
    main_price,
    make_keep,
    sane,
    slim,
)
from .http import ApiError
from .post import comparables, fetch_post, parse_post
from .registry import tool


def _stats(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Price distribution of trustworthy cards: sale price, or deposit and rent for rentals."""
    sale = [c["price"] for c in cards if c.get("price") and not c.get("suspect_price")]
    rent = [c["rent"] for c in cards if c.get("rent")]
    deposit = [c["deposit"] for c in cards if c.get("deposit")]
    return slim({"price": dist(sane(sale)), "rent": dist(sane(rent)), "deposit": dist(sane(deposit))})


@tool("Market price statistics")
async def dv_market(
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
    pages: Pages = 4,
) -> dict[str, Any]:
    """Asking-price statistics of a search: min, p10, p25, median, p75, p90, max, mean; split by district and by day.

    Scans the newest `pages` pages (about 25 ads each) and drops negotiable and suspect prices (`excluded`) and, from the statistics, prices under a tenth of the median. Sale
    searches give `price`; rentals give `rent` and `deposit` (Toman). `by_district` lists the busiest neighbourhoods
    with ad count and median; `per_day` shows how many ads were posted per UTC date and their median, a rough
    trend. These are what sellers ask, not what deals close at, and a sample of the newest ads, not the whole
    market: use a narrow query or filters (one model, one area) for a meaningful median. Next: dv_cheapest,
    dv_best_for_budget, dv_appraise.
    """
    q = await build_query(
        city, category, query, districts=districts, min_price=min_price, max_price=max_price,
        min_deposit=min_deposit, max_deposit=max_deposit, brand_model=brand_model, has_photo=has_photo,
        recent=recent, filters=filters,
    )  # fmt: skip
    got = await fetch_cards(q, pages=pages, keep=make_keep(max_age_hours, keywords, exclude))
    cards = got["cards"]
    ranked = [(c, main_price(c)) for c in cards]
    by_d: dict[str, list[int]] = defaultdict(list)
    by_day: dict[str, list[int]] = defaultdict(list)
    for c, p in ranked:
        if p:
            by_d[c.get("district", "?")].append(p)
            by_day[c.get("posted", "")[:10]].append(p)
    districts_out = sorted(by_d.items(), key=lambda kv: -len(kv[1]))[:10]
    return slim(
        {
            **q.head(),
            "scanned": len(cards),
            "priced": sum(1 for _, p in ranked if p),
            "excluded": slim(
                {
                    "suspect_price": sum(1 for c in cards if c.get("suspect_price")),
                    "no_price": sum(1 for c, p in ranked if not p and not c.get("suspect_price")),
                }
            ),
            "stats": _stats(cards),
            "by_district": [{"district": d, "n": len(v), "median": round(median(v))} for d, v in districts_out],
            "per_day": [{"date": d, "n": len(v), "median": round(median(v))} for d, v in sorted(by_day.items()) if d],
            "newest_age_h": min((c.get("age_h", 0) for c in cards), default=None),
            "oldest_age_h": max((c.get("age_h", 0) for c in cards), default=None),
        }
    )


def _verdict(pct_cheaper: float) -> str:
    if pct_cheaper < 10:
        return "well below market"
    if pct_cheaper < 30:
        return "below market"
    if pct_cheaper <= 70:
        return "fair"
    if pct_cheaper <= 90:
        return "above market"
    return "well above market"


@tool("Is this price fair")
async def dv_appraise(
    token: Annotated[
        str | None,
        Field(
            min_length=6,
            max_length=16,
            pattern=r"^[A-Za-z0-9_-]+$",
            description="An ad to appraise: its price is ranked against similar ads. Omit to appraise a price you give.",
        ),
    ] = None,
    price: Annotated[
        int | None,
        Field(
            ge=1,
            description="Without a token: the price in Toman to check against the search below (monthly rent for rent categories).",
        ),
    ] = None,
    city: City = "تهران",
    category: Category = None,
    query: QueryText = None,
    districts: Districts = None,
    brand_model: Brand = None,
    filters: Filters = None,
    pages: Annotated[int, Field(ge=1, le=5, description="Pages scanned per comparison level.")] = 3,
) -> dict[str, Any]:
    """Is an ad's price (or a price you have in mind) high or low compared with similar ads? Percentile, verdict, confidence.

    With a `token`: compares its price (or monthly rent) with similar ads found from its own breadcrumb (brand +
    district, widening step by step). Without: give `price` plus category/query/filters. Returns `percentile` (share
    of comparable ads that are cheaper), `verdict` ('insufficient comparables', with no lean either way, when confidence is low and the call would be "well below/above market"), `vs_median_pct`, the fair band p25-p75 and `confidence`
    (high: 40+ comparable ads at the first level, medium: 15+, else low). Comparisons ignore year, mileage, area and
    condition: refine with `filters`/`query`, or dv_compare against specific ads. Asking prices only.
    """
    subject: dict[str, Any]
    if token:
        data = await fetch_post(token)
        post = parse_post(token, data, max_images=0)
        value = post.get("price") or post.get("rent")
        if not value:
            raise ApiError("This ad has no price (negotiable), so there is nothing to appraise.")
        comps, used = await comparables(data, token, need=40, pages=pages, same_kind=True)
        subject = slim(
            {
                "token": token,
                "title": post["title"],
                "price": post.get("price"),
                "rent": post.get("rent"),
                "deposit": post.get("deposit"),
            }
        )
        widened = len(used) > 1
    else:
        if not price:
            raise ApiError("Give a token, or a price with a category/query to compare against.")
        q = await build_query(city, category, query, districts=districts, brand_model=brand_model, filters=filters)
        comps = (await fetch_cards(q, pages=pages))["cards"]
        value, used, widened = price, [q.query or q.category or "all ads"], False
        subject = {"price": price}
    prices = sane([p for c in comps if (p := main_price(c))])
    stats = dist(prices)
    if not stats or len(prices) < 5:
        return slim(
            {
                "subject": subject,
                "comparable_ads": len(prices),
                "basis": used,
                "verdict": "not enough comparable ads (need 5+): widen the query or pick a broader category",
            }
        )
    cheaper = sum(1 for p in prices if p < value) / len(prices) * 100
    confidence = "high" if len(prices) >= 40 and not widened else "medium" if len(prices) >= 15 else "low"
    far = not (
        stats["median"] / 10 <= value <= stats["median"] * 10
    )  # a different kind of item, not a cheap or dear one
    if far:
        confidence = "low"
    verdict = _verdict(cheaper)
    thin = confidence == "low" and verdict.startswith("well")  # an extreme call on few or unlike ads is not a verdict
    return slim(
        {
            "subject": subject,
            "basis": used,
            "comparable_ads": len(prices),
            "stats": stats,
            "percentile": round(cheaper),
            "verdict": "insufficient comparables" if thin else verdict,
            "vs_median_pct": round((value - stats["median"]) / stats["median"] * 100),
            "fair_band": [stats["p25"], stats["p75"]],
            "confidence": confidence,
            "caveat": "Asking prices of the newest similar ads; year, mileage, area and condition are not compared."
            + (
                " The price is more than 10x away from the median: the ad is probably not like the others."
                if far
                else ""
            ),
        }
    )
