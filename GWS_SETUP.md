# Google Workspace authentication and storage

The tracker uses GWS CLI 0.22.5 with an explicitly selected Google identity. New artifacts use
private Google Cloud Storage (GCS), not personal Google Drive. OAuth remains an opt-in compatibility
path for existing Drive storage only. Explicit credentials use an isolated GWS config directory;
an invalid service-account configuration never falls back to OAuth.

## Dependency map

| Flow | Implementation | Google resource |
|---|---|---|
| Job tracker reads/writes | `scripts/write_sheet.py`, `scripts/tracker_service.py` | Existing spreadsheet ID, `Jobs!A:M` |
| Interview records | `scripts/interview_store.py` | `Interviews` tab in the same spreadsheet |
| Resume uploads and pipeline mirror | `scripts/artifact_storage.py`, `scripts/drive_resumes.py`, `scripts/artifacts.py` | Private GCS bucket; Drive only in explicit legacy mode |
| Resume download | `scripts/dashboard_server.py` | Authenticated dashboard proxy to private GCS objects |
| Dashboard status | `scripts/google_auth.py`, `/api/health/google` | Read-only tracker range and GCS bucket checks |
| Credential selection | `scripts/google_auth.py`, `modal_app.py` | `GOOGLE_AUTH_MODE`, `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` |

## Service-account setup

1. Enable the Google Sheets API and Cloud Storage API in a Google Cloud project, create a service
   account, and issue a key through your approved key-management process. Never put the JSON in
   this repository or print it.
2. Share the **existing tracker spreadsheet** directly with the service-account email as a Viewer
   first. Keep its spreadsheet ID and tabs unchanged. Project IAM roles do not grant access to a
   spreadsheet shared with a different Google identity.
3. Configure the local identity and storage settings:

   ```powershell
   $env:GOOGLE_AUTH_MODE = 'service_account'
   $env:GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE = 'C:\secure\job-agent-service-account.json'
   $env:google_sheet_id = '<existing tracker spreadsheet id>'
   $env:ARTIFACT_STORAGE_BACKEND = 'gcs'
   $env:GCS_BUCKET = '<private bucket name>'
   $env:ARTIFACT_PUBLIC_BASE_URL = 'https://<stable dashboard host>'
   $env:GOOGLE_SHEETS_WRITE_ENABLED = 'false'
   ```

   `.env` may also contain these settings, but the JSON key itself must stay outside the repository.
   Do not set a Drive parent for GCS mode.
4. With writes disabled, run `python scripts/google_auth.py` or check **Google Service Health**
   in the dashboard. This reads the configured spreadsheet metadata and `Jobs!A1:M1`, then checks
   GCS access; it does not create, rename, or modify anything. Confirm Sheets and artifact storage
   report `available` before explicitly setting `GOOGLE_SHEETS_WRITE_ENABLED=true`.

The centralized write classifier in `write_sheet.gws` gates Sheets append, value updates/clears,
spreadsheet creation, and every `spreadsheets.batchUpdate` request (including interview appends,
row deletion, and worksheet changes). Before the first service-account write in each process, the
code repeats the read-only spreadsheet and `Jobs!A1:M1` checks. A failed read blocks the write.
Read-only calls continue to work while writes are disabled. OAuth mode keeps its existing behavior.
`google_sheet_id` is mandatory; the app never finds or creates a replacement sheet.

Interviews use the existing `Interviews` worksheet in that exact configured spreadsheet. The
application will not search Drive when `google_sheet_id` is missing or empty.

## Private GCS artifacts and historical Drive links

The service account never creates files or folders in personal Google Drive; personal Gmail does
not provide the Shared Drive membership option. GCS stores new resume
exports and pipeline recovery snapshots with private objects, stable filenames, per-resume object
prefixes, and resume metadata. Resume PDF/DOCX exports are rendered locally; the GCS path does not
create a temporary Google Doc.

Set `ARTIFACT_STORAGE_BACKEND=gcs`, `GCS_BUCKET`, and `ARTIFACT_PUBLIC_BASE_URL` (the stable HTTPS
dashboard origin). Keep the bucket private and grant the service account the minimum bucket/object
permissions needed to read metadata and create, read, and update objects. The health check verifies
configuration and bucket access without uploading or modifying any object.

