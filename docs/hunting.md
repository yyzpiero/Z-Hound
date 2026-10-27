# Z-Hound: asset discovery and evidence assessment

Z-Hound has two distinct workflows. Asset discovery and evidence assessment start from analyst-confirmed, high-value network IoCs. The integrated **Z-Hunter** module collects reports and extracts IoCs; it is an optional source of future seeds, not a required business step before every hunt. Z-Hunter is the user-facing name of the existing collector; its CLI remains `ioc-scraper`.

The current discovery engine adapts the seed → asset features → query combinations workflow from the local IoC-hunter project. That standalone project is unchanged. The implementation lives in `ioc_scraper/hunting/` and is not a subprocess wrapper around its CLI.

## Independent high-value seed workflow

The independent seed library works with an empty collector directory and requires no API keys. A seed is a network indicator with its own provenance and analyst decisions, stored in `hunting.sqlite3`, not in collector CSVs.

1. In **Hunting → Independent seed library**, paste one IP, domain or HTTP(S) URL per line, or import TXT, CSV or JSON. Use the [CSV template](../ioc_scraper/dashboard/static/seed-template.csv). Each import supports at most 500 rows and 2 MiB; each metadata field is limited to 16 KiB. Use ASCII/punycode DNS names. Invalid batches are rejected atomically.
2. Provide source/case reference and original evidence. These can be completed at review time. Imports always start **pending / unrated**; imported confirmation flags are rejected. Canonical duplicates preserve the existing record, evidence and review history, and are reported as duplicates.
3. Choose **Review / update**, record analyst label and a reason for this decision, and select **confirmed / high value**. Source and evidence are required to confirm. Labels are self-reported local analyst identifiers, not authenticated identities.
4. Only eligible records appear in the new-hunt selector. The backend verifies confirmation, high value and required provenance in the same database transaction that snapshots the seeds and creates the task. A prior completed revocation cannot be bypassed by an old browser selection.
5. To revoke, change status to pending/rejected or reduce value. This prevents future hunts; an already-created task retains its original input snapshot and can finish. Stop a running task separately if required. The append-only history retains decisions, times, reasons and previous/new source evidence. Concurrent stale reviews return 409; the UI preserves the draft and offers explicit reload.

Optionally expand **Transfer from Z-Hunter**, load collected indicators and select records to copy. Collector `accepted` may mean automatic structured-report acceptance, so transferred records still start pending/unrated. No import, review or transfer writes to collector CSVs. Existing collector rows are never silently migrated or confirmed.

Seed JSON is an array of records, or `{"items": [...]}`. Example record:

```json
{"content":"192.0.2.10","type":"ipv4","port":"443","source":"Synthetic case","evidence":"Replace with your original evidence"}
```

CSV requires `content`; optional fields are `type,port,source,evidence,report_url,report_title,threat_actor,indicator_role,published_at`. Type can be inferred. Do not include passwords, tokens or credential-bearing URLs. Seed import and **Import replay** are separate operations: replay takes an existing task snapshot and never qualifies a live seed.

## Evidence assessment

Candidate **Seed–candidate evidence** tables compare JARM, certificate, title, server, protocol, port and selected server headers against assets actually returned by the corresponding seed queries. Each row links back to its seed evidence and identifies the seed service. Match, difference and missing are separate states; a missing observation never counts as a match. Generic feature matches are labeled. Differences require interpretation and are not automatic disproof.

Candidates are paginated at 25 per page, and comparison tables render only when opened. Detail/export previews contain at most 2,000 comparison rows across the entire task and 200 per candidate. Candidates beyond the preview budget are marked `deferred`; opening one loads up to 200 comparisons directly from recorded local evidence without a provider call. Counts describe displayed rows, with an explicit truncation notice when a candidate has more than 200 comparisons. All original query results and asset snapshots remain in exports, so deferred previews can be reconstructed after import. The table records the candidate query retrieval time separately from the source's asset observation time. FOFA observation timestamps are not provided by this adapter, so source time stays **unknown**, including legacy query retrieval times that were never recorded. No freshness or maliciousness probability is inferred.

