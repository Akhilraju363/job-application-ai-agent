# Applications (`/applications`)

## Purpose
A clean list of everything in the tracker (the Google Sheet) and where each application stands.

## Layout
- PageHeader “Applications”, subtitle “The agent never submits an application — you apply, then update the status here.”,
  actions Open Google Sheet (outline, external-link icon, new tab) + Refresh.
- Toolbar: status tabs (All · Not Applied · Applied · Interviewing · Offer · Rejected — URL `?status=`), a
  client-side search (role / company), a “Sort by” select (`sort-select`) and a “Rows per page” select
  (`page-size-select`). The controls wrap on narrow screens.
- Result count above the table (`role="status"`): “Showing 1–10 of 88 applications”, or “7 applications” on a single page.
- Table: Role (bold) · Company · Fit (score badge) · Status (inline select) · Resume (Drive link or “—”) · Date
  (the application/log date, Sheet column G `timestamp`, as `MM/DD/YYYY` — e.g. 2026-01-05 → `01/05/2026`; never relative;
  “—” when missing or invalid) · Source · open-posting icon. `formatDateMMDDYYYY()` in `trackerView.js` parses the
  value (ISO dates as UTC calendar dates, so no timezone shift; impossible dates like 2026-02-30 are invalid) and is
  display-only — the date sorts compare `rowTime()`.
- Pager below the table (`nav.pager`, only when there is more than one page): Previous · page numbers · Next.

## Statuses
Exactly: Not Applied, Applied, Interviewing, Offer, Rejected. No others.

## Data
`GET /api/tracker?status=`; status change `PATCH /api/tracker/status {link, status}` (safe update + toast).
Tracker offline → error state with Retry; stale cache → warning notice; not configured → “No tracker sheet yet…”.

## Sort
Sort (client-side, `web/js/lib/trackerView.js`, over the rows already loaded — no refetch, never a status write):
Newest first (default) · Oldest first · Best match (fit score, high → low) · Company A–Z · Role A–Z.
Dates are the Date column's value (`timestamp`, Sheet column G) parsed as dates; rows without a valid date go last in both
date orders; equal dates fall back to the Sheet row (later row = logged later); missing scores/companies/roles go last.
The choice is remembered in `localStorage` (`jobagent.sort.applications`, the option name only); a missing, invalid or blocked value means Newest first. Status tab + search + sort combine.

## Pagination
Client-side: `GET /api/tracker` has no `offset`/`limit` and already returns every row (status-filtered), so paging only
re-slices the loaded rows (`paginate()` / `pageList()` in `web/js/lib/trackerView.js`) — no extra Sheet read, never a status write.
- Order: status tab (server) → search → sort → count → `rows.slice((page-1)*size, page*size)`; only that page is rendered.
  The count is of the filtered/searched rows.
- Rows per page: 10 (default) · 20 · 50 · 100, remembered in `localStorage` `jobagent.pageSize.applications` (the number
  only); missing, invalid or blocked storage means 10. The page number is not remembered — a reload starts on page 1.
- Page buttons: at most 7 entries — first, last, current ±1, “…” for gaps. Native `<button>`s; the current page is
  `btn-primary` + `aria-current="page"`; Previous/Next are `disabled` at the ends. On phones the numbers sit on their own row.
- Back to page 1 on: search, sort, rows-per-page change, status tab (a navigation), Refresh. A status change keeps the
  page; any re-render clamps the page to the last one that exists, so an empty page is never shown.
- After a page button re-renders the table, focus returns to that button (or to the current page when it became disabled).
- No matches → the existing empty state, no pager.

## Google Sheet link
- The link is `tracker.sheet_url` from `GET /api/tracker`, built server-side (`tracker_service.Tracker.sheet_url`) from the
  sheet id the tracker already uses (`google_sheet_id`, else the id found by name) -- no extra `gws` call, no credentials.
  The UI opens it in a new tab only if it is an https `docs.google.com/spreadsheets/d/…` URL (`sheetHref`); otherwise it
  shows “Google Sheets link is not configured.” Nothing is shown while loading or when no tracker sheet exists yet.

## Empty state
“No applications yet — Save a job from Find Jobs, or generate a tailored resume and save it to the tracker.” with a
Find Jobs action. Filtered empty: “No {status} applications”.
