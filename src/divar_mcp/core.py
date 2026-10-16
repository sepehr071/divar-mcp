"""Shared helpers: Persian text folding, price parsing, reference data (cities, categories, filters), search
building and the paged search loop. Tool modules import from here; nothing here is a tool."""

from __future__ import annotations

import difflib
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from urllib.parse import quote

from pydantic import Field

from .http import SITE, ApiError, api

# ---------------------------------------------------------------- text and numbers

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_FOLD = str.maketrans(
    {"ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "ہ": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "\u200c": " "}
)
_MARKS = re.compile("[\u064b-\u065f\u0670\u200f\u200e\u202a-\u202e]")


def fold(text: Any) -> str:
    """Normalise Persian/Arabic letter variants, digits, ZWNJ and case so 'كيا' == 'کیا' and '۱۴۰۰' == '1400'."""
    s = _MARKS.sub("", str(text or "")).translate(_DIGITS).translate(_FOLD).lower()
    return re.sub(r"\s+", " ", s).strip()


def to_int(text: Any) -> int | None:
    """'۳,۵۰۰,۰۰۰ تومان' -> 3500000; None when there are no digits."""
    digits = "".join(ch for ch in str(text or "").translate(_DIGITS) if ch.isdigit())
    return int(digits) if digits else None


_UNITS = {"میلیارد": 1_000_000_000, "میلیون": 1_000_000, "هزار": 1_000}


def short_toman(text: Any) -> int | None:
    """Map-card price text '۱٫۵ میلیارد' -> 1500000000, 'رایگان' -> 0, anything else without digits -> None."""
    s = str(text or "").translate(_DIGITS).replace("٫", ".").replace("٬", "").replace(",", "")
    if "رایگان" in s:
        return 0
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    mult = next((v for k, v in _UNITS.items() if k in s), 1)
    return int(round(float(m.group()) * mult))


_CACHE: dict[str, tuple[float, Any]] = {}


async def cached(key: str, ttl: float, make: Callable[[], Awaitable[Any]]) -> Any:
    """In-memory cache for reference data (cities, categories, filter schemas, districts)."""
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = await make()
    _CACHE[key] = (time.monotonic(), value)
    return value


HOUR = 3600


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(text: Any) -> datetime | None:
    """Divar's ISO UTC stamps carry 6-9 fractional digits and a Z; trim them for fromisoformat."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?", str(text or ""))
    if not m:
        return None
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    return datetime.fromisoformat(f"{m.group(1)}.{frac}+00:00")


_JALALI_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]  # fmt: skip
_JALALI_DATE = re.compile(r"(\d{1,2})\s+(\S+)\s+(\d{4})\D+(\d{1,2}):(\d\d)")


def jalali_time(text: Any) -> datetime | None:
    """'۲۶ خرداد ۱۴۰۵، ۱۷:۴۳' (Tehran time, no DST since 2022) -> UTC datetime; None when it does not parse."""
    m = _JALALI_DATE.search(fold(text))
    months = [fold(x) for x in _JALALI_MONTHS]
    if not m or m.group(2) not in months:
        return None
    jy, jm, jd = int(m.group(3)) + 1595, months.index(m.group(2)) + 1, int(m.group(1))
    days = (
        -355668 + 365 * jy + jy // 33 * 8 + (jy % 33 + 3) // 4 + jd + ((jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186)
    )
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    try:
        local = datetime(gy, 1, 1) + timedelta(days=days)
        return (local + timedelta(hours=int(m.group(4)), minutes=int(m.group(5)) - 210)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


_KEEP_EMPTY = {"ads", "best_value", "closest_to_budget", "deals"}


def slim(d: dict[str, Any]) -> dict[str, Any]:
    """Drop None, False, empty strings and empty containers (0 stays: a free price is real). Result lists
    (`ads`...) stay even when empty so callers can rely on them."""
    return {
        k: v
        for k, v in d.items()
        if v is not None and v is not False and v != "" and v != {} and (v != [] or k in _KEEP_EMPTY)
    }


def percentile(sorted_values: list[int], p: float) -> int:
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return round(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo))


def sane(values: list[int]) -> list[int]:
    """Drop prices under a tenth of the median (deposits, spare parts, bait) so one junk ad cannot skew statistics."""
    v = sorted(values)
    return [p for p in v if p * 10 >= v[len(v) // 2]] if v else []


def dist(values: list[int]) -> dict[str, int] | None:
    """Count, min, p10, p25, median, p75, p90, max, mean of a list of prices."""
    v = sorted(values)
    if not v:
        return None
    return {
        "n": len(v),
        "min": v[0],
        "p10": percentile(v, 0.1),
        "p25": percentile(v, 0.25),
        "median": percentile(v, 0.5),
        "p75": percentile(v, 0.75),
        "p90": percentile(v, 0.9),
        "max": v[-1],
        "mean": round(sum(v) / len(v)),
    }


# ---------------------------------------------------------------- reference data


async def cities() -> list[dict[str, Any]]:
    async def load() -> list[dict[str, Any]]:
        data = await api("/places/cities")
        return [
            {
                "id": c["id"],
                "name": c.get("name", ""),
                "slug": c.get("slug", ""),
                "second_slug": c.get("second_slug", ""),
                "province_id": c.get("parent"),
                "lat": (c.get("centroid") or {}).get("latitude"),
                "lon": (c.get("centroid") or {}).get("longitude"),
            }
            for c in data.get("cities") or []
        ]

    return await cached("cities", HOUR, load)


def rank(query: str, names: list[str]) -> int:
    """0 exact, 1 prefix, 2 contains, 9 no match."""
    best = 9
    for n in names:
        if not n:
            continue
        if n == query:
            return 0
        if n.startswith(query):
            best = min(best, 1)
        elif query in n:
            best = min(best, 2)
    return best


def match_cities(text: str, all_cities: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    q = fold(text)
    scored = []
    for c in all_cities:
        r = rank(q, [fold(c["name"]), fold(c["slug"]), fold(c["second_slug"])])
        if r < 9:
            scored.append((r, len(c["name"]), c))
    scored.sort(key=lambda t: t[:2])
    return [c for _, _, c in scored[:limit]]


async def resolve_cities(text: str | None) -> list[dict[str, Any]]:
    """'تهران', 'tehran', '1' or a comma list -> city dicts. Unknown names raise with close suggestions."""
    out = []
    all_cities = await cities()
    for part in re.split(r"[,،;]", text or "تهران"):
        part = part.strip()
        if not part:
            continue
        province = re.match(r"(?:استان|province:?)\s*(.+)", part, re.I)
        if province:
            part = province.group(1).strip()
        q = fold(part)
        if q.isdigit():
            hit = next((c for c in all_cities if c["id"] == int(q)), None)
        else:
            found = match_cities(part, all_cities, 1)
            hit = found[0] if found else None
        if not hit:
            names = [c["name"] for c in all_cities]
            close = difflib.get_close_matches(part, names, n=5, cutoff=0.5)
            raise ApiError(f"Unknown city '{part}'. Close matches: {close or 'none'}. Search names with dv_cities.")
        if (
            province
        ):  # Divar accepts a province id as a search scope; provinces have no list, so name one by a city in it
            if not hit["province_id"]:
                raise ApiError(f"City '{hit['name']}' has no province id.")
            hit = {**hit, "id": hit["province_id"], "name": f"استان {hit['name']}", "province": True}
        out.append(hit)
    if not out:
        raise ApiError("No city given. Use a name like 'تهران' or an id; dv_cities finds ids.")
    return out


# Web URL slugs the site shows in addresses that differ from the API slug (from the API map).
CATEGORY_ALIASES = {"car": "light", "auto": "cars", "buy-apartment": "apartment-sell"}


async def categories() -> list[dict[str, Any]]:
    """Flat list of the 236-node category tree: slug, name, second_name, parent, level, path, leaf."""

    async def load() -> list[dict[str, Any]]:
        root = (await api("/postlist/w/categories")).get("category") or {}
        flat: list[dict[str, Any]] = []

        def walk(node: dict[str, Any], parent: str | None, path: list[str], level: int) -> None:
            kids = node.get("children") or []
            if level:
                flat.append(
                    {
                        "slug": node["slug"],
                        "name": node.get("name", ""),
                        "second_name": node.get("second_name", ""),
                        "parent": parent,
                        "level": level,
                        "path": path + [node.get("name", "")],
                        "leaf": not kids,
                    }
                )
            for k in kids:
                walk(k, node["slug"] if level else None, path + ([node.get("name", "")] if level else []), level + 1)

        walk(root, None, [], 0)
        return flat

    return await cached("categories", HOUR, load)


async def is_sale_real_estate(slug: str | None) -> bool:
    """True for a for-sale property category (flats, houses, offices, shops, plots): ads there are comparable per m2."""
    flat = {c["slug"]: c for c in await categories()}
    while slug in flat:
        if slug in ("residential-sell", "commercial-sell"):
            return True
        slug = flat[slug]["parent"]
    return False


def match_categories(text: str, flat: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    q = fold(text)
    scored = []
    for c in flat:
        r = rank(q, [fold(c["slug"]), fold(c["name"]), fold(c["second_name"])])
        if r < 9:
            scored.append((r, -c["level"], c))
    scored.sort(key=lambda t: t[:2])
    return [c for _, _, c in scored[:limit]]


async def resolve_category(text: str | None) -> str | None:
    """Category slug or Persian name -> API slug. None/empty/'ROOT' means all categories."""
    if not text or text.strip().upper() == "ROOT":
        return None
    flat = await categories()
    slug = text.strip()
    slug = CATEGORY_ALIASES.get(slug, slug)
    if any(c["slug"] == slug for c in flat):
        return slug
    found = match_categories(text, flat, 1)
    if found and rank(fold(text), [fold(found[0]["name"]), fold(found[0]["second_name"])]) <= 1:
        return found[0]["slug"]
    close = [f"{c['slug']} ({c['name']})" for c in match_categories(text, flat, 5)]
    raise ApiError(f"Unknown category '{text}'. Candidates: {close or 'none'}. List them with dv_categories.")


@dataclass
class Spec:
    key: str
    type: str
    label: str = ""
    unit: str = ""
    options: list[tuple[str, str]] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    lazy: bool = False
    search_key: str = ""
    payload: dict[str, Any] | None = None
    hint: tuple[str, str] | None = None


def parse_filters(data: dict[str, Any]) -> list[Spec]:
    """The filter form (`page.widget_list`) -> one Spec per filter key."""
    widgets = (data.get("page") or {}).get("widget_list") or []
    titles = {}
    for w in widgets:
        d = w.get("data") or {}
        if w.get("widget_type") == "TITLE_ROW" and d.get("corresponding_input_widget_id"):
            titles[d["corresponding_input_widget_id"]] = (d.get("text") or "", d.get("description") or "")
    specs = []
    for w in widgets:
        d = w.get("data") or {}
        f = d.get("field") or {}
        if not f.get("key"):
            continue
        key = f["key"]
        text, desc = titles.get(f"filter_{key}", ("", ""))
        s = Spec(
            key=key,
            type=f.get("type", ""),
            label=d.get("title") or text,
            unit=d.get("unit") or desc.strip("()"),
            lazy="LAZY" in w.get("widget_type", ""),
        )
        for o in d.get("options") or []:
            v = o.get("value", o.get("key"))
            if v is not None:
                s.options.append((str(v), o.get("display") or o.get("title") or str(v)))
        s.steps = [str(o["value"]) for o in d.get("from_options") or [] if o.get("value") not in (None, "")]
        if d.get("minimum_dynamic_suggestion"):
            s.hint = (d["minimum_dynamic_suggestion"], d.get("maximum_dynamic_suggestion", ""))
        if s.lazy:
            s.payload = d.get("lazy_payload")
            s.search_key = (d.get("online_search") or {}).get("search_key", "")
        specs.append(s)
    return specs


async def filter_specs(category: str, city_id: int) -> dict[str, Spec]:
    async def load() -> dict[str, Spec]:
        body = {"data": {"form_data": {"data": {"category": {"str": {"value": category}}}}}, "city_ids": [str(city_id)]}
        return {s.key: s for s in parse_filters(await api("/postlist/w/filters", body=body))}

    return await cached(f"filters:{category}", HOUR, load)


async def district_list(city_id: int) -> list[dict[str, Any]]:
    async def load() -> list[dict[str, Any]]:
        data = await api(f"/places/cities/{city_id}/districts")
        return [
            {
                "id": x["id"],
                "name": x.get("name", ""),
                "slug": x.get("slug", ""),
                "lat": (x.get("centroid") or {}).get("latitude"),
                "lon": (x.get("centroid") or {}).get("longitude"),
                "streets": [t["title"] for t in x.get("tags") or [] if t.get("title")][:8],
            }
            for x in data.get("districts") or []
        ]

    return await cached(f"districts:{city_id}", HOUR, load)


async def resolve_districts(city: dict[str, Any], names: list[str]) -> tuple[list[str], list[str]]:
    """District names or ids -> (ids, names). Exact name wins; otherwise up to 3 partial matches; else an error."""
    rows = await district_list(city["id"])
    ids: list[str] = []
    out_names: list[str] = []
    for n in names:
        q = fold(n)
        if q.isdigit():
            hit = [d for d in rows if d["id"] == int(q)]
        else:
            ranked = sorted(((rank(q, [fold(d["name"])]), d) for d in rows), key=lambda t: t[0])
            ranked = [t for t in ranked if t[0] < 9]
            exact = [d for _, d in ranked if _ == 0]
            hit = exact[:1] or [d for _, d in ranked][:3]
        if not hit:
            raise ApiError(f"No district '{n}' in {city['name']}. List them with dv_districts.")
        for d in hit:
            if str(d["id"]) not in ids:
                ids.append(str(d["id"]))
                out_names.append(d["name"])
    return ids, out_names


async def option_tree(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The full option tree of a lazy filter (brand -> models), cached."""

    async def load() -> list[dict[str, Any]]:
        data = await api("/w/lazy-multi-select-hierarchy-options", body={"payload": payload})
        return (data.get("options") or {}).get("children") or []

    return await cached(f"tree:{payload.get('category')}:{payload.get('filter_name')}", HOUR, load)


async def option_keys(spec: Spec) -> list[str]:
    """Every valid key (brands and models) of a lazy filter; empty when the filter has no option tree."""
    if not spec.payload:
        return []
    out: list[str] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            if k := (n.get("data") or {}).get("key"):
                out.append(k)
            walk(n.get("children") or [])

    walk(await option_tree(spec.payload))
    return out


async def search_options(spec: Spec, text: str, city_id: int) -> list[dict[str, str]]:
    """Typed search inside a lazy filter (brand/model names, districts) via /fields-search."""
    body = {
        "field": spec.key,
        "search_key": spec.search_key or spec.key,
        "q": text,
        "query": text,
        "source": "filter",
        "cities": [str(city_id)],
        "city_id": city_id,
    }
    rows = (await api("/fields-search", body=body)).get("results") or []
    return [{"value": str(r["enum"]), "label": r.get("enumName", "")} for r in rows if r.get("enum") is not None]


# ---------------------------------------------------------------- query building

# Web address segments, confirmed in the browser (house-villa-sell and most API slugs 404 on the site).
_WEB_CATEGORY = {
    "apartment-sell": "buy-apartment",
    "apartment-rent": "rent-apartment",
    "light": "car",
    "mobile-phones": "mobile-phones",
}
RECENT = ("3h", "12h", "1d", "3d", "7d")


@dataclass
class Q:
    cities: list[dict[str, Any]]
    category: str | None
    query: str
    data: dict[str, Any]  # form_data.data
    applied: dict[str, Any] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)

    @property
    def city_ids(self) -> list[str]:
        return [str(c["id"]) for c in self.cities]

    def search_data(self) -> dict[str, Any]:
        sd: dict[str, Any] = {"form_data": {"data": self.data}}
        if self.query:
            sd["query"] = self.query
        return sd

    def body(self, cursor: str | None = None) -> dict[str, Any]:
        b: dict[str, Any] = {"city_ids": self.city_ids, "search_data": self.search_data()}
        if cursor:
            b["pagination_data"] = {
                "@type": "type.googleapis.com/post_list.PaginationData",
                "last_post_date": cursor,
                "page": 2,
                "layer_page": 2,
            }
        return b

    def web_url(self) -> str | None:
        """A divar.ir address that shows the same search, or None when the site cannot express it (several cities, a
        province, a category without a confirmed web slug, any filter other than districts / price / deposit / rent / size)."""
        if len(self.cities) != 1 or self.cities[0].get("province"):
            return None
        url = f"{SITE}/s/{self.cities[0]['slug']}"
        if self.category:
            if self.category not in _WEB_CATEGORY:
                return None
            url += f"/{_WEB_CATEGORY[self.category]}"
        params = []
        for key, value in self.data.items():
            if key == "category":
                continue
            if key == "districts" and "repeated_string" in value:
                params.append("districts=" + ",".join(value["repeated_string"]["value"]))
            elif key in ("price", "credit", "rent", "size") and "number_range" in value:
                r = value["number_range"]
                params.append(f"{key}={r.get('minimum', '')}-{r.get('maximum', '')}")
            else:
                return None
        if self.query:
            params.append(f"q={quote(self.query)}")
        return url + ("?" + "&".join(params) if params else "")

    def head(self) -> dict[str, Any]:
        return slim(
            {
                "city": ", ".join(c["name"] for c in self.cities),
                "category": self.category,
                "query": self.query,
                "applied": self.applied,
                "ignored": self.ignored,
            }
        )


