# UI Components

Where each component lives, what it is for, its states, and its accessibility contract. Names in the
first column are the conceptual names used in design discussions.

| Component | Module / export | Purpose |
|---|---|---|
| AppShell, Sidebar, Topbar | `app.js` `buildShell` | Frame of every signed-in page |
| PageHeader | `ui.js` `pageHeader({title, subtitle, actions, meta})` | The page `h1`, one-line purpose, actions |
| MetricCard | `components/metricCard.js` | One KPI with trend vs the previous period |
| StatusBadge | `ui.js` `statusBadge(status)` | Tracker status as dot + text |
| ScoreBadge | `components/jobs.js` `matchBadge(pct)`, `ui.js` `scoreBadge(score)` | Match % / fit score with tone |
| JobTable (DataTable) | `components/jobs.js` `jobsTable(jobs, onChange, {scroll})` | Jobs list with inline status + actions |
| Job detail drawer | `components/jobs.js` `openJob(job)` | Full job, matched/missing requirements, JD |
| FilterBar / SearchInput | `.toolbar` + `.tabs`, `ui.js` `searchInput({label, placeholder, onSearch})` | Filtering above a table |
| ResumePreview | `components/resumePreview.js` | Tailored resume + verification + export actions |
| JD analysis / ATS | `components/jdAnalysis.js`, `components/atsValidation.js` | Match ring, coverage, ATS checks |
| Workflow stepper | `components/progress.js` `workflowSteps(steps, current)` | Tailor Resume flow position |
| Stage list (AI processing) | `components/progress.js` `stageList(stages)` | Real server task stages |
| ResumeSection / SkillGroupEditor / ExperienceEditor / BulletEditor | `pages/masterResume.js` (`card`, `skillsCard`, `roleCard`, bullet rows) | Structured master editing |
| EmptyState / LoadingState / ErrorState | `ui.js` `emptyState`, `skeleton`, `errorState`, `loadable` | Section lifecycle |
| ConfirmDialog / Modal / Drawer | `ui.js` `confirmDialog`, `openDialog`, `openDrawer` | Overlays |
| Toast | `ui.js` `toast(message, tone, ms)` | Transient feedback |
| Menu (dropdown) | `ui.js` `menu(label, items)` | Row “more actions”, account menu |

## Contracts

### PageHeader
- States: default; with a meta row (badges); with actions.
- Exactly one per page, first in `<main>`; renders the page's only `h1`. At most one `.btn-primary` action.

### StatusBadge / ScoreBadge
- Tracker statuses only: Not Applied, Applied, Interviewing, Offer, Rejected. Pipeline states (Qualified, Below
  cutoff, Tailored, Unscored) use `badge(text, statusTone(text))`.
- Always text + colour (+ dot). Score badges always print the number.

### MetricCard
- States: value; zero with an empty hint; unavailable (`metric === null`: “—”, “Tracker unavailable”, Retry).
- The trend pill has a `title` with the previous value; ▲/▼ are paired with text.

### JobTable
- Columns: Job Title (button → drawer), Company, Match, Location, Posted, Source, Status (inline select), Action.
- Status select: native `<select>` labelled “Application status for {title}”; safe update (disabled + “Saving…”, restore on failure).
- Always inside `.table-wrap` — horizontal scroll inside the card, never page overflow.

### Loading / Empty / Error
- `loadable` owns the lifecycle: skeleton → render | empty | error + Retry; stale responses are dropped.
- Empty: title + one sentence + at most one real action. Error: plain message + Retry; details go to the logs.

### Dialogs, drawers, menus
- `role="dialog"`, `aria-modal="true"`, `aria-label`; focus moves into the panel and returns to the opener; Escape and backdrop close.
- `confirmDialog` resolves `true/false`; destructive confirmations say exactly what will happen.
- Menus: trigger has `aria-haspopup="menu"` + `aria-label`; items are `role="menuitem"` buttons/links.

### Toast
- Host is a `role="status"` live region; success 4.5s, error 9s. Never the only place a form error is reported.

### Master Resume editors
- One card per section (`section[data-section]`); `data-edited="true"` + “Edited” badge when changed.
- Every field is a labelled input with its own `.field-errors` list and `aria-invalid`.
- Row controls (move up/down, remove) are `.btn-icon` with explicit labels (“Move bullet down”, “Remove bullet 3”).

## Usage rules
1. Build with `h()`; never `innerHTML`.
2. Reuse before creating; document any new component here.
3. Icon-only buttons need `aria-label` and `title`.
4. Only tokens from `UI_DESIGN_SYSTEM.md`; no inline colours.
5. Keep test hooks stable: `data-section`, `data-field`, `data-kind`, `data-meta`, `data-action`, `data-choice`,
   `status-select`, `status-note`, `status-hint`, `toast-*`, `paper-master`.
