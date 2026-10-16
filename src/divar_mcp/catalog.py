"""Catalog: category tree, per-category filters and their options, query suggestions."""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Any

from pydantic import Field

from .core import (
    Category,
    City,
    categories,
    cities,
    district_list,
    filter_specs,
    fold,
    match_categories,
    option_tree,
    resolve_category,
    resolve_cities,
    search_options,
    slim,
)
from .http import ApiError, api
from .registry import tool


@tool("Browse categories")
async def dv_categories(
    query: Annotated[
        str | None,
        Field(description="Part of a category name or slug, Persian or Latin: 'موبایل', 'apartment'.", max_length=60),
    ] = None,
    parent: Annotated[
        str | None,
        Field(
            description="List the direct children of this category slug, e.g. 'vehicles' or 'real-estate'. Omit for the 10 top-level categories."
        ),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max categories to return.")] = 30,
) -> dict[str, Any]:
    """The Divar category tree (236 nodes, up to 4 levels): slug, Persian name, path and whether it is a leaf.

    Use to find the `category` slug for dv_search and the other tools (leaf slugs such as `light` = cars and vans,
    `apartment-sell`, `apartment-rent`, `mobile-phones`). Filters exist per category: dv_filters.
    """
    flat = await categories()
    if query:
        rows = match_categories(query, flat, limit)
    elif parent:
        rows = [c for c in flat if c["parent"] == (await resolve_category(parent))][:limit]
    else:
        rows = [c for c in flat if c["level"] == 1]
    return {
        "total_categories": len(flat),
        "count": len(rows),
        "categories": [
            slim({"slug": c["slug"], "name": c["name"], "path": " > ".join(c["path"][:-1]), "leaf": c["leaf"]})
            for c in rows
        ],
    }


@tool("Category filters")
async def dv_filters(category: Category, city: City = "تهران") -> dict[str, Any]:
    """Every filter a category supports: key, type, Persian label, unit and the allowed options.

    Use before dv_search when you need more than price and district. Pass the keys in `filters`:
    number_range -> {'size': {'min': 80, 'max': 120}}, repeated_string -> {'rooms': ['دو']}, str -> {'gearbox': 'automatic'},
    boolean -> {'parking': true}. `lazy` filters (districts, brand_model) have long option lists: use
    dv_filter_options or the `districts` / `brand_model` arguments of dv_search. Rent categories filter `credit`
    (deposit) and `rent`, sale categories `price`; both in Toman.
    """
    slug = await resolve_category(category)
    if not slug:
        raise ApiError("Give a category (see dv_categories); filters differ per category.")
    c = (await resolve_cities(city))[0]
    specs = await filter_specs(slug, c["id"])
    out = []
    for s in specs.values():
        opts = [f"{v}={label}" if label != v else v for v, label in s.options]
        out.append(
            slim(
                {
                    "key": s.key,
                    "type": s.type,
                    "label": s.label,
                    "unit": s.unit,
                    "options": opts[:40],
                    "more_options": len(opts) - 40 if len(opts) > 40 else None,
                    "steps": s.steps[:25],
                    "typical_min_max": list(s.hint) if s.hint else None,
                    "lazy": s.lazy,
                }
            )
        )
    return {"category": slug, "count": len(out), "filters": out}


def _node(n: dict[str, Any]) -> dict[str, Any]:
    d = n.get("data") or {}
    out = {"value": d.get("key", ""), "label": d.get("title", "")}
    if kids := n.get("children"):
        out["models_count"] = len(kids)
    return out


@tool("Filter options")
async def dv_filter_options(
    category: Category,
    filter: Annotated[
        str,
        Field(
            description="Filter key from dv_filters, usually 'brand_model' (cars, phones, ...) or 'districts'.",
            max_length=40,
        ),
    ] = "brand_model",
    city: City = "تهران",
    query: Annotated[
        str | None,
        Field(
            description="Text to look up inside the filter, e.g. 'سمند' or 'ونک'. Without it the whole list (or one brand) is returned.",
            max_length=60,
        ),
    ] = None,
    brand: Annotated[
        str | None,
        Field(description="brand_model only: a brand key (e.g. 'Peugeot') to list its models.", max_length=60),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="Max options to return.")] = 40,
) -> dict[str, Any]:
    """Allowed values of a filter: brand and model keys (161 car brands), districts, or any option list.

    With `query` it uses Divar's own typed search (best way to turn 'پراید' into the key 'Pride 111'). Without it:
    all brands (key, title, model count), or the models of one `brand`. Pass the keys to dv_search as
    `brand_model` / `filters`.
    """
    slug = await resolve_category(category)
    if not slug:
        raise ApiError("Give a category (see dv_categories); options differ per category.")
    c = (await resolve_cities(city))[0]
    spec = (await filter_specs(slug, c["id"])).get(filter)
    if spec is None:
        raise ApiError(f"Category {slug} has no filter '{filter}'. See dv_filters.")
    if query and spec.lazy:
        hits = await search_options(spec, query, c["id"])
        return {"filter": filter, "count": len(hits[:limit]), "options": hits[:limit]}
    if not spec.lazy:
        q = fold(query or "")
        opts = [{"value": v, "label": label} for v, label in spec.options if q in fold(label) or q in fold(v)]
        return {"filter": filter, "count": len(opts[:limit]), "options": opts[:limit]}
    nodes = await option_tree(spec.payload or {})
    if brand:
        node = next((n for n in nodes if fold((n.get("data") or {}).get("key")) == fold(brand)), None)
        if node is None:
            raise ApiError(f"No brand '{brand}'. List brands by calling this tool without `brand`.")
        models = [_node(k) for k in node.get("children") or []]
        return {"filter": filter, "brand": brand, "count": len(models[:limit]), "options": models[:limit]}
    rows = [_node(n) for n in nodes]
    return {"filter": filter, "total": len(rows), "count": len(rows[:limit]), "options": rows[:limit]}


