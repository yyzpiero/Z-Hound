# 使用与部署参考

[返回项目首页](../README.md) · [Dashboard](dashboard.md) · [拓线配置与回放](hunting.md)

本文保留 Z-Hunter 采集模块的 CLI、数据文件、采集边界，以及 Z-Hound 的 Docker 离线交付说明。Z-Hunter 是现有报告采集模块的统一名称，命令名仍为 `ioc-scraper`。已有高价值 IoC 的拓线与证据研判是另一条工作流，见[拓线指南](hunting.md)。首次使用请从项目首页的快速开始进入。

## What you get after a run

All durable state is under `data/` on the host (or the Compose mount of the same directory).


| File                             | Role                                                                                                                        |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `data/indicators.csv`            | Cumulative **accepted** IoCs with provenance (`first_run_id`, `last_run_id`, threat actor, role). This is the master table. |
| `data/reports.csv`               | Every discovered report: URL, cache, collection state.                                                                      |
| `data/source_status.csv`         | Per-source method, final URL, counts, and status for the latest attempt.                                                    |
| `data/failures.csv`              | Source-discovery and retryable report failures.                                                                             |
| `data/review_decisions.csv`      | Append-only accept/reject log from the dashboard Review view.                                                               |
| `data/report_cache/`             | Fetched HTML/JSON keyed by stable report IDs.                                                                               |
| `data/runs/<timestamp>_<scope>/` | Immutable snapshot of that run: accepted, new, review, reports, source status, failures, `summary.json`, log.               |


**What enters the master vs the review queue**

- **Accepted** (`indicators.csv`): only the trailing appendix after a final `C2` / IoC heading (tables and list items under it). A `参考链接` / `阅读原文` / lab-intro footer stops the appendix.
- **Review queue** (`runs/.../review.csv`): mid-article tables, victim-site lists, and other prose matches. Open **Review** in the dashboard, filter by status or type, open the original report, then accept or reject. Accept merges into `indicators.csv`. A run’s `review.csv` is never edited in place.

Only one writer process may mutate these files at a time. A run lock rejects a second collector.

---

## Install (native)

Python **3.11+**. Create your own virtualenv; this repo does not create one for you.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e '.[dashboard]'   # collector + FastAPI dashboard
python -m playwright install chromium
```

Collector-only (no dashboard packages):

```bash
pip install -e .
python -m playwright install chromium
```

**Source catalog.** `data/source.csv` is gitignored (private catalogs stay local). A fresh clone needs a copy of the public starter:

```bash
cp data/source.example.csv data/source.csv
```

Edit that file for your own vendors. `list-sources` and collection both read `data/source.csv`.

Optional proxy: set `IOC_SCRAPER_PROXY`. There is no default proxy. The value is not written to logs.

---

## Use the CLI

In a TTY, run with **no arguments** for a numbered menu:

```text
1) Collect IoCs
2) Validate sources
3) List sources
4) Start dashboard
0) Quit
```

The menu does **not** accept or reject IoCs. Scripts and CI should keep using explicit subcommands.

```bash
# See which sources would run (no fetch)
ioc-scraper list-sources
ioc-scraper list-sources --strategy wechat

# Collect (calendar months; dated reports older than the cutoff are skipped)
ioc-scraper collect --lookback 3m
ioc-scraper collect --lookback 6m
ioc-scraper collect --since 2026-01-01
ioc-scraper collect --source 18 24 --lookback 3m
ioc-scraper collect --strategy wechat --lookback 3m
ioc-scraper collect --lookback 3m --dry-run
ioc-scraper collect --lookback 3m -v      # or -vv / --quiet

