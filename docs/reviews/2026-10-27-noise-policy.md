# Noise filtering and offline real-snapshot review — 2026-10-27

Checkpoint before this work: `16962bb` (`feat: connect compatible FOFA and verify bounded live hunting`), on `codex/hunter-integration`.

## Result

The real Stan Ghouls trial snapshot was replayed with engine 1 and evaluated with engine 2 in an isolated temporary database. External `requests.get` and `requests.post` were patched to fail, so a hidden FOFA/DeepSeek call would fail verification. No new paid requests were made.

| Measure | Engine 1 historical replay | Engine 2 current-policy evaluation |
| --- | ---: | ---: |
| Recorded seed responses consumed | 2 | 2 |
| Recorded expansion responses consumed | 4 | 0 |
| Candidates | 23 | 0 |
| Rules skipped before execution | 0 | 42 |
| Broad responses rejected after execution | 0 | 0 |
| Queries without a recorded response | 0 | 0 |
| New external API calls | 0 | 0 |

The current catalog contains only weak parking/shared-hosting/common-service features, so expansion stops before any query. The 42 skips are generated singles/pairs, not 42 requests saved from the original six-request run. The four original paid expansion requests would be avoided with these same seed responses. Ordinary replay of the new evaluation reproduced its policy audit and candidates exactly. The original task and all 23 historical candidate reviews remained unchanged; original candidates and rule rankings matched the real snapshot exactly.

Source task: `7a789aaa979642eb818f29ba23af7263`. Local snapshot: `/private/tmp/z-hound-live/live-reviewed-snapshot.json`; SHA-256 `f286a29d91033c79ca2465b094de231383305559d4274d5afeaa432f299bc45f`. Full raw snapshot and account metadata are not included in Git. Local verification artifacts: `evaluate_policy.py`, `noise-policy-summary.json`, and `noise-policy-evaluation.json` in the same directory. These temporary artifacts may be removed by OS cleanup; the task/export remains in the local database.

## Behavior and limits

- Engine 1 replay preserves legacy ordering and assessment semantics. New tasks use engine 2.
- Weak features are classified before the 24-feature cap. Parking/default pages, common headers/services and known/contextually shared JARM cannot launch weak-only queries. Strong titles/certificates on shared infrastructure can still qualify. Server plus its identical header is not independent corroboration. All raw asset observations remain available.
- At total >= 10,000, an expansion retains its raw response and rule audit but does not promote candidates. Two consecutive broad expansions stop exploration; narrow results and unknown offline gaps reset the streak. Seed lookups do not count. Useful later narrow rules may be skipped: this is an explicit cost/recall tradeoff.
- Current-policy evaluation uses recorded responses only, resets old LLM priorities and candidate reviews, and marks absent queries untested. It does not assert absence of related assets.
- V2 assessment retrieval times follow the response supplying the promoted observation, not an earlier rejected broad response. Source observation time remains unknown.
- The known-common JARM label includes the fingerprint observed on 2,697,840 services in this trial. This is a deliberately targeted regression fix, not an independently validated general classifier. This single snapshot provides no precision/recall or benign/malicious verdict.
- The real snapshot is stopped by the pre-query gate, so broad-response stopping is verified separately with deterministic mocked provider responses.

## Verification and independent review

Final full suite: **369 passed**, including Chromium UI workflow tests; one existing upstream Starlette/httpx deprecation warning. Commands:

```sh
PLAYWRIGHT_BROWSERS_PATH=/private/tmp/z-hound-browsers .venv/bin/pytest -q
node --check ioc_scraper/dashboard/static/hunting.js
node --check ioc_scraper/dashboard/static/i18n.js
git diff --check
```

Regression coverage includes distinctive-feature retention, custom numeric titles, weak-only gating, broad-result raw evidence preservation, two-rule stopping, narrow-result reset, missing seed/expansion responses, unknown-gap reset, source immutability, original/current replay, cancelled evaluation boundaries, duplicate cached seed lookups and promoted-observation timestamp provenance. Browser tests exercise the new evaluation button, audit rendering and preservation of the source task.

Independent review found six replay/evidence edge cases across two passes. All were fixed and regression-tested. The final reviewer independently ran ten relevant tests (10 passed) and found no remaining actionable issues. README and the hunting guide document thresholds, tradeoffs and versioned replay.
