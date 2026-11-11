"""Cached, failure-tolerant access to the "Job Application Tracker" Google Sheet.

Thin layer over write_sheet.py (the existing tracker integration): the Sheet stays the one
system of record for application status; this only adds a short TTL cache (each read is a
`gws` subprocess) and turns Google/gws failures into a structured "unavailable" result so
the dashboard can show a per-section error + retry instead of a 500.
"""
import os
import re
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
SUITABILITIES = write_sheet.SUITABILITY_OPTIONS
SHEET_ID_RE = re.compile(r"[A-Za-z0-9_-]{10,200}")  # Google spreadsheet ids; anything else gets no link


class TrackerError(RuntimeError):
    pass


class TrackerConflict(TrackerError):
    pass


def _short(exc):
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


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
        sid = os.environ.get("google_sheet_id", "").strip()
        if not sid:
            if create:
                sid = write_sheet.get_or_create_sheet_id()
            else:
                return None
        self._sheet_id = sid
        return sid

    def sheet_url(self):
        """Browser link to the explicitly configured tracker ID -- never triggers a lookup."""
        sid = os.environ.get("google_sheet_id", "").strip()
        if not sid or not SHEET_ID_RE.fullmatch(sid):
            return None
        return f"https://docs.google.com/spreadsheets/d/{sid}/edit"

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
        return next((r for r in rows if key and canonical_link(r.get("link")) == key), None)

    def delete_job(self, link):
        """Delete exactly the tracker row matching the canonical job link."""
        key = canonical_link(link)
        if not key:
            raise ValueError("job link is required")
        try:
            sid = self._lookup_sheet_id()
            if not sid:
                raise TrackerError("No tracker sheet is configured")
            matches = [r for r in write_sheet.read_tracker(sid) if canonical_link(r.get("link")) == key]
            if not matches:
                raise LookupError("That job isn't in the tracker")
            if len(matches) != 1:
                raise TrackerConflict("Multiple tracker rows match this job; no row was deleted")
            row = matches[0]
            write_sheet.delete_row(sid, row["row"], expected_link=key)
            self.invalidate()
        except TrackerConflict:
            raise
        except write_sheet.AmbiguousTrackerRowError as e:
            raise TrackerConflict(str(e)) from e
        except (LookupError, ValueError):
            raise
        except Exception as e:  # noqa: BLE001
            log.error("Tracker row delete failed", exc_info=True)
            raise TrackerError(_short(e)) from e
        log.info("Tracker row deleted", extra={"company": row["company"], "row": row["row"]})
        activity.log_event("tracker_deleted", f"Removed {row['title']} at {row['company']} from the tracker",
                           company=row["company"], title=row["title"], link=row["link"])
        return {"deleted": True}

    def delete_jobs(self, links):
        """Delete multiple uniquely matched tracker rows with one Sheets batch update."""
        if not isinstance(links, list) or not links:
            raise ValueError("at least one application is required")
        keys = [canonical_link(link) for link in links]
        if any(not key for key in keys):
            raise ValueError("every application must have a valid job link")
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate applications are not allowed")
        try:
            sid = self._lookup_sheet_id()
            if not sid:
                raise TrackerError("No tracker sheet is configured")
            outcomes = write_sheet.delete_rows(sid, keys)
        except Exception as e:  # noqa: BLE001 -- Google/GWS may fail for several reasons
            log.error("Tracker bulk delete failed", exc_info=True)
            raise TrackerError("The tracker could not complete the bulk delete") from e

        deleted = [outcome for outcome in outcomes if outcome["status"] == "deleted"]
        if deleted:
            self.invalidate()
        for outcome in deleted:
            log.info("Tracker row deleted", extra={"company": outcome["company"], "row": outcome["row"]})
            activity.log_event(
                "tracker_deleted",
                f"Removed {outcome['title']} at {outcome['company']} from the tracker",
                company=outcome["company"], title=outcome["title"], link=outcome["link"],
            )
        return outcomes

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
                    write_sheet.update_range(sid, f"{write_sheet.TRACKER_TAB}!E{existing['row']}", [[resume_path]])
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

    def set_suitability(self, link, suitability):
        if suitability not in SUITABILITIES:
            raise ValueError(f"suitability must be one of: {', '.join(SUITABILITIES)}")
        try:
            sid = self._lookup_sheet_id()
            if not sid:
                raise TrackerError("No tracker sheet is configured")
            write_sheet.ensure_extra_headers(sid)
            row = self._find(write_sheet.read_tracker(sid), link)
            if not row:
                raise LookupError("That job isn't in the tracker yet -- save it first")
            write_sheet.set_suitability(sid, row["row"], suitability)
            self.invalidate()
        except (LookupError, ValueError):
            raise
        except Exception as e:  # noqa: BLE001
            log.error("Tracker suitability update failed", exc_info=True, extra={"suitability": suitability})
            raise TrackerError(_short(e)) from e
        log.info("Tracker row suitability updated", extra={"company": row["company"], "row": row["row"],
                                                           "suitability": suitability})
        activity.log_event("suitability_changed", f"{row['title']} at {row['company']} marked {suitability}",
                           company=row["company"], title=row["title"], link=link or None,
                           suitability=suitability)
        return {"row": row["row"], "suitability": suitability}


tracker = Tracker()
