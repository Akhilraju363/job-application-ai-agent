# UI Pages

| Route | Page module | Nav group | Spec | The question the page answers |
|---|---|---|---|---|
| `/` | `pages/dashboard.js` | Workspace | `UI_DASHBOARD.md` | What is happening with my job search? |
| `/find-jobs` | `pages/findJobs.js` | Workspace | `UI_JOBS.md` | Which new jobs fit me, and what do I do with them? |
| `/tailor-resume` | `pages/tailor.js` | Workspace | `UI_TAILOR_RESUME.md` | Give me a truthful resume for this JD |
| `/master-resume` | `pages/masterResume.js` | Workspace | `UI_MASTER_RESUME.md` | What are my career facts? (source of truth) |
| `/applications` | `pages/applications.js` `applicationsPage` | Tracking | `UI_APPLICATIONS.md` | Where do my applications stand? |
| `/job-tracker` | `pages/applications.js` `trackerBoardPage` | Tracking | `UI_JOB_TRACKER.md` | Board view of the same tracker |
| `/job-alerts` | `pages/alerts.js` | Tracking | `UI_JOB_ALERTS.md` | What does Find New Jobs search for? |
| `/settings` | `pages/settings.js` | System | `UI_SETTINGS.md` | How is this app configured? |
| `/logs` | `pages/logs.js` | System | `UI_LOGS.md` | What did the system do (technical)? |

Signed out: the login view (`components/loginForm.js`) replaces the shell.

## Common page anatomy
1. `pageHeader` — title, one-sentence purpose, actions (≤1 primary).
2. Optional filter bar (`.toolbar`: tabs left, search/sort right).
3. Content cards; each is an independent `loadable` with its own skeleton / empty / error.

## Honesty rules (every page)
- Show only data the backend returns — no placeholder numbers, sample rows or fake badges.
- Features that don't exist are not shown as disabled teasers (no notifications bell, no work-mode filter).
- Automation vs the human is explicit: the agent prepares; the user applies.
