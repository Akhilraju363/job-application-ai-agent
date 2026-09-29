# Logs (`/logs`)

## Purpose
Technical/admin view of the rotating application logs (`GET /api/logs`, `scripts/log_reader.py`). Read-only.

## Layout
- PageHeader “Logs”, subtitle “Application logs from the dashboard and pipeline. Secrets are never recorded.”;
  actions: auto-refresh select, Refresh.
- Filter card: level tabs (All · Debug · Info · Warning · Error · Critical), component select, date, Request ID,
  Task ID, Resume ID, Job ID.
- Results card: table (Time · Level badge · Component · Message · ID chips); rows are keyboard-focusable and open the detail drawer.
- Pager: “Showing X–Y” + Newer / Older.
- Detail drawer: timestamp, component, message, every ID, extra fields, exception/traceback in a monospace block.

## Rules
- Redaction is the backend's job; the UI shows only what the API returns.
- Readable, not terminal-like: proportional font for messages; monospace only for IDs and tracebacks.
- Error: “Unable to load application logs.” + Retry (no raw exception text).
