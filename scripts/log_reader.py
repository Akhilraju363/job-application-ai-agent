"""Bounded, read-only access to the JSON-lines application logs for the dashboard's /api/logs.

Safety properties (this is reachable from the browser, so they are enforced here, not trusted
to the caller):
  * only files named application.log / errors.log (+ their numbered rotations) inside the log
    directory are ever opened -- there is no path, file name or glob parameter to abuse;
  * every filter value is validated against a strict pattern before use;
  * files are read newest-first from the end in fixed-size blocks and the scan stops at
    MAX_SCAN_BYTES, so a huge log is never loaded into memory;
  * entries are re-redacted on the way out.
"""
import json
import re
from pathlib import Path

from logging_config import COMPONENTS, LEVELS, redact

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
MAX_OFFSET = 100_000
MAX_SCAN_BYTES = 32 * 1024 * 1024
_BLOCK = 64 * 1024
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_COMPONENT = re.compile(r"^[a-z_]+(\.[a-z_]+)*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_FIELDS = ("request_id", "task_id", "resume_id", "job_id")


class LogQueryError(ValueError):
    """A bad filter value -- a 400, not a server fault."""


def _bounded_int(value, default, lo, hi, name):
    if value in (None, ""):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise LogQueryError(f"{name} must be a whole number") from None
    return max(lo, min(hi, n))


def parse_filters(params):
    """Validate the raw query values (each a str or None) into a filter dict."""
    level = (params.get("level") or "").strip().upper() or None
    if level and level not in LEVELS:
        raise LogQueryError(f"level must be one of: {', '.join(LEVELS)}")
    component = (params.get("component") or "").strip().lower() or None
    if component and not _COMPONENT.match(component):
        raise LogQueryError("component must be a logger name such as tailoring or dashboard.tasks")
    date = (params.get("date") or "").strip() or None
    if date and not _DATE.match(date):
        raise LogQueryError("date must be YYYY-MM-DD")
    filters = {"level": level, "component": component, "date": date}
    for name in ID_FIELDS:
        value = (params.get(name) or "").strip() or None
        if value and not _ID.match(value):
            raise LogQueryError(f"{name} contains characters that are not allowed")
        filters[name] = value
    filters["limit"] = _bounded_int(params.get("limit"), DEFAULT_LIMIT, 1, MAX_LIMIT, "limit")
    filters["offset"] = _bounded_int(params.get("offset"), 0, 0, MAX_OFFSET, "offset")
    return filters


def _files(log_dir, base):
    """base, base.1, base.2 ... newest first. Only names this module builds are opened."""
    found = []
    for i in range(0, 50):
        p = Path(log_dir) / (base if i == 0 else f"{base}.{i}")
        if not p.is_file():
            if i == 0:
                continue
            break
        found.append(p)
    return found


def _reverse_lines(path, budget):
    """Yield the lines of a file newest-first, reading from the end; charges `budget` (a
    one-item list of remaining bytes) and stops when it is spent."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        pos, tail = f.tell(), b""
        while pos > 0 and budget[0] > 0:
            step = min(_BLOCK, pos)
            pos -= step
            f.seek(pos)
            chunk = f.read(step)
            budget[0] -= step
            parts = (chunk + tail).split(b"\n")
            tail = parts[0]
            for line in reversed(parts[1:]):
                if line.strip():
                    yield line
        if pos == 0 and tail.strip():
            yield tail


def _matches(entry, f):
    level = entry.get("level")
    if f["level"]:
        wanted = ("ERROR", "CRITICAL") if f["level"] == "ERROR" else (f["level"],)
        if level not in wanted:
            return False
    if f["component"]:
        name = str(entry.get("component") or "")
        if name != f["component"] and not name.startswith(f["component"] + "."):
            return False
    if f["date"] and not str(entry.get("timestamp") or "").startswith(f["date"]):
        return False
    return all(not f[k] or entry.get(k) == f[k] for k in ID_FIELDS)


def _public(entry):
    out = {k: (redact(v) if isinstance(v, str) else v) for k, v in entry.items()}
    out.setdefault("level", "INFO")
    return out


def query(log_dir, filters):
    """Newest-first page of matching entries: {entries, limit, offset, has_more, truncated}."""
    limit, offset = filters["limit"], filters["offset"]
    want = offset + limit + 1  # one extra row tells us whether another page exists
    base = "application.log"
    if filters["level"] in ("ERROR", "CRITICAL") and (Path(log_dir) / "errors.log").is_file():
        base = "errors.log"  # far smaller than application.log, and holds every ERROR/CRITICAL
    budget, matched, truncated = [MAX_SCAN_BYTES], [], False
    for path in _files(log_dir, base):
        try:
            for raw in _reverse_lines(path, budget):
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(entry, dict) and _matches(entry, filters):
                    matched.append(entry)
                    if len(matched) >= want:
                        break
        except FileNotFoundError:
            continue  # rotated away between listing and opening
        if len(matched) >= want:
            break
        if budget[0] <= 0:
            truncated = True
            break
    page = matched[offset:offset + limit]
    return {"entries": [_public(e) for e in page], "limit": limit, "offset": offset,
            "has_more": len(matched) > offset + limit, "truncated": truncated and len(matched) < want,
            "components": list(COMPONENTS)}
