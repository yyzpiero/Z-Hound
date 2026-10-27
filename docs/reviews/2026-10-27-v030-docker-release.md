# v0.3.0 Docker and release validation — 2026-10-27

## Evidence

[PR #1](https://github.com/yyzpiero/Z-Hound/pull/1) contains the clean public release branch, based on the existing public `main` rather than private local development history.

[Docker CI run 40475190517](https://github.com/yyzpiero/Z-Hound/actions/runs/40475190517) passed on Linux amd64. The log records:

```json
{
  "version": "0.3.0",
  "empty_initial_state": true,
  "seed_import_and_review": true,
  "external_api_calls": 0,
  "restart_port_reassigned": true,
  "restart_preserves_review": true,
  "recreated_container_preserves_review": true
}
```

Compose validation and image build also passed. The check uses a disposable host directory, imports a reserved documentation IP, records confirmation/value and review history, restarts the container, then removes/recreates it against the same mount. It never uses FOFA or DeepSeek credentials. ARM64 image execution was not tested by this runner.

## Fixes from real CI

1. Initial startup could reset the TCP connection before HTTP was ready. Readiness now retries reset/disconnect errors until a bounded deadline; it does not treat them as a successful response.
2. A container restart reallocated its ephemeral published host port. The checker now reads the mapping again after restart; the successful run explicitly records that reassignment occurred.
3. Bash reserves `UID`, so deployment examples now use `env UID="$(id -u)" GID="$(id -g)" docker compose ...` consistently.

Regression coverage reproduces both network readiness failures and the changed-port lifecycle. The final local suite passed **384 tests**, with one existing upstream Starlette/httpx deprecation warning. Independent review found no remaining blockers. The stable README points to the v0.3.0 tag and users' own official API configuration.

## Publication scope

The public branch includes production code, Docker/CI files, user documentation, public examples and evidence snapshots. It excludes the private development history, actual API keys, local hunting database, private source catalog and local-only tests. The public starter catalog equals `data/source.example.csv`. No registry image is required: build from the release source tag. Real asset-provider validation and its limits remain documented in the [CN case](../cases/certificate-cn-2026-10-27.md).
