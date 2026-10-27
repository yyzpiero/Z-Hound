# Changelog

## 0.3.0 (2026-10-27)

- Import existing high-value IoCs into an independent seed library; record confirmation, value and source evidence before a live hunt.
- Discover related infrastructure through FOFA JARM, certificate CN, title and service queries, with bounded budgets and weak-evidence gates.
- Compare seed/candidate evidence, retain analyst decisions and replay recorded responses offline.
- Use documented official FOFA certificate fields and key-only authentication; DeepSeek prioritization is optional.
- Add a real certificate-CN case, Docker-first setup and a clean-container smoke workflow.

Build the Docker image from the v0.3.0 source tag and configure your own official FOFA API key; DeepSeek is optional. A public registry image is not required.
