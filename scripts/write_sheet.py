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
import logging_config as lc  # noqa: E402
import google_auth  # noqa: E402

log = lc.get_logger("tracker")

HEADERS = ["Job Title", "Company", "Job Link", "Fit Score", "Resume Path", "Status", "Timestamp",
           "Company Notes", "Source", "Status Updated", "Resume ID", "Match %", "Suitability"]
TRACKER_TAB = "Jobs"
LEGACY_TRACKER_TAB = "Sheet1"
TRACKER_RANGE = f"{TRACKER_TAB}!A:M"
# Columns I:L were added for the dashboard (where a row came from, when its status last changed
# via the dashboard, which generated resume + JD match it came from). Older sheets only have
# A:H -- ensure_extra_headers() adds the rest.
EXTRA_HEADERS = HEADERS[8:]
SHEET_TITLE = "Job Application Tracker"
STATUS_OPTIONS = ["Not Applied", "Applied", "Interviewing", "Offer", "Rejected"]
SUITABILITY_OPTIONS = ["Not reviewed", "Shortlist", "Not Shortlist"]
DEFAULT_SUITABILITY = SUITABILITY_OPTIONS[0]


def rename_tracker_tab(sheet_id):
    """Rename the existing first tracker tab in place, preserving its sheetId and data."""
    metadata = gws("sheets", "spreadsheets", "get", "--params", json.dumps({
        "spreadsheetId": sheet_id, "fields": "sheets.properties",
    }))
    sheets = metadata.get("sheets", [])
    for sheet in sheets:
        props = sheet.get("properties", {})
        if props.get("title") == TRACKER_TAB:
            return props["sheetId"]
    source = next((s for s in sheets if s.get("properties", {}).get("title", "").strip() == TRACKER_TAB), None)
    if source:
        sheet_id_value = source["properties"]["sheetId"]
        gws("sheets", "spreadsheets", "batchUpdate", "--params", json.dumps({
            "spreadsheetId": sheet_id,
        }), "--json", json.dumps({"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": sheet_id_value, "title": TRACKER_TAB},
            "fields": "title",
        }}]}))
        return sheet_id_value
    source = next((s for s in sheets if s.get("properties", {}).get("title") == LEGACY_TRACKER_TAB), None)
    if not source:
        raise RuntimeError("Jobs worksheet not found; refusing to create or rename a replacement")
    sheet_id_value = source["properties"]["sheetId"]
    gws("sheets", "spreadsheets", "batchUpdate", "--params", json.dumps({
        "spreadsheetId": sheet_id,
    }), "--json", json.dumps({"requests": [{"updateSheetProperties": {
        "properties": {"sheetId": sheet_id_value, "title": TRACKER_TAB},
        "fields": "title",
    }}]}))
    return sheet_id_value


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
_SERVICE_ACCOUNT_SHEETS_READ_VERIFIED = False


def is_sheets_write_operation(args):
    """Classify the GWS Sheets commands that can mutate spreadsheet data or structure."""
    command = tuple(str(arg).lower() for arg in args)
    if not command or command[0] != "sheets":
        return False
    route = command[1:]
    if route[:1] == ("+append",):
        return True
    if route[:2] in (("spreadsheets", "create"), ("spreadsheets", "batchupdate")):
        return True
    return route[:2] == ("spreadsheets", "values") and route[2:3] in (
        ("append",), ("update",), ("batchupdate",), ("clear",), ("batchclear",))


