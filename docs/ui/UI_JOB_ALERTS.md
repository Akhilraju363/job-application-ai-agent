# Job Alerts (`/job-alerts`)

## Purpose
Manage the job-search preferences that drive **Find New Jobs** (`scripts/scrape_jobs.py load_preferences`). The
project has no notification/alert delivery feature; the page says so and does not pretend otherwise.

## Settings that exist (and only these)
| Field | API key | Notes |
|---|---|---|
| Keywords (roles / skills searched) | `keywords` | ≤200 chars; the default is shown as a hint |
| Location | `location` | ≤100 chars |
| Posted within | `date_posted` | Past 24 hours · Past week · Past month |
| Jobs per run | `limit` | 1…`limit_cap` (depends on cloud/local mode) |

Not exposed because they don't exist: a remote/WFO preference, a separate skills list, a score threshold (the 8+
cutoff is a fixed hard rule), an alert on/off state.

## Layout
Two cards: “Search preferences” form (Save preferences primary, Run search now outline) · “How this is used” (plain
facts: used by Find New Jobs and local runs; the Modal cron keeps its own defaults; the 8+ cutoff is fixed; the daily
failure alert is Telegram, configured in the environment).

## Data
`GET` / `PUT /api/preferences`. Errors inline under the form (`role="alert"`) and as a toast.
