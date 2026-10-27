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

## Initial publication gates (subsequently satisfied)

At this initial local checkpoint, the machine had no Docker CLI/daemon and container execution was unverified. Subsequent GitHub CI verified the Linux image build/start and persisted reviews across restart and recreation; see the [release validation](2026-10-27-v030-docker-release.md). Run:

```bash
docker compose --env-file .env.example config --quiet
docker build --tag z-hound:local .
python3 tools/smoke_container.py --image z-hound:local
```

This checkpoint required a green container check and public-branch integration before publication. No tag or release existed at that point. Current version availability is recorded by the [v0.3.0 Release](https://github.com/yyzpiero/Z-Hound/releases/tag/v0.3.0). A source archive alone is not evidence that Docker passed. Native fresh-state tests and mocked official transport tests do not substitute for a real official-account integration test.