@tool("Search suggestions")
async def dv_suggest(
    query: Annotated[
        str,
        Field(min_length=2, max_length=80, description="Text as typed in the Divar search box, e.g. 'پراید', 'پژو'."),
    ],
    city: City = "تهران",
) -> dict[str, Any]:
    """What Divar's search box would suggest for some text, and which categories the text belongs to.

    Use to find the right `category` and a good `query` when the user only gave a product name. Each suggestion has
    the category slug and a probability; `categories` are guesses for the bare word. Next: dv_search.
    """
    c = (await resolve_cities(city))[0]
    sug_body = {
        "query": query,
        "cities": [str(c["id"])],
        "user_selected_location": {"places": [{"place_id": str(c["id"])}]},
    }
    sug, cats = await asyncio.gather(
        api("/prediction/w/query", body=sug_body), api("/prediction/cat-component-search", body={"query": query})
    )
    flat = {x["slug"]: x for x in await categories()}
    suggestions = []
    for s in sug.get("suggestions") or []:
        slug = (
            ((((s.get("search_data") or {}).get("form_data") or {}).get("data") or {}).get("category") or {})
            .get("str", {})
            .get("value")
        )
        suggestions.append(
            slim(
                {
                    "text": s.get("title"),
                    "in": s.get("subtitle"),
                    "category": slug,
                    "probability": round(s.get("probability") or 0, 3),
                }
            )
        )
    guesses = [
        {"category": x["category_slug"], "name": x.get("title") or flat.get(x["category_slug"], {}).get("name", "")}
        for x in cats.get("suggestions") or []
        if x.get("category_slug")
    ]
    return {"query": query, "suggestions": suggestions, "categories": guesses}


