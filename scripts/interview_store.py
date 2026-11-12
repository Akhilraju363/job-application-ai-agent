"""Google Sheets-backed Interview Tracker storage.

The existing ``google_sheet_id`` identifies the Job Application Tracker
spreadsheet. Interviews use a separate tab in that spreadsheet and never
touch the application's Jobs rows.
"""
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import paths
import write_sheet

FILE_NAME = "interviews.json"
TAB_TITLE = "Interviews"
HEADERS = [
    "Interview ID", "Application ID", "Company", "Job Title", "Interview Date",
    "Interview Time", "Timezone", "Interview Round", "Interview Mode", "Meeting URL",
    "Location", "Interviewer Name", "Interviewer Contact", "Preparation Topics", "Notes",
    "Reminder Enabled", "Reminder Minutes Before", "Interview Status", "Interview Outcome",
    "Created At", "Updated At",
]
STATUSES = ("Scheduled", "Completed", "Rescheduled", "Cancelled", "No Show")
OUTCOMES = ("Pending", "Selected", "Rejected", "Next Round")
MODES = ("Online", "Phone", "In-person")
REMINDER_MINUTES = (1440, 60, 30)
_ID_RE = re.compile(r"^[0-9a-f]{16}$")


class InterviewError(ValueError):
    pass


class StorageError(InterviewError):
    pass


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value, field, required=False, maximum=500):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise InterviewError(f"{field} must be text")
    value = " ".join(value.split()).strip()
    if required and not value:
        raise InterviewError(f"{field} is required")
    if len(value) > maximum:
        raise InterviewError(f"{field} is too long")
    return value


def _validate_date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise InterviewError("interview_date must be YYYY-MM-DD") from None


def _validate_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise InterviewError("interview_time must be HH:MM")
    return value


def _validate_email(value):
    value = _clean(value, "interviewer_contact", maximum=254)
    if value and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise InterviewError("interviewer_contact must be a valid email address")
    return value


def _validate_url(value):
    value = _clean(value, "meeting_url", maximum=1000)
    if value and not re.fullmatch(r"https?://[^\s]+", value, re.I):
        raise InterviewError("meeting_url must be an http(s) URL")
    return value