def gws(*args):
    # See format_resume_doc.py's gws() for why encoding="utf-8" is required on Windows.
    is_write = is_sheets_write_operation(args)
    if (is_write and google_auth.is_service_account() and
            os.environ.get("GOOGLE_SHEETS_WRITE_ENABLED", "").strip().lower() not in ("1", "true", "yes", "on")):
        raise RuntimeError("Google Sheets writes are disabled; verify read-only access, then explicitly set "
                           "GOOGLE_SHEETS_WRITE_ENABLED=true")
    global _SERVICE_ACCOUNT_SHEETS_READ_VERIFIED
    if is_write and google_auth.is_service_account() and not _SERVICE_ACCOUNT_SHEETS_READ_VERIFIED:
        sheet_id = os.environ.get("google_sheet_id", "").strip()
        if not sheet_id:
            raise RuntimeError("google_sheet_id must identify the existing tracker spreadsheet")
        gws("sheets", "spreadsheets", "get", "--params",
            json.dumps({"spreadsheetId": sheet_id, "fields": "spreadsheetId"}))
        gws("sheets", "+read", "--spreadsheet", sheet_id, "--range", f"{TRACKER_TAB}!A1:M1")
        _SERVICE_ACCOUNT_SHEETS_READ_VERIFIED = True
    result = subprocess.run([*GWS_CMD, *args], capture_output=True, text=True, encoding="utf-8",
                            env=google_auth.configure_environment())
    if result.returncode != 0:
        operation = []
        for arg in args:
            if str(arg).startswith("--"):
                break
            operation.append(str(arg))
        command = "gws " + " ".join(operation)
        raise RuntimeError("\n".join([
            f"{command} failed (exit code {result.returncode})",
            f"stdout: {google_auth.safe_gws_error(result.stdout)}",
            f"stderr: {google_auth.safe_gws_error(result.stderr)}",
        ]))
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
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
        "corpora": "allDrives",
    }))
    files = result.get("files", [])
    return files[0]["id"] if files else None


def _remember_sheet_id(sheet_id):
    # Only when a local .env actually exists -- on Modal there's no .env to write
    # (and the container is ephemeral anyway); google_sheet_id belongs in the secret.
    if ENV_PATH.exists():
        set_key(str(ENV_PATH), "google_sheet_id", sheet_id)


def get_or_create_sheet_id():
    """Return only the explicitly configured existing spreadsheet; never search or create."""
    sheet_id = os.environ.get("google_sheet_id", "").strip()
    if not sheet_id:
        raise RuntimeError("google_sheet_id must identify the existing tracker spreadsheet")
    return sheet_id


def get_existing_links(sheet_id):
    result = gws("sheets", "+read", "--spreadsheet", sheet_id, "--range", f"{TRACKER_TAB}!C:C")
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
        DEFAULT_SUITABILITY,
    ]


def build_new_rows(saved_jobs, existing_canonical, today):
    """Rows for saved jobs not yet in the Sheet (deduped by canonical link, also within this run).
    Each row's Source is the job's own source (LinkedIn, Naukri); older records without one are LinkedIn."""
    new_rows, seen_this_run = [], set()
    for job in saved_jobs:
        key = canonical_link(job["link"])
        if key in existing_canonical or key in seen_this_run:
            continue
        seen_this_run.add(key)
        new_rows.append(build_row(job, today, source=job.get("source") or "LinkedIn"))
    return new_rows


def read_tracker(sheet_id):
    """All tracker rows as dicts, each with its 1-based sheet row number."""
    values = gws("sheets", "+read", "--spreadsheet", sheet_id, "--range", TRACKER_RANGE).get("values", [])
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
            "suitability": cells[12] or DEFAULT_SUITABILITY,
        })
    return rows


def ensure_extra_headers(sheet_id):
    have = gws("sheets", "+read", "--spreadsheet", sheet_id, "--range", f"{TRACKER_TAB}!A1:M1").get("values") or [[]]
    current = [str(c) for c in have[0]] if have else []
    expected_prefix = HEADERS[:8]
    if current[:len(expected_prefix)] != expected_prefix:
        raise RuntimeError(f"tracker header mismatch in {TRACKER_TAB}!A:H; refusing schema migration")
    for index, header in enumerate(EXTRA_HEADERS, start=8):
        existing = current[index] if index < len(current) else ""
        if existing and existing != header:
            raise RuntimeError(f"tracker column {index + 1} has unexpected header {existing!r}")
        if not existing:
            update_range(sheet_id, f"{TRACKER_TAB}!{_column_name(index)}1", [[header]])
    gws("sheets", "spreadsheets", "batchUpdate", "--params",
        json.dumps({"spreadsheetId": sheet_id}),
        "--json", json.dumps({"requests": _validation_requests()}))