The tracker stores an authenticated dashboard URL for a new GCS artifact. The dashboard streams
the private object only to a signed-in session; it does not issue public or expiring GCS URLs.
Therefore `ARTIFACT_PUBLIC_BASE_URL` must be the same stable dashboard host users can sign in to.

Existing Drive URLs in the tracker and old `tailored_jobs.json` records are left unchanged and are
not migrated, rewritten, copied, or deleted. Their existing Drive targets remain directly usable.
`google_drive_folder_id` is not used in GCS mode. The optional legacy backend
`ARTIFACT_STORAGE_BACKEND=drive` is only for an explicitly selected authorized-user OAuth identity
and requires `HISTORICAL_DRIVE_ARTIFACTS=true`; service-account credentials are rejected for Drive
uploads. Do not select this mode for personal My Drive with a service account.

`artifact_sync=0` disables stage-artifact mirroring. Otherwise, GCS storage errors are surfaced and
fail the pipeline rather than being treated as a successful recovery.
Resume filenames retain readable role names, including `+`, `#`, spaces, and Unicode. Dashboard
links URL-encode these characters and validate the decoded key before accessing the private object;
path separators and traversal components remain rejected.

Service-account Sheets writes are initially disabled. After the read-only tracker check passes and
you verify the service account has edit access, set `GOOGLE_SHEETS_WRITE_ENABLED=true`. Tracker
updates and interview CRUD share this spreadsheet and both require this explicit opt-in.

## Modal setup

The Modal functions continue to reference existing secrets; this change does not create, update,
or deploy any production secret. Before an operator-approved rollout, configure
`GOOGLE_AUTH_MODE=service_account`, add `GOOGLE_SERVICE_ACCOUNT_JSON` to the protected credentials
secret, and configure `ARTIFACT_STORAGE_BACKEND=gcs`, `GCS_BUCKET`, `ARTIFACT_PUBLIC_BASE_URL`,
`google_sheet_id`, and `GOOGLE_SHEETS_WRITE_ENABLED=false` in the appropriate protected settings.
At startup, the app validates the credential type, writes it to a temporary directory outside the
repository with restrictive permissions where supported, and selects it explicitly. Never put the
JSON in image files, source control, logs, or deployment artifacts.

To explicitly select OAuth compatibility, set `GOOGLE_AUTH_MODE=oauth`; the existing `client_id`,
`client_secret`, `refresh_token`, and `type` fields remain available. The Modal bootstrap never
switches between these modes implicitly. Service-account mode without service-account JSON fails
closed instead of attempting an old OAuth refresh token.

## OAuth migration and rollback

- Local read-only rollout: keep `GOOGLE_SHEETS_WRITE_ENABLED=false`, run the health check, confirm
  the exact spreadsheet and bucket, then explicitly enable writes only after the owner approves.
- Before any GCS URL is written to the Sheet, rollback is to keep writes disabled and restore the
  prior application release/configuration; leave GCS objects and historical Drive files untouched.
- After a GCS URL is written to the Sheet, do **not** roll back to code that lacks the authenticated
  `/api/artifacts/` retrieval route. Keep the route, service-account read access, bucket, and stable
  dashboard host available so those URLs continue working. Turning off writes does not require
  changing or migrating any stored URL.
- OAuth is never an automatic fallback. An operator may explicitly select the legacy Drive backend
  only before enabling a service account, using a valid authorized-user credential and an accessible
  Drive folder. Do not select service-account uploads to personal My Drive.
- Credential rotation: issue a replacement key through the organization’s approved process,
  update the protected local file or Modal secret, verify health, then revoke the old key. Never
  print the JSON as a verification step.

## Troubleshooting

The dashboard status endpoint distinguishes missing/invalid credentials, insufficient scopes,
disabled APIs, permission denial, network errors, and unavailable storage. For `permission_denied`,
share the exact spreadsheet with the selected service-account email and grant the required bucket
permissions in GCS mode. In legacy OAuth Drive mode, grant the authorized user access to the
configured Drive parent. For `insufficient_scopes`, the selected OAuth credentials need Sheets and
Drive scopes; a service account does not use user OAuth scopes. Interviews use a worksheet in the
tracker spreadsheet and are checked independently, so a missing `Interviews` tab is reported
separately from general Sheets access.

The pipeline performs read-only Sheets and configured artifact-storage checks before scraping or
tailoring, and stops if the required services are unavailable or the service-account Sheet write
opt-in remains disabled. Health checks do not create files, write spreadsheet cells, or upload
objects.
