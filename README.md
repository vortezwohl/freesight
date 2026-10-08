<div align="center">

# Freesight

**74 free data sources. Zero API keys. One polite client.**

A thin multi-channel access SDK for competitive-intelligence research:
every free, keyless public endpoint — app stores, search engines, company
registries, funding filings, package ecosystems, threat intel and more —
wrapped in one client that handles rate limits, 429 cooldowns, TTL caching
and request coalescing for you.

[![version](https://img.shields.io/badge/version-0.4.1-blue)](pyproject.toml)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![sources](https://img.shields.io/badge/sources-74%20free%20%26%20keyless-orange)](#-source-catalog)
[![tests](https://img.shields.io/badge/tests-131%20passed-brightgreen)](#-development)
[![stars](https://img.shields.io/github/stars/vortezwohl/freesight)](https://github.com/vortezwohl/freesight/stargazers)

```bash
pip install freesight
```

**Why · Quick Start · Source Catalog · Concurrency · Caching · Extending · Boundaries · Development**

[Why](#-why-freesight) · [Quick Start](#-quick-start) · [Source Catalog](#-source-catalog) ·
[Concurrency](#-built-for-polite-concurrency) · [Caching](#-caching--storage) ·
[Extending](#-extending-the-kernel-layer) · [Boundaries](#-design-boundaries) · [Development](#-development)

</div>

## Why freesight?

Every serious research workflow ends up talking to the same free public
endpoints — and hitting the same walls: undocumented rate limits, 429 walls,
per-IP throttling, HTML that changes overnight, and one brittle script per
source. freesight turns that sprawl into one disciplined access layer.

| Pain | freesight |
| --- | --- |
| Free APIs throttle per IP; one burst kills your access | Per-host token buckets, adaptive 429 cooldown, and concurrency seats |
| A thousand users ask the same question | Single-flight coalescing: one upstream call, everyone shares the result |
| Every source has different params and failure modes | Uniform `FetchResult` contract; failures return `ok=False` instead of raising |
| You don't know what a source accepts | Auto-derived JSON Schema per source via `describe()` — agent-tool ready |
| Frameworks want to own your pipeline | No aggregation, no ranking, no persistence — freesight only fetches |

The SDK deliberately does one thing: **single-channel fetch**. Each source is
an independent, thin wrapper. Merging, filtering, ranking and semantic
judgment stay with you — that's why the runtime footprint is just
`httpx` + `h2` + `ddgs`.

## Quick Start

```bash
pip install freesight
# or
uv add freesight
```

One-liner (module-level default client):

```python
import freesignt

result = freesignt.fetch("hn_algolia", query="show hn")
print(result.ok, result.data)
```

Sync client for scripts and humans — source names become methods:

```python
import freesignt

with freesignt.FreeSight() as client:
    result = client.itunes_search(term="notion", limit=5)   # -> FetchResult
    reviews = client.itunes_reviews(app_id=1239583776)
    info = client.describe("crt_sh")          # param schema + rate notes
    names = [s.name for s in client.list_sources()]
```

Async client for servers and agent hosts — same method surface:

```python
from freesignt import AsyncFreeSight

async with AsyncFreeSight() as client:
    result = await client.fetch("hn_algolia", query="show hn")
    domain = await client.rdap_domain(domain="openai.com")
```

Every fetch returns the same contract:

```python
result.ok        # True when HTTP 200 and parsing held up
result.data      # the source's own payload — raw, unfiltered, unranked
result.error     # failure detail (when ok is False)
result.cached    # served from cache / single-flight
result.to_dict() # JSON-ready export
```

> [!TIP]
> Failures never raise (parameter validation `ValueError` excepted).
> Branch on `ok` and decide your own retry policy. To query several channels
> at once, orchestrate with `asyncio.gather` — freesight stays out of it.

```python
# Caller-side orchestration example
results = await asyncio.gather(
    client.fetch("crt_sh", domain="openai.com"),
    client.fetch("rdap_domain", domain="openai.com"),
    client.fetch("rapiddns", domain="openai.com"),
    client.fetch("urlscan", domain="openai.com"),
    return_exceptions=False,
)
```

## Source Catalog

74 sources across 20 modules, all free and keyless. Rate limits are
measured snapshots (2026-09); adaptive cooldown absorbs upstream changes
without code edits.

| Domain | Sources | What you get |
| --- | --- | --- |
| App stores | `itunes_search` / `itunes_reviews` / `itunes_charts` | App Store metadata, reviews, per-country charts |
| Communities | `hn_algolia` / `hn_firebase` | Full Hacker News history + live feeds |
| Open source | `github_public` / `ecosyste_ms` / `npm_registry` / `pypi_metadata` / `pypi_downloads` / `wordpress_plugins` / `huggingface_hub` | Repos, packages, download counts, model trends |
| Hiring | `greenhouse_jobs` / `lever_jobs` | Public ATS boards — stealth-company direction signals |
| Social | `bluesky` / `mastodon_trends` / `v2ex` | Public search and trends |
| Legal disclosure | `sec_edgar` / `sec_form_d` / `rdap_domain` / `common_crawl` | SEC filings, Form D raises, new domains, web-wide snapshots |
| Games | `steam_store` / `steamspy` / `itchio_feed` | Steam search, player estimates, indie RSS |
| Infra footprint | `crt_sh` / `rapiddns` / `subdomain_center` / `otx_passive_dns` / `hackertarget` / `shodan_internetdb` / `certspotter` / `wayback_cdx` | Subdomain discovery, passive DNS, IP/port profiles, archive indexes |
| Threat intel | `urlscan` / `hudsonrock` | Public scan records; infostealer exposure (sensitive — your compliance) |
| Web search | `ddg_search` / `searxng` / `baidu` / `yahoo` / `mojeek` / `wikipedia` / `wikidata` / `gdelt` | DuckDuckGo (browser-fingerprinted), metasearch, three SERPs, knowledge graphs, global news index |
| Page fetching | `jina_reader` / `allorigins` / `codetabs` / `corsproxy` | Headless-render-to-Markdown, raw content proxies |
| Company registries | `jp_houjin_bangou` / `fr_sirene` / `fr_bodacc` / `no_brreg` / `fdic_banks` | Japan NTA, France (Sirene/BODACC), Norway Brreg, US FDIC |
| Venture | `yc_companies` / `signal_nfx` | YC directory (yc-oss static JSON), NFX investor lists |
| Macro statistics | `worldbank` / `eurostat` / `oecd` / `imf` / `cn_stats` | SDMX/REST series from major statistical agencies |
| Product communities | `discourse` / `fdroid` | Any Discourse forum's public JSON, F-Droid app metadata |
| Content feeds | `reddit` / `youtube_rss` / `google_news` / `rsshub` / `lobsters` | Reddit (json/rss), YouTube channels, Google News, RSSHub routes, Lobsters |
| Academia | `crossref` / `openalex` / `arxiv` | 150M+ DOIs, 250M+ scholarly entities, preprints |
| Package ecosystems | `rubygems` / `crates` / `packagist` / `nuget` / `dockerhub` / `repology` | Ruby / Rust / PHP / .NET packages, Docker images, cross-distro versions |

<details>
<summary><b>Source-by-source notes and known edges</b></summary>

- Infra-footprint and threat-intel endpoints follow theHarvester community
  behavior (2026-09); declared rates are conservative pending re-testing.
  `shodan_internetdb` takes an IP (freesight does no DNS resolution);
  `hackertarget` has a small daily keyless quota.
- Anti-bot sensitive: `baidu` (security-verification page, detected
  explicitly), `reddit` (anonymous ~10 qpm; register free OAuth for heavy
  use), `cn_stats` (403 from some networks), `rsshub` (public instances
  fluctuate — self-host for production), `searxng` (public instances often
  run browser checks — self-host recommended), `mojeek` (httpx gets an empty
  shell page; needs a browser session). SERP parsing is best-effort: on
  redesigns the raw HTML is returned for the caller to inspect.
- Network reachability varies: `jp_houjin_bangou`, `imf`, `repology`,
  `lobsters` had TLS handshake failures from some egress networks during
  testing (the endpoints themselves are public and keyless) — verify from
  your deployment network first.
- `ddg_search` routes through the `ddgs` library (browser-fingerprint via
  primp) with its own retry logic, outside the `HttpEngine` pipeline.
- Deliberately not included after hands-on probing (2026-09): EDINET v2
  (requires a free subscription key; v1 is gone), German Handelsregister
  (JSF form site, no keyless API; the OffeneRegister mirror was down),
  OpenVC (Cloudflare browser wall — reachable via `jina_reader` instead),
  INPI RNE API (same wall; `fr_bodacc` covers French disclosures),
  Crunchbase API (free tier removed in 2025). Removed 2026-10 after the
  endpoint moved behind a key: `uspto_trademark` (USPTO IBD API now
  requires an api.uspto.gov key; the legacy URL 301s to a web page).
- Chinese company registry (gsxt.gov.cn) is excluded on principle:
  automated scraping is explicitly forbidden and legally risky.

</details>

## Built for polite concurrency

Every fetch flows through a fixed three-layer pipeline:
**cache → single-flight → rate engine**.

1. **Per-host token buckets** — each source's measured rate becomes a
   steady drip (burst 1 by default — pure pacing, friendliest shape for
   free APIs). Sources sharing a host share the budget (the three iTunes
   sources draw from one bucket), matching how free APIs actually meter.
2. **Adaptive cooldown** — a 429, a `Retry-After`, or
   `X-RateLimit-Remaining: 0` freezes the whole host until recovery time;
   all waiters queue automatically. Short cooldowns (≤ 30s) retry once,
   transparently. Long cooldowns fail fast with the cooldown seconds in
   the error, so callers — not the SDK — decide what to do.
3. **Per-host concurrency seats** (default 5) prevent connection storms.
4. **TTL cache** — per-source suggested TTLs (charts 1h, reviews 15m,
   registries 24h); only successful results enter the cache.
5. **Single-flight coalescing** — concurrent identical requests share one
   upstream call. A thousand users checking the same competitor trigger
   exactly one request; if the leader is cancelled, waiters take over.

```python
# High-traffic deployment knobs
client = freesignt.FreeSight(
    rate_multiplier=0.3,          # tighten global rates (0.2–0.5 typical)
    max_concurrency_per_host=3,   # fewer in-flight per endpoint
    cache_maxsize=4096,           # in-process cache capacity
)
snapshot = client.rate_snapshot()  # per-host rate / cooldown -> monitoring
```

## Caching & storage

Default is an in-process TTL+LRU cache; persistence is deliberately left
to callers. Implement three methods and plug in your own store:

```python
class MyRedisCache:
    """Satisfy CacheProtocol (get/set/clear) and it's injectable."""
    def get(self, key): ...
    def set(self, key, value, ttl_s): ...
    def clear(self): ...

client = freesignt.FreeSight(cache=MyRedisCache())  # or cache=None to disable
```

Cached objects are treated as read-only; deep-copy before mutating.

## Threads & event loops

- `AsyncFreeSight` binds to the event loop that created it; use it inside
  servers and agent hosts.
- `FreeSight` owns a dedicated background loop thread (double-checked lock,
  unique per client instance — multiple clients spawn one thread each).
  All sync methods are thread-safe across worker threads.
- Using `FreeSight` inside a running event loop raises explicitly — switch
  to `AsyncFreeSight` there.

## Extending: the kernel layer

The root package stays minimal; power users build on `freesignt.core`.
Defining a `BaseSource` subclass registers it automatically:

```python
from freesignt.core.base import BaseSource
from freesignt.core.models import SourceCategory

class MySource(BaseSource):
    name = "my_source"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    description = "What this source gives you"

    async def fetch(self, query: str) -> ...:
        """Docstring Args become the auto-derived JSON Schema."""
        return await self._get("https://example.com/api", params={"q": query})
```

Kernel primitives for deeper integration:

```python
from freesignt.core.base import BaseSource     # define-and-register base class
from freesignt.core import registry            # get / catalogue / by_category
from freesignt.core.cache import TTLCache, SingleFlight, CacheProtocol
from freesignt.core.ratelimit import TokenBucket, RateGovernor
from freesignt.core.http import HttpEngine, HttpConfig
```

`describe()` / `SourceInfo.input_schema` expose per-source parameter JSON
Schemas — ready for OpenAI function calling or any agent tool layer you
build on top. The SDK ships the schemas, not the framework.

## Design boundaries

Deliberately **not** provided (yours to own):

- Cross-source aggregation, fan-out orchestration, merging
- Filtering, ranking, deduplication, semantic relevance
- Agent tool wrappers — schemas are exposed, wrapping is not
- Persistence or durable storage — `CacheProtocol` is the seam
- Distributed coordination — buckets, caches and single-flight are
  per-process; use `rate_multiplier` to split budgets across workers

## Development

```bash
uv sync                 # install (dev included)
uv run pytest           # 131 offline tests (httpx MockTransport, no network)
uv run ruff check .     # lint
```

Coverage: registry & schema derivation, token bucket & cooldown math,
rate-header parsing, cache & single-flight, all 74 sources' fetch and
in-source normalization, RSS/Atom parsing, sync-bridge thread safety.

## Contributing

Bug reports, new keyless sources (with measured rate limits), and test
hardening are welcome. Feature PRs that grow freesight into an aggregation
framework will be declined — the thin boundary is the point.

## License

[MIT](LICENSE)