Comparisons are derived from recorded seed queries and candidate asset snapshots on detail/export. Replay reconstructs them from the recorded responses; imported comparison summaries are not accepted as authoritative evidence. Candidate relevance, actor attribution and notes remain separately reviewed with append-only history.

JARM is read from FOFA asset responses and may be used in generated queries. The application does not actively probe assets to calculate JARM. Shared fingerprint or service features support investigation; the interface records the analyst's judgment separately from query ranking and actor attribution.

## Try it without API keys

Install the dashboard/test extras and run:

```sh
pip install -e '.[dashboard,test]'
ioc-scraper dashboard --host 127.0.0.1 --port 8080
```

Open http://127.0.0.1:8080/#/hunting and choose **RUN OFFLINE DEMO** (Chinese: **运行离线演示**). A fresh directory without `source.csv` also works for this demonstration. The collector still needs its source catalog for real collection.

The synthetic example has a known seed and one related-looking service on a reserved documentation IP. It proves the workflow, not maliciousness or real-world detection effectiveness. Inspect the observed features, generated rules, query trace and candidate evidence. Choose related/unrelated/uncertain, optionally enter an independent actor attribution and evidence note, and save. Export the JSON snapshot, replay it, or import it into another local instance. These actions never modify accepted IoCs.

Use a separate data directory if you want an isolated demonstration:

```sh
IOC_SCRAPER_DATA_DIR=/tmp/z-hound-demo ioc-scraper dashboard --host 127.0.0.1 --port 8080
```

## Configure providers later

Copy `.env.example` to `.env` and edit locally. Keys are server-side only; there is no credential form in the browser.

| Variable | Purpose |
| --- | --- |
| `FOFA_BASE_URL` | HTTPS origin only; default `https://fofa.info`. Set explicitly for a trusted FOFA-compatible service. No URL credentials, path, query or fragment. |
| `FOFA_EMAIL` | Optional legacy account email; the current official API supports key-only access |
| `FOFA_API_KEY` | FOFA API key; usable account credit/permissions still required |
| `DEEPSEEK_API_KEY` | Optional DeepSeek API key |
| `DEEPSEEK_MODEL` | Account-supported model; default `deepseek-flash` |

Docker Compose passes these variables from `.env`. Native invocation does not automatically load `.env`; export the variables in the shell before starting the dashboard, or use a shell-compatible `.env`:

```sh
set -a
. ./.env
set +a
ioc-scraper dashboard --host 127.0.0.1 --port 8080
```

Restart the server after changing configuration. The UI displays presence of configuration, not validity or account balance. Starting FOFA is an explicit action and may consume the account's allowance. Demo/import/replay never calls FOFA or DeepSeek, even when keys are present.

Official FOFA and explicitly configured compatible services receive the configured key at their `/api/v1/search/all` endpoint. Certificate subject fields use `cert.subject.org` / `cert.subject.cn` on the wire and retain the application's normalized names internally. Redirects are disabled for authenticated FOFA and DeepSeek requests. Select only a service you intend to share the key and queries with. There is no automatic provider retry or pagination.

For a small first run, use one seed, `query_budget=4`, and `max_results=10`: at most four searches and 40 returned rows before deduplication. Billing depends on the selected service. DeepSeek runs at most once per task when requested and observed features exist; thinking is disabled and output is capped at 2,000 tokens. Opening evidence comparisons or replaying a snapshot does not repeat paid requests.