# Probe discovery before a full collect (does not extract IoCs or write masters)
ioc-scraper validate-sources --source 18 19 --max-pages 2 --max-reports 20
```

`--source` is numeric ids from `data/source.csv`. `--strategy` selects by adapter (`wechat`, …). Combined flags keep the intersection. `--refresh` re-fetches already-known report URLs.

After collection, open the dashboard to triage the review queue (see below).

---

## Open the dashboard

```bash
ioc-scraper dashboard --host 127.0.0.1 --port 8080
```

Then open **[http://127.0.0.1:8080](http://127.0.0.1:8080)**.

There is **no authentication**. Bind only to loopback. Do not publish the port on a LAN, reverse proxy, or cloud load balancer.


| View        | What you do there                                                                           |
| ----------- | ------------------------------------------------------------------------------------------- |
| **Control** | Start / stop a collection, metrics, execution lanes, attention, source field, event stream. |
| **Sources** | Catalog + latest status. Validate catalog (disabled while a run is active).                 |
| **Runs**    | Newest-first history and artifact counts.                                                   |
| **Review**  | Accept or reject candidates; decisions append to `data/review_decisions.csv`.               |


Theme (dark/light) and language (English/Chinese) persist in `localStorage`. A second collection while one is running returns HTTP 409. Starting the dashboard without the extra installed prints `pip install -e '.[dashboard]'` and exits 2.

Operator notes and API: [`docs/dashboard.md`](dashboard.md).

---

## Docker

The image includes the CLI and the dashboard. Chromium is in the image. **`data/source.csv` is not baked in** — Compose mounts `./data`. Copy the starter catalog on the host first (`cp data/source.example.csv data/source.csv`) if that file is missing.

**Dashboard** (default `docker compose up`):

```bash
UID=$(id -u) GID=$(id -g) docker compose up --build
```

Copy `.env.example` to `.env` and set `FOFA_API_KEY` before starting a real hunt. The default FOFA origin is official; `FOFA_EMAIL` is optional. `DEEPSEEK_API_KEY` enables optional feature prioritization. Editing `.env` requires `docker compose up -d --force-recreate dashboard`.

Open **[http://127.0.0.1:8080](http://127.0.0.1:8080)**. Stop with `docker compose down` (`./data` is kept).

**CLI menu** (interactive; `cli` is a Compose profile, so it does not start with `up`):

```bash
UID=$(id -u) GID=$(id -g) docker compose run --rm cli
```

**CLI one-shot:**

```bash
UID=$(id -u) GID=$(id -g) docker compose run --rm cli collect --lookback 3m
UID=$(id -u) GID=$(id -g) docker compose run --rm cli validate-sources --max-pages 2 --max-reports 20
UID=$(id -u) GID=$(id -g) docker compose run --rm cli list-sources
```

Host publish is `127.0.0.1:8080:8080`. If mounted files are owned by you, pass `UID`/`GID` as above (image default is `1000:1000`). Proxy: `IOC_SCRAPER_PROXY` in the environment. The image sets `IOC_SCRAPER_DATA_DIR=/app/data` so collection writes to the mounted directory (a plain `pip install` inside the image does not keep the checkout path).

### Verify a built image

```bash
docker compose --env-file .env.example config --quiet
docker build -t z-hound:local .
python3 tools/smoke_container.py --image z-hound:local
```

The smoke check uses a temporary data directory and no API keys. It checks startup, empty hunting state, seed import/qualification, and preservation of the review after restart; it never queries FOFA or DeepSeek. `.github/workflows/container.yml` runs the same image check on Linux. A green workflow is required before publishing an image. The current development host has no Docker executable; image build/start has not been verified here.

### Build and copy to another machine

For an offline deployment, export a **gzip tarball** (USB, `scp`, shared disk). This repository currently builds images locally; no public registry image is published.

The Dockerfile is **Linux-only** (`python:3.11-slim-bookworm` + Playwright Chromium). Docker Desktop on macOS or Windows still runs that Linux image in a VM. There is no Windows-native container. Chromium is per-architecture: build **one tarball per CPU**, do not load an amd64 image on ARM.

| Image platform | Runs on |
| --- | --- |
| `linux/amd64` | Intel/AMD Linux, Intel Mac, Windows (x86) Docker Desktop |
| `linux/arm64` | Apple Silicon Mac, ARM Linux (Graviton, etc.) |

**This machine only:**

```bash
docker build -t z-hound:0.3.0 .
```

**Tarball for another host** (repeat with `linux/arm64` if you also need Apple Silicon / ARM):

```bash
docker buildx build --platform linux/amd64 -t z-hound:0.3.0 --load .
docker save z-hound:0.3.0 | gzip > z-hound-0.3.0-amd64.tar.gz

docker buildx build --platform linux/arm64 -t z-hound:0.3.0 --load .
docker save z-hound:0.3.0 | gzip > z-hound-0.3.0-arm64.tar.gz
```

If you are already on that arch, `docker build -t z-hound:0.3.0 .` then `docker save` is enough; `--platform` is for cross-building.

On the other host:

```bash
gunzip -c z-hound-0.3.0-amd64.tar.gz | docker load   # or the arm64 file
mkdir -p data
cp source.csv data/source.csv   # their catalog; never bake yours into the image
docker run --rm -p 127.0.0.1:8080:8080 \
  -v "$PWD/data:/app/data" \
  --env-file .env \
  -e IOC_SCRAPER_PROXY \
  --user "$(id -u):$(id -g)" \
  z-hound:0.3.0
```

CLI:

```bash
docker run --rm -it -v "$PWD/data:/app/data" --user "$(id -u):$(id -g)" \
  --entrypoint ioc-scraper z-hound:0.3.0
```

The files are large (Chromium). `docker run --rm --entrypoint ls z-hound:0.3.0 /app/data` must not show a baked `source.csv`.

---

## Coverage and limits

Discovery tries RSS/Atom, sitemap, public WordPress JSON, HTML, WeChat albums, targeted adapters, then Playwright when needed. WeChat albums are public HTTP; individual `mp.weixin.qq.com/s` articles often need Chromium. The collector does not solve CAPTCHAs, reuse your Chrome profile, or send login cookies. A protected source is recorded as `blocked`; the rest of the run continues.

If Playwright is installed but Chromium is missing, browser fallback is `unsupported` once, with the hint `python -m playwright install chromium`. The collector never runs that install itself.

Status values in `source_status.csv`: `success`, `partial`, `no_recent_reports`, `empty`, `layout_changed`, `blocked`, `unsupported`, `failed`.

For domains and IPs, `content` excludes the port; `port` is 1–65535 when present. `first_seen_at` / `last_seen_at` come only from explicit activity statements, never from `published_at`. Attribution is local and rule-based (`ioc_scraper/data/actor_aliases.csv`). Primary `indicator_role` priority: `c2`, `phishing`, `malware_distribution`, `payload`, `exfiltration`, then `unknown`.

---

## Roadmap

These are not implemented yet.

1. **Database for IoCs (collector)** — The asset-discovery workspace has a separate SQLite store; CSV is the Z-Hunter collector source of truth today (`indicators.csv`, run snapshots, append-only decisions). A real store would make query, dedup, and dashboard filters cheaper than rewriting whole files, and would still need a migration path from the current masters.
2. **Smarter audit, less human labour** — Review still depends on an analyst for mid-article / ambiguous extractions. Next work is ranking, clustering, and safer auto-accept rules so the queue shrinks without silently polluting the master table.