def _encode(spec: Spec, value: Any) -> dict[str, Any] | None:
    if spec.type == "number_range":
        if isinstance(value, dict):
            lo, hi = value.get("min"), value.get("max")
        elif isinstance(value, (list, tuple)) and len(value) == 2:
            lo, hi = value
        else:
            lo = hi = value
        r = {k: str(int(float(v))) for k, v in (("minimum", lo), ("maximum", hi)) if v not in (None, "")}
        return {"number_range": r} if r else None
    if spec.type == "repeated_string":
        vals = value if isinstance(value, (list, tuple)) else [value]
        return {"repeated_string": {"value": [str(v) for v in vals]}} if vals else None
    if spec.type == "boolean":
        return {"boolean": {"value": bool(value)}}
    if spec.type == "str":
        return {"str": {"value": str(value)}}
    return None


FALLBACK = {
    "price": Spec("price", "number_range"),
    "districts": Spec("districts", "repeated_string"),
    "has-photo": Spec("has-photo", "boolean"),
    "recent_ads": Spec("recent_ads", "str"),
}


async def build_query(
    city: str | None = None,
    category: str | None = None,
    query: str | None = None,
    *,
    districts: list[str] | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    min_deposit: int | None = None,
    max_deposit: int | None = None,
    brand_model: list[str] | None = None,
    has_photo: bool | None = None,
    recent: str | None = None,
    filters: dict[str, Any] | None = None,
) -> Q:
    """Turn friendly arguments into a Divar search. Filters the category does not support are listed in `ignored`
    instead of being sent (Divar would silently drop them)."""
    cs = await resolve_cities(city)
    cat = await resolve_category(category)
    specs = await filter_specs(cat, cs[0]["id"]) if cat else dict(FALLBACK)
    q = Q(cs, cat, (query or "").strip(), {})
    if cat:
        q.data["category"] = {"str": {"value": cat}}
    want: dict[str, Any] = {}
    price_key = "price" if "price" in specs or "rent" not in specs else "rent"
    if min_price is not None or max_price is not None:
        want[price_key] = {"min": min_price, "max": max_price}
    if min_deposit is not None or max_deposit is not None:
        want["credit"] = {"min": min_deposit, "max": max_deposit}
    if has_photo is not None:
        want["has-photo"] = has_photo
    if recent:
        if recent not in RECENT:
            raise ApiError(f"recent must be one of {', '.join(RECENT)}.")
        want["recent_ads"] = recent
    if districts:
        if len(cs) != 1:
            raise ApiError("Districts need exactly one city.")
        if cs[0].get("province"):
            raise ApiError("Districts need a city, not a province.")
        ids, names = await resolve_districts(cs[0], districts)
        want["districts"] = ids
        q.applied["districts"] = names
    if brand_model:
        spec = specs.get("brand_model")
        if spec is None:
            q.ignored.append("brand_model (this category has no brand/model filter)")
        else:
            valid = {fold(k): k for k in await option_keys(spec)}
            keys = []
            for t in brand_model:
                if fold(t) in valid:  # Divar silently ignores keys it does not know, so only known keys pass
                    keys.append(valid[fold(t)])
                    continue
                want_t = fold(
                    re.sub(r"[-_]", " ", t)
                )  # the typed search is fuzzy ('car' finds Kia Carens): keep real hits
                found = [
                    o
                    for o in await search_options(spec, t, cs[0]["id"])
                    if want_t in fold(o["label"]) or want_t in fold(o["value"])
                ]
                if not found:
                    raise ApiError(f"No brand/model matches '{t}'. Try dv_filter_options with a shorter text.")
                keys.append(next((o["value"] for o in found if fold(o["label"]) == fold(t)), found[0]["value"]))
            want["brand_model"] = keys
            q.applied["brand_model"] = keys
    for k, v in (filters or {}).items():
        want[k] = v
    for key, value in want.items():
        spec = specs.get(key)
        enc = _encode(spec, value) if spec else None
        if spec is None:
            near = [k for k in specs if key in k or k in key]  # 'year' -> 'production-year'
            hint = f"did you mean {', '.join(near)}?" if near else "see dv_filters"
            q.ignored.append(f"{key} (not a filter of {cat or 'this search'}; {hint})")
        elif enc is None:
            q.ignored.append(f"{key} (value {value!r} not usable for type {spec.type})")
        else:
            q.data[key] = enc
            if key not in q.applied:
                q.applied[key] = {k: v for k, v in value.items() if v is not None} if isinstance(value, dict) else value
    return q


