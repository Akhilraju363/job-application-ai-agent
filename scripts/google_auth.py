"""Explicit Google Workspace CLI credential selection and read-only health checks.

The CLI is the existing transport for Sheets, Drive and Docs. When a credential file is
configured, isolate its cache/config directory so stale OAuth credentials cannot win over it.
With no explicit file, the user's existing GWS OAuth setup remains the migration mode.
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


ENV_CREDENTIALS = "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"
SERVICE_ACCOUNT_JSON_ENV = "GOOGLE_SERVICE_ACCOUNT_JSON"
_CONFIG_DIR_ENV = "GOOGLE_WORKSPACE_CLI_CONFIG_DIR"
_KNOWN_TYPES = {"service_account", "authorized_user"}
_ISOLATED_CONFIG_DIR = None


class GoogleAuthError(RuntimeError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


def configure_environment(env=None):
    """Validate explicit credentials and return an environment for one GWS subprocess.

    Existing OAuth credentials are retained when no explicit credential path is selected.
    An explicit path always gets an isolated config directory to avoid v0.22.5 cache precedence.
    """
    global _ISOLATED_CONFIG_DIR
    env = dict(os.environ if env is None else env)
    path = (env.get(ENV_CREDENTIALS) or "").strip()
    auth_mode = (env.get("GOOGLE_AUTH_MODE") or "").strip().lower()
    if auth_mode == "service_account" and not path:
        raise GoogleAuthError("missing_credentials", "Service-account credential file is not configured")
    if not path:
        return env
    credential_path = Path(path).expanduser()
    if not credential_path.is_file():
        raise GoogleAuthError("missing_credentials", "Configured Google credential file does not exist")
    try:
        data = json.loads(credential_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise GoogleAuthError("invalid_credentials", "Configured Google credential file is not valid JSON") from None
    kind = data.get("type") if isinstance(data, dict) else None
    if kind not in _KNOWN_TYPES:
        raise GoogleAuthError("invalid_credentials", "Google credential type must be service_account or authorized_user")
    if auth_mode and auth_mode != kind:
        raise GoogleAuthError("invalid_credentials", "GOOGLE_AUTH_MODE does not match the selected credential type")
    if kind == "service_account" and not all(isinstance(data.get(k), str) and data[k].strip()
                                               for k in ("client_email", "private_key", "token_uri")):
        raise GoogleAuthError("invalid_credentials", "Service-account credential JSON is missing required fields")
    if kind == "authorized_user" and not all(isinstance(data.get(k), str) and data[k].strip()
                                               for k in ("client_id", "client_secret", "refresh_token")):
        raise GoogleAuthError("invalid_credentials", "OAuth credential JSON is missing required fields")
    env[ENV_CREDENTIALS] = str(credential_path.resolve())
    env["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] = "file"
    # Keep old encrypted credentials and token cache out of the selected auth path.
    if not env.get(_CONFIG_DIR_ENV):
        if _ISOLATED_CONFIG_DIR is None:
            _ISOLATED_CONFIG_DIR = tempfile.mkdtemp(prefix="gws-explicit-auth-")
        env[_CONFIG_DIR_ENV] = _ISOLATED_CONFIG_DIR
    env.pop("GOOGLE_WORKSPACE_CLI_TOKEN", None)
    return env


def is_service_account(env=None):
    """Identify explicit service-account selection without exposing credential content."""
    env = os.environ if env is None else env
    mode = (env.get("GOOGLE_AUTH_MODE") or "").strip().lower()
    if mode:
        return mode == "service_account"
    if (env.get(SERVICE_ACCOUNT_JSON_ENV) or "").strip():
        return True
    path = (env.get(ENV_CREDENTIALS) or "").strip()
    if not path:
        return False
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("type") == "service_account"


def credentials_path(env=None):
    """Return the validated explicit credential file, or None when not configured."""
    configured = configure_environment(env)
    path = (configured.get(ENV_CREDENTIALS) or "").strip()
    return Path(path) if path else None


def classify_error(message):
    text = str(message).lower()
    if "not configured" in text or "required for gcs" in text:
        return "missing_configuration"
    if "no credentials" in text or "not signed in" in text or "run `gws auth login`" in text:
        return "missing_credentials"
    if "insufficient authentication scopes" in text or "insufficientpermissions" in text or "insufficient scopes" in text:
        return "insufficient_scopes"
    if "api has not been used" in text or "api is disabled" in text or "service_disabled" in text:
        return "api_not_enabled"
    if "403" in text or "permission" in text or "forbidden" in text:
        return "permission_denied"
    if "invalid_grant" in text or "failed to get token" in text or "unauthenticated" in text or "401" in text:
        return "invalid_credentials"
    if any(s in text for s in ("timed out", "timeout", "network", "connection", "dns")):
        return "network_error"
    return "storage_unavailable"


def safe_gws_error(exc):
    """Return a short redacted CLI diagnostic suitable for dashboard/API responses."""
    message = str(exc)
    message = re.sub(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", "[redacted private key]",
                     message, flags=re.IGNORECASE | re.DOTALL)
    message = re.sub(r"(?i)\bBearer\s+[^\s,}\"]+", "Bearer [redacted]", message)
    message = re.sub(
        r'(?i)(["\']?(?:private[_-]?key|client[_-]?(?:secret|id)|refresh[_-]?token|'
        r'access[_-]?token|id[_-]?token|api[_-]?key|token)'
        r'["\']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|\'[^\']*\'|[^\s,}\]]+)',
        r"\1[redacted]", message)
    message = re.sub(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
                     "[redacted token]", message)
    return message.splitlines()[0][:240]


def actionable_error(category):
    return {
        "missing_credentials": "Set GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE or complete the existing GWS OAuth login.",
        "missing_configuration": "Configure the selected artifact backend, bucket, and HTTPS dashboard URL.",
        "unsupported_storage": "Service accounts cannot create files in personal My Drive; use private GCS for new artifacts or explicitly selected OAuth Drive compatibility.",
        "permission_denied": "Grant this identity access to the configured spreadsheet and, in GCS mode, the private bucket.",
        "insufficient_scopes": "Refresh OAuth credentials with Sheets and Drive scopes, or select service-account credentials.",
        "api_not_enabled": "Enable the Google Sheets or Drive API in the credential project's Google Cloud project.",
        "invalid_credentials": "Google credentials could not be used; verify the selected credential configuration and rotate if needed.",
        "network_error": "Google could not be reached; check network access and retry.",
        "storage_unavailable": "Google storage could not complete the read-only check; inspect local service logs.",
    }.get(category, "Google service is unavailable; check its configuration and permissions.")


def health_check(run_gws, environ=None, check_gcs=None):
    """Read-only checks for the configured tracker and selected artifact storage."""
    env = os.environ if environ is None else environ
    result = {
        "sheets": {"status": "unknown", "category": None, "message": None},
        "drive": {"status": "not_required", "category": None, "message": "Historical Drive links remain direct links"},
        "artifact_storage": {"status": "unknown", "category": None, "message": None},
        "interviews": {"status": "unknown", "category": None, "message": None},
    }
    try:
        configure_environment(env)
    except GoogleAuthError as exc:
        for key in result:
            result[key] = {"status": "unavailable", "category": exc.category, "message": str(exc)}
        return result
    sid = (env.get("google_sheet_id") or "").strip()
    if not sid:
        result["sheets"] = {"status": "unavailable", "category": "missing_configuration",
                            "message": "google_sheet_id is not configured"}
        result["interviews"] = dict(result["sheets"])
    else:
        try:
            run_gws("sheets", "spreadsheets", "get", "--params",
                    json.dumps({"spreadsheetId": sid, "fields": "spreadsheetId"}))
            run_gws("sheets", "+read", "--spreadsheet", sid, "--range", "Jobs!A1:M1")
            result["sheets"] = {"status": "available", "category": None,
                                "message": "Tracker spreadsheet and Jobs header range are readable"}
            try:
                run_gws("sheets", "+read", "--spreadsheet", sid, "--range", "Interviews!A1:U1")
                result["interviews"] = {"status": "available", "category": None,
                                        "message": "Interview worksheet is readable"}
            except Exception as exc:
                result["interviews"] = {"status": "unavailable", "category": classify_error(exc),
                                        "message": "Interview worksheet is missing or unavailable in the tracker spreadsheet."}
        except Exception as exc:
            category = classify_error(exc)
            msg = actionable_error(category)
            result["sheets"] = {"status": "unavailable", "category": category, "message": msg}
            result["interviews"] = {"status": "unavailable", "category": category, "message": msg}

    try:
        import artifact_storage
        storage_backend = artifact_storage.backend(env)
    except Exception as exc:
        result["artifact_storage"] = {
            "status": "unavailable", "category": getattr(exc, "category", "missing_configuration"),
            "message": str(exc)}
        return result

    if storage_backend == "gcs":
        try:
            artifact_storage.check_configuration(env)
            (check_gcs or artifact_storage.check_bucket)()
            result["artifact_storage"] = {"status": "available", "category": None,
                                          "message": "Configured GCS bucket is readable"}
        except Exception as exc:
            category = classify_error(exc)
            result["artifact_storage"] = {"status": "unavailable", "category": category,
                                          "message": actionable_error(category)}
    else:
        parent = (env.get("google_drive_folder_id") or "").strip()
        if not parent:
            result["drive"] = result["artifact_storage"] = {
                "status": "unavailable", "category": "missing_configuration",
                "message": "google_drive_folder_id is required for the legacy Drive backend"}
        else:
            try:
                run_gws("drive", "files", "get", "--params", json.dumps({
                    "fileId": parent, "fields": "id,mimeType", "supportsAllDrives": True}))
                result["drive"] = {"status": "available", "category": None,
                                   "message": "Configured legacy Drive parent is readable"}
                result["artifact_storage"] = {"status": "available", "category": None,
                                              "message": "Legacy Drive artifact storage is readable"}
            except Exception as exc:
                category = classify_error(exc)
                msg = actionable_error(category)
                result["drive"] = result["artifact_storage"] = {
                    "status": "unavailable", "category": category, "message": msg}
    return result


def main():
    """Run a credential-safe, read-only preflight for the configured Google services."""
    import write_sheet

    result = health_check(write_sheet.gws)
    print(json.dumps(result, indent=2))
    return 0 if (result["sheets"]["status"] == "available" and
                 result["artifact_storage"]["status"] == "available") else 1


if __name__ == "__main__":
    raise SystemExit(main())
