# Z-Hound

A deterministic, CSV-first collector for public threat-intelligence reports. The Python package and CLI remain `ioc-scraper`. It uses ordinary HTTP discovery first and falls back to Playwright for JavaScript-rendered sites. The same source code targets macOS and Linux and does not require a database, LLM, API key, bundled browser driver, or local proxy.

## Install

```bash
git clone https://github.com/yyzpiero/Z-Hound.git
cd Z-Hound
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dashboard]'
python -m playwright install chromium
```

The collector CLI is the core install. The local dashboard is an optional extra.

## Runtime

- Python 3.11 or newer
- Runtime packages declared in `pyproject.toml`
- Chromium installed through Playwright only when browser fallback is needed

This repository does not install or create an environment automatically. After creating your own environment, install the project and, if browser fallback is required, install Playwright Chromium using the command documented by your installed Playwright version.

## Collection

```bash
python -m ioc_scraper list-sources
python -m ioc_scraper list-sources --strategy wechat
python -m ioc_scraper collect --lookback 3m
python -m ioc_scraper collect --lookback 6m
python -m ioc_scraper collect --lookback 12m
python -m ioc_scraper collect --since 2026-01-01
python -m ioc_scraper collect --source 12 24 --lookback 3m
python -m ioc_scraper collect --strategy wechat --lookback 3m
python -m ioc_scraper collect --lookback 3m --dry-run
python -m ioc_scraper collect --lookback 3m -v
python -m ioc_scraper collect --lookback 3m -vv
python -m ioc_scraper collect --lookback 3m --quiet
```

`--lookback` uses calendar months. Dated reports older than the cutoff are skipped. Undated reports are included up to each source's page and report limits. `--refresh` re-fetches already-known report URLs. `--source` selects numeric ids from `data/source.csv`. `--strategy` selects by adapter, such as `wechat`. Combined flags keep the intersection. `list-sources` prints the current selection without fetching.

An optional proxy can be supplied through `IOC_SCRAPER_PROXY`; there is no default proxy.

WeChat album listings are public over ordinary HTTP. Individual `mp.weixin.qq.com/s` articles often refuse a non-browser client and return a verification interstitial. Collection therefore uses a desktop Chromium User-Agent for HTTP and, if that page is still blocked, retries the same public article URL with Playwright. It does not solve the CAPTCHA, reuse your Chrome profile, or send login cookies.

## Source validation

Validate discovery before a collection run, especially after changing a source URL:

```bash
python -u -m ioc_scraper validate-sources --source 1 2 3 --max-pages 2 --max-reports 20
python -u -m ioc_scraper validate-sources --strategy wechat --max-pages 2 --max-reports 20 --output data/wechat_validation.csv
python -u -m ioc_scraper collect --strategy wechat --lookback 1m
```

`validate-sources` reports the canonical and final URL, detected source type,
HTTP/browser method, status, article count, newest recoverable publication
time, publisher identity, and a concise reason. It does not extract IoCs, run
legacy migration, create caches or run snapshots, or modify the source registry
and master tables. It writes a separate CSV only when `--output` is supplied.

The first-party WeChat adapter accepts public `mp.weixin.qq.com` album and
article URLs. It reads ordinary links, exposed public page state, and exposed
pagination, then applies each source's page/report limits. It does not use
Sogou, repost indexes, login cookies, personal WeChat sessions, undocumented
authenticated endpoints, CAPTCHA solving, fingerprint spoofing, or proxy
rotation. Chinese titles, publisher identities, and threat-actor labels remain
Unicode and are not transliterated.

## Output

- `data/indicators.csv`: normalized IoCs and provenance
- `data/reports.csv`: report discovery, cache, and collection state
- `data/source_status.csv`: method, final URL, counts, and status for every attempted source
- `data/failures.csv`: source-discovery and retryable report failures
- `data/report_cache/`: source HTML/JSON stored under stable report IDs
- `data/runs/<timestamp>_<scope>/`: immutable per-run accepted, new, review,
  report, source-status, failure, summary, and log snapshots

