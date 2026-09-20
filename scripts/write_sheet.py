"""Log successfully-tailored resumes to the "Job Application Tracker" Google Sheet.

Reads output/tailored_jobs.json (produced by the tailor-resume skill), appends one row
per status=="saved" job that isn't already logged (deduped by job link).
"""
import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH)

import sys  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_links import canonical_link  # noqa: E402
from activity import log_event  # noqa: E402

HEADERS = ["Job Title", "Company", "Job Link", "Fit Score", "Resume Path", "Status", "Timestamp",
           "Company Notes", "Source", "Status Updated", "Resume ID", "Match %"]
# Columns I:L were added for the dashboard (where a row came from, when its status last changed
# via the dashboard, which generated resume + JD match it came from). Older sheets only have
# A:H -- ensure_extra_headers() adds the rest.
EXTRA_HEADERS = HEADERS[8:]
SHEET_TITLE = "Job Application Tracker"
STATUS_OPTIONS = ["Not Applied", "Applied", "Interviewing", "Offer", "Rejected"]


def _resolve_gws():
    # See format_resume_doc.py's _resolve_gws() for why this bypasses the .cmd shim on Windows.
    if os.name != "nt":
        return ["gws"]
    gws_cmd = shutil.which("gws.cmd")
    if gws_cmd:
        run_js = os.path.join(os.path.dirname(gws_cmd), "node_modules", "@googleworkspace", "cli", "run.js")
        if os.path.exists(run_js):
            return ["node", run_js]
    return ["gws"]


GWS_CMD = _resolve_gws()