def _column_name(index):
    return chr(ord("A") + index)


def _validation_rule(values):
    return {
        "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in values]},
        "showCustomUi": True,
        "strict": True,
    }


def _validation_requests():
    return [
        {"setDataValidation": {
            "range": {"sheetId": 0, "startRowIndex": 1, "endRowIndex": 1000,
                      "startColumnIndex": 5, "endColumnIndex": 6},
            "rule": _validation_rule(STATUS_OPTIONS),
        }},
        {"setDataValidation": {
            "range": {"sheetId": 0, "startRowIndex": 1, "endRowIndex": 1000,
                      "startColumnIndex": 12, "endColumnIndex": 13},
            "rule": _validation_rule(SUITABILITY_OPTIONS),
        }},
    ]


def update_range(sheet_id, a1_range, values, value_input_option="USER_ENTERED"):
    gws("sheets", "spreadsheets", "values", "update", "--params",
        json.dumps({"spreadsheetId": sheet_id, "range": a1_range, "valueInputOption": value_input_option}),
        "--json", json.dumps({"values": values}))


def append_rows(sheet_id, rows):
    if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != len(HEADERS) for row in rows):
        raise ValueError(f"tracker append rows must be lists of {len(HEADERS)} values")
    gws("sheets", "+append", "--spreadsheet", sheet_id, "--json-values",
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")))


def set_status(sheet_id, row_number, status, today):
    if status not in STATUS_OPTIONS:
        raise ValueError(f"unknown status {status!r}")
    update_range(sheet_id, f"{TRACKER_TAB}!F{row_number}", [[status]])
    update_range(sheet_id, f"{TRACKER_TAB}!J{row_number}", [[today]])


def set_suitability(sheet_id, row_number, suitability):
    if suitability not in SUITABILITY_OPTIONS:
        raise ValueError(f"unknown suitability {suitability!r}")
    update_range(sheet_id, f"{TRACKER_TAB}!M{row_number}", [[suitability]])


class AmbiguousTrackerRowError(ValueError):
    pass


def delete_row(sheet_id, row_number, expected_link):
    """Re-resolve the unique canonical link immediately before positional row deletion.

    Google Sheets only deletes dimensions by row index, so an external edit can still
    race the final read and batchUpdate.
    """
    if not isinstance(row_number, int) or row_number < 2:
        raise ValueError("tracker row must be a data row number")
    metadata = gws("sheets", "spreadsheets", "get", "--params", json.dumps({
        "spreadsheetId": sheet_id, "fields": "sheets.properties",
    }))
    sheet = next((s.get("properties", {}) for s in metadata.get("sheets", [])
                  if s.get("properties", {}).get("title") == TRACKER_TAB), None)
    if not sheet:
        raise RuntimeError(f"{TRACKER_TAB} worksheet not found")
    key = canonical_link(expected_link)
    if not key:
        raise ValueError("expected tracker job link is required")
    matches = [row for row in read_tracker(sheet_id) if canonical_link(row.get("link")) == key]
    if not matches:
        raise LookupError("That job isn't in the tracker")
    if len(matches) != 1:
        raise AmbiguousTrackerRowError("Multiple tracker rows match this job; no row was deleted")
    row_number = matches[0]["row"]
    gws("sheets", "spreadsheets", "batchUpdate", "--params",
        json.dumps({"spreadsheetId": sheet_id}),
        "--json", json.dumps({"requests": [{"deleteDimension": {
            "range": {"sheetId": sheet["sheetId"], "dimension": "ROWS",
                      "startIndex": row_number - 1, "endIndex": row_number}
        }}]}))


def delete_rows(sheet_id, expected_links):
    """Delete unique tracker rows in one batch, resolving each canonical link from the live Sheet.

    Requests are ordered from the last row upward so deleting one row cannot shift the next
    requested row. As with delete_row(), an external edit can still race the final read and write.
    """
    if not isinstance(expected_links, list) or not expected_links:
        raise ValueError("at least one tracker job link is required")
    keys = [canonical_link(link) for link in expected_links]
    if any(not key for key in keys):
        raise ValueError("every tracker job link must be valid")
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate tracker job links are not allowed")

    metadata = gws("sheets", "spreadsheets", "get", "--params", json.dumps({
        "spreadsheetId": sheet_id, "fields": "sheets.properties",
    }))
    sheet = next((s.get("properties", {}) for s in metadata.get("sheets", [])
                  if s.get("properties", {}).get("title") == TRACKER_TAB), None)
    if not sheet:
        raise RuntimeError(f"{TRACKER_TAB} worksheet not found")

    rows = read_tracker(sheet_id)
    outcomes = {}
    targets = []
    for key in keys:
        matches = [row for row in rows if canonical_link(row.get("link")) == key]
        if not matches:
            outcomes[key] = {"status": "failed", "code": "not_found",
                             "message": "Application not found"}
        elif len(matches) != 1:
            outcomes[key] = {"status": "failed", "code": "tracker_conflict",
                             "message": "Multiple tracker rows match this application"}
        elif not isinstance(matches[0].get("row"), int) or matches[0]["row"] < 2:
            outcomes[key] = {"status": "failed", "code": "tracker_conflict",
                             "message": "Tracker row position is invalid"}
        else:
            targets.append((key, matches[0]))

    if targets:
        requests = [{"deleteDimension": {
            "range": {"sheetId": sheet["sheetId"], "dimension": "ROWS",
                      "startIndex": row["row"] - 1, "endIndex": row["row"]}
        }} for _, row in sorted(targets, key=lambda item: item[1]["row"], reverse=True)]
        gws("sheets", "spreadsheets", "batchUpdate", "--params",
            json.dumps({"spreadsheetId": sheet_id}),
            "--json", json.dumps({"requests": requests}))
        for key, row in targets:
            outcomes[key] = {"status": "deleted", "row": row["row"],
                             "title": row.get("title", ""), "company": row.get("company", ""),
                             "link": row.get("link", "")}

    return [outcomes[key] for key in keys]


if __name__ == "__main__":
    lc.configure_logging("pipeline")
    import artifacts

    # Recovery: log whatever a failed earlier run tailored. Dedup below (by job link
    # against the live sheet) makes this safe to run repeatedly.
    artifacts.pull("tailored_jobs.json")

    tailored_jobs = json.loads((ROOT / "output" / "tailored_jobs.json").read_text(encoding="utf-8"))
    saved_jobs = [j for j in tailored_jobs if j.get("status") == "saved"]

    if not saved_jobs:
        log.info("0 saved jobs, nothing to log")
        raise SystemExit(0)

    sheet_id = get_or_create_sheet_id()
    # Canonical (tracking-param-stripped) link -- a job re-scraped on a different day
    # with a new trackingId/position is still the same posting and must not get a
    # second row. See scripts/job_links.py.
    existing_canonical = {canonical_link(link) for link in get_existing_links(sheet_id)}

    today = date.today().isoformat()
    new_rows = build_new_rows(saved_jobs, existing_canonical, today)

    if new_rows:
        try:
            ensure_extra_headers(sheet_id)
        except Exception as e:  # noqa: BLE001 -- header cosmetics must not block logging rows
            log.warning("Could not ensure Source/Status Updated headers", exc_info=True)
        append_rows(sheet_id, new_rows)
        log_event("tracker_rows_added", f"Logged {len(new_rows)} job(s) to the Job Application Tracker",
                  count=len(new_rows))

    log.info(f"{len(saved_jobs)} saved jobs, {len(new_rows)} new rows added, "
             f"{len(saved_jobs) - len(new_rows)} already logged",
             extra={"saved_count": len(saved_jobs), "new_rows": len(new_rows),
                    "duplicates_avoided": len(saved_jobs) - len(new_rows)})
