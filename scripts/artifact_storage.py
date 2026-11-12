"""Private artifact storage for generated resumes and pipeline recovery snapshots."""
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import google_auth


class ArtifactStorageError(RuntimeError):
    """Artifact storage is misconfigured or unavailable."""


_KEY_PART_RE = re.compile(r"^[\w .+#%-]+$", re.UNICODE)
_TRUE = {"1", "true", "yes", "on"}


def _enabled(value, default=False):
    return str(value).strip().lower() in _TRUE if value is not None else default


def backend(environ=None):
    env = os.environ if environ is None else environ
    selected = (env.get("ARTIFACT_STORAGE_BACKEND") or "").strip().lower()
    if not selected:
        if google_auth.is_service_account(env):
            raise ArtifactStorageError("ARTIFACT_STORAGE_BACKEND must be set to gcs for service-account credentials")
        selected = "drive"
    if selected not in ("gcs", "drive"):
        raise ArtifactStorageError("ARTIFACT_STORAGE_BACKEND must be gcs or drive")
    if selected == "drive":
        if google_auth.is_service_account(env):
            raise ArtifactStorageError("Service-account credentials cannot use Drive artifact uploads; configure GCS")
        if not _enabled(env.get("HISTORICAL_DRIVE_ARTIFACTS"), True):
            raise ArtifactStorageError("Drive artifact compatibility is disabled")
    return selected


def bucket_name(environ=None):
    env = os.environ if environ is None else environ
    name = (env.get("GCS_BUCKET") or "").strip()
    if not name:
        raise ArtifactStorageError("GCS_BUCKET is not configured")
    return name


def public_base_url(environ=None):
    env = os.environ if environ is None else environ
    value = (env.get("ARTIFACT_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ArtifactStorageError("ARTIFACT_PUBLIC_BASE_URL must be an HTTPS dashboard origin")
    return value


def check_configuration(environ=None):
    env = os.environ if environ is None else environ
    if not google_auth.is_service_account(env):
        raise ArtifactStorageError("GCS requires explicit service-account credentials")
    if google_auth.credentials_path(env) is None:
        raise ArtifactStorageError("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE is required for GCS")
    bucket_name(env)
    public_base_url(env)


def _key(key):
    value = str(key)
    parts = value.split("/")
    if (value.startswith("/") or "\\" in value or
            any(part in ("", ".", "..") or not _KEY_PART_RE.fullmatch(part) for part in parts)):
        raise ArtifactStorageError("Invalid artifact object key")
    return value


def _client():
    if not google_auth.is_service_account():
        raise ArtifactStorageError("GCS requires explicit service-account credentials")
    credentials = google_auth.credentials_path()
    if credentials is None:
        raise ArtifactStorageError("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE is required for GCS")
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise ArtifactStorageError("GCS support is unavailable; install the project requirements") from exc
    try:
        return storage.Client.from_service_account_json(str(credentials))
    except Exception as exc:
        raise ArtifactStorageError(f"GCS client initialization failed ({type(exc).__name__})") from exc


def _blob(key):
    return _client().bucket(bucket_name()).blob(_key(key))


def check_bucket():
    """Read-only check that the configured identity can access the target bucket."""
    try:
        _client().get_bucket(bucket_name())
    except Exception as exc:
        if isinstance(exc, ArtifactStorageError):
            raise
        raise ArtifactStorageError(f"GCS bucket access failed ({type(exc).__name__})") from exc


def object_metadata(key):
    try:
        blob = _blob(key)
        if not blob.exists():
            return None
        blob.reload()
        return {"updated": blob.updated, "content_type": blob.content_type, "metadata": blob.metadata or {}}
    except ArtifactStorageError:
        raise
    except Exception as exc:
        raise ArtifactStorageError(f"GCS metadata lookup failed ({type(exc).__name__})") from exc


def upload_file(path, key, *, content_type=None, metadata=None):
    """Upload privately to GCS, retaining object name and metadata; return whether reused."""
    local_path = Path(path)
    if not local_path.is_file():
        raise ArtifactStorageError("Artifact file does not exist")
    try:
        blob = _blob(key)
        existed = blob.exists()
        blob.metadata = {str(k): str(v) for k, v in (metadata or {}).items()}
        blob.upload_from_filename(str(local_path), content_type=content_type or
                                  mimetypes.guess_type(local_path.name)[0] or "application/octet-stream")
        return {"key": _key(key), "reused": existed}
    except ArtifactStorageError:
        raise
    except Exception as exc:
        raise ArtifactStorageError(f"GCS artifact upload failed ({type(exc).__name__})") from exc


def download_bytes(key):
    try:
        return _blob(key).download_as_bytes()
    except ArtifactStorageError:
        raise
    except Exception as exc:
        raise ArtifactStorageError(f"GCS artifact download failed ({type(exc).__name__})") from exc


def public_url(key):
    """Return the authenticated dashboard proxy URL, never a public GCS object URL."""
    base = public_base_url()
    return f"{base}/api/artifacts/{quote(_key(key), safe='/')}"


def resume_object_key(resume_id, version, filename, folder="job"):
    if not re.fullmatch(r"[0-9a-f]{12}", str(resume_id)) or int(version) < 1:
        raise ArtifactStorageError("Invalid resume artifact identity")
    filename = str(filename)
    if ("/" in filename or "\\" in filename or
            not re.fullmatch(r"[^\x00-\x1f\x7f]+\.(?:pdf|docx)", filename, re.IGNORECASE)):
        raise ArtifactStorageError("Invalid resume artifact filename")
    safe_folder = re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9_-]+", "-", str(folder))).strip("-") or "job"
    return _key(f"generated_resumes/{resume_id}/{safe_folder}/v{int(version)}/{filename}")


def decode_resume_object_key(encoded_key):
    """Decode one authenticated dashboard URL key and validate its generated-resume shape."""
    if re.search(r"%(?![0-9A-Fa-f]{2})", str(encoded_key)):
        raise ArtifactStorageError("Invalid artifact object key")
    try:
        key = unquote(str(encoded_key), errors="strict")
    except (UnicodeError, ValueError):
        raise ArtifactStorageError("Invalid artifact object key") from None
    _key(key)
    if not re.fullmatch(
            r"generated_resumes/[0-9a-f]{12}/[A-Za-z0-9_-]+/v[1-9][0-9]*/[^/\\]+\.(?:pdf|docx)",
            key, re.IGNORECASE):
        raise ArtifactStorageError("Invalid resume artifact object key")
    return key


def pipeline_object_key(filename):
    return _key(f"pipeline-artifacts/{filename}")
