# Find Jobs (`/find-jobs`)

## Purpose
Review scraped + scored jobs and act on them: view, tailor, track.

## Layout
- PageHeader “Find Jobs”; actions **Find New Jobs** (primary, `runPipelineFlow('find')`) and **Run full pipeline** (outline).
- Card toolbar: filter tabs (All · Qualified (8+) · Below cutoff · Tailored — server `filter`); search (title / company /
  location — server `q`, 300ms debounce); **Sort** (Newest · Best match · Company A–Z — client-side on the loaded list);
  result count.
- `jobsTable`: Job Title (bold, opens the detail drawer) · Company · Match · Location · Posted · Source · Status (inline) ·
  Action (View, Tailor, ⋯ menu).

## Fields that do not exist
The scraper has no **work mode** field. Location is shown as scraped; the UI does not infer Remote/Hybrid.

## Data
`GET /api/jobs?filter&q&limit=200` (the server also accepts `offset` for future server pagination). Detail:
`GET /api/jobs/:key`. Status changes: `POST /api/jobs/:key/save` (when untracked) then `PATCH /api/tracker/status`.

## States
Empty: “No jobs found yet — Use Find New Jobs…” / “No jobs match this filter — Try another filter or search.” Error: Retry.

## Responsive
The table scrolls horizontally inside its card below ~900px; the toolbar wraps (tabs, then search + sort).
