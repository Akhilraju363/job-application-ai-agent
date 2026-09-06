"""Best-effort Google Drive mirror for the pipeline's stage artifacts.

Why this exists: the daily Modal run and a local recovery run are different
machines. Modal's ``output/`` directory is thrown away when the container exits,
so a Modal run that fails partway leaves nothing on local disk for an Ollama
recovery run to resume from. This module mirrors the three stage artifacts --
``output/raw_jobs.json``, ``output/scored_jobs.json``, ``output/tailored_jobs.json``
-- to a ``pipeline-artifacts`` subfolder of the Drive folder in
``google_drive_folder_id``:

    pull(name): copy the Drive copy over output/<name> when Drive's copy is newer
                (or the local file is missing). Never clobbers local work that is
                ahead of Drive.
    push(name): upload output/<name>, replacing the Drive copy.

The Drive copy is date-stamped (``scored_jobs-2026-09-06.json``) so a fresh daily
run and a recovery of an earlier day never collide. The date defaults to today;
set ``PIPELINE_DATE=YYYY-MM-DD`` to recover a specific earlier day's run.

Everything here is best-effort. With ``google_drive_folder_id`` unset,
``artifact_sync=0``, or ``gws`` unusable (a local box with no Google auth), every
call prints one line to stderr and returns -- the pipeline still runs against
whatever is already in ``output/``.

Source-of-truth rule: when both the Drive copy and a local copy changed since the
last sync, the newer mtime wins. In this pipeline the only writer of these files
is the pipeline itself, so that resolves correctly.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
SIDECAR = OUTPUT_DIR / ".artifact_sync.json"
SUBFOLDER_NAME = "pipeline-artifacts"
ARTIFACT_NAMES = ("raw_jobs.json", "scored_jobs.json", "tailored_jobs.json")

_PARENT_ID = os.environ.get("google_drive_folder_id", "").strip()
_DISABLED = os.environ.get("artifact_sync", "").strip().lower() in ("0", "false", "off", "no")
_RUN_DATE = os.environ.get("PIPELINE_DATE", "").strip() or date.today().isoformat()
_PULL_GRACE_SECONDS = 2


def _resolve_gws():
    # Mirrors format_resume_doc.py's _resolve_gws(): on Windows the .cmd shim breaks
    # subprocess arg handling, so call node against the CLI's run.js directly.
    if os.name != "nt":
        return ["gws"]
    gws_cmd = shutil.which("gws.cmd")
    if gws_cmd:
        run_js = os.path.join(os.path.dirname(gws_cmd), "node_modules",
                              "@googleworkspace", "cli", "run.js")
        if os.path.exists(run_js):
            return ["node", run_js]
    return ["gws"]


_GWS = _resolve_gws()
_state = {"checked": False, "folder_id": None, "file_ids": {}}


def _run(args, cwd=None):
    result = subprocess.run([*_GWS, *args], capture_output=True, text=True,
                            encoding="utf-8", cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"gws {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _json(args, cwd=None):
    out = _run(args, cwd=cwd)
    return json.loads(out) if out.strip() else {}


def _remote_name(name):
    stem, _, suffix = name.rpartition(".")
    return f"{stem}-{_RUN_DATE}.{suffix}"


def _parse_rfc3339(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_sidecar():
    try:
        return json.loads(SIDECAR.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_sidecar(data):
    try:
        OUTPUT_DIR.mkdir(exist_ok=True)
        SIDECAR.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _warn(msg):
    print(f"  [artifacts] {msg}", file=sys.stderr)


def _folder_id():
    """Resolve (creating once) the pipeline-artifacts subfolder id, or None if the
    Drive sync is unavailable. Cached for the life of the process."""
    if _state["checked"]:
        return _state["folder_id"]
    _state["checked"] = True
    if _DISABLED or not _PARENT_ID:
        return None
    try:
        query = (f"name = '{SUBFOLDER_NAME}' and '{_PARENT_ID}' in parents and "
                 "mimeType = 'application/vnd.google-apps.folder' and trashed = false")
        found = _json(["drive", "files", "list", "--params",
                       json.dumps({"q": query, "fields": "files(id)"})]).get("files", [])
        if found:
            _state["folder_id"] = found[0]["id"]
        else:
            _state["folder_id"] = _json(["drive", "files", "create", "--params",
                                         json.dumps({"fields": "id"}), "--json",
                                         json.dumps({"name": SUBFOLDER_NAME,
                                                     "mimeType": "application/vnd.google-apps.folder",
                                                     "parents": [_PARENT_ID]})])["id"]
    except Exception as e:  # noqa: BLE001 -- any gws/auth/network failure disables sync
        _warn(f"Drive sync unavailable ({type(e).__name__}) -- using local output/ only")
        _state["folder_id"] = None
    return _state["folder_id"]


def _remote_meta(name):
    folder_id = _folder_id()
    if not folder_id:
        return None
    remote_name = _remote_name(name)
    query = f"name = '{remote_name}' and '{folder_id}' in parents and trashed = false"
    files = _json(["drive", "files", "list", "--params",
                   json.dumps({"q": query, "fields": "files(id,modifiedTime)",
                               "orderBy": "modifiedTime desc"})]).get("files", [])
    if not files:
        return None
    _state["file_ids"][name] = files[0]["id"]
    return files[0]


def pull(name):
    """Best-effort: refresh output/<name> from the Drive copy when Drive is ahead."""
    try:
        meta = _remote_meta(name)
    except Exception as e:  # noqa: BLE001
        _warn(f"pull {name} skipped ({type(e).__name__})")
        return
    if not meta:
        return

    sidecar = _load_sidecar()
    if sidecar.get(name) == meta["modifiedTime"]:
        return  # already in sync on this machine

    local = OUTPUT_DIR / name
    if local.exists() and name not in sidecar:
        # Never synced this file here and it already exists locally: only overwrite
        # if the Drive copy is genuinely newer (protects local-only progress).
        local_dt = datetime.fromtimestamp(local.stat().st_mtime, tz=timezone.utc)
        remote_dt = _parse_rfc3339(meta["modifiedTime"])
        if (remote_dt - local_dt).total_seconds() < _PULL_GRACE_SECONDS:
            return

    try:
        content = _run(["drive", "files", "get", "--params",
                        json.dumps({"fileId": meta["id"], "alt": "media"})])
        OUTPUT_DIR.mkdir(exist_ok=True)
        local.write_text(content, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        _warn(f"pull {name} download failed ({type(e).__name__})")
        return

    sidecar[name] = meta["modifiedTime"]
    _save_sidecar(sidecar)
    _warn(f"pulled {name} from Drive ({_remote_name(name)})")


def push(name):
    """Best-effort: upload output/<name>, replacing the date-stamped Drive copy."""
    local = OUTPUT_DIR / name
    if not local.exists():
        return
    folder_id = _folder_id()
    if not folder_id:
        return

    remote_name = _remote_name(name)
    try:
        file_id = _state["file_ids"].get(name)
        if not file_id:
            meta = _remote_meta(name)
            file_id = meta["id"] if meta else None
        if file_id:
            result = _json(["drive", "files", "update", "--params",
                            json.dumps({"fileId": file_id, "fields": "id,modifiedTime"}),
                            "--upload", name, "--upload-content-type", "application/json"],
                           cwd=OUTPUT_DIR)
        else:
            result = _json(["drive", "files", "create", "--params",
                            json.dumps({"fields": "id,modifiedTime"}), "--json",
                            json.dumps({"name": remote_name, "parents": [folder_id]}),
                            "--upload", name, "--upload-content-type", "application/json"],
                           cwd=OUTPUT_DIR)
        _state["file_ids"][name] = result["id"]
    except Exception as e:  # noqa: BLE001
        _warn(f"push {name} failed ({type(e).__name__})")
        return

    sidecar = _load_sidecar()
    sidecar[name] = result["modifiedTime"]
    _save_sidecar(sidecar)
    try:
        # Align local mtime with Drive's so the next pull() sees no phantom diff.
        ts = _parse_rfc3339(result["modifiedTime"]).timestamp()
        os.utime(local, (ts, ts))
    except OSError:
        pass
    _warn(f"pushed {name} to Drive ({remote_name})")
