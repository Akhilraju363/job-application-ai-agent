import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import google_auth


class GoogleAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sa.json"
        self.credential = {"type": "service_account", "client_email": "agent@example.iam.gserviceaccount.com",
                           "private_key": "TEST_PRIVATE_KEY_MATERIAL", "token_uri": "https://oauth2.googleapis.com/token"}

    def env(self, value=None):
        return {google_auth.ENV_CREDENTIALS: str(self.path), **(value or {})}

    def test_valid_service_account_selects_isolated_gws_config(self):
        self.path.write_text(json.dumps(self.credential), encoding="utf-8")
        env = google_auth.configure_environment(self.env())
        self.assertEqual(env[google_auth.ENV_CREDENTIALS], str(self.path.resolve()))
        self.assertEqual(env["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"], "file")
        self.assertTrue(env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"])
        self.assertNotIn("private_key", str(env))

    def test_missing_malformed_and_unsupported_credentials_are_classified(self):
        with self.assertRaises(google_auth.GoogleAuthError) as missing:
            google_auth.configure_environment(self.env())
        self.assertEqual(missing.exception.category, "missing_credentials")
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaises(google_auth.GoogleAuthError) as malformed:
            google_auth.configure_environment(self.env())
        self.assertEqual(malformed.exception.category, "invalid_credentials")
        self.path.write_text(json.dumps({"type": "authorized_service"}), encoding="utf-8")
        with self.assertRaises(google_auth.GoogleAuthError) as unsupported:
            google_auth.configure_environment(self.env())
        self.assertEqual(unsupported.exception.category, "invalid_credentials")

    def test_health_separates_sheets_and_drive_permission_failures(self):
        self.path.write_text(json.dumps(self.credential), encoding="utf-8")
        env = self.env({"google_sheet_id": "sheet", "ARTIFACT_STORAGE_BACKEND": "gcs",
                        "GCS_BUCKET": "private-bucket", "ARTIFACT_PUBLIC_BASE_URL": "https://agent.example"})
        def fake_gws(*args):
            if "sheets" in args:
                raise RuntimeError("HTTP 403 permission denied")
            raise AssertionError("GCS mode should not call Google Drive")
        result = google_auth.health_check(fake_gws, env, check_gcs=lambda: None)
        self.assertEqual(result["sheets"]["category"], "permission_denied")
        self.assertEqual(result["interviews"]["category"], "permission_denied")
        self.assertEqual(result["drive"]["status"], "not_required")
        self.assertEqual(result["artifact_storage"]["status"], "available")

    def test_read_only_health_checks_sheet_and_shared_drive_parent(self):
        self.path.write_text(json.dumps(self.credential), encoding="utf-8")
        calls = []
        result = google_auth.health_check(lambda *args: calls.append(args) or {}, self.env({
            "google_sheet_id": "sheet-id", "ARTIFACT_STORAGE_BACKEND": "gcs", "GCS_BUCKET": "private-bucket",
            "ARTIFACT_PUBLIC_BASE_URL": "https://agent.example"}), check_gcs=lambda: None)
        self.assertEqual(result["sheets"]["status"], "available")
        self.assertEqual(result["drive"]["status"], "not_required")
        self.assertEqual(result["artifact_storage"]["status"], "available")
        self.assertEqual(len(calls), 3)  # spreadsheet metadata, Jobs header, Interviews header
        self.assertTrue(all("create" not in args and "update" not in args for args in calls))

    def test_service_account_does_not_silently_select_drive_artifact_storage(self):
        self.path.write_text(json.dumps(self.credential), encoding="utf-8")
        env = self.env({"google_sheet_id": "sheet-id", "ARTIFACT_STORAGE_BACKEND": "drive",
                        "google_drive_folder_id": "folder-id"})
        result = google_auth.health_check(lambda *args: {}, env)
        self.assertEqual(result["artifact_storage"]["status"], "unavailable")
        self.assertIn("Service-account credentials cannot use Drive", result["artifact_storage"]["message"])

    def test_auth_errors_are_classified_and_diagnostics_do_not_expose_credentials(self):
        self.assertEqual(google_auth.classify_error("insufficient authentication scopes"), "insufficient_scopes")
        self.assertEqual(google_auth.classify_error("API has not been used"), "api_not_enabled")
        self.assertEqual(google_auth.classify_error("invalid_grant: Failed to get token"), "invalid_credentials")
        diagnostic = google_auth.safe_gws_error('client_secret="do-not-log" failed')
        self.assertNotIn("do-not-log", diagnostic)
        for sensitive in (
                'refresh_token: refresh-value-123',
                'Authorization: Bearer bearer-value-123',
                'private_key="-----BEGIN PRIVATE KEY-----\\nprivate-value-123\\n-----END PRIVATE KEY-----"',
                'access_token=eyJabcdefghijk.abcdefghij.abcdefghij'):
            with self.subTest(sensitive=sensitive):
                self.assertNotIn("123", google_auth.safe_gws_error(sensitive))
                self.assertNotIn("bearer-value", google_auth.safe_gws_error(sensitive))
                self.assertNotIn("private-value", google_auth.safe_gws_error(sensitive))
                self.assertNotIn("eyJ", google_auth.safe_gws_error(sensitive))


if __name__ == "__main__":
    unittest.main()
