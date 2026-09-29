# UI Implementation Guide (for Claude Code)

How to change the dashboard UI without breaking the architecture, the backend contract or the hard rules.

## Before you change anything
1. Read `UI_DESIGN_SYSTEM.md` (tokens, visuals) and the page's spec in this folder.
2. Read the page module and every component it uses; search `web/tests/` for the page's test hooks.
3. Confirm the data you want exists in the API response (`scripts/dashboard_server.py`, `scripts/dashboard_data.py`).
   If it doesn't, stop: that is a backend change with its own tests, not a UI change.

## Hard constraints
- Vanilla ES modules, no framework, no bundler, no new runtime dependency.
- DOM only via `h()` / `mount()` — never `innerHTML`, `insertAdjacentHTML` or `document.write`.
- Relative `/api/...` only; no absolute URLs, CDNs or web fonts; no `sessionStorage`; `localStorage` only for the theme.
- Do not change API contracts, auth, the tailoring / no-fabrication pipeline, the master-resume rules, the 8+ cutoff,
  tracker behaviour, Drive, logging, Modal or cron from a UI task.
- Never invent data or features: no sample rows, fake counts, fake notifications, invented statuses or fields.

## How to build
- New primitive → `web/js/ui.js`; composite widget → `web/js/components/`; pure logic → `web/js/lib/` (+ a `node --test`).
- Colours, spacing, radius, shadow, type size: tokens only (`var(--…)`). Add a token to `app.css` **and** `UI_DESIGN_SYSTEM.md` together.
- CSS lives in `web/css/app.css` in the right section; page-specific rules go under the page's comment header.
- Every async section uses `loadable` (skeleton / empty / error + Retry). Every write is a safe update.
- Keep these test hooks stable: `data-section`, `data-field`, `data-kind`, `data-meta`, `data-action`, `data-choice`,
  `status-select`, `status-note`, `status-hint`, `toast-*`, `paper-master`, nav order in `NAV`.

## Checklist before you finish
- `node --test web/tests/*.test.mjs` passes; `python -m unittest discover -s tests` passes except the known pre-existing
  `test_tracker_outage_degrades_sections_instead_of_failing`.
- Run the dashboard (`python scripts/dashboard_server.py --port <free port>`) and look at every changed page in light and
  dark at 1600, 1024 and 390 wide (Playwright + local Chrome works: see `UI_RESPONSIVE.md`). No horizontal page overflow.
- Keyboard pass: Tab through the changed page; focus is visible; dialogs trap and return focus; Escape closes.
- Update the relevant `docs/ui/*.md` in the same change.

## Status of the redesign
Phase 1 (done): design tokens in `app.css`; shell (grouped light sidebar, collapsed icon rail on tablet, skip link,
topbar breadcrumb, account menu, mobile drawer with Escape + focus return, first-load focus rule, page fade); shared
`pageHeader`, `statusBadge`, `scoreBadge`, `searchInput`, `workflowSteps`; refreshed buttons, inputs, tabs (segmented),
tables, cards, badges, toasts, dialogs, drawers, menus, states; dashboard greeting header + actions (no emoji/quote);
metric cards label-first, 2×2 on phones; Find Jobs sort; Applications and Job Tracker client-side search + empty-state
actions; Settings in six sections; Tailor Resume workflow stepper driven by real task stages; resume previews no
longer add a second `h1`.

Phase 2 (open): server-side pagination on Find Jobs (the API already accepts `offset`); a compact pipeline-status card on
the dashboard once the backend exposes the last run's outcome; Logs component column filters as chips.
