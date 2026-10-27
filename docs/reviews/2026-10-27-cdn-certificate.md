# CDN error pages and structured certificate evidence — 2026-10-27

New tasks use engine 3. Engine 1/2 snapshots retain their original extraction, rules and assessment rows. No active target probing was performed.

## Behavior

- Cloudflare's observed `Attention Required! | Cloudflare` page, vendor-context challenge/error titles and AkamaiGHost `Invalid URL` / `Access Denied` templates are weak evidence. Error-only fingerprints and certificate names cannot independently generate expansion queries. Raw evidence is retained.
- A feature also observed on a dedicated non-error service remains eligible. Assessment rows from the error service remain weak even when the same feature is useful elsewhere. Akamai shared-service JARM and server names are weak.
- Structured extraction separates subject CN/organization, issuer CN/organization, serial and validity strings. Only CN is queryable. Generic names such as `CloudFlare Origin Certificate` are weak; issuer/organization/serial/validity remain contextual evidence, not independent pivots.
- Extraction uses the normalized provider subject fields and its rendered certificate text, bounded to 16 KiB. Conflicting values become unknown; identical repetitions may agree. Unknown formats remain unknown. This is not PEM/DER validation, SAN extraction, certificate fingerprint computation or a current validity determination.

## Recorded-response evaluation

Three original real snapshots were replayed in temporary databases with `requests.get` and `requests.post` blocked. Features, rules, policy and candidates matched their original engine-2 exports, including the reviewed SpiceRAT assessment and decisions. Original file hashes remained unchanged. Each replay was then evaluated separately under engine 3; old LLM rankings and analyst decisions were not reused.

| Snapshot | Recorded queries consumed: v2 → v3 | Candidates: v2 → v3 | Weak/duplicate rules skipped in v3 | New/unrecorded queries (untested) |
| --- | --- | --- | --- | --- |
| Brazetsu / Cloudflare error page | 4 → 1 | 0 → 0 | 18 | 0 |
| Tortoiseshell / Akamai error page + separate TLS services | 3 → 1 | 0 → 0 | 56 | 9 |
| SpiceRAT reviewed case | 4 → 4 | 16 → 16 | 14 | 3 |

All offline evaluations made **0 FOFA and 0 DeepSeek requests**. Query counts above describe recorded responses used, not new API expenditure. Missing responses are untested, not zero-result evidence. No precision/recall improvement is claimed from these three cases.

The Tortoiseshell seed retains CN `zbn0QSeL2z8rYauj5A32emAB` on port 2222 and `ablesealanddesign.org` on a separate non-error service; the Akamai error service's `www.icloud.com` CN is weak. These are leads, not evidence of maliciousness.

The SpiceRAT fixture extracts matching CN `azure.uzrailwaystax.com`, issuer CN `TLC DV TLS CA`, and serial `81628176171941507003526847276457465393` from the recorded seed-IP HTTPS service and `45.153.125.20`. The original seed query was port 80 without a certificate, so this field comparison is a fixture check, **not a claim that the original task pivoted on a certificate**.

## Small live compatibility check

One successful search on the configured FOFA-compatible service, capped at one row:

```text
cert.subject.cn="zbn0QSeL2z8rYauj5A32emAB"
```

Returned total 1 / one row; the subject CN matched and the service was the already known seed. This validates query syntax and transport, not discovery of a new asset. A prior sandbox attempt failed before a usable response; the one network-enabled retry succeeded. There was no pagination, Host/stats request or DeepSeek call. The request ledger and response are local ignored files in `data/runs/live-case-20261027/cn-query-verification*.json`. The provider's billing balance was not rechecked.

FOFA documents the structured certificate response fields in its [API reference](https://fofa.info/api). The live probe validates this installation's compatible service; other providers may have different matching behavior.

## Reproduce

A minimal public-response fixture is tracked at `tests/fixtures/hunting/cdn-certificate-real.json` with source hashes. Full original responses remain local ignored artifacts. Given those snapshots:

```bash
.venv/bin/python tools/verify_hunting_snapshots.py \
  data/runs/live-case-20261027/brazetsu-snapshot.json \
  data/runs/live-case-20261027/tortoiseshell-snapshot.json \
  data/runs/live-case-20261027/spicerat-reviewed-snapshot.json
```

The verifier writes only a temporary database and prints source hashes and aggregate results. Regression tests also cover exact vendor context, custom-title preservation, mixed evidence, subject/issuer boundaries, flat/text conflicts, invalid values, late conflicting lines, placeholder CNs, missing-vs-match semantics, and engine-3 export/replay with lazy assessment.

## Final checks

- Full suite: **378 passed**; one existing Starlette/httpx deprecation warning.
- Independent code review: no remaining blockers. Review findings about late conflicting certificate lines and unsupported nested sections were reproduced, fixed, and regression-tested; empty recognized fields remain invalid evidence.
- Final real-snapshot verifier: all three legacy exports equal, all source hashes unchanged, zero offline API calls; counts match the table above.
- `git diff --check` passed. Credentials, full local responses, and the unrelated `.gitignore` edit are excluded from the commit.
