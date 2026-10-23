# divar-mcp

Unofficial, read-only MCP server for Divar (divar.ir), Iran's classifieds site (real estate, cars, phones, jobs, services).
Published: GitHub `sepehr071/divar-mcp`, PyPI `divar-mcp`, MCP Registry `io.github.sepehr071/divar-mcp`, listed in `../../iran-mcp`.
It is a sibling of `../fidibo-mcp` and `../digikala-mcp` and copies their layout and idioms; keep them in step.

API map this server is built on (endpoints, samples, quirks, test report, competitor checklist): `../divar/`.

## Layout

- `src/divar_mcp/http.py`: the only HTTP code. One shared `httpx.AsyncClient` (`trust_env=False`) on a Chrome-impersonating
  `httpx_curl_cffi.AsyncCurlTransport` (TLS/HTTP2 and the User-Agent / `sec-ch-ua` headers come from the profile; we send no
  User-Agent of our own, only Accept-Language `fa-IR`, Origin/Referer and XHR `sec-fetch-*`), a concurrency semaphore
  (`MAX_CONCURRENCY` 2) plus a random 0.15-0.6 s `JITTER` before each call, `api(path, body=None)` for the gateway
  `https://api.divar.ir/v8` (GET, or POST when `body` is given: every Divar POST used here is a read-only query), up to 2
  retries (`MAX_RETRIES`, jittered exponential backoff from `RETRY_DELAY`, `Retry-After` honoured, wait capped at 10 s) on
  timeouts, dropped connections, an empty 200 body, 429 and 5xx (a 403 is never retried), no redirects followed,
  `ApiError(ToolError)` (passes on the API's own `message` for 400/404), `set_transport()` for tests.
  `DIVAR_MCP_PROXY` may be a comma-separated list: one proxy is used until a block (403/429/challenge) or connect error,
  then `_next_proxy` rebuilds the client on the next. A challenge page (`_is_challenge`) is cleared once with a headless
  patchright browser (optional extra: install `divar-mcp[browser]`, then `patchright install chromium`), its cookies are
  copied into the client and the request is retried once; without patchright it raises `ApiError` saying so.
- `core.py`: not a tool module. Persian `fold`, price parsing (`to_int`, `short_toman`), `price_flags`, `row_prices`, `card`
  (a POST_ROW -> compact ad), `dist` / `sane` statistics, cached reference data (cities, categories, filter schemas,
  districts; 1 h, `_CACHE`), resolvers (`resolve_cities`, `resolve_category`, `resolve_districts`), `build_query` (friendly
  args -> Divar search, reports `ignored` filters), `fetch_cards` (the paged loop) and the shared `Annotated` parameter types.
- `registry.py`: `@tool("Title")` collects tools. `server.py`: registers them with read-only annotations, holds `INSTRUCTIONS`.
- Tools: `places.py` (cities, districts, neighbours, locate, place search, mapping), `catalog.py` (categories, filters,
  filter options, suggest), `search.py` (search, count, cheapest, best for budget, find deals, new since, export, nearby), `post.py`
  (post, posts, compare, seller, dealer, similar; `parse_post`, `comparables`), `market.py` (market, appraise),
  `watch.py` (saved watches, price trend; the only tools that write, to the local file of `store.py`), `meta.py` (status).
  `places.py` also has `dv_boundary` (GeoJSON), `catalog.py` `dv_parse_query` (rule-based text -> dv_search args).
  31 tools, all `dv_*`. `server.py` also registers MCP resources (`divar://cities`, `categories`, `category-filters/{slug}`)
  and prompts. No `outputSchema` on purpose (a test checks it): tools return one compact JSON text block.
  Token budget: `server._rows` trims every ad row on the way out (no `image` unless the call has `thumbnails=true`,
  no `posted` next to `age_h`, no false/empty values), and `_untitled` flattens optional `anyOf [X, null]` schemas and
  dedents descriptions. Shared parameter descriptions in `core.py` stay one short line: they repeat across 11 tools.
- `tests/`: `test_<module>.py` unit tests (MockTransport + trimmed real fixtures in `tests/fixtures/`, clock frozen in
  `conftest.py`), `test_live_<group>.py` live tests (`@pytest.mark.live`), `test_http.py` for the client and the README tool tables.

## Commands

uv needs the local proxy to download packages; the server and tests call Divar directly (env proxies are ignored, only `DIVAR_MCP_PROXY` counts).

```bash
export HTTPS_PROXY=http://127.0.0.1:10898 HTTP_PROXY=http://127.0.0.1:10898
uv sync
uv run ruff check . && uv run ruff format --check .
uv run python -m pytest -q            # unit tests, offline (plain `uv run pytest` hits a uv trampoline error here)
uv run python -m pytest -m live -q    # one live test per tool, about 25 s
```

## Rules for changes

- Read-only on Divar. The two `local=True` tools (`dv_watch`, `dv_price_trend`) write only `~/.divar-mcp/state.json`
  (`DIVAR_MCP_STATE`) and are annotated not-read-only. Never call login/OTP (`/v5/auth`), `postcontact` (seller phone), chat, bookmarks, notes, ad submission or
  drafts, payments, the business panel, or any analytics beacon (`/v1/client-exporter`). Seller phone numbers stay out of scope.
- Output units: prices in **Toman** (the API is Toman; nothing to convert). Card: `price` = sale price; rentals `deposit`
  and `rent` (`rent` 0 + `full_deposit` = رهن کامل, from "رهن کامل" rows); no price key = negotiable (`negotiable` true when
  the seller wrote توافقی or 0); `suspect_price` list = `tiny`, `placeholder`, `absurd`, `tiny_rent`. `slim` drops
  None / false / empty values (0 stays; `ads` lists stay even when empty). Rankings use `main_price` (sale price, else
  rent) and skip suspect prices; statistics also drop prices under a tenth of the median (`sane`).
- Tools never invent data Divar lacks: price ordering, appraisal, best value and trend are computed from fetched asking
  prices and the answer says so (`scanned`, `comparable_ads`, `caveat`).
- Filters are validated against the category's filter schema (`filter_specs`, cached): unknown keys go to `ignored`,
  because Divar silently drops them. Value shapes: number_range (strings), repeated_string, str, boolean.
- Slugs from API data go into paths only as validated tokens (`Token` pattern); user text is never put into a path.
- New tool: `@tool("Title")` async function, every param `Annotated[..., Field(...)]` with constraints and an example,
  docstring = what it returns + when to use + what to call next, compact dict output. Add a unit test with a real trimmed
  fixture, a live test, a README tool-table row, and an `INSTRUCTIONS` mention.
- Keep `README.md` tool tables equal to `list_tools()` (a unit test checks it). Deps stay `mcp`, `httpx`, `httpx-curl-cffi` and `curl-cffi` (optional extra `browser` = `patchright==1.63.0`).
- Write ZWNJ as `\u200c` in code, never the invisible character.

## Divar quirks (keep them)

- No auth, token or header needed. Unknown paths and wrong HTTP methods answer `403 RBAC: access denied`, not 404.
- List rows are protobuf-JSON widgets; `POST_ROW` is the ad card. Row prices are Persian-digit Toman strings; rent rows put
  the deposit in `top_description_text` and rent in `middle_description_text` (`رهن کامل` = full deposit). The exact publish time
  is `action_log.server_side_info.info.sort_date`; row text only has relative time.
- `POST /v8/postlist/w/search` is newest first only; the first 1-3 rows can be promoted. Paging: send
  `pagination_data {"@type": "type.googleapis.com/post_list.PaginationData", "last_post_date": <cursor>, "page": 2, "layer_page": 2}`;
  the cursor is the previous response's `pagination.data.last_post_date`. `page: N` alone returns page 1.
- Category slugs differ from web URL slugs (`car` = `light`, `buy-apartment` = `apartment-sell`); a wrong one is HTTP 400.
  Web search URLs with API slugs 404 inconsistently (`house-villa-sell` 404s; `buy-apartment`, `rent-apartment`, `car`,
  `mobile-phones` work), so `web_url` (`Q.web_url`) is built only for those categories, with `districts=1,2`, `price|credit|rent|size=min-max`
  and `q=`, and is omitted for anything else.
- Post detail (`/posts-v2/web/{token}`): `webengage.price/credit/rent` are float32-rounded for big numbers
  (2,670,000,000 arrives as 2,670,000,128). The exact numbers are in the `UNEXPANDABLE_ROW` "قیمت پایه" / "ودیعه" / "اجارهٔ ماهانه"
  rows; `parse_post` prefers them.
- Filter option lists: `/v8/w/lazy-multi-select-hierarchy-options` (no `/postlist`), typed search `/v8/fields-search` (needs
  `search_key`, e.g. `car_brand_model`; `count` is always 0). `reverse-geocode` needs the `location` key and 404s outside
  covered districts. Dealer pages (`brand-landing`) give only the first 11 ads.
- Map pins are fuzzed about 500 m; map price chips are rounded text ("۱٫۵ میلیارد"), parsed by `short_toman`. Sale pins
  have two chips: "قیمت:" (total, `price`) and "متری:" (per m2, `price_per_m2`). `mapview/viewport` returns nothing without a
  category. Post detail sale ads (homes) put the exact total in the "قیمت کل" row.
- A province id (`parent` of a city) works as a search `city_ids` scope; there is no province list, so `city: "استان <city>"`
  names the province of that city (no `web_url`: the site has no province address we could confirm). Typed brand search (`fields-search`) is fuzzy ('car' finds Kia Carens): `build_query`
  keeps only hits that contain the typed text and validates keys against the option tree.
- Business card of a personal ad: `business-data/{token}/lazy` answers 404 "business not found"; `dv_seller` treats that as
  a personal seller. `/places/districts/{id}/neighbours` is short (Vanak lists only Jordan), so `dv_neighbours` adds `nearest`
  by centroid distance. `webengage.business_ref` (17 chars) is not a `brand_token`; `dv_post` does not return it.
- `dv_best_for_budget` / `dv_find_deals` skip ads under 20% of the uncapped market median and bait titles (`_IMPLAUSIBLE`, `_BAIT`, unless the
  word is in the query). Property for sale is ranked per m2: list rows have no area, so it comes from the title, else from the ad page
  (`with_areas`, at most 6 fetches: ~30 rapid post fetches got HTTP 429). Per-m2 values are cut to whole thousands like the site (`per_m2`).
- `sort_date` / `age_h` is the last bump (نردبان), not the publication: only the ad page has the real time ("انتشار آگهی" /
  "آخرین نردبان" Jalali lines, `published_at` / `bumped_at` via `jalali_time`). `rank_pool` opens the pages of the 6 best per-m2
  ads to read it plus floor (زیرهمکف), "پارکینگ ندارد", "آسانسور ندارد", and rescales the score; unchecked ads get only +5 for a
  recent bump. `dv_similar` drops property cards priced 5x off or with a title area 0.4x-2.5x off (`excluded_outliers`).
- `dv_search` flags prices at or under a tenth of the fetched median as `outlier` (`flag_outliers`, needs 5 priced ads).
- No rate limit was seen (40 searches in 15 s). Keep calls modest anyway: the semaphore allows 2 at once, with a small jitter before each call.
- Not available: price valuation, a similar-ads endpoint (`dv_similar` searches the ad's breadcrumb), sort by price,
  dealer paging, SEO slug routes, anything behind login.

## Release

Bump `version` in `pyproject.toml` and both versions in `server.json`, update `uv.lock` if deps changed, commit,
then `git tag vX.Y.Z && git push --tags`. `.github/workflows/publish.yml` tests, publishes to PyPI (trusted
publishing, environment `pypi`), creates the GitHub Release and publishes to the MCP Registry (GitHub OIDC).
Then check the server table in `../README.md` and `../../iran-mcp` (see `../CLAUDE.md`).
