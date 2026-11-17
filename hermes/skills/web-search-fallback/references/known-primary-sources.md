# Known primary sources (verified from the jarvis host, 2026-09-02)

Sources confirmed reachable from this house (then over the tailnord exit node, retired 2026-10-27) on the day the
search layer was degraded. Re-verify at use time; reachability can drift.

## News RSS feeds

- **cointelegraph.com/rss** — worked (HTTP 200, ~47 KB, full `<item>` blocks with title,
  pubDate, description, link). General crypto news.
- coindesk.com/arc/outboundfeeds/rss/ — timed out (HTTP 000) that day; keep as a candidate.
- theblock.co/rss.xml — timed out (HTTP 000) that day; keep as a candidate.
- decrypt.co/feed — timed out (HTTP 000) that day; keep as a candidate.

Pattern: batch 3-5 candidates in one loop; expect most to time out, at least one to serve.

## Public data APIs (no key)

- **CoinGecko** — crypto prices/market data:
  - `api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true&include_last_updated_at=true`
  - `api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=1&interval=hourly` (time series)
  - worked both that day; rate-limits on anonymous tier (~10-30 req/min).
- **endoflife.date** — software release-cycle data:
  - `https://endoflife.date/api/python.json` (also `nodejs`, `java`, etc.): per-cycle
    `latest`, `latestReleaseDate`, `eol`, `pep`, `support`.
  - verified working from the jarvis host, 2026-09-03.

## Vendor primary sources (version/release facts)

- **python.org** — authoritative trio for "latest Python 3.x.y":
  - `https://www.python.org/downloads/source/` — newest release per series
    (serves `Content-Encoding: gzip`; needs `curl --compressed`).
  - `https://www.python.org/downloads/release/python-31214/` — per-release page.
  - `https://www.python.org/ftp/python/<ver>/Python-<ver>.tgz` — HEAD request's
    `Last-Modified` header is the release date.
  - `https://docs.python.org/3.12/whatsnew/changelog.html` — changelog; can lag one
    release behind (observed 2026-09-03), so use as a supplement, not the source of
    truth.

## Local SearXNG (search layer)

- `$SEARXNG_URL/search?q=<query>&format=json` (`http://searxng:8080` in the stack)
  - `unresponsive_engines` lists per-engine failures; all-engine timeouts point at the
    house's egress, not SearXNG itself.
  - Degradation can also surface as `web_search` results that are generic junk
    (unrelated news homepages) instead of an empty array — treat both as the same state.
- Config gap seen: `web_extract` says "SearXNG is a search-only backend" — fix by setting
  `web.extract_backend` (firecrawl / keenable / exa / parallel) in Hermes config.

## Parsing recipe (stdlib only — no pip on this host)

```python
import re, email.utils
data = open("/tmp/feed.xml", encoding="utf-8").read()
items = re.findall(r"<item>(.*?)</item>", data, re.S)
def clean(s):
    s = re.sub(r"<[^>]+>", "", s)
    for a, b in ("&amp;","&"), ("&lt;","<"), ("&gt;",">"), ("&quot;",'"'):
        s = s.replace(a, b)
    return s.strip()
# per item: <title>, <pubDate> (parsedate_to_datetime), <description>, <link>
```
