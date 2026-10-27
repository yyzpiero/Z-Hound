# Z/HOUND dashboard visual contract

The local dashboard is a control room, not an admin template. Compare live screens against:

- `z-hound-dashboard-dark-en.png`
- `z-hound-dashboard-light-zh.png`
- `z-hound-dashboard-mobile-light-zh.png`

`dashboard.png` is the original design figure. The root README now uses actual application screenshots in `docs/images/`: `dashboard.png` uses the dashboard test fixtures, and `hunting.png` shows the built-in synthetic demo, and `seeds.png` shows independent seed qualification with reserved synthetic indicators. None represents a live intelligence finding.

## Required language

Blue system palette, square geometry, hard rules, compact mono accents, numbered destinations, and visible operational state. No decorative gradients, glass panels, chat bubbles, or remote fonts.

## Intentional deviations from the figures

- Metric values, source counts, and run IDs come from local CSV artifacts rather than the 46-source mock numbers.
- Attention titles and event lines are generated from live source status and structured events, not the sample Mandiant/Proofpoint copy unless those rows exist in the catalog.
- The event stream shows typed collector events (`RUN_STARTED`, `MASTER` writes as `run_completed`) instead of a stylized terminal script.
- Desktop navigation is a left rail; below 760 px it becomes a horizontal tab bar, matching the mobile figure.
- Typefaces are system sans/mono stacks. The figures use a similar grotesque, but this MVP never loads a CDN font.

## Browser checks

`tests/test_dashboard_browser.py` exercises hash navigation, theme/language persistence, keyboard reachability of chrome actions, and body-level overflow at 360 px. Playwright Chromium must be installed for those checks.