DeepSeek uses the [official chat-completions endpoint](https://api-docs.deepseek.com/api/create-chat-completion/) and [JSON output mode](https://api-docs.deepseek.com/guides/json_mode). It receives bounded report evidence, asset context and an observed-feature catalog. Suggestions must reference existing feature IDs and their supplied evidence. It cannot supply executable queries or invented feature values. Missing credentials, malformed JSON, invalid evidence or provider failure trigger a visible deterministic fallback. This is contextual feature prioritization, not unrestricted semantic extraction or trained reinforcement learning.

## Run a real hunt

1. Import existing IoCs into the independent seed library and record confirmation, high value and provenance as described above. Z-Hunter collection is optional.
2. In Hunting, filter the eligible seeds by report, actor, role or content. Select at most 20 from a coherent activity and relevant time window. The backend independently enforces qualification when the task is created.
3. Give the hunt a name and choose request/result limits. The query budget must leave room beyond distinct seed lookups for expansion. A request failure still consumes a request attempt.
   Port metadata is preserved. Imported URLs normalize the scheme/host and effective HTTP/HTTPS port, retain path and query in the stored seed, and discard fragments. The infrastructure query uses only authority and effective port. If the original spelling or fragment is relevant, retain it explicitly in the evidence text. Conflicting explicit ports are rejected.
4. Optionally enable DeepSeek. This sends the selected report/asset evidence to that provider.
5. Start the hunt. The backend queries seed services, extracts bounded observed features, and explores supported combinations and individual features. Inspect actual retrieved seed coverage and novel service identities separately from provider total estimates.
6. Assess candidates against the seed evidence, observed JARM/service features, matching queries and asset snapshots. Record related/unrelated/uncertain, the rationale and any independent attribution. Marking a candidate related does not assign it to the seed's actor, promote it into the master CSV, or make it a new seed automatically.

The rank is a heuristic search-utility score. It is not a maliciousness probability or verified attribution. Generic/shared infrastructure can match. FOFA snapshots may be stale or truncated; returned counts and total estimates are deliberately separate. Limited live interoperability has been verified; false-positive rate and useful new-IoC yield still require evaluation with a labelled dataset.

## Query noise and cost control

New tasks use engine 3. Parking/default-page titles, common server/framework headers, ports/protocols, known common JARM and JARM observed only on parking/shared-hosting services are weak features. Weak-only queries and duplicate server/header evidence are skipped. A distinctive title or certificate subject CN on shared hosting remains eligible unless observed only on recognized CDN error pages. Classification happens before selecting the 24-feature catalog, so weak fingerprints do not crowd out distinctive evidence. DeepSeek prioritization cannot bypass these gates, and an entirely weak catalog does not trigger a DeepSeek request.

An expansion with provider total **at least 10,000** retains its rule and raw response but contributes no candidates. **Two consecutive broad expansions** stop the task; a narrower response resets the streak, and seed lookups do not count. These fixed defaults reduce spending but may leave useful narrower rules unexecuted. The UI explicitly shows this partial-analysis limitation and the skipped-rule audit. Filters are investigation heuristics, not benign/malicious verdicts or a complete catalogue of shared infrastructure.

Engine 3 recognizes Cloudflare's `Attention Required! | Cloudflare` template and vendor-context challenge/error titles, plus Akamai `Invalid URL` / `Access Denied` pages when the service or Server header identifies AkamaiGHost. These observations remain in raw evidence; features observed only on such pages are weak. A feature also seen on a dedicated non-error service can remain eligible. Akamai shared-service JARM is weak even without an error title. These bounded patterns do not detect every possible CDN error page.

Certificate evidence is extracted from normalized provider subject fields and the bounded (16 KiB) FOFA rendered text: subject CN/organization, issuer CN/organization, serial and validity strings. Subject and issuer sections are kept separate; conflicting/invalid values become unknown. Identical repeated values may agree. Unsupported certificate formats remain unknown; this is not PEM/DER verification, SAN extraction or certificate fingerprint computation. Full raw records remain available.

Only subject CN is a query feature (`cert.subject.cn="observed value"`), subject to weak-evidence gates. Placeholder names such as `CloudFlare Origin Certificate` are weak. Issuer/organization/serial/validity are context-only rows, never standalone pivots; equal values do not establish common ownership. FOFA search matching is provider-defined and may be broader than the evidence table's exact comparison. Unknown values never count as matches. Legacy replay retains the old raw certificate row. See [real snapshot validation](reviews/2026-10-27-cdn-certificate.md).

**Replay** preserves engine 1, 2 or 3 semantics. **Evaluate current policy offline** creates a separate engine-3 task using recorded responses only; it never calls FOFA/DeepSeek, reuses neither old LLM priorities nor old candidate decisions, and leaves the original task/reviews unchanged. Queries absent from the snapshot are **untested**, not zero-result searches. Unknown gaps reset the broad-response streak. Cancelled evaluations replay only their previously recorded responses and gaps. V2/V3 comparison timestamps refer to the response that supplied the promoted candidate observation, including when an earlier broad response contained the same service.

See the [real-snapshot comparison and review record](reviews/2026-10-27-noise-policy.md).

## Storage, replay and lifecycle

`data/hunting.sqlite3` contains independent seeds, append-only seed reviews, task payloads, seed/asset evidence, bounded query results, provider outcomes, LLM choices and append-only candidate feedback. The data-directory ignore rule excludes the database and its journals from Git. Normal collector CSVs and original report caches retain their existing semantics.

Snapshot JSON has `version` and `engine_version`, seeds, task limits, context, query strings with exact recorded responses/errors, observed features, rules, candidate evidence, LLM selections, and review history. There are no credentials. Exported snapshots still contain intelligence and analyst notes; choose their destination accordingly.

Replay uses recorded query results, not a live search and not a general simulator. It does not ask the LLM to generate new choices. A required query absent from a completed snapshot is a visible replay failure; an intentionally stopped/interrupted snapshot replays its recorded boundary. Unknown versions and invalid records are rejected. Import uses the same seed/query/asset-field limits as capture; there is no separate total-file-size or audit-count limit that would reject a valid export. Large JSON snapshots and long review histories consume local memory in both browser and server; use smaller query/result budgets when resources are limited. Imported feedback remains historical evidence, not a fresh analyst confirmation. Neither current collector CSVs nor current seed eligibility are required to replay a snapshot. Version-1 snapshots without qualification remain replayable offline, but cannot authorize a live hunt.

There is one active hunt per process, with an additional database guard. Collection can run concurrently because hunting writes only its own database and collector transfer reads CSVs. Stop is cooperative: a current provider request finishes or times out before cancellation takes effect. Unfinished jobs owned by a dead or replaced process become interrupted at startup. Ownership includes process birth identity, so Docker PID reuse does not preserve stale locks. Process inspection uses Linux/macOS identity data and a non-destructive Windows API path; Windows API behavior is covered by mocked tests, not a native Windows run in this development session. Provider failures never masquerade as successful empty queries. Evidence collected before cancellation/failure remains inspectable.

## Verification

```sh
python -m pytest -q --ignore=tests/test_dashboard_browser.py --ignore=tests/test_hunting_browser.py
python -m playwright install chromium
python -m pytest tests/test_dashboard_browser.py tests/test_hunting_browser.py -q
```

Browser tests use temporary data, local loopback HTTP, Chromium, and synthetic results. They check demo → candidate feedback → export → replay → import, navigation, Chinese labels and narrow-screen overflow. Backend tests exercise provider transports with fakes, no-key handling, strict evidence validation, request limits, cancellation, restart behavior, immutable feedback and replay corruption. They do not prove live API compatibility.

Future RL work needs validated feedback and a controlled benchmark. This release records the observations/actions/outcomes needed to build that benchmark; it contains no learned RL policy.

## Development validation on 2026-10-26

The final local suite passed **295 tests**, including real Chromium workflow tests, with one upstream Starlette/httpx deprecation warning. Python compilation, JavaScript syntax checks, and wheel packaging passed. Two independent review passes identified and verified fixes for lifecycle, replay and seed-scope failures. FOFA/DeepSeek transport tests used fakes; no paid/live API requests were made. Native Windows process inspection is mocked, and the Docker image was not built/run in this session.

## Development validation on 2026-10-27

The completed independent-seed and evidence-assessment update passed **347 tests**, including real Chromium import, qualification, revocation, stale-review handling, mocked FOFA discovery, evidence comparison, offline replay, pagination and out-of-order response tests. The one warning is the existing upstream Starlette/httpx deprecation. Python/JavaScript syntax and wheel asset checks passed. Backend and final broad code reviews approved after fixes to malformed CSV headers, snapshot provenance validation and aggregate comparison allocation.

The README includes real API task screenshots; the standalone offline example uses synthetic data. The initial development checks simulated FOFA/DeepSeek. A subsequent limited live run on 2026-10-27 verified one compatible FOFA service and the official DeepSeek endpoint; see [the live-run report](reviews/2026-10-27-live-provider-trial.md). This is an interoperability result, not a measured precision/recall benchmark.