def _normalise_bool(value, field, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return default
        if normalized in ("true", "1", "yes"):
            return True
        if normalized in ("false", "0", "no"):
            return False
    raise InterviewError(f"{field} must be a boolean")


def _normalise(body, existing=None, partial=False):
    if not isinstance(body, dict):
        raise InterviewError("interview must be an object")
    source = dict(existing or {})
    source.update(body or {})
    required = not partial
    out = {
        "company": _clean(source.get("company"), "company", required, 200),
        "job_title": _clean(source.get("job_title"), "job_title", required, 200),
        "interview_date": _validate_date(source.get("interview_date")) if source.get("interview_date") else "",
        "interview_time": _validate_time(source.get("interview_time")) if source.get("interview_time") else "",
        "timezone": _clean(source.get("timezone") or "Asia/Kolkata", "timezone", maximum=100),
        "interview_round": _clean(source.get("interview_round"), "interview_round", required, 100),
        "application_id": _clean(source.get("application_id"), "application_id", maximum=200),
        "mode": source.get("mode") or "Online",
        "meeting_url": _validate_url(source.get("meeting_url")),
        "location": _clean(source.get("location"), "location", maximum=300),
        "interviewer_name": _clean(source.get("interviewer_name"), "interviewer_name", maximum=200),
        "interviewer_contact": _validate_email(source.get("interviewer_contact")),
        "preparation_topics": _clean(source.get("preparation_topics"), "preparation_topics", maximum=2000),
        "notes": _clean(source.get("notes"), "notes", maximum=5000),
        "reminder_enabled": _normalise_bool(source.get("reminder_enabled"), "reminder_enabled"),
        "reminder_minutes_before": source.get("reminder_minutes_before"),
        "status": source.get("status") or "Scheduled",
        "outcome": source.get("outcome") or "Pending",
    }
    if required and (not out["interview_date"] or not out["interview_time"]):
        raise InterviewError("interview_date and interview_time are required")
    if out["mode"] not in MODES:
        raise InterviewError(f"mode must be one of: {', '.join(MODES)}")
    if out["status"] not in STATUSES:
        raise InterviewError(f"status must be one of: {', '.join(STATUSES)}")
    if out["outcome"] not in OUTCOMES:
        raise InterviewError(f"outcome must be one of: {', '.join(OUTCOMES)}")
    if out["reminder_enabled"]:
        try:
            out["reminder_minutes_before"] = int(out["reminder_minutes_before"])
        except (TypeError, ValueError):
            raise InterviewError("reminder_minutes_before must be 30, 60, or 1440") from None
        if out["reminder_minutes_before"] not in REMINDER_MINUTES:
            raise InterviewError("reminder_minutes_before must be 30, 60, or 1440")
    else:
        out["reminder_minutes_before"] = None
    return out


def _local_file():
    return paths.OUTPUT_DIR / FILE_NAME


def _local_rows():
    try:
        value = json.loads(_local_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise InterviewError(f"Interview storage is unavailable: {exc}") from exc
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise InterviewError("Interview storage contains malformed data")
    return value


def _spreadsheet_id():
    sid = os.environ.get("google_sheet_id", "").strip()
    if not sid:
        raise StorageError("google_sheet_id must identify the existing tracker spreadsheet")
    return sid


def _metadata(sid):
    try:
        return write_sheet.gws("sheets", "spreadsheets", "get", "--params", json.dumps({
            "spreadsheetId": sid, "fields": "sheets.properties",
        }))
    except Exception as exc:
        raise StorageError(f"Google Sheets metadata is unavailable: {exc}") from exc


def _tab(sid, create=True):
    sheets = _metadata(sid).get("sheets", [])
    for sheet in sheets:
        props = sheet.get("properties", {})
        if props.get("title") == TAB_TITLE:
            return props["sheetId"]
    if not create:
        raise InterviewError("The Interviews worksheet does not exist")
    try:
        write_sheet.gws("sheets", "spreadsheets", "batchUpdate", "--params", json.dumps({
            "spreadsheetId": sid,
        }), "--json", json.dumps({"requests": [{"addSheet": {"properties": {"title": TAB_TITLE}}}]}))
    except Exception as exc:
        raise StorageError(f"Could not create the Interviews worksheet: {exc}") from exc
    sheet = next((s for s in _metadata(sid).get("sheets", [])
                  if s.get("properties", {}).get("title") == TAB_TITLE), None)
    if not sheet:
        raise StorageError("Google Sheets did not return the new Interviews worksheet")
    return sheet["properties"]["sheetId"]


def _ensure_tab(sid):
    sheet_id = _tab(sid)
    try:
        result = write_sheet.gws("sheets", "+read", "--spreadsheet", sid, "--range", f"{TAB_TITLE}!A1:U1")
        current = result.get("values", [])
        if not current or current[0] != HEADERS:
            if current and any(str(v).strip() for v in current[0]):
                raise StorageError("Interviews worksheet header does not match the required schema")
            write_sheet.update_range(sid, f"{TAB_TITLE}!A1:U1", [HEADERS])
    except InterviewError:
        raise
    except Exception as exc:
        raise StorageError(f"Interviews worksheet is unavailable: {exc}") from exc
    return sheet_id


def _read_rows(sid):
    _ensure_tab(sid)
    try:
        values = write_sheet.gws("sheets", "+read", "--spreadsheet", sid, "--range", f"{TAB_TITLE}!A:U").get("values", [])
    except Exception as exc:
        raise StorageError(f"Interview rows could not be read from Google Sheets: {exc}") from exc
    rows = []
    ids = set()
    for row_number, raw in enumerate(values[1:], start=2):
        cells = [str(value) for value in raw] + [""] * (len(HEADERS) - len(raw))
        if not any(cells):
            continue
        row = dict(zip(HEADERS, cells[:len(HEADERS)]))
        if not _ID_RE.fullmatch(row["Interview ID"]):
            raise InterviewError(f"Malformed interview ID in worksheet row {row_number}")
        if row["Interview ID"] in ids:
            raise InterviewError(f"Duplicate interview ID in worksheet row {row_number}")
        ids.add(row["Interview ID"])
        rows.append((_from_sheet(row), row_number))
    return rows


def _from_sheet(row):
    reminder = _normalise_bool(row["Reminder Enabled"], "Reminder Enabled")
    minutes = row["Reminder Minutes Before"].strip()
    return {
        "id": row["Interview ID"], "application_id": row["Application ID"], "company": row["Company"],
        "job_title": row["Job Title"], "interview_date": row["Interview Date"],
        "interview_time": row["Interview Time"], "timezone": row["Timezone"] or "Asia/Kolkata",
        "interview_round": row["Interview Round"], "mode": row["Interview Mode"],
        "meeting_url": row["Meeting URL"], "location": row["Location"],
        "interviewer_name": row["Interviewer Name"], "interviewer_contact": row["Interviewer Contact"],
        "preparation_topics": row["Preparation Topics"], "notes": row["Notes"],
        "reminder_enabled": reminder, "reminder_minutes_before": int(minutes) if minutes.isdigit() else None,
        "status": row["Interview Status"], "outcome": row["Interview Outcome"],
        "created_at": row["Created At"], "updated_at": row["Updated At"],
    }


def _to_sheet(row):
    return [row["id"], row["application_id"], row["company"], row["job_title"], row["interview_date"],
            row["interview_time"], row["timezone"], row["interview_round"], row["mode"], row["meeting_url"],
            row["location"], row["interviewer_name"], row["interviewer_contact"], row["preparation_topics"],
            row["notes"], "TRUE" if row["reminder_enabled"] else "FALSE",
            "" if row["reminder_minutes_before"] is None else str(row["reminder_minutes_before"]),
            row["status"], row["outcome"], row["created_at"], row["updated_at"]]


def _append(sid, values):
    try:
        write_sheet.gws("sheets", "spreadsheets", "values", "append", "--params", json.dumps({
            "spreadsheetId": sid, "range": f"{TAB_TITLE}!A:U",
            "valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS",
        }), "--json", json.dumps({"values": [values]}))
    except Exception as exc:
        raise StorageError(f"Interview could not be appended to Google Sheets: {exc}") from exc


def _update(sid, row_number, values):
    try:
        write_sheet.update_range(sid, f"{TAB_TITLE}!A{row_number}:U{row_number}", [values],
                                 value_input_option="RAW")
    except Exception as exc:
        raise StorageError(f"Interview could not be updated in Google Sheets: {exc}") from exc


def _delete(sid, sheet_id, row_number):
    if row_number < 2:
        raise InterviewError("Cannot delete the Interviews header")
    try:
        write_sheet.gws("sheets", "spreadsheets", "batchUpdate", "--params", json.dumps({
            "spreadsheetId": sid,
        }), "--json", json.dumps({"requests": [{"deleteDimension": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS",
                      "startIndex": row_number - 1, "endIndex": row_number}
        }}]}))
    except Exception as exc:
        raise StorageError(f"Interview could not be deleted from Google Sheets: {exc}") from exc


