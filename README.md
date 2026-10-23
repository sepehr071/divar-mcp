<!-- mcp-name: io.github.sepehr071/divar-mcp -->

# divar-mcp: Divar MCP server for AI agents

**Let your AI agent search Divar (دیوار) ads in any Iranian city, and tell it whether a price is fair.**<br>
An unofficial, read-only [MCP](https://modelcontextprotocol.io) server for [Divar](https://divar.ir), Iran's largest classifieds site (homes, cars, phones, jobs, services).<br>
Search, read ads, compare, find the cheapest and best-value ads, appraise prices and browse the map from Claude, Cursor or Copilot. Prices are in Toman.

[![PyPI](https://img.shields.io/pypi/v/divar-mcp?color=2563eb)](https://pypi.org/project/divar-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/divar-mcp)](https://pypi.org/project/divar-mcp/)
[![CI](https://github.com/sepehr071/divar-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sepehr071/divar-mcp/actions/workflows/ci.yml)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.sepehr071%2Fdivar--mcp-7c3aed)](https://registry.modelcontextprotocol.io/?q=divar-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/divar-mcp/blob/main/LICENSE)

[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_divar--mcp-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=divar&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22divar-mcp%22%5D%7D)

[For AI agents](#for-ai-agents) · [Why](#why) · [What it can do](#what-it-can-do) · [Quick start](#quick-start) · [Tools](#tools) · [How it works](#how-it-works) · [Good to know](#good-to-know) · [FAQ](#faq) · [Configuration](#configuration) · [فارسی](#فارسی) · [Development](#development)

---

## For AI agents

- **What:** unofficial MCP server (model context protocol) for Divar (دیوار, divar.ir), Iran's classifieds site. Persian queries work best; Arabic letter variants (ي/ك) are folded.
- **Install:** `uvx divar-mcp` or `claude mcp add divar -- uvx divar-mcp`. Transport: stdio. Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.10+).
- **Auth:** none, no API key or account. Optional `DIVAR_MCP_PROXY` (HTTP proxy URL, or a comma-separated list); system proxy settings are ignored.
- **Access:** read-only on Divar. It cannot log in, post, chat, bookmark or reveal a seller's phone number, so no confirmation is needed before calling a tool. Only `dv_watch` and `dv_price_trend` write, and only to a local file of your own searches.
- **Tools:** 31, all prefixed `dv_`. Start with `dv_search`, `dv_post`, `dv_cheapest`, `dv_best_for_budget`, `dv_market`.
- **Typical sequence:** `dv_cities` / `dv_categories` -> `dv_filters` -> `dv_search` -> `dv_post` -> `dv_similar` / `dv_appraise` / `dv_compare`.
- **Also:** resources `divar://cities`, `divar://categories`, `divar://category-filters/{slug}`; prompts `compare_ads`, `best_under_budget`, `appraise_listing`, `watch_market`.
- **Units:** prices in **Toman** (Divar's own unit, nothing is converted). `price` for a sale; `deposit` + `rent` for a rental (`rent` 0 with `full_deposit` means رهن کامل); no price means negotiable.
- **Use when:** the user wants to find, price-check or monitor Divar ads (flats to buy or rent, used cars, phones, furniture, jobs...), or asks what something costs in a city.
- **Do not use when:** the user wants to post an ad, message a seller or get a phone number (login-only on Divar), or needs a market other than Divar.
- **Output:** compact JSON ad cards. Pass `token` between tools and `next_cursor` back as `cursor`.
- **Machine-readable:** [`server.json`](https://github.com/sepehr071/divar-mcp/blob/main/server.json) and the [iran-mcp catalog](https://github.com/sepehr071/iran-mcp) (`TOOLS.md`, `servers.json`, `llms.txt`).

## Why

Divar is where Iranians buy and sell flats, cars and phones, but answering "what is a fair price for a 2-bedroom flat in Vanak?" means scrolling through dozens of ads and ignoring the fake prices. With `divar-mcp` an agent fetches the ads, drops placeholder prices, computes the median and quartiles of the asking prices, and tells you which ads are well below them.

> **You:** Find the best 2-bedroom flat to rent in Vanak with a deposit under 1 billion Toman.
>
> **Agent:** *calls* `dv_parse_query` -> `dv_best_for_budget` -> `dv_post`
>
> It lists the best-value ads against the median of the ads that fit, with the reasons for each score, then opens the one you pick.

## What it can do

- **Search** ads in one or several cities by category, text, neighbourhood, price (and deposit for rentals), brand and model, photo, freshness and every other filter of the category; merge several pages with duplicates removed; continue with a cursor
- **Read a full ad:** price in Toman, specs, features, description, all images, map position, seller type, placeholder-price detection
- **Find value:** cheapest ads (junk prices skipped), best value and most-for-the-budget, ads priced well below the median, side-by-side comparison showing only the differences, similar ads
- **Judge a price:** fair-price appraisal with percentile and confidence, market statistics by district and by day
- **Monitor:** poll `dv_new_since` with the last timestamp to see only new ads; keep saved searches and a daily median price trend locally; export results as CSV or JSON lines
- **Use the map:** ads around a GPS point or landmark with distances; neighbourhoods, neighbours, borders, address of a point
- **Look at sellers and dealers:** how many ads a seller has, the business card and its ads

Example prompts:

- "Cheapest used Peugeot 206 in Tehran, and is it priced fairly compared with similar ads?"
- "Find apartments to rent near Vanak under 8 billion Toman and compare the top three."
- "Show new iPhone 15 ads in Mashhad since this morning."
- "What is the median price per square metre for apartments for sale in Gisha?"
- <span dir="rtl">ارزان&zwnj;ترین آپارتمان ۲ خوابه اجاره&zwnj;ای در ونک با ودیعه زیر ۱ میلیارد کدام است؟</span>
- <span dir="rtl">قیمت این ماشین نسبت به آگهی&zwnj;های مشابه گران است یا ارزان؟</span>
- <span dir="rtl">آگهی&zwnj;های جدید گوشی آیفون ۱۵ در مشهد را از امروز صبح نشان بده.</span>

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). No API key or account.

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add divar -- uvx divar-mcp
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config, then add:

```json
{
  "mcpServers": {
    "divar": { "command": "uvx", "args": ["divar-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Cursor</b></summary>

Add the Claude Desktop block above to `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project).
</details>

<details>
<summary><b>VS Code (Copilot agent mode)</b></summary>

Click **Install in VS Code** above, or add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "divar": { "type": "stdio", "command": "uvx", "args": ["divar-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Codex CLI</b></summary>

```bash
codex mcp add divar -- uvx divar-mcp
```

Or in `~/.codex/config.toml`:

```toml
[mcp_servers.divar]
command = "uvx"
args = ["divar-mcp"]
```
</details>

<details>
<summary><b>Gemini CLI</b></summary>

Add to `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "divar": { "command": "uvx", "args": ["divar-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Windsurf</b></summary>

Add the Claude Desktop block to `~/.codeium/windsurf/mcp_config.json`.
</details>

<details>
<summary><b>Anything else</b></summary>

It is a standard stdio MCP server: run `uvx divar-mcp`, or `pip install divar-mcp` and run `divar-mcp`.
</details>

## Tools

An ad card has `token`, `title`, `price` (sale) or `deposit` + `rent` (`rent` 0 with `full_deposit` means رهن کامل), `info` (mileage, condition...), `district`, `seller` (agency label), `age_h` (hours since the last bump), `photos`, `image` (thumbnail URL, only from `dv_search` with `thumbnails`), `chat`, `tag` (paid promotion), `url`, and `suspect_price` when the price looks like a placeholder. Absent flags mean false. Default page size is 10 to 20 ads.

All tools start with `dv_` and are annotated read-only, except `dv_watch` and `dv_price_trend`, which write only to a local file.

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

## How it works

```text
  AI agent  (Claude, Cursor, Copilot, ...)
      │
      │  MCP over stdio
      ▼
  divar-mcp  (runs on your machine)
      │
      │  HTTPS
      └──────▶  api.divar.ir   ads, categories, places, map
```

`divar-mcp` runs locally and calls the same JSON gateway the divar.ir web app uses. It has no hosted server in between and no API key. Every POST it sends is a read-only query. Requests go out through a Chrome-impersonating client (TLS/HTTP2 fingerprint and headers), at most 2 at a time with a small random delay, and a failed call is retried up to twice. Divar's list answers are widget JSON; the server turns them into compact ad cards and computes rankings and statistics from the asking prices it fetched.

## Good to know

- **Prices are Toman.** Divar's prices are Toman already; nothing is converted. A missing price means negotiable (توافقی), never 0. Rentals have `deposit` (ودیعه) and `rent` (اجاره); `rent` 0 with `full_deposit` is رهن کامل.
- **Asking prices.** Everything is what sellers typed, not what deals close at. Some sellers type junk (1,111,111 or a deposit as a car price): `suspect_price` flags placeholders, and rankings and statistics skip them and prices under a tenth of the median.
- **Province scope.** `city: "استان مشهد"` (or `province:مشهد`) searches the whole province of that city; districts and boundaries need a city.
- **Order.** Divar's API only returns newest first; the first 1-3 rows can be paid promotions with older dates. `dv_search` `sort` and the price tools sort the ads fetched in the call, not the whole market. Paging is a time cursor (`next_cursor` -> `cursor`).
- **Freshness.** `age_h` is the time since the last bump (نردبان), not since publication; only the ad page has the real publish time.
- **Filters.** `dv_filters` lists the keys of a category; filters a category lacks are reported in `ignored`, not silently dropped. Web URL slugs (`car`) work next to API slugs (`light`).
- **Map positions** of most ads are fuzzed by about 500 m (`approximate_location`).
- **No phone numbers.** Divar shows them only to logged-in users; this server never logs in. Use `url` to open the ad.
- **Large prices.** Detail prices come from the ad's price rows; Divar's raw number elsewhere in the ad is float-rounded for large values.
- **Not available:** a Divar price valuation from the site itself, sort by price on Divar's side, paging through all of a dealer's ads, anything behind login.
- **Rate limits:** none was seen in testing, but keep calls modest.
- **Bot check:** if Divar ever serves a challenge page, install `divar-mcp[browser]` and run `patchright install chromium`; the server then clears it once with a headless browser. Without it, the call fails with a message saying so.

## FAQ

<details>
<summary><b>Is there a Divar API or MCP server for AI agents?</b></summary>

Yes: this unofficial MCP server wraps the public JSON API the Divar website itself uses, so Claude, Cursor or Copilot can search ads, read details and compare prices. Divar's official Kenar API is for partners (OAuth and an API key) and is not used here.
</details>

<details>
<summary><b>Is it official, safe and free?</b></summary>

It is unofficial, open source (MIT) and free. It is read-only, needs no account or API key, and runs on your machine with no hosted server in between.
</details>

<details>
<summary><b>Can it message a seller, post an ad or get a phone number?</b></summary>

No. Those need a Divar login and are deliberately left out.
</details>

<details>
<summary><b>Can it alert me when a matching ad appears?</b></summary>

It does not push. `dv_new_since` returns only ads newer than a timestamp, and `dv_watch` remembers that timestamp for a saved search (in `~/.divar-mcp/state.json`, or `DIVAR_MCP_STATE`), so a scheduler (cron, a Claude Code loop) can call `dv_watch` with `action: "check"`.
</details>

<details>
<summary><b>Are prices in Rial or Toman?</b></summary>

Toman. Divar's API already uses Toman, so nothing is converted.
</details>

<details>
<summary><b>How do I use it with Claude, Cursor or Copilot?</b></summary>

Add the server with `claude mcp add divar -- uvx divar-mcp`, or use the matching config in [Quick start](#quick-start), then ask in plain language, in Persian or English.
</details>

<details>
<summary><b>Does it work for cars, phones and jobs, not only homes?</b></summary>

Yes. `dv_categories` lists the 236-node category tree and `dv_filters` shows the filters of each category (brand and model, mileage, price and so on), so the same search tools work across categories.
</details>

<details>
<summary><b>How do I debug what the agent sees?</b></summary>

```bash
npx @modelcontextprotocol/inspector uvx divar-mcp
```
</details>

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DIVAR_MCP_PROXY` | unset | Optional HTTP proxy URL for requests to divar.ir, or a comma-separated list: one is used until it gets blocked or fails to connect, then the next. System proxy settings are ignored |
| `DIVAR_MCP_STATE` | `~/.divar-mcp/state.json` | File for saved watches and price history (used by `dv_watch` and `dv_price_trend`) |

<a id="فارسی"></a>

## فارسی: سرور MCP دیوار

<div dir="rtl">

**divar-mcp** یک سرور MCP غیررسمی و فقط&zwnj;خواندنی برای [دیوار](https://divar.ir)، بزرگ&zwnj;ترین سایت نیازمندی ایران، است. این سرور به دستیار هوش مصنوعی شما (Claude، Cursor، Copilot و ...) اجازه می&zwnj;دهد در آگهی&zwnj;های هر شهر ایران (خرید و فروش و اجاره خانه، خودرو، موبایل و ...) جستجو کند، آگهی را کامل ببیند، چند آگهی را مقایسه کند و ارزان&zwnj;ترین و مناسب&zwnj;ترین آگهی را برای یک بودجه پیدا کند.

**چه کارهایی می&zwnj;کند**

- **جستجوی آگهی** بر اساس شهر، محله، دسته، متن، قیمت (و ودیعه برای اجاره)، برند و مدل و فیلترهای هر دسته.
- **ارزان&zwnj;ترین آگهی&zwnj;ها** و آگهی&zwnj;های زیر قیمت بازار، با حذف قیمت&zwnj;های الکی.
- **ارزیابی قیمت:** گران یا ارزان بودن قیمت یک آگهی نسبت به آگهی&zwnj;های مشابه، همراه با آمار قیمت (میانه و چارک&zwnj;ها).
- **مقایسه آگهی&zwnj;ها** و پیدا کردن آگهی&zwnj;های مشابه.
- **آگهی&zwnj;های اطراف یک نقطه** روی نقشه، محله&zwnj;ها و مرز محله&zwnj;ها.
- **پایش آگهی&zwnj;های جدید** و ذخیره جستجو و روند قیمت روی سیستم خودتان.

**نکته&zwnj;ها**

- فقط خواندنی است: ورود به حساب، ثبت آگهی، گفتگو با فروشنده و نمایش شماره تلفن فروشنده را انجام نمی&zwnj;دهد. کلید API یا حساب کاربری لازم نیست و روی سیستم خود شما اجرا می&zwnj;شود.
- همه قیمت&zwnj;ها به تومان است؛ قیمت نداشتن یعنی توافقی. قیمت&zwnj;ها همان چیزی است که فروشنده نوشته، نه قیمت نهایی معامله.
- پرسش فارسی بهترین نتیجه را می&zwnj;دهد.
- اگر لازم بود پراکسی بگذارید، متغیر `DIVAR_MCP_PROXY` را تنظیم کنید.

**نصب** (اول [uv](https://docs.astral.sh/uv/getting-started/installation/) را نصب کنید). در Claude Code:

</div>

```bash
claude mcp add divar -- uvx divar-mcp
```

<div dir="rtl">

در Claude Desktop، Cursor و بقیه برنامه&zwnj;ها همان تنظیم بخش [Quick start](#quick-start) را بگذارید.

**نمونه پرسش&zwnj;ها**

- «ارزان&zwnj;ترین آپارتمان ۲ خوابه اجاره&zwnj;ای در ونک با ودیعه زیر ۱ میلیارد کدام است؟»
- «قیمت این آگهی خودرو نسبت به آگهی&zwnj;های مشابه چطور است؟»
- «آگهی&zwnj;های جدید گوشی در مشهد را از امروز صبح نشان بده.»
- «میانه قیمت هر متر آپارتمان برای فروش در یک محله چقدر است؟»

</div>

## Development

```bash
git clone https://github.com/sepehr071/divar-mcp && cd divar-mcp
uv sync
uv run ruff check . && uv run ruff format --check .
uv run python -m pytest -q            # offline, recorded fixtures
uv run python -m pytest -m live -q    # real API, one test per tool
uv build
```

Tools live in `src/divar_mcp/` (`places.py`, `catalog.py`, `search.py`, `post.py`, `market.py`, `watch.py`, `meta.py`); each is a typed async function with a docstring that tells the agent when to use it. Issues and PRs are welcome.

Releases: bump the version in `pyproject.toml` and `server.json`, then push a `v*` tag. GitHub Actions publishes to PyPI.

## Related servers

- [iran-mcp](https://github.com/sepehr071/iran-mcp): catalog of all Iranian-service MCP servers
- [digikala-mcp](https://github.com/sepehr071/digikala-mcp) for Digikala products and prices
- [torob-mcp](https://github.com/sepehr071/torob-mcp) for price comparison across Iranian shops on Torob
- [snapp-mcp](https://github.com/sepehr071/snapp-mcp) for Snapp
- [technolife-mcp](https://github.com/sepehr071/technolife-mcp) for Technolife electronics
- [jabama-mcp](https://github.com/sepehr071/jabama-mcp) for Jabama stays

## Disclaimer

Unofficial and not affiliated with or endorsed by Divar. It reads public data through the endpoints of the divar.ir web app, which can change without notice. Everything it returns is asking prices typed by sellers, not advice. Keep request rates low.

If this saves you clicks, a star helps others find it.

## Author

<a href="https://www.linkedin.com/in/sepehr-radmard"><img src="https://raw.githubusercontent.com/sepehr071/iran-mcp/main/assets/author-banner.png" alt="Sepehr Radmard: open-source MCP servers for the Iranian web. LinkedIn: linkedin.com/in/sepehr-radmard" width="100%"></a>

Built and maintained by **Sepehr Radmard** (سپهر رادمرد). Connect on [LinkedIn](https://www.linkedin.com/in/sepehr-radmard) or follow [@sepehr071 on GitHub](https://github.com/sepehr071) for more MCP servers for Iranian websites, all listed in [iran-mcp](https://github.com/sepehr071/iran-mcp). If this saves you time, a star on the repo helps other people find it.

## License

[MIT](https://github.com/sepehr071/divar-mcp/blob/main/LICENSE)
