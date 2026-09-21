"""Persistence for dashboard-generated resumes: output/generated_resumes/<id>/.

    meta.json   job, JD analysis, match, provider, tracker link, per-version metadata
    v<n>.md     the markdown of each version (generated / regenerated / edited)
    v<n>.pdf|docx   exports, created on demand

Same local-artifact family as raw/scored/tailored_jobs.json -- not a second tracker.
The Google Sheet stays the system of record for application status.
"""
import json
import re
import threading
import uuid
from datetime import datetime, timezone

import logging_config as lc
import paths

log = lc.get_logger("resume")

_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_lock = threading.RLock()


def _root():
    return paths.OUTPUT_DIR / "generated_resumes"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dir(rid):
    if not _ID_RE.match(str(rid)):  # ids are ours; anything else is a traversal attempt
        raise KeyError(rid)
    return _root() / rid


def new_id():
    return uuid.uuid4().hex[:12]


def exists(rid):
    try:
        return (_dir(rid) / "meta.json").exists()
    except KeyError:
        return False


def get(rid, with_markdown=True):
    try:
        meta = json.loads((_dir(rid) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError(rid) from None
    if with_markdown:
        for v in meta["versions"]:
            try:
                v["markdown"] = (_dir(rid) / f"v{v['n']}.md").read_text(encoding="utf-8")
            except OSError:
                v["markdown"] = ""
    return meta


def _write_meta(rid, meta):
    d = _dir(rid)
    d.mkdir(parents=True, exist_ok=True)
    slim = {**meta, "versions": [{k: x for k, x in v.items() if k != "markdown"} for v in meta["versions"]]}
    (d / "meta.json").write_text(json.dumps(slim, indent=2), encoding="utf-8")


def create(*, source, job, analysis, match, provider, dedupe_key=None, master_version=None, user_id=None):
    rid = new_id()
    meta = {"id": rid, "source": source, "created_at": now_iso(), "updated_at": now_iso(),
            "job": job, "analysis": analysis, "match": match, "provider": provider,
            "dedupe_key": dedupe_key, "master_version": master_version, "user_id": user_id,
            "versions": [], "tracker": None}
    with _lock:
        _write_meta(rid, meta)
    log.info("Resume record created", extra={"resume_id": rid, "source": source})
    return rid


def add_version(rid, markdown, kind, validation, match=None, analysis=None):
    with _lock:
        meta = get(rid, with_markdown=False)
        n = len(meta["versions"]) + 1
        d = _dir(rid)
        (d / f"v{n}.md").write_text(markdown, encoding="utf-8")
        meta["versions"].append({"n": n, "kind": kind, "created_at": now_iso(), "validation": validation,
                                 "exports": {}})
        if match is not None:
            meta["match"] = match
        if analysis is not None:
            meta["analysis"] = analysis
        meta["updated_at"] = now_iso()
        _write_meta(rid, meta)
    log.info("Resume version stored", extra={"resume_id": rid, "version": n, "kind": kind,
                                              "verification_ok": bool((validation or {}).get("ok"))})
    return n


def update_meta(rid, **fields):
    with _lock:
        meta = get(rid, with_markdown=False)
        meta.update(fields)
        meta["updated_at"] = now_iso()
        _write_meta(rid, meta)
    return meta


def version_path(rid, n, ext="md"):
    return _dir(rid) / f"v{int(n)}.{ext}"


def mark_export(rid, n, fmt):
    with _lock:
        meta = get(rid, with_markdown=False)
        for v in meta["versions"]:
            if v["n"] == int(n):
                v["exports"][fmt] = f"v{int(n)}.{fmt}"
        _write_meta(rid, meta)


def mark_drive(rid, n, fmt, info):
    """Record a version's Google Drive outcome for one format: {status: uploaded|failed, ...}."""
    with _lock:
        meta = get(rid, with_markdown=False)
        for v in meta["versions"]:
            if v["n"] == int(n):
                v.setdefault("drive", {})[fmt] = info
        _write_meta(rid, meta)


def list_all():
    out = []
    root = _root()
    if not root.exists():
        return out
    for d in root.iterdir():
        if _ID_RE.match(d.name):
            try:
                out.append(get(d.name, with_markdown=False))
            except KeyError:
                continue
    return sorted(out, key=lambda m: m["created_at"], reverse=True)


def find_reusable(dedupe_key):
    """Newest stored resume built for this exact job + JD + master-resume version whose latest
    version passed verification, or None. Used to avoid regenerating (and re-spending LLM
    calls on) something that already exists; an explicit Regenerate bypasses this."""
    for meta in list_all():  # newest first
        if meta.get("dedupe_key") == dedupe_key and meta["versions"] and \
                meta["versions"][-1]["validation"].get("ok"):
            return meta
    return None
