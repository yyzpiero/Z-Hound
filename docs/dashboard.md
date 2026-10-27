# Z-Hound local dashboard

The dashboard is Z-Hound's local analyst workspace. Control, Sources, Runs and Review expose **Z-Hunter**, the report/IoC collection module; Hunting provides asset discovery and evidence assessment. Collection reuses the same discovery, fetch, pipeline and repository objects as `ioc-scraper collect`. Collector data remains in CSV; hunting tasks and feedback use their own SQLite store.

These are separate workflows: existing analyst-confirmed, high-value IoCs should be able to enter a hunt without a preceding collection run. The independent seed library supports direct import, explicit confirmation and value marking, optional Z-Hunter transfer, and revocation; see the [seed workflow](hunting.md#independent-high-value-seed-workflow).

## Install

Native:

```bash
pip install -e '.[dashboard]'
python -m playwright install chromium
ioc-scraper dashboard --host 127.0.0.1 --port 8080
```

The core extra stays lean. FastAPI and Uvicorn are installed only with the `dashboard` extra. If those packages are missing, the CLI prints the install hint and exits 2.

Docker:

```bash
UID=$(id -u) GID=$(id -g) docker compose up --build
```

- Container listen address: `0.0.0.0:8080`
- Host publish address: `127.0.0.1:8080:8080`
- Data: `./data:/app/data` with `IOC_SCRAPER_DATA_DIR=/app/data`
- Optional proxy: `IOC_SCRAPER_PROXY` (not written to logs)
- Health check: `GET /api/health`
- Stop: `docker compose down` (bind-mounted `./data` is kept)

The same image also ships the CLI. Interactive menu:

```bash
UID=$(id -u) GID=$(id -g) docker compose run --rm cli
```

One-shot collection:

```bash
UID=$(id -u) GID=$(id -g) docker compose run --rm cli collect --lookback 3m
```

The `cli` service is a Compose profile, so `docker compose up` still starts only the dashboard. The CLI does not perform human review.

The image must not contain the private source catalog. `.dockerignore` excludes `data/source.csv` and run artifacts. Provide the catalog through the host `data/` directory.

If file ownership on the mount is wrong, rerun Compose with the host `UID` and `GID`. The default image user is `1000:1000`.

## Security assumptions

There is no login, role, or remote multi-user mode in this MVP. Treat the process as equivalent to local filesystem access to `data/`. Bind to loopback. Do not put this service on a LAN port, reverse proxy, or cloud load balancer.

Review accept/reject writes an append-only `data/review_decisions.csv` under the collector writer lock. Generated `data/runs/<run_id>/review.csv` files stay evidence and are never edited in place. A process restart may lose an in-memory job; existing run artifacts remain.

The collector still does not solve CAPTCHA, reuse a browser profile, or bypass access controls.

## Views

| View | Purpose |
|------|---------|
| Control | Latest run, metrics, execution lanes, collection alerts, source matrix, event stream |
| Sources | Catalog rows joined to latest status; optional validate action |
| Runs | Newest-first summaries and artifact counts |
| Review | Pending and decided candidates; accept merges into `indicators.csv` |
| Hunting | Independent seed import/qualification, FOFA/JARM-based discovery, evidence comparison and offline replay; see [discovery guide](hunting.md) |

English/Chinese labels and dark/light themes are complete dictionaries and CSS variables. The first visit may follow `prefers-color-scheme`; afterwards the choice is stored in `localStorage`.

Current UI screenshots with test data: [Control](images/dashboard.png), [Seed library](images/seeds.png) and [Hunting](images/hunting.png). Original design references and intentional deviations are in [the visual contract](design/README.md).

## API

JSON under `/api`. Counts are integers. Dates are ISO 8601 UTC. Unknown API routes return JSON. The UI is served from packaged static files with no CDN.

Only one collection job runs in the process. A second `POST /api/runs` returns 409. `POST /api/runs/current/stop` requests a cooperative stop after the current source or report; `POST /api/sources/validate` also returns 409 while a job is running or stopping.
