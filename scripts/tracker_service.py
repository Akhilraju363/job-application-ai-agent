"""Cached, failure-tolerant access to the "Job Application Tracker" Google Sheet.

Thin layer over write_sheet.py (the existing tracker integration): the Sheet stays the one
system of record for application status; this only adds a short TTL cache (each read is a
`gws` subprocess) and turns Google/gws failures into a structured "unavailable" result so
the dashboard can show a per-section error + retry instead of a 500.
"""
import os
import threading
import time
from datetime import date

import activity
import logging_config as lc
import write_sheet
from job_links import canonical_link

log = lc.get_logger("tracker")

TTL_SECONDS = 45
FAILURE_TTL_SECONDS = 15  # don't re-run a failing `gws` call once per dashboard widget
STATUSES = write_sheet.STATUS_OPTIONS


class TrackerError(RuntimeError):
    pass


def _short(exc):
    return f"{type(exc).__name__}: {str(exc).strip().splitlines()[0][:200]}" if str(exc).strip() \
        else type(exc).__name__


def _is_url(value):
    return str(value or "").strip().lower().startswith(("https://", "http://"))


class Tracker:
    def __init__(self):
        self._lock = threading.RLock()
        self._cache = None  # (timestamp, rows)
        self._failure = None  # (timestamp, error string)
        self._sheet_id = None

    def invalidate(self):
        with self._lock:
            self._cache = None
            self._failure = None

    def _lookup_sheet_id(self, create=False):
        sid = os.environ.get("google_sheet_id", "").strip() or self._sheet_id
        if not sid:
            sid = write_sheet.get_or_create_sheet_id() if create else write_sheet.find_sheet_by_title()
        self._sheet_id = sid
        return sid

    def snapshot(self, force=False):
        """{"rows": [...], "available": bool, "error": str|None, "stale": bool, "configured": bool}"""
        with self._lock:
            if not force and self._cache and time.time() - self._cache[0] < TTL_SECONDS:
                return {"rows": self._cache[1], "available": True, "error": None, "stale": False,
                        "configured": True}
            if not force and self._failure and time.time() - self._failure[0] < FAILURE_TTL_SECONDS:
                return self._failed(self._failure[1])
            try:
                sid = self._lookup_sheet_id()
                if not sid:
                    return {"rows": [], "available": True, "error": None, "stale": False,
                            "configured": False}
                rows = write_sheet.read_tracker(sid)
                self._cache = (time.time(), rows)
                log.info("Tracker read", extra={"row_count": len(rows)})
                return {"rows": rows, "available": True, "error": None, "stale": False, "configured": True}
            except Exception as e:  # noqa: BLE001 -- gws missing / not authed / offline
                log.error("Tracker read failed", exc_info=True)
                self._failure = (time.time(), _short(e))
                return self._failed(self._failure[1])

    def _failed(self, error):
        if self._cache:
            return {"rows": self._cache[1], "available": True, "error": error, "stale": True,
                    "configured": True}
        return {"rows": [], "available": False, "error": error, "stale": False, "configured": True}

    def _find(self, rows, link):
        key = canonical_link(link)
        return next((r for r in rows if canonical_link(r["link"]) == key), None)

    def save_job(self, *, title, company, link, score, resume_path="", source="LinkedIn",
                 company_notes="", resume_id="", match_pct=None):
        """Add one row (status "Not Applied"), or return the existing one -- never a duplicate.
        `resume_path` is the resume's Google Drive URL (or empty); a local path is refused."""
        if resume_path and not _is_url(resume_path):
            raise ValueError("The tracker's resume link must be a Google Drive URL, not a local file path")
        try:
            sid = self._lookup_sheet_id(create=True)
            write_sheet.ensure_extra_headers(sid)
            rows = write_sheet.read_tracker(sid)
            existing = self._find(rows, link)
            if existing:
                if resume_path and not _is_url(existing["resume_path"]):  # blank, or a legacy local path
                    write_sheet.update_range(sid, f"Sheet1!E{existing['row']}", [[resume_path]])
                    existing["resume_path"] = resume_path
                    log.info("Tracker row updated", extra={"resume_id": resume_id, "company": company, "row": existing["row"]})
                else:
                    log.info("Duplicate tracker row avoided", extra={"resume_id": resume_id, "company": company,
                                                                      "row": existing["row"]})
                self.invalidate()
                return {"result": "exists", "row": existing}
            row = write_sheet.build_row({"title": title, "company": company, "link": link,
                                         "score": score, "resume_path": resume_path,
                                         "company_notes": company_notes, "resume_id": resume_id,
                                         "match_pct": match_pct}, date.today().isoformat(), source)
            write_sheet.append_rows(sid, [row])
            self.invalidate()
        except Exception as e:  # noqa: BLE001
            log.error("Tracker row save failed", exc_info=True, extra={"resume_id": resume_id, "company": company})
            raise TrackerError(_short(e)) from e
        log.info("Tracker row created", extra={"resume_id": resume_id, "company": company})
        activity.log_event("tracker_saved", f"Saved {title} at {company} to the tracker",
                           company=company, title=title, link=link or None)
        return {"result": "added", "row": None}

    def set_status(self, link, status):
        if status not in STATUSES:
            raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
        try:
            sid = self._lookup_sheet_id()
            if not sid:
                raise TrackerError("No tracker sheet is configured")
            write_sheet.ensure_extra_headers(sid)
            row = self._find(write_sheet.read_tracker(sid), link)
            if not row:
                raise LookupError("That job isn't in the tracker yet -- save it first")
            write_sheet.set_status(sid, row["row"], status, date.today().isoformat())
            self.invalidate()
        except (LookupError, ValueError):
            raise
        except Exception as e:  # noqa: BLE001
            log.error("Tracker status update failed", exc_info=True, extra={"status": status})
            raise TrackerError(_short(e)) from e
        log.info("Tracker row updated", extra={"company": row["company"], "row": row["row"], "status": status})
        activity.log_event("status_changed", f"{row['title']} at {row['company']} marked {status}",
                           company=row["company"], title=row["title"], link=link or None, status=status)
        return {"row": row["row"], "status": status}


tracker = Tracker()
