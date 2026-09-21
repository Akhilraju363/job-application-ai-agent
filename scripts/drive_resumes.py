"""Persist a generated resume's PDF/DOCX in Google Drive and hand back a shareable link.

The dashboard writes resumes to output/generated_resumes/<id>/v<n>.<fmt> -- a local cache that
means nothing off this machine. The tracker Sheet must link to Drive instead, so every
exported resume is uploaded here (through the same `gws` CLI + auth as the rest of the
project; no second auth system, no credentials handled in this module).

    <google_drive_folder_id>/                  the existing "Job Applications" parent
        {company}-{job-title-slug}/            same per-job folder name the pipeline uses
            {resume_id}-v{n}.pdf | .docx

Idempotent: a file's identity is (resume_id, version, format), stored in the Drive file's
appProperties and looked up before any upload -- independent of the folder -- so a retried
export, a retried Sheet write, or a re-run Modal job reuses the existing file instead of
creating a copy. Failures raise DriveUploadError; nothing here ever fabricates a link.
"""
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone

import logging_config as lc
import resume_store
import write_sheet  # reuses its gws resolution (Windows .cmd-shim workaround)

log = lc.get_logger("drive")

FOLDER_MIME = "application/vnd.google-apps.folder"
MIME = {"pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
KEY_PROP = "resume_key"
ATTEMPTS = 2
RETRY_DELAY_SECONDS = 2
AUTH_HINT = "Re-authenticate with `gws auth login -s drive,docs,sheets` (see GWS_SETUP.md)."
_AUTH_WORDS = re.compile(r"auth|credential|invalid_grant|unauthenticated|\b401\b|\b403\b|sign(ed)? in|login|token", re.I)

_lock = threading.Lock()  # one upload at a time: two threads must not both miss the lookup and both create


class DriveUploadError(RuntimeError):
    """The resume could not be placed in Drive. The message is safe to show the user."""


def _gws(args, cwd=None):
    result = subprocess.run([*write_sheet.GWS_CMD, *args], capture_output=True, text=True,
                            encoding="utf-8", cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "gws failed").strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


def _explain(exc):
    first = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:200]
    hint = f" {AUTH_HINT}" if _AUTH_WORDS.search(str(exc)) else ""
    return f"Google Drive upload failed: {first}.{hint}"


def _q(value):
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _slug(text):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(text).lower())).strip("-") or "job"


def resume_key(rid, n, fmt):
    return f"{rid}-v{int(n)}-{fmt}"


def drive_file_name(rid, n, fmt):
    return f"{rid}-v{int(n)}.{fmt}"


def folder_name(job):
    company = re.sub(r"[/\\\x00-\x1f]+", " ", str(job.get("company") or "")).strip() or "Company Not Specified"
    return f"{company}-{_slug(job.get('title'))}"


def _parent_id():
    pid = os.environ.get("google_drive_folder_id", "").strip()
    if not pid:
        raise DriveUploadError("Google Drive upload failed: google_drive_folder_id is not set "
                               "(the 'Job Applications' parent folder id; see README).")
    return pid


def _link(file):
    """Google's own canonical link when it gives one, else the standard /view form."""
    return file.get("webViewLink") or f"https://drive.google.com/file/d/{file['id']}/view"


def _ensure_folder(name, parent):
    found = _gws(["drive", "files", "list", "--params", json.dumps({
        "q": f"name = '{_q(name)}' and '{_q(parent)}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false",
        "orderBy": "createdTime", "fields": "files(id)", "pageSize": 1})]).get("files", [])
    if found:
        return found[0]["id"]
    return _gws(["drive", "files", "create", "--params", json.dumps({"fields": "id"}), "--json",
                 json.dumps({"name": name, "mimeType": FOLDER_MIME, "parents": [parent]})])["id"]