`data/indicators.csv` is the cumulative accepted master table. `first_run_id`
and `last_run_id` show when a row first entered the master and when it was most
recently observed. Only indicators from the trailing appended IoC list (a final `C2` / IoC heading
and the tables or list items under it) enter the master. If the article has a
`参考链接` / `阅读原文` / lab-intro footer, the appendix stops there; otherwise it
runs to the end of the article. Mid-article tables, victim-site lists, and other
prose matches stay in the run's `review.csv`. Missing report titles and
publication times are filled from fetched HTML metadata when available.

Default output prints one compact progress line per source. `-v` adds report
fetch results, `-vv` adds accepted/review extraction counts, and `--quiet`
suppresses progress while retaining the final summary. Every non-dry run keeps
the full detail in `collector.log`. Non-success lines include the method, error
category, and compact reason.

Source statuses mean:

- `success`: at least one in-scope report was discovered.
- `partial`: reports were discovered, but a page or report fetch also failed.
- `no_recent_reports`: parsing succeeded, but all dated reports were older than the cutoff.
- `empty`: a supported page structure contained no articles.
- `layout_changed`: public content returned, but its article structure was not recognized.
- `blocked`: the source returned verification or access-control content.
- `unsupported`: an optional capability, normally browser rendering, is unavailable.
- `failed`: another request or parsing failure prevented discovery.

For domains and IP addresses, `content` excludes the port and `port` contains an explicit valid port from 1 through 65535. `first_seen_at` and `last_seen_at` are populated only from explicit activity statements. They are never inferred from `published_at` or `collected_at`.

Attribution is local and rule-based. `threat_actor` stores a verified canonical name while `threat_actor_source_name` preserves the exact Chinese or English source label. Add verified aliases to `ioc_scraper/data/actor_aliases.csv`; do not add uncertain mappings. Each row stores one primary `indicator_role` using the priority `c2`, `phishing`, `malware_distribution`, `payload`, `exfiltration`, then `unknown`.

## Coverage and blocking

The hybrid engine attempts RSS/Atom, sitemap, public WordPress JSON, ordinary HTML, WeChat, targeted source adapters, and optional Playwright rendering. For a WeChat source, Playwright renders the same public first-party URL and sends the result back through the WeChat parser; it is a compatibility fallback, not anti-bot evasion. Website layouts and access policies change, so successful access to every catalog entry cannot be guaranteed. The collector does not solve CAPTCHAs, spoof fingerprints, rotate residential proxies, or bypass access controls. A protected source is recorded as `blocked` rather than terminating the run.

If Playwright is installed but its Chromium binary is absent, browser fallback
is marked `unsupported` once with the operator command
`python -m playwright install chromium`; the collector never runs that command.
Requests dependency warnings come from the selected Python environment rather
than the collector. Use `python -m pip check` in that environment to inspect
them when you choose; collection does not modify the environment.

CSV persistence supports one writer process. Atomic replacement protects existing files, and a run lock rejects simultaneous writers.

## Starter sources

`data/source.csv` ships with a small English-language set of public vendor blogs so a fresh clone can run immediately. Edit or replace that file for your own catalog. Run `validate-sources` with a small page/report cap first; then use one source and `--dry-run` for a collection smoke test when you choose.

## Local dashboard

The dashboard is a single-operator control room for the same CSV pipeline. It does not replace the CLI. Both call the same Python services; the dashboard never launches the collector as a subprocess.

```bash
pip install -e '.[dashboard]'
ioc-scraper dashboard --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080`. There is no authentication. Bind only to loopback. Do not publish the port on a shared or public interface.

Views:

- Control: run health, metrics, execution lanes, alerts, source field, event stream
- Sources: catalog status and optional validation
- Runs: newest-first history and artifact counts
- Review: accept or reject review candidates; decisions append to `data/review_decisions.csv` and never edit a run's `review.csv`

Theme (dark/light) and language (English/Chinese) persist in `localStorage`. Starting a second collection while one is active returns HTTP 409.

Docker Compose mounts `./data` from the host so your catalog and outputs stay outside the image:

```bash
UID=$(id -u) GID=$(id -g) docker compose up --build
```

Compose publishes `127.0.0.1:8080:8080`. The image does not copy `data/source.csv`. Pass `IOC_SCRAPER_PROXY` through the environment if a proxy is required; it is not logged. If mounted files are owned by the host user, set `UID`/`GID` as above.
