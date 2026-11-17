---
name: web-search-fallback
description: "Use when web_search returns empty or junk — pivot to RSS, data APIs, vendor primary sources."
version: 0.1.0
author: JARVIS
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Research, Web, Fallback, News, RSS]
    related_skills: [blocked-page-recovery, grounded-citations]
---

# Web Search Fallback (this homelab)

The configured search backend is the local SearXNG the stack runs beside JARVIS
(`SEARXNG_URL`, `http://searxng:8080` in the deploy compose; the old tailnet SearXNG
went with Tailscale on 2026-10-27). It is an address in the config,
not one to type anywhere. When it is degraded, the failure modes are:

- `web_search` returns an empty results array (no error)
- `web_search` returns a few results that are wholly irrelevant (e.g. generic news
  homepages for a software-version question) — same degraded state, same pivot
- `web_extract` errors: *"SearXNG is a search-only backend and cannot extract URL
  content"* — a config gap: `web.extract_backend` is unset. The durable fix is to set it
  to firecrawl, keenable, exa, or parallel; until then the browser tools read a page
  (`browser_navigate`, then the snapshot it returns). The shell is not a fallback: it has
  no network (ADR-0045), so a fetch from there fails every time.

Do not retry the search in a loop. Pivot to primary sources. This is a degraded-
infrastructure fallback, not a claim that search is broken — when engines recover, the
normal path works again.

## 1. Diagnose

Two empty or junk answers in a row are the signal. There is no direct probe: SearXNG's JSON
endpoint was only ever reachable from the shell, and the shell has no network now. If
`web_extract` of a well-known public page also comes back empty, suspect the egress path
(the exit node) rather than SearXNG — feeds and APIs will fail the same way, so say so and
stop rather than working down the list.

## 2. RSS/Atom feeds (news content)

One `web_extract` per feed URL — hosts are flaky, so try several in turn (in one session
cointelegraph.com/rss answered while coindesk, theblock and decrypt all timed out). An empty
result is a timeout or an extractor that did not read the XML, not an empty feed: try the
next feed, then the site's news index page with `web_extract`. Titles, dates and links come
back as text; filter by topic in `execute_code` from the text you already have (Python there
has no network either). See `references/known-primary-sources.md` for the feed list and data
APIs confirmed working in this house.

## 3. Public data APIs (numbers, market data)

Data endpoints usually survive when news sites and search engines do not, and `web_extract`
returns a JSON body as text. No keys needed for the basics (see references). Example, crypto:
`web_extract` of
`https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true`.

## 4. Release versions and software facts

For "what is the latest version / when does X go EOL" questions, prefer the vendor's
own download page or a no-key data API over the search layer:

- **endoflife.date** — JSON per software release cycle, no key needed:
  `web_extract` of `https://endoflife.date/api/python.json` → per-cycle `latest`,
  `latestReleaseDate`, `eol`, `pep`. (Verified working from the jarvis host, 2026-09-03.)
- **Vendor primary sources** — e.g. python.org: the downloads page
  (`/downloads/source/`), the per-release page, and the FTP listing
  (`/ftp/python/<ver>/`, whose dates stand in for the `Last-Modified` header no tool reads)
  are the authoritative trio. The docs changelog page can lag one release behind —
  cross-check before citing (2026-09-03: changelog listed 3.12.13; downloads + FTP showed
  3.12.14).
- **Model weight sizes (Hugging Face)** — for "how big is model X" the search
  layer is useless (returns generic junk); read the HF API directly: `web_extract` of
  `https://huggingface.co/api/models/<owner>/<repo>/tree/main?recursive=true`
  returns per-file sizes in bytes; sum them for the real download size. The
  `safetensors` param-count field in the model API is often 0 — don't trust it.
  (Verified 2026-09-03: ResembleAI/chatterbox-turbo ≈ 4.0 GB, coqui/XTTS-v2
  ≈ 2.1 GB, hexgrad/Kokoro-82M ≈ 0.36 GB. Some repos are gated and return
  `{"error": ...}` on the tree endpoint — note that and fall back to the README.)

State the source and date in the answer; when sources disagree, say so explicitly
rather than picking silently.

## Pitfalls

- Retrying `web_search` after empty *or* junk results — the backend, not the query,
  is the problem.
- Reaching for the terminal when a tool answers empty: `curl`, `wget` and Python's `urllib`
  all fail in the sandbox, and the failure looks like the site being down when it is not.
- Citing the docs changelog page for "latest version" — it can lag one release
  behind; verify against vendor downloads/release pages (see §4).
- Treating an empty extract as "site has no feed" instead of a per-host timeout.
- Presenting fallback-sourced results as if they came from search — state provenance:
  "search engines were down; this is from Cointelegraph's feed + CoinGecko."
- Assuming one feed per site: many sites expose multiple RSS endpoints; try several.

## Verification

- Every item in the answer has a timestamp/date and a named source.
- Provenance stated honestly (live feed vs API vs archive, and that the search layer was degraded).