def _find_existing(key):
    files = _gws(["drive", "files", "list", "--params", json.dumps({
        "q": f"appProperties has {{ key='{KEY_PROP}' and value='{_q(key)}' }} and trashed = false",
        "orderBy": "createdTime", "fields": "files(id,name,webViewLink,parents)", "pageSize": 5})]).get("files", [])
    return files[0] if files else None  # oldest wins if a past race ever left two


def upload_resume(rid, n, fmt, job=None):
    """Upload output/generated_resumes/<rid>/v<n>.<fmt> to Drive, or reuse the file already
    uploaded for (rid, n, fmt). Returns {file_id, url, name, folder_id, reused}."""
    ctx = {"resume_id": rid, "version": int(n), "format": fmt}
    if fmt not in MIME:
        log.error("Drive upload rejected: unsupported format", extra=ctx)
        raise DriveUploadError(f"Unsupported resume format for Drive: {fmt}")
    path = resume_store.version_path(rid, n, fmt)
    if not path.exists():
        log.error("Drive upload rejected: local file not generated yet", extra=ctx)
        raise DriveUploadError(f"Google Drive upload failed: the {fmt.upper()} hasn't been generated locally yet.")
    job = job or resume_store.get(rid, with_markdown=False)["job"]
    key, name = resume_key(rid, n, fmt), drive_file_name(rid, n, fmt)

    log.info("Drive upload started", extra=ctx)
    with _lock:
        last = None
        for attempt in range(1, ATTEMPTS + 1):
            try:
                # Look first on every attempt: a create that "failed" may have landed on Drive.
                existing = _find_existing(key)
                if existing:
                    log.info("Drive existing file reused", extra={**ctx, "drive_file_id": existing["id"]})
                    return {"file_id": existing["id"], "url": _link(existing), "name": existing.get("name", name),
                            "folder_id": (existing.get("parents") or [None])[0], "reused": True}
                folder_id = _ensure_folder(folder_name(job), _parent_id())
                created = _gws(["drive", "files", "create", "--params",
                                json.dumps({"fields": "id,name,webViewLink,parents"}), "--json",
                                json.dumps({"name": name, "parents": [folder_id], "appProperties": {KEY_PROP: key}}),
                                "--upload", path.name, "--upload-content-type", MIME[fmt]],
                               cwd=str(path.parent.resolve()))
                if not created.get("id"):
                    raise RuntimeError("Drive returned no file id")
                log.info("Drive upload completed", extra={**ctx, "drive_file_id": created["id"], "attempt": attempt})
                return {"file_id": created["id"], "url": _link(created), "name": name, "folder_id": folder_id,
                        "reused": False}
            except DriveUploadError as e:
                log.error("Drive upload failed", extra={**ctx, "reason": str(e)[:200]})
                raise
            except Exception as e:  # noqa: BLE001 -- any gws/auth/network failure
                last = e
                if _AUTH_WORDS.search(str(e)) or attempt == ATTEMPTS:
                    break  # re-trying an expired login only repeats the failure
                log.warning("Drive upload retry", extra={**ctx, "attempt": attempt, "reason": type(e).__name__})
                time.sleep(RETRY_DELAY_SECONDS)
        log.error("Drive upload failed", exc_info=last, extra={**ctx, "attempts": attempt})
        raise DriveUploadError(_explain(last)) from last


def upload_and_record(rid, n, fmt):
    """upload_resume + remember the outcome on the resume (success or failure) so the UI and the
    tracker can tell 'in Drive' from 'not in Drive'. Re-raises DriveUploadError."""
    try:
        out = upload_resume(rid, n, fmt)
    except DriveUploadError as e:
        resume_store.mark_drive(rid, n, fmt, {"status": "failed", "error": str(e), "at": _now()})
        raise
    resume_store.mark_drive(rid, n, fmt, {"status": "uploaded", "file_id": out["file_id"], "url": out["url"],
                                          "name": out["name"], "at": _now()})
    return out


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
