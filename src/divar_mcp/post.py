"""One ad and what hangs off it: details, batches, seller, dealer, similar ads and side-by-side comparison."""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Any

from pydantic import Field

from .core import (
    SITE,
    TITLE_AREA,
    Q,
    Token,
    card,
    fetch_cards,
    fold,
    jalali_time,
    main_price,
    per_m2,
    price_flags,
    slim,
    to_int,
)
from .http import ApiError, api
from .registry import tool

FullText = Annotated[bool, Field(description="Do not cut the description (default cuts at 1500 characters).")]

_PRICE_TITLES = {"ودیعه", "اجارهٔ ماهانه", "اجاره ماهانه", "قیمت", "قیمت پایه", "قیمت کل", "قیمت هر متر"}
_SHARED = re.compile(r"هم[\s\u200c]?(?:خانه|خونه)|شریکی")


def _sections(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {s.get("section_name", ""): s.get("widgets") or [] for s in data.get("sections") or []}


def _clip(text: str, n: int | None) -> str:
    return text[:n] + "…" if n and len(text) > n else text


def crumb_searches(data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The ad's breadcrumb, narrowest first, as (name, search_data) pairs usable for 'similar ads'."""
    out = []
    for b in (data.get("seo") or {}).get("bread_crumb") or []:
        sd = b.get("search_data") or {}
        if (sd.get("form_data") or {}).get("data"):
            out.append((b.get("name", ""), sd))
    return out


def _iso(m: re.Match[str] | None) -> str | None:
    when = jalali_time(m.group(1)) if m else None
    return when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else None


def parse_post(token: str, data: dict[str, Any], *, full_text: bool = False, max_images: int = 6) -> dict[str, Any]:
    sec = _sections(data)
    web, seo, city = data.get("webengage") or {}, data.get("seo") or {}, data.get("city") or {}
    title, where, dates = "", "", ""
    for w in sec.get("TITLE", []):
        d = w.get("data") or {}
        if w.get("widget_type") == "LEGEND_TITLE_ROW":
            title = d.get("title", "")
        elif w.get("widget_type") == "EXPANDABLE_SECTION":
            where = d.get("title", "")
            dates = "\n".join(
                (x.get("data") or {}).get("text", "")
                for x in d.get("widget_list") or []
                if x.get("widget_type") == "DESCRIPTION_ROW"
            )
    desc = next(
        (
            (w.get("data") or {}).get("text", "")
            for w in sec.get("DESCRIPTION", [])
            if w.get("widget_type") == "DESCRIPTION_ROW"
        ),
        "",
    )
    images = [
        (i.get("image") or {}).get("url")
        for w in sec.get("IMAGE", [])
        for i in (w.get("data") or {}).get("items") or []
        if (i.get("image") or {}).get("url")
    ]
    specs: dict[str, str] = {}
    shown: dict[str, int | None] = {}  # exact prices of the spec rows; webengage rounds big numbers (float32)
    features, missing = [], []
    for w in sec.get("LIST_DATA", []):
        d = w.get("data") or {}
        if w.get("widget_type") == "GROUP_INFO_ROW":
            for it in d.get("items") or []:
                specs.setdefault(it.get("title", ""), it.get("value", ""))
        elif w.get("widget_type") == "UNEXPANDABLE_ROW":
            t, v = d.get("title", ""), d.get("value", "")
            if t in _PRICE_TITLES and "تومان" in v:
                shown[t] = to_int(v)
            elif t:
                specs.setdefault(t, v)
        elif w.get("widget_type") == "GROUP_FEATURE_ROW":
            for it in d.get("items") or []:
                (features if it.get("available") else missing).append(it.get("title", ""))
    loc = next(((w.get("data") or {}).get("location") or {} for w in sec.get("MAP", [])), {})
    pt = (loc.get("exact_data") or loc.get("fuzzy_data") or {}).get("point") or {}
    price = shown.get("قیمت کل") or shown.get("قیمت پایه") or shown.get("قیمت") or web.get("price") or 0
    credit = shown.get("ودیعه") or web.get("credit") or 0
    rent = shown.get("اجارهٔ ماهانه") or shown.get("اجاره ماهانه") or web.get("rent") or 0
    rental = bool(credit or rent) or "اجارهٔ ماهانه" in specs or "ودیعه" in specs
    pf = price_flags(price)
    area = to_int(specs.get("متراژ"))
    ppm = None if rental or not price or not area else shown.get("قیمت هر متر") or per_m2(price, area)
    updated = re.search(r"به\u200cروز\u200cرسانی:\s*(.+)", dates)
    published = re.search(r"انتشار آگهی:\s*(.+)", dates)
    bumped = re.search(
        r"آخرین نردبان:\s*(.+)", dates
    )  # a bump moves the ad to the top; the list's sort_date follows it
    out = {
        "token": token,
        "title": title,
        "price": price or None,
        "deposit": (credit or None) if rental else None,
        "rent": rent if rental else None,
        "full_deposit": rental and not rent and bool(credit),
        "negotiable": not rental and not price,
        "suspect_price": pf,
        "category": web.get("category"),
        "category_path": [
            b["name"]
            for b in reversed(seo.get("bread_crumb") or [])
            if ((b.get("search_data") or {}).get("form_data", {}).get("data") or {}).get("category")
        ],
        "brand_model": web.get("brand_model"),
        "city": city.get("name"),
        "city_id": to_int(city.get("city_id")),
        "district": (seo.get("web_info") or {}).get("district_persian"),
        "location_text": where,
        # Jalali text only when it could not be converted; `updated` only when it differs from the publication
        "published": published.group(1).strip() if published and not _iso(published) else None,
        "published_at": _iso(published),
        "bumped_at": _iso(bumped),
        "updated": updated.group(1).strip()
        if updated and not (published and updated.group(1).strip() == published.group(1).strip())
        else None,
        "expires": (seo.get("unavailable_after") or "")[:10] or None,
        "area": area,
        "price_per_m2": ppm,
        "specs": specs,
        "features": features,
        "missing_features": missing,
        "description": _clip(desc, None if full_text else 1500),
        "photos": web.get("image_count") or len(images),
        "images": images[:max_images],
        "lat": round(pt["latitude"], 5) if "latitude" in pt else None,
        "lon": round(pt["longitude"], 5) if "longitude" in pt else None,
        "approximate_location": loc.get("type") == "FUZZY" if pt else None,
        "seller_type": web.get("business_type") or None,
        "chat": (data.get("contact") or {}).get("chat_enabled"),
        "shared_housing": bool(_SHARED.search(f"{title} {desc}")) if rental else False,
        "url": (data.get("share") or {}).get("web_url") or f"{SITE}/v/{token}",
    }
    return slim(out)


async def fetch_post(token: str) -> dict[str, Any]:
    return await api(f"/posts-v2/web/{token}")


@tool("Ad details")
async def dv_post(
    token: Token,
    full_text: FullText = False,
    max_images: Annotated[int, Field(ge=0, le=30, description="How many full-size image URLs to return.")] = 6,
) -> dict[str, Any]:
    """Everything public about one ad: price in Toman, specs, features, description, images, map position, seller type.

    Prices: `price` (sale) or `deposit` + `rent` (rental; rent 0 + full_deposit = رهن کامل); `negotiable` true when the
    seller gave none; `suspect_price` marks placeholder prices. `specs` are the Persian spec rows of the ad (mileage,
    year, area, rooms...). `lat`/`lon` are fuzzed by about 500 m for most categories. `shared_housing` flags همخانه
    ads. No phone number: Divar shows it only to logged-in users, use `url` to contact the seller.
    Next: dv_similar, dv_appraise (is the price fair), dv_seller, dv_compare.
    """
    return parse_post(token, await fetch_post(token), full_text=full_text, max_images=max_images)


@tool("Several ads")
async def dv_posts(
    tokens: Annotated[
        list[str],
        Field(min_length=1, max_length=10, description="Ad tokens, e.g. ['gaRPIP2m', 'gaRPYuZk'] (at most 10)."),
    ],
) -> dict[str, Any]:
    """Details of up to 10 ads in one call (compact: specs and one image, description cut at 300 characters).

    Use to read a shortlist from dv_search. An ad that was removed shows an `error` instead of failing the call.
    """

    async def one(t: str) -> dict[str, Any]:
        try:
            p = parse_post(t, await fetch_post(t), max_images=1)
        except ApiError as e:
            return {"token": t, "error": str(e)}
        p["description"] = _clip(p.get("description", ""), 300)
        return p

    ads = await asyncio.gather(*(one(t) for t in dict.fromkeys(tokens)))
    return {"count": len(ads), "ads": list(ads)}


def _cheaper(a: int | None, b: int | None) -> int | None:
    return round((b - a) / b * 100) if a and b else None


def _unit_price(a: dict[str, Any]) -> int | None:
    """The number ads are compared by: price per m2 when the ad has an area (flats, land), else the main price."""
    return a.get("price_per_m2") or main_price(a)


@tool("Compare ads")
async def dv_compare(
    tokens: Annotated[list[str], Field(min_length=2, max_length=6, description="2 to 6 ad tokens to compare.")],
) -> dict[str, Any]:
    """Side by side: the price of each ad and only the specs that differ (the identical ones are listed once).

    Gives `cheapest`, per other ad `cheapest_saves_pct` (how much less the cheapest costs than that ad; per metre when
    every priced ad has an area, see `compared_by`), the differing
    spec rows with each ad's value, shared specs, and features one ad has and another lacks. Works across ads of the
    same kind (two cars, two flats); specs are the Persian rows of each ad.
    """
    uniq = list(dict.fromkeys(tokens))
    if len(uniq) < 2:
        raise ApiError("Give at least two different tokens.")
    posts = await asyncio.gather(*(fetch_post(t) for t in uniq))
    ads = [parse_post(t, d, max_images=1) for t, d in zip(uniq, posts, strict=True)]
    keys = list(dict.fromkeys(k for a in ads for k in a.get("specs", {})))
    diff, same = {}, {}
    for k in keys:
        vals = {a["token"]: a.get("specs", {}).get(k) for a in ads}
        if len({v for v in vals.values()}) == 1:
            same[k] = next(iter(vals.values()))
        else:
            diff[k] = vals
    feats = {a["token"]: set(a.get("features", [])) for a in ads}
    all_feats = set().union(*feats.values())
    feat_diff = {
        f: [t for t in feats if f in feats[t]] for f in sorted(all_feats) if any(f not in feats[t] for t in feats)
    }
    by_m2 = all(a.get("price_per_m2") for a in ads if main_price(a)) and any(a.get("price_per_m2") for a in ads)
    value = _unit_price if by_m2 else main_price  # a 61 m flat and a 180 m flat are only comparable per metre
    ranked = [a for a in ads if main_price(a)]
    best = min(ranked, key=lambda a: value(a) or 0) if ranked else None
    summary = [
        slim(
            {
                "token": a["token"],
                "title": a["title"],
                "price": a.get("price"),
                "deposit": a.get("deposit"),
                "rent": a.get("rent"),
                "area": a.get("area"),
                "price_per_m2": a.get("price_per_m2"),
                "suspect_price": a.get("suspect_price"),
                "district": a.get("district"),
                "seller_type": a.get("seller_type"),
                "cheapest_saves_pct": _cheaper(value(best), value(a)) if best and a is not best else None,
                "url": a["url"],
            }
        )
        for a in ads
    ]
    return slim(
        {
            "cheapest": best["token"] if best else None,
            "compared_by": "price_per_m2" if by_m2 else "price",
            "ads": summary,
            "differences": diff,
            "shared_specs": same,
            "features_only_in": feat_diff,
        }
    )


def _count(text: str) -> int | None:
    return to_int(text)


@tool("Seller and their ads")
async def dv_seller(
    token: Token,
    limit: Annotated[int, Field(ge=1, le=50, description="Max of the seller's other ads to return.")] = 12,
) -> dict[str, Any]:
    """The seller behind an ad: how many personal and business ads they have, the business card (name, brand_token) and their ads.

    Use to judge a seller (one flat or fifty cars?) and to list their other ads. For a dealer's whole page call
    dv_dealer with the brand_token (`dv_post` does not give one). A personal seller has no business card (`seller_kind`). No phone number or identity is exposed.
    """

    async def business_card() -> dict[str, Any]:
        try:
            return await api(f"/premium-user/post-page/web/business-data/{token}/lazy", body={})
        except ApiError as e:
            if "HTTP 404" in str(e):  # a personal seller has no business: Divar answers 404 "business not found"
                return {}
            raise

    owner, biz = await asyncio.gather(
        api(f"/premium-user/post-page/{token}/owner-posts-list", body={}), business_card()
    )
    widgets = (owner.get("page") or {}).get("widget_list") or []
    counts = {}
    for w in widgets:
        if w.get("widget_type") == "GROUP_INFO_ROW":
            for it in (w.get("data") or {}).get("items") or []:
                counts["personal" if "شخصی" in it.get("title", "") else "business"] = _count(it.get("value", ""))
    ads = [card(w) for w in widgets if w.get("widget_type") == "POST_ROW"]
    business = None
    for w in biz.get("widget_list") or []:
        d = w.get("data") or {}
        if w.get("widget_type") == "EVENT_ROW":
            payload = (d.get("action") or {}).get("payload") or {}
            business = slim(
                {
                    "name": d.get("title"),
                    "active_ads": _count(d.get("subtitle", "")),
                    "brand_token": payload.get("brand_token"),
                }
            )
    return slim(
        {
            "token": token,
            "personal_ads": counts.get("personal"),
            "business_ads": counts.get("business"),
            "business": business,
            "seller_kind": "business" if business else "personal (no business account)",
            "count": len(ads[:limit]),
            "ads": ads[:limit],
        }
    )


@tool("Dealer page")
async def dv_dealer(
    brand_token: Annotated[
        str,
        Field(
            min_length=4,
            max_length=16,
            pattern=r"^[A-Za-z0-9_-]+$",
            description="Dealer id from dv_seller, e.g. 'ZbeypyTr'.",
        ),
    ],
) -> dict[str, Any]:
    """A business account's public page: name, active ad count, member since and its first 11 ads.

    Divar's web API gives only the first batch of a dealer's ads (no paging); search with the dealer's name or use
    the ad filters for more. Ads are compact rows (token, title, price, district, age, photos, tag); the ad link is
    `url_prefix` + token and `age_h` is hours since the last bump.
    """
    data = await api(f"/premium-user/web/business/brand-landing/{brand_token}", body={})
    name = count = joined = None
    for w in data.get("header_widget_list") or []:
        d = w.get("data") or {}
        if w.get("widget_type") == "LEGEND_TITLE_ROW":
            name, count = d.get("title"), _count(d.get("subtitle", ""))
        elif w.get("widget_type") == "UNEXPANDABLE_ROW" and "عضویت" in d.get("title", ""):
            joined = d.get("value")
    drop = {"image", "url", "seller", "posted", "chat"}  # the dealer is the seller; open `url_prefix` + token
    ads = [
        {k: v for k, v in card(w).items() if k not in drop}
        for w in data.get("post_row_widget_list") or []
        if w.get("widget_type") == "POST_ROW"
    ]
    return slim(
        {
            "brand_token": brand_token,
            "name": name or data.get("title"),
            "active_ads": count,
            "member_since": joined,
            "count": len(ads),
            "url_prefix": f"{SITE}/v/",
            "ads": ads,
        }
    )


def _same_kind(levels: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    """Breadcrumb levels that still describe the same kind of ad as the narrowest one: same category, and the brand
    kept when the ad has one (so a Pride is never priced against every car)."""

    def data(sd: dict[str, Any]) -> dict[str, Any]:
        return (sd.get("form_data") or {}).get("data") or {}

    if not levels:
        return levels
    first = data(levels[0][1])
    cat = (first.get("category") or {}).get("str", {}).get("value")
    return [
        lv
        for lv in levels
        if (data(lv[1]).get("category") or {}).get("str", {}).get("value") == cat
        and ("brand_model" in data(lv[1]) or "brand_model" not in first)
    ]


async def comparables(
    data: dict[str, Any], token: str, *, need: int, pages: int, same_kind: bool = False
) -> tuple[list[dict[str, Any]], list[str]]:
    """Ads like this one: search the ad's breadcrumb, widening (brand+district -> brand -> category+district ...)
    until `need` priced ads (not the ad itself) are found. `same_kind` stops widening before the level that drops
    the brand or the category. Returns (cards, names of the levels used)."""
    city_id = to_int((data.get("city") or {}).get("city_id")) or 1
    web = (data.get("seo") or {}).get("web_info") or {}
    city = {"id": city_id, "name": web.get("city_persian", ""), "slug": ""}
    found: dict[str, dict[str, Any]] = {}
    used: list[str] = []
    levels = crumb_searches(data)
    for name, sd in _same_kind(levels) if same_kind else levels:
        q = Q([city], None, sd.get("query", ""), ((sd.get("form_data") or {}).get("data")) or {})
        got = await fetch_cards(q, pages=pages)
        used.append(name)
        found = {c["token"]: c for c in got["cards"] if c["token"] != token}
        if sum(1 for c in found.values() if main_price(c)) >= need:
            break
    return list(found.values()), used


@tool("Similar ads")
async def dv_similar(
    token: Token,
    limit: Annotated[int, Field(ge=1, le=50, description="Max similar ads.")] = 10,
    pages: Annotated[int, Field(ge=1, le=3, description="Pages scanned per breadcrumb level.")] = 1,
) -> dict[str, Any]:
    """Ads similar to one ad: same brand/model (or category) and neighbourhood, widening step by step until enough are found.

    Divar has no similar-ads endpoint; this searches the ad's own breadcrumb (brand+district, brand, category+district,
    category). `basis` names the levels used, narrowest first. Sorted newest first. For property, ads priced over 5x or under a fifth
    of this ad, or whose title area is under 0.4x or over 2.5x of its area (a 950B bulk listing next to a 180 m flat), are
    left out and counted in `excluded_outliers`. Next: dv_compare, dv_appraise.
    """
    data = await fetch_post(token)
    cards, used = await comparables(data, token, need=limit, pages=pages)
    me = parse_post(token, data, max_images=0)
    kept = [c for c in cards if not _outlier(c, me.get("price") or me.get("rent"), me.get("area"))]
    return slim(
        {
            "token": token,
            "basis": used,
            "count": len(kept[:limit]),
            "excluded_outliers": len(cards) - len(kept),
            "ads": kept[:limit],
        }
    )


def _outlier(c: dict[str, Any], price: int | None, area: int | None) -> bool:
    """For an ad with an area: a card far from it on price (5x either way) or on the area its title states (0.4x-2.5x)."""
    if not area:  # only property is judged: cars and phones have no area and a wide honest price range
        return False
    p = main_price(c)
    if price and p and not 0.2 <= p / price <= 5:
        return True
    m = TITLE_AREA.search(fold(c.get("title", "")))
    return bool(m and not 0.4 <= int(m.group(1)) / area <= 2.5)
