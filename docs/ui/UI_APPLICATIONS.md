# Applications (`/applications`)

## Purpose
A clean list of everything in the tracker (the Google Sheet) and where each application stands.

## Layout
- PageHeader “Applications”, subtitle “The agent never submits an application — you apply, then update the status here.”, action Refresh.
- Toolbar: status tabs (All · Not Applied · Applied · Interviewing · Offer · Rejected — URL `?status=`) and a
  client-side search (role / company).
- Table: Role (bold) · Company · Fit (score badge) · Status (inline select) · Resume (Drive link or “—”) · Logged
  (relative date = application/log date) · Source · open-posting icon.

## Statuses
Exactly: Not Applied, Applied, Interviewing, Offer, Rejected. No others.

## Data
`GET /api/tracker?status=`; status change `PATCH /api/tracker/status {link, status}` (safe update + toast).
Tracker offline → error state with Retry; stale cache → warning notice; not configured → “No tracker sheet yet…”.

## Empty state
“No applications yet — Save a job from Find Jobs, or generate a tailored resume and save it to the tracker.” with a
Find Jobs action. Filtered empty: “No {status} applications”.