def gws(*args):
    # See format_resume_doc.py's gws() for why encoding="utf-8" is required on Windows.
    result = subprocess.run([*GWS_CMD, *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"gws {' '.join(args)} failed: {result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def find_sheet_by_title():
    """Look for an existing, non-trashed spreadsheet named SHEET_TITLE in Drive.

    Returns the oldest match's id (deterministic across runs) or None. This is the
    fallback when google_sheet_id isn't configured: without it, the Modal cron --
    whose container .env write from set_key() doesn't persist -- would create a
    brand-new "Job Application Tracker" every run. Configuring google_sheet_id
    (in the Modal secret for production) skips this lookup entirely.
    """
    escaped = SHEET_TITLE.replace("\\", "\\\\").replace("'", "\\'")
    result = gws("drive", "files", "list", "--params", json.dumps({
        "q": (f"name = '{escaped}' and "
              "mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"),
        "orderBy": "createdTime",
        "fields": "files(id,name)",
        "pageSize": 10,
    }))
    files = result.get("files", [])
    return files[0]["id"] if files else None


def _remember_sheet_id(sheet_id):
    # Only when a local .env actually exists -- on Modal there's no .env to write
    # (and the container is ephemeral anyway); google_sheet_id belongs in the secret.
    if ENV_PATH.exists():
        set_key(str(ENV_PATH), "google_sheet_id", sheet_id)


def get_or_create_sheet_id():
    """Lookup priority: configured google_sheet_id -> existing sheet by name in
    Drive -> create a new one (only when no tracker exists at all)."""
    import os
    sheet_id = os.environ.get("google_sheet_id", "").strip()
    if sheet_id:
        return sheet_id

    sheet_id = find_sheet_by_title()
    if sheet_id:
        _remember_sheet_id(sheet_id)
        print(f"Reusing existing sheet: {SHEET_TITLE} ({sheet_id})")
        return sheet_id

    created = gws("sheets", "spreadsheets", "create", "--json",
                   json.dumps({"properties": {"title": SHEET_TITLE}}))
    sheet_id = created["spreadsheetId"]

    gws("sheets", "spreadsheets", "values", "update", "--params",
        json.dumps({"spreadsheetId": sheet_id, "range": "Sheet1!A1:L1", "valueInputOption": "USER_ENTERED"}),
        "--json", json.dumps({"values": [HEADERS]}))

    gws("sheets", "spreadsheets", "batchUpdate", "--params",
        json.dumps({"spreadsheetId": sheet_id}),
        "--json", json.dumps({"requests": [{
            "setDataValidation": {
                "range": {
                    "sheetId": 0, "startRowIndex": 1, "endRowIndex": 1000,
                    "startColumnIndex": 5, "endColumnIndex": 6,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": v} for v in STATUS_OPTIONS],
                    },
                    "showCustomUi": True,
                    "strict": True,
                },
            }
        }]}))

    _remember_sheet_id(sheet_id)
    print(f"Created new sheet: {SHEET_TITLE} ({sheet_id})")
    return sheet_id


def get_existing_links(sheet_id):
    result = gws("sheets", "+read", "--spreadsheet", sheet_id, "--range", "Sheet1!C:C")
    values = result.get("values", [])
    return {row[0] for row in values[1:] if row}


def build_row(job, today, source="LinkedIn"):
    """One tracker row (A:J) for a saved job. Shared by the pipeline and the dashboard's
    Save-to-Tracker so both write identical rows."""
    resume_path = job.get("resume_link") or job.get("desktop_file") or job.get("resume_path", "")
    return [
        job["title"], job["company"], job["link"],
        str(job["score"]), resume_path, "Not Applied", today,
        job.get("company_notes", ""), source, "", job.get("resume_id", ""),
        "" if job.get("match_pct") in (None, "") else str(job["match_pct"]),
    ]


def read_tracker(sheet_id):
    """All tracker rows as dicts, each with its 1-based sheet row number."""
    values = gws("sheets", "+read", "--spreadsheet", sheet_id, "--range", "Sheet1!A:L").get("values", [])
    rows = []
    for i, raw in enumerate(values[1:], start=2):
        cells = [str(c) for c in raw] + [""] * (len(HEADERS) - len(raw))
        if not any(cells[:3]):
            continue
        rows.append({
            "row": i, "title": cells[0], "company": cells[1], "link": cells[2], "score": cells[3],
            "resume_path": cells[4], "status": cells[5] or "Not Applied", "timestamp": cells[6],
            "company_notes": cells[7], "source": cells[8], "status_updated": cells[9],
            "resume_id": cells[10], "match_pct": cells[11],
        })
    return rows


def ensure_extra_headers(sheet_id):
    have = gws("sheets", "+read", "--spreadsheet", sheet_id, "--range", "Sheet1!I1:L1").get("values") or [[]]
    if [str(c) for c in have[0]][:len(EXTRA_HEADERS)] != EXTRA_HEADERS:
        update_range(sheet_id, "Sheet1!I1:L1", [EXTRA_HEADERS])


def update_range(sheet_id, a1_range, values):
    gws("sheets", "spreadsheets", "values", "update", "--params",
        json.dumps({"spreadsheetId": sheet_id, "range": a1_range, "valueInputOption": "USER_ENTERED"}),
        "--json", json.dumps({"values": values}))


def append_rows(sheet_id, rows):
    gws("sheets", "+append", "--spreadsheet", sheet_id, "--json-values", json.dumps(rows))


def set_status(sheet_id, row_number, status, today):
    if status not in STATUS_OPTIONS:
        raise ValueError(f"unknown status {status!r}")
    update_range(sheet_id, f"Sheet1!F{row_number}", [[status]])
    update_range(sheet_id, f"Sheet1!J{row_number}", [[today]])


if __name__ == "__main__":
    import artifacts

    # Recovery: log whatever a failed earlier run tailored. Dedup below (by job link
    # against the live sheet) makes this safe to run repeatedly.
    artifacts.pull("tailored_jobs.json")

    tailored_jobs = json.loads((ROOT / "output" / "tailored_jobs.json").read_text(encoding="utf-8"))
    saved_jobs = [j for j in tailored_jobs if j.get("status") == "saved"]

    if not saved_jobs:
        print("0 saved jobs, nothing to log")
        raise SystemExit(0)

    sheet_id = get_or_create_sheet_id()
    # Canonical (tracking-param-stripped) link -- a job re-scraped on a different day
    # with a new trackingId/position is still the same posting and must not get a
    # second row. See scripts/job_links.py.
    existing_canonical = {canonical_link(link) for link in get_existing_links(sheet_id)}

    today = date.today().isoformat()
    new_rows = []
    seen_this_run = set()
    for job in saved_jobs:
        key = canonical_link(job["link"])
        if key in existing_canonical or key in seen_this_run:
            continue
        seen_this_run.add(key)
        new_rows.append(build_row(job, today))

    if new_rows:
        try:
            ensure_extra_headers(sheet_id)
        except Exception as e:  # noqa: BLE001 -- header cosmetics must not block logging rows
            print(f"could not ensure Source/Status Updated headers: {e}")
        append_rows(sheet_id, new_rows)
        log_event("tracker_rows_added", f"Logged {len(new_rows)} job(s) to the Job Application Tracker",
                  count=len(new_rows))

    print(f"{len(saved_jobs)} saved jobs, {len(new_rows)} new rows added, "
          f"{len(saved_jobs) - len(new_rows)} already logged")
