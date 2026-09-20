"""Append-only activity log (output/activity_log.jsonl) feeding the dashboard's Recent
Activity timeline. Pipeline scripts and dashboard actions call log_event(); it never
raises -- a logging failure must not break a pipeline run.
"""
import json
from datetime import datetime, timezone

import paths


def _log_path():
    return paths.OUTPUT_DIR / "activity_log.jsonl"


def log_event(kind, message, **data):
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "kind": kind, "message": message, **data}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:  # noqa: BLE001 -- best-effort by design
        pass


def read_events(limit=200):
    try:
        lines = _log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events
