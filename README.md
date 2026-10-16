<!-- mcp-name: io.github.sepehr071/divar-mcp -->

# divar-mcp: Divar MCP server for AI agents

**Unofficial, read-only MCP server that lets your AI agent search Divar (دیوار) ads in any Iranian city: homes, cars, phones and more. It finds the cheapest and best-value ads, compares ads, checks whether a price is fair and browses the map. Prices are in Toman.**

An [MCP](https://modelcontextprotocol.io) server for [Divar](https://divar.ir), Iran's largest classifieds site.
Search by city, neighbourhood, category, text and any of the category's filters, read a full ad, compare several, see
the seller's other ads, get market statistics, appraise a price against similar ads and poll for new ads, all from
Claude, Cursor or Copilot. No account, no API key, nothing is posted or changed.

[Quick start](#quick-start) · [Tools](#tools) · [FAQ](#faq) · [فارسی](#فارسی)

---

## For AI agents

- **What:** Unofficial MCP server for Divar (دیوار, divar.ir), Iran's classifieds site. Persian queries work best; prices in Toman.
- **Install:** `claude mcp add divar -- uvx divar-mcp` (stdio, needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.10+).
- **Auth:** none. Optional `DIVAR_MCP_PROXY` sets an HTTP proxy for every request.
- **Access:** read-only on Divar. It cannot log in, post, chat, bookmark or reveal a seller's phone number. Only `dv_watch` and `dv_price_trend` write, to a local file of your own searches.
- **Also:** resources `divar://cities`, `divar://categories`, `divar://category-filters/{slug}`; prompts `compare_ads`, `best_under_budget`, `appraise_listing`, `watch_market`.
- **Tools:** 31, all prefixed `dv_`. Start with `dv_search`, `dv_post`, `dv_cheapest`, `dv_best_for_budget`, `dv_market`.
- **Typical sequence:** `dv_cities` / `dv_categories` -> `dv_filters` -> `dv_search` -> `dv_post` -> `dv_similar` / `dv_appraise` / `dv_compare`.
- **Use when:** the user wants to find, price-check or monitor Divar ads (flats to buy or rent, used cars, phones, furniture, jobs...), or asks what something costs in a city.
- **Do not use when:** the user wants to post an ad, message a seller or get a phone number (login-only on Divar).
- **Output:** compact JSON ad cards. `price` for a sale; `deposit` + `rent` for a rental; no price means negotiable. Pass `token` between tools and `next_cursor` back as `cursor`.
- **Machine-readable:** [server.json](https://github.com/sepehr071/divar-mcp/blob/main/server.json), and the catalog of all Iranian MCP servers [awesome-iran-mcp](https://github.com/sepehr071/awesome-iran-mcp) (`TOOLS.md`, `servers.json`, `llms.txt`).

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
claude mcp add divar -- uvx divar-mcp
```

Any other MCP client: command `uvx`, args `["divar-mcp"]` (or `pip install divar-mcp` and run `divar-mcp`).

## What it can do

- Search ads in one or several cities by category, text, neighbourhood, price (and deposit for rentals), brand and model, photo, freshness and every other filter of the category; merge several pages with duplicates removed; continue with a cursor
- Full ad: price in **Toman**, specs, features, description, all images, map position, seller type, shared-housing flag, placeholder-price detection
- Smart tools: cheapest ads (junk prices skipped), best value and most-for-the-budget, side-by-side comparison showing only the differences, similar ads, fair-price appraisal with percentile and confidence, market statistics by district and by day
- Monitoring: poll `dv_new_since` with the last timestamp to see only new ads; export results as CSV or JSON lines
- Map: ads around a GPS point or landmark with distances; neighbourhoods, neighbours, address of a point
- Sellers and dealers: how many ads a seller has, the business card and its ads

## Tools

All tools are read-only and start with `dv_`. An ad card has `token`, `title`, `price` (sale) or `deposit` + `rent`
(`rent` 0 with `full_deposit` means رهن کامل), `info` (mileage, condition...), `district`, `seller` (agency label),
`age_h` (hours since the last bump), `photos`, `image` (thumbnail URL, only from `dv_search` with `thumbnails`), `chat`, `tag` (paid promotion), `url`, and
`suspect_price` when the price looks like a placeholder. Absent flags mean false. Default page size is 10 to 20 ads.

### Find places and categories

| Tool | What it does |
|---|---|
| `dv_cities` | City ids for a name (1,177 cities); no query lists the biggest cities |
| `dv_districts` | Neighbourhoods of a city with ids, centre and main streets |
| `dv_neighbours` | Neighbourhoods adjacent to a district (Divar's list plus the nearest ones by distance) |
| `dv_locate` | City, neighbourhood and street address of a GPS point |
| `dv_place_search` | Map autocomplete: landmark or street to coordinates |
| `dv_place_mapping` | Convert old Divar place ids to current ones |
| `dv_categories` | The 236-node category tree; search by name, list children |
| `dv_filters` | Every filter of a category: key, type, label, unit, options |
| `dv_filter_options` | Brand and model keys (161 car brands), districts and other option lists, with typed search |
| `dv_suggest` | The search box's suggestions and category guesses for some text |
| `dv_parse_query` | A plain Persian request ("آپارتمان اجاره ونک زیر ۸ میلیارد") to `dv_search` arguments: city, neighbourhoods, price bounds, area (`80 متری`), category |
| `dv_boundary` | Neighbourhood borders of a city (GeoJSON): the city box, or one neighbourhood's polygon |

### Search and price

| Tool | What it does |
|---|---|
| `dv_search` | Search ads with city, category, text, districts, price, brand, photo, freshness and any category filter; multi-page merge, client-side age and keyword filters, cursor paging |
| `dv_count` | How many ads match a search (capped at 10,000) |
| `dv_cheapest` | The cheapest ads of a search; placeholder prices and far-below-median outliers skipped |
| `dv_best_for_budget` | Within a budget: best value with an explainable `deal_score` (and `reasons`) against the median of the ads that fit (price per m2 for property for sale), and the most you can get; bait / co-op / loan titles are skipped |
| `dv_find_deals` | Ads priced well below the median of a search, no budget needed; junk prices and bait titles removed |
| `dv_market` | Price statistics (min, quartiles, median, max) plus breakdown by district and by day |
| `dv_appraise` | Is an ad's price (or a price you give) high or low versus similar ads: percentile, verdict ("insufficient comparables" when the call is extreme but thin), confidence |
| `dv_new_since` | Ads published after a timestamp: poll with the returned `latest` to watch a search |
| `dv_export` | The ads of a search as CSV or JSON lines |
| `dv_nearby` | Map ads around a point (category required), nearest first, with coordinates, distance and total price |
| `dv_watch` | Saved searches: create, then check to get only the ads newer than the last check (local file) |
| `dv_price_trend` | One median asking price per day for a search, kept locally, with change since the previous day |
| `dv_status` | Is Divar reachable, latency, caches, saved watches and the tool list |

### One ad and its seller

| Tool | What it does |
|---|---|
| `dv_post` | Full ad: price, specs, features, description, images, map position, seller type, flags |
| `dv_posts` | Up to 10 ads in one call, compact |
| `dv_compare` | 2 to 6 ads side by side: prices and only the specs that differ |
| `dv_similar` | Ads like this one (same brand and area, widening step by step) |
| `dv_seller` | A seller's personal and business ad counts, business card (or "personal seller") and other ads |
| `dv_dealer` | A business's public page: name, member since, first 11 ads |

## Good to know

- **Prices are Toman.** Divar's prices are Toman already; nothing is converted. A missing price means negotiable (توافقی), never 0. Rentals have `deposit` (ودیعه) and `rent` (اجاره); `rent` 0 with `full_deposit` is رهن کامل.
- **Asking prices.** Everything is what sellers typed, not what deals close at. Some sellers type junk (1,111,111 or a deposit as a car price): `suspect_price` flags placeholders, and rankings and statistics skip them and prices under a tenth of the median.
- **Province scope.** `city: "استان مشهد"` (or `province:مشهد`) searches the whole province of that city; districts and boundaries need a city.
- **Order.** Divar's API only returns newest first; the first 1-3 rows can be paid promotions with older dates. `dv_search` `sort` and the price tools sort the ads fetched in the call, not the whole market. Paging is a time cursor (`next_cursor` -> `cursor`).
- **Filters.** `dv_filters` lists the keys of a category; filters a category lacks are reported in `ignored`, not silently dropped. Web URL slugs (`car`) work next to API slugs (`light`).
- **Map positions** of most ads are fuzzed by about 500 m (`approximate_location`).
- **No phone numbers.** Divar shows them only to logged-in users; this server never logs in. Use `url` to open the ad.
- Detail prices come from the ad's price rows; Divar's raw number elsewhere in the ad is float-rounded for large values.
- Persian queries work best; Arabic letter variants (ي/ك) are folded, and an empty Persian query is retried once folded.

## FAQ

**Is there a Divar API or MCP server for AI agents?**
Yes: this unofficial MCP server wraps the public JSON API the Divar website itself uses, so Claude, Cursor or
Copilot can search ads, read details and compare prices. Divar's official Kenar API is for partners (OAuth and an
API key) and is not used here.

**Is it official, safe and free?**
It is unofficial, open source (MIT) and free. It is read-only, needs no account or API key, and runs on your
machine with no hosted server in between.

**Can it message a seller, post an ad or get a phone number?**
No. Those need a Divar login and are deliberately left out.

**Can it alert me when a matching ad appears?**
It does not push. `dv_new_since` returns only ads newer than a timestamp, and `dv_watch` remembers that timestamp for a saved search (in `~/.divar-mcp/state.json`, or `DIVAR_MCP_STATE`), so a scheduler (cron, a Claude Code loop) can call `dv_watch` with `action: "check"`.

## Configuration

The server calls `api.divar.ir` directly and ignores system proxy variables. To use a proxy set `DIVAR_MCP_PROXY=http://host:port`.
Saved watches and price history go to `~/.divar-mcp/state.json`; set `DIVAR_MCP_STATE` to use another file.

## Development

```bash
export HTTPS_PROXY=http://127.0.0.1:10898 HTTP_PROXY=http://127.0.0.1:10898   # uv downloads only
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest -q            # offline, recorded fixtures
uv run pytest -m live -q    # real API, one test per tool
```

The API map this server is built on lives next to it in `../divar/`.

<a id="فارسی"></a>

## فارسی: سرور MCP دیوار

<div dir="rtl">

**divar-mcp** یک سرور MCP غیررسمی برای دیوار است که به دستیار هوش مصنوعی شما (Claude، Cursor، Copilot و ...) اجازه
می&zwnj;دهد در آگهی&zwnj;های هر شهر ایران جستجو کند، آگهی را کامل ببیند، چند آگهی را مقایسه کند، ارزان&zwnj;ترین و
مناسب&zwnj;ترین آگهی را برای یک بودجه پیدا کند، منصفانه بودن قیمت را بسنجد و آگهی&zwnj;های اطراف یک نقطه روی نقشه را ببیند.

- فقط خواندنی است و حساب کاربری یا کلید API نمی&zwnj;خواهد؛ شماره تلفن فروشنده را نشان نمی&zwnj;دهد.
- همه قیمت&zwnj;ها به تومان است؛ قیمت نداشتن یعنی توافقی.

بپرسید: «ارزان&zwnj;ترین آپارتمان ۲ خوابه اجاره&zwnj;ای در ونک با ودیعه زیر ۱ میلیارد کدام است؟»

</div>

## Related servers

- All Iranian MCP servers: [awesome-iran-mcp](https://github.com/sepehr071/awesome-iran-mcp)

## Disclaimer

Unofficial; not affiliated with Divar. Read-only access to the data of public pages. Keep request rates low (Divar showed no rate limit, but be polite).

If this saves you clicks, a star helps others find it.

## License

MIT