_UNITS = {"میلیارد": 1_000_000_000, "میلیون": 1_000_000, "هزار": 1_000}
_NUM = r"(\d+(?:\.\d+)?)"
_RANGE = re.compile(rf"از\s*{_NUM}\s*تا\s*{_NUM}\s*(میلیارد|میلیون|هزار)")
_BOUND = re.compile(rf"(زیر|کمتر از|حداکثر|تا|بالای|بیشتر از|حداقل|از)?\s*{_NUM}\s*(میلیارد|میلیون|هزار)")
_AREA = re.compile(rf"(?<!\S){_NUM}\s*(?:متری|متر)(?!\S)")
_PREPOSITIONS = {"در", "با", "برای", "و", "به", "از"}
_FILLER = {"اجاره", "رهن", "فروش", "خرید", "ودیعه", "ماهانه", "ماهیانه", "کرایه"}
_MAX_WORDS = ("زیر", "کمتر از", "حداکثر", "تا")
_BOUND_WORDS = r"زیر|کمتر از|حداکثر|تا|بالای|بیشتر از|حداقل|از"
# 'ودیعه ۵۰۰ میلیون' / 'اجاره تا ۲۰ میلیون': the word before an amount says which price it is (deposit or monthly rent)
_KEYED_RANGE = re.compile(rf"(ودیعه|رهن|اجاره)\s*از\s*{_NUM}\s*تا\s*{_NUM}\s*(میلیارد|میلیون|هزار)")
_KEYED = re.compile(rf"(ودیعه|رهن|اجاره)\s*(?:ماهانه\s*|ماهیانه\s*)?({_BOUND_WORDS})?\s*{_NUM}\s*(میلیارد|میلیون|هزار)")
_RENT_WORDS = re.compile(r"اجاره|رهن|ودیعه|کرایه")
# Common nouns -> (sale or only slug, rent slug). Divar's own guess (cat-component-search) handles the rest.
_CATEGORY_WORDS = {
    fold(w): v
    for ws, v in (
        (("آپارتمان",), ("apartment-sell", "apartment-rent")),
        (("خانه", "ویلا", "باغ"), ("house-villa-sell", "house-villa-rent")),
        (("زمین", "کلنگی"), ("plot-old", None)),
        (("مغازه", "غرفه"), ("shop-sell", "shop-rent")),
        (("دفتر", "مطب"), ("office-sell", "office-rent")),
        (("ماشین", "خودرو", "سواری"), ("light", None)),
        (("موتورسیکلت", "موتور سیکلت"), ("motorcycles", None)),
        (("موبایل", "گوشی"), ("mobile-phones", None)),
        (("لپ تاپ", "لپتاپ"), ("laptops", None)),
        (("تبلت",), ("tablet", None)),
        (("دوچرخه",), ("bicycle", None)),
    )
    for w in ws
}


def _as_typed(folded: str, text: str) -> str:
    """Folded words ('اپارتمان') shown as the user typed them ('آپارتمان'); words not found stay folded."""
    typed = {fold(w): w for w in re.findall(r"[^\s،,.؟?]+", text)}
    return " ".join(typed.get(w, w) for w in folded.split())


def _mentions(names: list[str], text: str) -> list[str]:
    """Names (folded) that appear in `text` as whole words, longest first, dropping any inside a longer hit."""
    padded = f" {text} "
    hits = sorted((n for n in set(names) if n and f" {n} " in padded), key=len, reverse=True)
    return [n for i, n in enumerate(hits) if not any(n in longer for longer in hits[:i])]


