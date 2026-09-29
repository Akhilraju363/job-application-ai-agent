# Dashboard (`/`)

## Purpose
Answer “What is happening with my job search?” on one screen, with a path to act.

## Layout (desktop)
1. PageHeader — “Good morning / afternoon / evening, {first name}”, subtitle “Here's where your job search stands.”,
   actions **Find New Jobs** (primary; the existing find flow) and **Tailor Resume** (outline link).
2. KPI row (four MetricCards, range-aware): Jobs Found, Resumes Tailored (resume matches), Applications Sent, Interviews.
3. Middle row: Pipeline overview chart (range select; series Found, Qualified (8+), Tailored, Applications,
   Interviews) · Application status donut (Not Applied, Applied, Interviewing, Offer, Rejected; click → Applications
   filtered) · Quick actions.
4. Bottom row: Latest Job Matches (6 rows, internally scrolling table, “View all” → Find Jobs) · Recent Activity · Configured Sources.

Qualified jobs and Offers come from the overview series and the status counts; the page never computes numbers the
backend doesn't return.

## Data
`/api/dashboard/summary|overview` (with `range`, `from`, `to`), `/status`, `/jobs?limit=6`, `/activity?limit=8`, `/sources`.
Each section is an independent `loadable`; the tracker being offline degrades only the tracker-backed parts
(a metric shows “Tracker unavailable” + Retry).

## States
Empty copy: “No activity in this period” (chart), “No applications yet” (status), “No jobs found yet — Use Find New
Jobs…” (table), “No activity yet” (activity). Errors are section-level with Retry.

## Responsive
≤1280: middle row 2 columns (quick actions spans both). ≤1100: KPIs 2×2. ≤760: single column.
