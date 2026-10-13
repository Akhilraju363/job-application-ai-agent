# Find Jobs (`/find-jobs`)

## Purpose
Review scraped + scored jobs and act on them: view, tailor, track.

## Layout
- PageHeader “Find Jobs”; actions **Find New Jobs** (primary, `runPipelineFlow('find')`) and **Run full pipeline** (outline).
- Card toolbar: filter tabs (All · Qualified (8+) · Below cutoff · Tailored — server `filter`); search (title / company /
  location — server `q`, 300ms debounce); **Sort** (Newest · Best match · Company A–Z — client-side on the loaded list);
  **Rows per page**; result count.
- `jobsTable`: Job Title (bold, opens the detail drawer) · Company · Match · Location · Posted · Source · Status (inline) ·
  Action (View, Tailor, ⋯ menu).
- **Posted** (and the drawer's “Posted …”) is the job's `posted_date` as `MM/DD/YYYY` (`formatPostedDate` in
  `lib/format.js`): the written calendar date, no timezone shift; epoch timestamps read in UTC. Missing, invalid or
  relative values show `—` (drawer: “unknown”) -- never `found_at` (the scrape day) or today. Display only; the
  Newest sort keeps the API order.

## Pagination
Client-side, same controls and rules as Applications (`UI_APPLICATIONS.md` → Pagination): `pager` / `pageSizeSelect`
from `web/js/components/pagination.js`, `paginate()` / `pageList()` from `web/js/lib/trackerView.js`.
- Order: filter tab + search (server, one `GET /api/jobs`) → sort → page; only that page is rendered. Sort, page and
  rows-per-page changes re-slice the loaded jobs (no request).
- Count: “Showing 11–20 of 93 jobs” with more than one page, else “93 jobs”. The API returns at most 200 jobs; when more
  match, the count adds “(first 200 of N loaded)” -- narrow with a filter or search.
- Rows per page 10 (default) · 20 · 50 · 100, remembered in `localStorage` `jobagent.pageSize.findJobs` (separate from
  Applications). The page number is not remembered.
- Back to page 1 on: filter tab, search, sort, rows-per-page change, Find New Jobs / Run full pipeline finishing. A row's
  status change reloads and keeps the page, clamped to the last page that exists. No jobs → the empty state, no pager.
- The dashboard's Latest Job Matches uses the same `jobsTable` but is not paginated (it shows its 6 latest).

## Fields that do not exist
The scraper has no **work mode** field. Location is shown as scraped; the UI does not infer Remote/Hybrid.

## Data
`GET /api/jobs?filter&q&limit=200` (the server also accepts `offset` for future server pagination). Detail:
`GET /api/jobs/:key`. Status changes: `POST /api/jobs/:key/save` (when untracked) then `PATCH /api/tracker/status`.

## States
Empty: “No jobs found yet — Use Find New Jobs…” / “No jobs match this filter — Try another filter or search.” Error: Retry.

## Responsive
The table scrolls horizontally inside its card below ~900px; the toolbar wraps (tabs, then search + sort).
