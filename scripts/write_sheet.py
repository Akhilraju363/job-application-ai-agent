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

HEADERS = ["Job Title", "Company", "Job Link", "Fit Score", "Resume Path", "Status", "Timestamp",
           "Company Notes"]
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
        json.dumps({"spreadsheetId": sheet_id, "range": "Sheet1!A1:H1", "valueInputOption": "USER_ENTERED"}),
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


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
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
    existing_links = get_existing_links(sheet_id)

    today = date.today().isoformat()
    new_rows = []
    for job in saved_jobs:
        if job["link"] in existing_links:
            continue
        resume_path = job.get("resume_link") or job.get("desktop_file", "")
        new_rows.append([
            job["title"], job["company"], job["link"],
            str(job["score"]), resume_path, "Not Applied", today,
            job.get("company_notes", ""),
        ])

    if new_rows:
        gws("sheets", "+append", "--spreadsheet", sheet_id, "--json-values", json.dumps(new_rows))

    print(f"{len(saved_jobs)} saved jobs, {len(new_rows)} new rows added, "
          f"{len(saved_jobs) - len(new_rows)} already logged")
