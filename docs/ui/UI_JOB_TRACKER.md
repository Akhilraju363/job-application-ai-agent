# Job Tracker (`/job-tracker`)

## Purpose
The same tracker data as Applications, as a status board — a workflow view.

## Layout
- PageHeader “Job Tracker”, subtitle “A board view of the same tracker data — change a status and the Google Sheet updates.”, action Refresh.
- Client-side search (role / company) above the board.
- Five columns (Not Applied, Applied, Interviewing, Offer, Rejected), each headed by a status badge + count.
  Cards: role (bold), company, fit badge, resume link, status select (the card moves on success).

## Rules
- Google Sheets stays the backend (`scripts/tracker_service.py`); the board only uses `GET /api/tracker` and `PATCH /api/tracker/status`.
- No drag-and-drop (the select is accessible and already a safe update); no extra columns or statuses.
- The board scrolls horizontally inside itself on narrow screens; columns keep a 220px minimum.