# ---------------------------------------------------------------- ad rows


TITLE_AREA = re.compile(r"(\d{2,4})\s*(?:متری|متر|m(?![a-z]))")  # on folded text


def per_m2(price: int, area: int) -> int:
    """Price per m2 the way the site shows it: cut (not rounded) to whole thousands, 52.5B / 180 m = 291,666,000."""
    return price // area // 1000 * 1000


def flag_outliers(cards: list[dict[str, Any]], min_sample: int = 5) -> None:
    """Mark cards priced at or under a tenth of the sample's median price as suspect ('outlier'), in place. A Pride at
    180,000 Toman among 200-900 million ones is a deposit or a placeholder. Needs `min_sample` priced ads to judge."""
    prices = sorted(p for c in cards if (p := main_price(c)))
    if len(prices) < min_sample:
        return
    floor = prices[len(prices) // 2] // 10
    for c in cards:
        if (p := main_price(c)) and p <= floor:
            c["suspect_price"] = [*c.get("suspect_price", []), "outlier"]


def price_flags(price: int | None) -> list[str]:
    """Why a sale price should not be trusted: seller placeholders (1111111, 12345678), token prices, absurd values."""
    if not price:
        return []
    s = str(price)
    flags = []
    if price < 10_000:
        flags.append("tiny")
    if len(s) >= 5 and len(set(s)) == 1 or s in ("123456", "1234567", "12345678", "123456789"):
        flags.append("placeholder")
    if price > 1_000_000_000_000:
        flags.append("absurd")
    return flags


def row_prices(d: dict[str, Any]) -> dict[str, Any]:
    """Price fields of a list row (Toman). Sale rows give `price`; rent rows `deposit` and `rent` (0 = full deposit
    'رهن کامل'); jobs and others keep the raw text in `info`."""
    top, mid = d.get("top_description_text") or "", d.get("middle_description_text") or ""
    out: dict[str, Any] = {}
    used_top = used_mid = False
    if top.startswith("ودیعه"):
        out["deposit"] = to_int(top)
        used_top = True
        if mid.startswith("اجاره"):
            out["rent"] = 0 if "رایگان" in mid else to_int(mid)
            used_mid = True
        elif "رهن" in mid:
            out["rent"] = 0
            out["full_deposit"] = True
            used_mid = True
    elif mid.startswith("اجاره"):
        out["rent"] = 0 if "رایگان" in mid else to_int(mid)
        used_mid = True
    elif "تومان" in mid:
        out["price"] = to_int(mid)
        used_mid = True
    elif "تومان" in top and to_int(top) is not None:
        out["price"] = to_int(top)
        used_top = True
    elif "توافق" in mid:
        out["negotiable"] = True
        used_mid = True
    if out.get("price") == 0:
        out.pop("price")
        out["negotiable"] = True
    info = [t for t, used in ((top, used_top), (mid, used_mid)) if t and not used]
    out["info"] = " | ".join(info)
    flags = price_flags(out.get("price"))
    if out.get("rent") and out["rent"] < 100_000:
        flags.append("tiny_rent")
    if flags:
        out["suspect_price"] = flags
    return out


def card(widget: dict[str, Any], at: datetime | None = None) -> dict[str, Any]:
    """A POST_ROW widget -> a compact ad card."""
    d = widget.get("data") or {}
    info = ((widget.get("action_log") or {}).get("server_side_info") or {}).get("info") or {}
    web = ((d.get("action") or {}).get("payload") or {}).get("web_info") or {}
    token = d.get("token") or info.get("post_token") or ""
    posted = info.get("sort_date")
    when = parse_time(posted)
    bottom = d.get("bottom_description_text") or ""
    prefix = bottom.split(" در ")[0] if " در " in bottom else ""
    seller = "" if re.search(r"پیش|دیروز|امروز|پریروز|لحظاتی", prefix) else prefix
    tag = d.get("red_text") or ""
    out = {
        "token": token,
        "title": d.get("title", ""),
        **row_prices(d),
        "district": web.get("district_persian", ""),
        "seller": seller,
        "posted": posted[:19] + "Z" if posted else "",
        "age_h": round(((at or now()) - when).total_seconds() / 3600, 1) if when else None,
        "photos": d.get("image_count") or 0,
        "image": d.get("image_url", ""),
        "chat": bool(d.get("has_chat")),
        "tag": tag,
        "url": f"{SITE}/v/{token}",
    }
    return slim(out)


def main_price(c: dict[str, Any]) -> int | None:
    """The number a card is ranked by: a trustworthy sale price, else the monthly rent."""
    if c.get("suspect_price"):
        return None
    if c.get("price"):
        return c["price"]
    return c.get("rent") or None


Keep = Callable[[dict[str, Any]], bool]


def make_keep(
    max_age_hours: float | None = None, keywords: list[str] | None = None, exclude: list[str] | None = None
) -> Keep:
    """Client-side filters Divar lacks: ad age, words that must / must not appear in the title."""
    must = [fold(k) for k in keywords or [] if k.strip()]
    never = [fold(k) for k in exclude or [] if k.strip()]

    def keep(c: dict[str, Any]) -> bool:
        if max_age_hours is not None and (c.get("age_h") is None or c["age_h"] > max_age_hours):
            return False
        t = fold(c.get("title", ""))
        return all(k in t for k in must) and not any(k in t for k in never)

    return keep


async def fetch_cards(
    q: Q,
    *,
    pages: int = 1,
    cursor: str | None = None,
    want: int | None = None,
    keep: Keep | None = None,
    stop_before: datetime | None = None,
) -> dict[str, Any]:
    """Walk up to `pages` result pages (24-26 ads each) by time cursor, dedupe, apply `keep`. Stops early when
    `want` ads passed or a whole page is older than `stop_before`. On an empty first page with a text query it retries once with the Persian-folded query."""
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    retried = None
    has_next = False
    cur = cursor
    done = 0
    stopped = False
    at = now()
    for _ in range(pages):
        data = await api("/postlist/w/search", body=q.body(cur))
        rows = [w for w in data.get("list_widgets") or [] if w.get("widget_type") == "POST_ROW"]
        if not rows and not done and not cursor and q.query and fold(q.query) != q.query:
            q.query = retried = fold(q.query)
            data = await api("/postlist/w/search", body=q.body(cur))
            rows = [w for w in data.get("list_widgets") or [] if w.get("widget_type") == "POST_ROW"]
        done += 1
        times = []
        for w in rows:
            c = card(w, at)
            times.append(parse_time(c.get("posted")))
            if c["token"] and c["token"] not in seen:
                seen.add(c["token"])
                if keep is None or keep(c):
                    cards.append(c)
        if stop_before and times and all(t and t < stop_before for t in times):
            stopped = True
            break
        pag = data.get("pagination") or {}
        has_next = bool(pag.get("has_next_page"))
        cur = (pag.get("data") or {}).get("last_post_date")
        if not rows or not has_next or not cur or (want and len(cards) >= want):
            break
    return {
        "cards": cards,
        "next_cursor": cur if has_next else None,
        "pages": done,
        "stopped": stopped,
        "retried_with": retried,
    }


# ---------------------------------------------------------------- shared tool parameter types

City = Annotated[
    str,
    Field(
        description="City name or id; several comma-separated, e.g. 'تهران', 'mashhad', 'تهران,کرج'.", max_length=200
    ),
]
Category = Annotated[
    str | None,
    Field(
        description="Category slug or Persian name, e.g. 'light' (cars), 'apartment-sell', 'apartment-rent', 'mobile-phones'."
    ),
]
QueryText = Annotated[
    str | None, Field(description="Free text in title or description, e.g. 'پراید 111'.", max_length=200)
]
Districts = Annotated[
    list[str] | None, Field(description="Neighbourhood names or ids in the one city, e.g. ['ونک'].", max_length=20)
]
MinPrice = Annotated[int | None, Field(ge=0, description="Toman; monthly rent in rent categories.")]
MaxPrice = Annotated[int | None, Field(ge=0, description="Toman; monthly rent in rent categories.")]
MinDeposit = Annotated[int | None, Field(ge=0, description="Rent categories: deposit (ودیعه) in Toman.")]
MaxDeposit = Annotated[int | None, Field(ge=0, description="Rent categories: deposit (ودیعه) in Toman.")]
Brand = Annotated[list[str] | None, Field(description="Vehicles, phones: brand/model, e.g. ['پژو 206'].")]
HasPhoto = Annotated[bool | None, Field(description="Only ads with photos.")]
Recent = Annotated[str | None, Field(description="Published within '3h', '12h', '1d', '3d' or '7d'.")]
Filters = Annotated[
    dict[str, Any] | None,
    Field(
        description=(
            "Other category filters (keys from dv_filters): {'size': {'min': 80, 'max': 120}, 'rooms': ['دو'], "
            "'parking': true}. Unknown keys are returned in `ignored`."
        )
    ),
]
Pages = Annotated[int, Field(ge=1, le=6, description="Result pages to merge, about 25 ads each.")]
Cursor = Annotated[str | None, Field(description="`next_cursor` of the previous call, for older ads.")]
MaxAge = Annotated[float | None, Field(gt=0, le=24 * 60, description="Only ads bumped within this many hours.")]
Keywords = Annotated[list[str] | None, Field(description="Words that must all be in the title.")]
Exclude = Annotated[list[str] | None, Field(description="Drop ads whose title has any of these words.")]
Token = Annotated[
    str,
    Field(
        min_length=6,
        max_length=16,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Ad token: the last part of divar.ir/v/<slug>/<token> or `token` of a search result, e.g. 'gaRPIP2m'.",
    ),
]
