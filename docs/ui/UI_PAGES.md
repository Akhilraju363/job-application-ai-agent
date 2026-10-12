# UI Pages

| Route | Page module | Nav group | Spec | The question the page answers |
|---|---|---|---|---|
| `/` | `pages/dashboard.js` | Workspace | `UI_DASHBOARD.md` | What is happening with my job search? |
| `/find-jobs` | `pages/findJobs.js` | Workspace | `UI_JOBS.md` | Which new jobs fit me, and what do I do with them? |
| `/tailor-resume` | `pages/tailor.js` | Workspace | `UI_TAILOR_RESUME.md` | Give me a truthful resume for this JD |
| `/master-resume` | `pages/masterResume.js` | Workspace | `UI_MASTER_RESUME.md` | What are my career facts? (source of truth) |
| `/applications` | `pages/applications.js` `applicationsPage` | Tracking | `UI_APPLICATIONS.md` | Where do my applications stand? |
| `/job-alerts` | `pages/alerts.js` | Tracking | `UI_JOB_ALERTS.md` | What does Find New Jobs search for? |
| `/settings` | `pages/settings.js` | System | `UI_SETTINGS.md` | How is this app configured? |

Signed out: the login view (`components/loginForm.js`) replaces the shell.

Removed 2026-10-12: **Job Tracker** (`/job-tracker`, a board over the same tracker — Applications covers search, sort,
status changes and the Sheet link; the Dashboard's status chart gives per-status counts and links into Applications)
and **Logs** (`/logs`). Both old URLs redirect (`MOVED` in `app.js`: → `/applications`, → `/settings`). Backend kept:
`/api/tracker[/status]`, `/api/logs`, `log_reader.py`, the log files.

## Common page anatomy
1. `pageHeader` — title, one-sentence purpose, actions (≤1 primary).
2. Optional filter bar (`.toolbar`: tabs left, search/sort right).
3. Content cards; each is an independent `loadable` with its own skeleton / empty / error.

## Honesty rules (every page)
- Show only data the backend returns — no placeholder numbers, sample rows or fake badges.
- Features that don't exist are not shown as disabled teasers (no notifications bell, no work-mode filter).
- Automation vs the human is explicit: the agent prepares; the user applies.
