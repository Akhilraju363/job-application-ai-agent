# Google Workspace OAuth and personal Drive storage

The pipeline uses one personal Google OAuth identity through GWS CLI for Google Drive and Google
Sheets. Generated PDF/DOCX resumes and pipeline recovery artifacts are stored in the existing
personal Drive folder. No service account, GCS bucket, or paid cloud storage is required.

## Google OAuth credential setup

1. Use the existing Google Cloud OAuth project/client that owns the personal OAuth grant. In the
   Google Cloud Console, confirm the OAuth client is a **Desktop app**, your Google account is an
   allowed test user if the consent screen is in Testing, and the Drive, Docs, and Sheets APIs are
   enabled. No GCS bucket or service-account key is needed.
2. On a trusted local machine with the `gws` CLI installed, authorize the account with the scopes
   the application uses:

   ```powershell
   gws auth login -s drive,docs,sheets
   ```

   Complete the browser consent flow as the personal Google account that can access both the
   existing Drive folder and tracker spreadsheet.
3. Export the authorized-user credentials directly to a protected local file. Do not send the
   output to a terminal, chat, source control, or an unprotected location:

   ```powershell
   gws auth export --unmasked > "$env:USERPROFILE\gws-authorized-user.json"
   ```

   The export should contain `type=authorized_user` and the `client_id`, `client_secret`, and
   `refresh_token` fields. Confirm only the field names and `type`; never print or log their values.
   Store the file securely and delete temporary copies after securely configuring the Modal secret.
4. For a local read-only preflight, set `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` to that file and
   `GOOGLE_AUTH_MODE=oauth`, then run `python scripts/google_auth.py`. This makes read-only calls
   against the configured spreadsheet and Drive parent folder; it does not upload, create, update,
   or delete files or rows. Keep `GOOGLE_SHEETS_WRITE_ENABLED=false`.

### OAuth `invalid_grant`

`invalid_grant` means Google rejected the refresh grant. From that error alone it is not possible
to tell whether the refresh token was revoked/expired, the OAuth client changed, or the credentials
do not belong together. The application reports safe reauthorization guidance and never logs token
values. If Google rejects the refresh token, code cannot repair it: repeat the browser authorization
and export a fresh authorized-user credential file, then replace the protected Modal credentials.
If the OAuth app is still in Testing, check Google's current refresh-token lifetime policy for
testing apps; an authorized token can expire even if the application code is unchanged.

## Modal secrets

Use the protected Modal secret manager; do not put credential values in this repository, command
history, deployment arguments, or chat. Do not change the schedule, deploy, or enable Sheets writes
as part of credential rotation.

| Secret | Required settings |
|---|---|
| `gws-credentials` | `GOOGLE_AUTH_MODE=oauth`, `client_id`, `client_secret`, `refresh_token`, `type=authorized_user` |
| `job-apply-agent-secrets` | Existing `google_sheet_id`, `google_drive_folder_id`, `ARTIFACT_STORAGE_BACKEND=drive` (or unset), and `GOOGLE_SHEETS_WRITE_ENABLED=false`; retain existing LLM, Apify, contact, and alert settings |
| `job-apply-agent-dashboard-secrets` | Keep existing `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD_HASH`, and `DASHBOARD_ALLOWED_HOSTS` unchanged |

`modal_app.py` materializes the four OAuth fields from `gws-credentials` into a temporary,
permission-restricted credential file at process startup and points GWS at an isolated config
directory. `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` is generated internally; do not set it in the
secret. `GOOGLE_SERVICE_ACCOUNT_JSON`, `GCS_BUCKET`, `ARTIFACT_PUBLIC_BASE_URL`, and
`HISTORICAL_DRIVE_ARTIFACTS` are obsolete and can be removed from Modal configuration after the
code change is reviewed. Keep `GOOGLE_SHEETS_WRITE_ENABLED=false` until the owner explicitly
authorizes spreadsheet mutations.

## Storage and compatibility

- New resumes are exported through Google Docs, uploaded to the configured personal Drive folder,
  and linked with the Drive `webViewLink`. Uploads are idempotent by resume id, version, and format.
- The authenticated dashboard serves its local cached export and, if that file is absent, retrieves
  the recorded Drive file using the same OAuth identity.
- Pipeline stage JSON artifacts use the existing Drive `pipeline-artifacts` folder. Disable this
  optional mirror with `artifact_sync=0`.
- Existing Drive files and links are not migrated, rewritten, deleted, or recreated. Historical
  Drive URLs in the tracker remain direct Drive links.
- Explicit `ARTIFACT_STORAGE_BACKEND=gcs` is rejected. There is no GCS fallback or GCS dependency.
- Google Sheets access uses the same GWS OAuth identity, but the centralized write gate blocks all
  Sheets mutations—including OAuth mutations—unless `GOOGLE_SHEETS_WRITE_ENABLED=true`.

The Modal schedule remains `0 7 * * 1-5` in `Asia/Kolkata`. Do not deploy until the OAuth secret has
been updated securely, the read-only health check succeeds, the write gate has been explicitly
authorized if needed, and the local changes have been reviewed.
