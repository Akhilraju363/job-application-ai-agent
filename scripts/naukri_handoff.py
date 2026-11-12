"""Validated Naukri handoff import and personal Google Drive storage.

The shared handoff lives in a dedicated folder beneath the configured personal Drive
folder. The GWS CLI uses the existing authorized-user OAuth setup.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import google_auth

FOLDER_NAME = "naukri-handoff"
FILE_NAME = "naukri_jobs.json"
REQUIRED = ("title", "company", "link", "description")


class HandoffError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def validate_jobs(value):
    if not isinstance(value, list):
        raise HandoffError("Handoff JSON must be a top-level list")
    if not value:
        raise HandoffError("Handoff file must contain at least one job")
    errors = []
    for i, job in enumerate(value):
        missing = [key for key in REQUIRED if not isinstance(job, dict) or not str(job.get(key) or "").strip()]
        if missing:
            errors.append({"record": i + 1, "missing": missing})
    if errors:
        raise HandoffError("Some job records are missing required fields", {"invalid_records": errors})
    return value


def parse_json(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HandoffError("Upload must contain valid JSON") from None
    return validate_jobs(value)


def _gws(args, cwd=None):
    exe = shutil.which("gws")
    if not exe and os.name == "nt":
        cmd = shutil.which("gws.cmd")
        run_js = Path(cmd).parent / "node_modules/@googleworkspace/cli/run.js" if cmd else None
        command = ["node", str(run_js)] if run_js and run_js.exists() else ["gws"]
    else:
        command = [exe or "gws"]
    try:
        result = subprocess.run([*command, *args], capture_output=True, text=True, encoding="utf-8",
                                cwd=cwd, env=google_auth.configure_environment())
    except Exception as exc:
        raise HandoffError("Google Drive operation failed", {"reason": type(exc).__name__}) from None
    if result.returncode:
        raise HandoffError("Google Drive operation failed", {"reason": google_auth.safe_gws_error(result.stderr or result.stdout)})
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except ValueError:
        raise HandoffError("Google Drive returned an invalid response") from None


def _parent():
    return os.environ.get("google_drive_folder_id", "").strip()


def _find(name, parent, mime=None):
    query = f"name = '{name}' and '{parent}' in parents and trashed = false"
    if mime:
        query += f" and mimeType = '{mime}'"
    result = _gws(["drive", "files", "list", "--params", json.dumps({
        "q": query, "fields": "files(id,name,mimeType,modifiedTime,size,appProperties)",
        "orderBy": "modifiedTime desc", "supportsAllDrives": True,
        "includeItemsFromAllDrives": True, "corpora": "allDrives"})])
    return result.get("files", [])


def _folder(create=False):
    parent = _parent()
    if not parent:
        return None
    folders = _find(FOLDER_NAME, parent, "application/vnd.google-apps.folder")
    if folders:
        return folders[0]["id"]
    if not create:
        return None
    return _gws(["drive", "files", "create", "--params", json.dumps({"fields": "id"}), "--json",
                 json.dumps({"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder", "parents": [parent]})])["id"]


def status():
    if not _parent():
        return {"status": "not_configured", "label": "Not configured", "last_successful_import": None}
    try:
        folder = _folder()
        if not folder:
            return {"status": "file_missing", "label": "File missing", "last_successful_import": None}
        files = _find(FILE_NAME, folder)
        if not files:
            return {"status": "file_missing", "label": "File missing", "last_successful_import": None}
        meta = files[0]
        raw = _gws(["drive", "files", "get", "--params", json.dumps({"fileId": meta["id"], "alt": "media"})])
        validate_jobs(raw)
        props = meta.get("appProperties", {})
        return {"status": "ready", "label": "Ready", "job_count": len(raw),
                "last_successful_import": props.get("lastSuccessfulImport") or meta.get("modifiedTime")}
    except HandoffError as exc:
        if exc.details and "invalid_records" not in exc.details:
            return {"status": "unavailable", "label": "Drive unavailable", "last_successful_import": None}
        return {"status": "invalid_json", "label": "Invalid JSON", "last_successful_import": None}
    except Exception:  # Drive/auth configuration failures are private and non-fatal to LinkedIn.
        return {"status": "unavailable", "label": "Drive unavailable", "last_successful_import": None}


def retrieve_jobs():
    """Return latest valid Drive handoff, or [] for missing/unavailable data."""
    try:
        folder = _folder()
        if not folder:
            return []
        files = _find(FILE_NAME, folder)
        if not files:
            return []
        raw = _gws(["drive", "files", "get", "--params", json.dumps({"fileId": files[0]["id"], "alt": "media"})])
        return validate_jobs(raw)
    except Exception:
        return []


def import_jobs(jobs):
    jobs = validate_jobs(jobs)
    folder = _folder(create=True)
    if not folder:
        raise HandoffError("Google Drive is not configured")
    existing = _find(FILE_NAME, folder)
    payload = json.dumps(jobs, ensure_ascii=False, indent=2)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    props = {"sha256": digest, "lastSuccessfulImport": stamp}
    with tempfile.TemporaryDirectory(prefix="naukri-handoff-") as tmp:
        path = Path(tmp) / FILE_NAME
        path.write_text(payload, encoding="utf-8")
        if existing:
            prior = existing[0]
            if prior.get("appProperties", {}).get("sha256") == digest:
                return {"status": "ready", "job_count": len(jobs), "unchanged": True,
                        "last_successful_import": prior.get("appProperties", {}).get("lastSuccessfulImport")}
            result = _gws(["drive", "files", "update", "--params", json.dumps({
                "fileId": prior["id"], "fields": "id,modifiedTime,appProperties",
                "supportsAllDrives": True}), "--json", json.dumps({"appProperties": props}),
                "--upload", FILE_NAME, "--upload-content-type", "application/json"], cwd=tmp)
        else:
            result = _gws(["drive", "files", "create", "--params", json.dumps({
                "fields": "id,modifiedTime,appProperties", "supportsAllDrives": True}), "--json",
                json.dumps({"name": FILE_NAME, "parents": [folder], "appProperties": props}),
                "--upload", FILE_NAME, "--upload-content-type", "application/json"], cwd=tmp)
    return {"status": "ready", "job_count": len(jobs), "unchanged": False,
            "last_successful_import": result.get("appProperties", {}).get("lastSuccessfulImport") or stamp}
