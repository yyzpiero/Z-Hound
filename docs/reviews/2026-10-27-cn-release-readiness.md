# CN case and release preparation checks — 2026-10-27

## Completed

- Engine-3 live CN task `3d2a6f2f074a460597088da44c571185`: 4 FOFA-compatible searches, cap 10, 29 returned rows, no retries/pagination/DeepSeek. Nine non-seed services on seven IPs, all sharing the seed's complete rendered certificate text. Six IPs independently cross-checked against the primary research; one remains uncertain. See [case and actual snapshot](../cases/certificate-cn-2026-10-27.md).
- The public snapshot replays with network calls forbidden. Candidates, rules, features, audit and analyst decisions agree; structured comparison rows are recomputed from recorded responses. Full suite **381 passed**, one existing upstream Starlette/httpx deprecation warning.
- Fixed official FOFA request fields to `cert.subject.cn` / `cert.subject.org`, retaining existing normalized snapshot names. Email is optional; supplied legacy email is forwarded. Regression tests exercise the official origin, key-only configuration, normalized results and redirect prevention. References: [official API](https://fofa.info/api), [official Go client](https://github.com/FofaInfo/GoFOFA/blob/main/client.go). No official FOFA account was used in this session; the live case used the explicitly configured compatible service.
- DeepSeek's default model and official endpoint agree with the [current official first-call documentation](https://api-docs.deepseek.com/). No new DeepSeek credit was spent.
- README starts with Docker and the user's own official API configuration, then real seed import, confirmation/value, live hunt and review. The offline sample is opt-in; clean state contains no seeds/tasks. Humanizer editing removed demo-led onboarding and update narration from the user-facing instructions.
- Added `tools/smoke_container.py` and a read-only GitHub Actions workflow for image build, clean startup, seed import/qualification and persistence after restart. The shared application-level check passes against an empty local data directory without provider calls. YAML parsing, Python/JavaScript syntax and `git diff --check` pass.
- Built the **0.3.0 wheel** from a clean temporary source copy, verified inclusion of hunting/certificate code, assessment JavaScript and seed template; no `.env` or hunting database in the wheel. Docker build context additionally excludes local hunting database/WAL files and environment variants.
- Independent code review found no functional blockers. Its only remaining table-escaping comment was fixed.

## Publication gates

This machine has no Docker CLI/daemon. The image build, actual container smoke check and GitHub workflow execution remain **unverified**. Run:

```bash
docker compose --env-file .env.example config --quiet
docker build --tag z-hound:local .
python3 tools/smoke_container.py --image z-hound:local
```

A green container check and integration of this development branch into the public release branch are required before announcing/publishing v0.3.0. No tag, registry image, push or GitHub release was created. A source archive is an inspection/install artifact, not evidence that Docker passed. Native fresh-state tests and mocked official transport tests do not substitute for a real official-account integration test.