@tool("Question to search")
async def dv_parse_query(
    text: Annotated[
        str,
        Field(
            min_length=2,
            max_length=200,
            description="A search as a person would write it, e.g. 'آپارتمان اجاره ونک زیر ۸ میلیارد' or 'پراید کرج تا ۲۰۰ میلیون'.",
        ),
    ],
) -> dict[str, Any]:
    """Turn a plain Persian request into dv_search arguments: city, neighbourhoods, price bounds, category and leftover words.

    A rule-based helper, not a language model: it finds a city and its neighbourhoods by name, amounts with
    میلیارد / میلیون / هزار (with زیر, کمتر از, تا, بالای, حداقل or از ... تا ...), a category from Divar's own guess for the
    leftover words, and moves rental words (اجاره, رهن) to the matching rent category. `search` can be passed to
    dv_search as it is; check `notes` for what was assumed. An area like '80 متری' becomes a `size` filter of ±10%. Rooms, year or mileage are not extracted: use dv_filters.
    """
    t = fold(text).replace("٫", ".")
    notes: list[str] = []
    search: dict[str, Any] = {}
    rent_intent = bool(_RENT_WORDS.search(t))
    for m in _KEYED_RANGE.finditer(t):  # 'ودیعه از ۱ تا ۲ میلیارد'
        base = "price" if m.group(1) == "اجاره" else "deposit"
        unit = _UNITS[m.group(4)]
        search[f"min_{base}"], search[f"max_{base}"] = int(float(m.group(2)) * unit), int(float(m.group(3)) * unit)
        t = t.replace(m.group(0), " ", 1)
    if m := _RANGE.search(t):
        unit = _UNITS[m.group(3)]
        search["min_price"], search["max_price"] = int(float(m.group(1)) * unit), int(float(m.group(2)) * unit)
        t = t.replace(m.group(0), " ")
    for m in _KEYED.finditer(t):  # 'ودیعه ۵۰۰ میلیون' -> max_deposit, 'اجاره تا ۲۰ میلیون' -> max_price (monthly rent)
        base = "price" if m.group(1) == "اجاره" else "deposit"
        key = f"{'max' if (m.group(2) or 'تا') in _MAX_WORDS else 'min'}_{base}"
        if key not in search:
            search[key] = int(float(m.group(3)) * _UNITS[m.group(4)])
            t = t.replace(m.group(0), " ", 1)
    for m in list(_BOUND.finditer(t)) if "max_price" not in search else []:
        key = "max_price" if (m.group(1) or "تا") in _MAX_WORDS else "min_price"  # a bare amount means 'up to'
        if key not in search:
            search[key] = int(float(m.group(2)) * _UNITS[m.group(3)])
            t = t.replace(m.group(0), " ", 1)
    if m := _AREA.search(
        t
    ):  # '80 متری' -> size 72-88 m2 (±10%); dv_search reports `ignored` if the category has no size
        n = float(m.group(1))
        search["filters"] = {"size": {"min": round(n * 0.9), "max": round(n * 1.1)}}
        notes.append(
            f"'{m.group(0).strip()}' mapped to an area filter of ±10% ({search['filters']['size']['min']}-{search['filters']['size']['max']} m2)."
        )
        t = t.replace(m.group(0), " ", 1)
    all_cities = await cities()
    names = {fold(c["name"]): c for c in all_cities}
    city = None
    if found := _mentions(list(names), t):
        city = names[found[0]]
        search["city"] = city["name"]
        t = re.sub(rf"(?<!\S){re.escape(found[0])}(?!\S)", " ", t)
    else:
        notes.append("No city found: dv_search defaults to تهران.")
        city = names.get(fold("تهران"))
    if city:  # neighbourhoods are looked up even without a city: the default city is Tehran
        try:
            rows = {fold(d["name"]): d for d in await district_list(city["id"])}
        except ApiError:  # a city without a district list: the words stay in the query
            rows = {}
        if ds := _mentions(list(rows), t):
            search["districts"] = [rows[d]["name"] for d in ds[:3]]
            for d in ds[:3]:
                t = re.sub(rf"(?<!\S){re.escape(d)}(?!\S)", " ", t)
    rest = " ".join(w for w in t.split() if w not in _PREPOSITIONS)
    flat = {x["slug"]: x for x in await categories()}
    slug = None
    words = _mentions([w for w, v in _CATEGORY_WORDS.items() if v[0] in flat], rest)
    if words:
        word = min(words, key=lambda w: f" {rest} ".find(f" {w} "))
        sale, rent = _CATEGORY_WORDS[word]
        slug = rent if rent and rent in flat and rent_intent else sale
        kind = " (rent)" if slug == rent else " (sale)" if rent else ""
        assumed = " No rent or sale word: assumed sale." if rent and not rent_intent and "فروش" not in t else ""
        notes.append(f"Category from the word '{_as_typed(word, text)}': {flat[slug]['name']}{kind}.{assumed}")
        rest = re.sub(rf"(?<!\S){re.escape(word)}(?!\S)", " ", rest)
    elif rest.split() and (
        guesses := (await api("/prediction/cat-component-search", body={"query": rest})).get("suggestions") or []
    ):
        slug = next((g["category_slug"] for g in guesses if g.get("category_slug") in flat), None)
        if slug and rent_intent and slug.endswith("-sell") and slug[:-5] + "-rent" in flat:
            slug = slug[:-5] + "-rent"
            notes.append("Rental words found: switched to the rent category.")
        if slug:
            notes.append(f"Category guessed from '{_as_typed(rest, text)}': {flat[slug]['name']}.")
    if slug:
        search["category"] = slug
    rest = " ".join(
        w for w in rest.split() if w not in _FILLER and not (slug and w in fold(flat[slug]["name"]).split())
    )
    if rest:
        search["query"] = rest
    if search.get("category", "").endswith("-rent") and ("min_price" in search or "max_price" in search):
        notes.append("In rent categories min_price / max_price mean monthly rent; use max_deposit for the deposit.")
    return {"text": text, "search": search, "notes": notes}
