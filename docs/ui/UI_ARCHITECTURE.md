# UI Architecture

## Stack (keep it)

- **No framework, no build step.** Plain ES modules served as static files by
  `scripts/dashboard_server.py` from `web/`. Adding React/Vue/Tailwind/a bundler is out of scope.
- **Rendering:** `web/js/dom.js` — `h(tag, props, ...children)`, `svg()`, `mount(host, ...children)`.
  Text always goes through `createTextNode`/`textContent`; there is **no `innerHTML` path** (job
  descriptions and resume text are untrusted). Keep it that way.
- **Styling:** one stylesheet, `web/css/app.css`, ordered tokens → base → shell → primitives →
  page sections. Theme = `data-theme` on `<html>` (`web/js/theme.js`, pre-paint `theme-init.js`).
- **Constraints enforced by tests:** relative `/api/...` URLs only; no absolute URLs, external fonts or
  scripts (`tests/test_frontend_same_origin.py`); no token/credential storage (`tests/test_dashboard_auth.py`);
  strict CSP (`script-src 'self'`, no inline scripts or handlers).

## Layers

```
index.html ── theme-init.js (pre-paint theme)
          └── js/app.js            AppShell: sidebar + topbar + router outlet, session bootstrap, NAV table
                ├── router.js      History-API router, data-link interception, leave guards
                ├── api.js         fetch wrapper: same-origin JSON, ApiError{status, code, details}, pollTask, download
                ├── ui.js          primitives: pageHeader, loadable, states, badge/statusBadge, toast, dialogs, drawer, menu, withBusy
                ├── components/    reusable widgets (jobsTable, metricCard, charts, resumePreview, stageList, …)
                ├── lib/           pure logic, no DOM (format, logs, resumeMarkdown, masterResume, tailorValidation, authClient)
                └── pages/         one module per route; composes ui + components; owns page state
```

Rules
- **Pages** own their state and loading; they never import another page.
- **Components** take data + callbacks; they may call `api` for their own actions (e.g. `jobsTable` status changes)
  and navigate only via `router.navigate`.
- **lib/** is pure and unit-tested with `node --test`.
- Shared behaviour goes into `ui.js` (primitive) or `components/` (composite) — never copy-pasted markup.

## Routing

`NAV` in `web/js/app.js` is the single route table (`path, label, icon, page, group`). The server serves
`index.html` for every extension-less path, so every route is reloadable. Pages with unsaved state register
`setLeaveGuard(fn)` (currently the Master Resume editor).

## Data flow

- Every page section loads independently with `loadable(host, {load, render, isEmpty, empty})`, so one failing
  backend (e.g. the Google Sheet) never blanks the page.
- Long-running work (tailoring, exports, pipeline runs) = server task → `pollTask(taskId)` → `stageList`.
- Writes are **safe, not optimistic**: disable the control, show progress, apply the server's answer, restore on failure.

## Backend contract (never changed from the UI side)

All endpoints live in `scripts/dashboard_server.py`:
`/api/auth*`, `/api/me`, `/api/dashboard/{summary,overview,status,jobs,activity,sources}`, `/api/jobs[/:key[/save|/tailor]]`,
`/api/tailor`, `/api/tasks/:id`, `/api/resumes[/:id[/regenerate|/export|/download|/save-to-tracker]]`,
`/api/tracker[/status]`, `/api/actions/find-jobs`, `/api/preferences`, `/api/master-resume`, `/api/settings`, `/api/logs`.
A UI change that needs a new field is a backend change with its own review and Python tests.