def _find(rows, interview_id):
    if not isinstance(interview_id, str) or not _ID_RE.fullmatch(interview_id):
        raise InterviewError("interview ID is invalid")
    return next(((row, number) for row, number in rows if row["id"] == interview_id), None)


def list_interviews(*, status="", company="", date_from="", date_to="", application_id=""):
    sid = _spreadsheet_id()
    rows = [row for row, _ in _read_rows(sid)]
    out = []
    for row in rows:
        if status and row["status"] != status:
            continue
        if company and company.lower() not in row["company"].lower():
            continue
        if application_id and row["application_id"] != application_id:
            continue
        if date_from and row["interview_date"] < date_from:
            continue
        if date_to and row["interview_date"] > date_to:
            continue
        out.append(row)
    return sorted(out, key=lambda r: (r["interview_date"], r["interview_time"], r["created_at"]))


def get_interview(interview_id):
    sid = _spreadsheet_id()
    found = _find(_read_rows(sid), interview_id)
    return found[0] if found else None


def create_interview(body):
    row = _normalise(body)
    row.update({"id": uuid.uuid4().hex[:16], "created_at": _now(), "updated_at": _now()})
    sid = _spreadsheet_id()
    existing = _read_rows(sid)
    if _find(existing, row["id"]):
        raise InterviewError("Interview ID already exists")
    _append(sid, _to_sheet(row))
    return row


def update_interview(interview_id, body):
    sid = _spreadsheet_id()
    found = _find(_read_rows(sid), interview_id)
    if not found:
        raise KeyError(interview_id)
    old, _ = found
    unexpected = set(body) - set(old)
    if unexpected:
        raise InterviewError(f"Unexpected field(s): {', '.join(sorted(unexpected))}")
    row = _normalise(body, old, partial=True)
    row.update({"id": interview_id, "created_at": old["created_at"], "updated_at": _now()})
    latest = _find(_read_rows(sid), interview_id)
    if not latest:
        raise KeyError(interview_id)
    _update(sid, latest[1], _to_sheet(row))
    return row


def delete_interview(interview_id):
    sid = _spreadsheet_id()
    sheet_id = _tab(sid, create=False)
    # Resolve the current row after metadata lookup; Sheets deletes by position, not ID.
    latest = _find(_read_rows(sid), interview_id)
    if not latest:
        raise KeyError(interview_id)
    _delete(sid, sheet_id, latest[1])
    if _find(_read_rows(sid), interview_id):
        raise StorageError("Google Sheets still contains the deleted interview")


def migrate_local_records():
    """Import valid local records without modifying the JSON file."""
    records = _local_rows()
    imported = already_exists = failed = 0
    failures = []
    for record in records:
        try:
            sid = _spreadsheet_id()
            current = _read_rows(sid)
            if _find(current, record.get("id")):
                already_exists += 1
                continue
            row = _normalise(record, partial=False)
            row.update({"id": record["id"], "created_at": record.get("created_at") or _now(),
                        "updated_at": record.get("updated_at") or _now()})
            _append(sid, _to_sheet(row))
            imported += 1
        except (KeyError, InterviewError) as exc:
            failed += 1
            failures.append(str(exc))
    return {"imported": imported, "already_exists": already_exists, "failed": failed, "errors": failures}


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "--migrate":
        raise SystemExit("usage: python scripts/interview_store.py --migrate")
    print(json.dumps(migrate_local_records()))
