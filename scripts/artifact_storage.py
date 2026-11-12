"""Select personal Google Drive for resume and pipeline artifact storage."""
import os

import google_auth


class ArtifactStorageError(RuntimeError):
    """Drive artifact storage is misconfigured or uses an unsupported identity."""


def backend(environ=None):
    env = os.environ if environ is None else environ
    selected = (env.get("ARTIFACT_STORAGE_BACKEND") or "").strip().lower()
    if selected and selected != "drive":
        raise ArtifactStorageError("ARTIFACT_STORAGE_BACKEND must be drive; GCS storage is not supported")
    try:
        google_auth.configure_environment(env)
    except google_auth.GoogleAuthError as exc:
        raise ArtifactStorageError(str(exc)) from exc
    return "drive"


def check_configuration(environ=None):
    env = os.environ if environ is None else environ
    backend(env)
    if not (env.get("google_drive_folder_id") or "").strip():
        raise ArtifactStorageError("google_drive_folder_id is required for personal Google Drive artifact storage")
