import json
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
        self.path = Path(self.tmp.name) / "oauth.json"
        self.credential = {"type": "authorized_user", "client_id": "test-client",
                           "client_secret": "TEST_SECRET", "refresh_token": "TEST_REFRESH"}

    def env(self, value=None):
        return {google_auth.ENV_CREDENTIALS: str(self.path), "GOOGLE_AUTH_MODE": "oauth", **(value or {})}

    def test_valid_oauth_selects_isolated_gws_config_without_exposing_credentials(self):
        self.path.write_text(json.dumps(self.credential), encoding="utf-8")
        env = google_auth.configure_environment(self.env())
        self.assertEqual(env[google_auth.ENV_CREDENTIALS], str(self.path.resolve()))
        self.assertEqual(env["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"], "file")
        self.assertTrue(env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"])
        self.assertNotIn("TEST_SECRET", str(env))
        self.assertNotIn("TEST_REFRESH", str(env))

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
        self.assertEqual(unsupported.exception.category, "unsupported_identity")

    def test_read_only_health_checks_sheets_and_drive_with_same_oauth_identity(self):
        self.path.write_text(json.dumps(self.credential), encoding="utf-8")
        calls = []
        result = google_auth.health_check(lambda *args: calls.append(args) or {}, self.env({
            "google_sheet_id": "sheet-id", "google_drive_folder_id": "folder-id"}))
        self.assertEqual(result["sheets"]["status"], "available")
        self.assertEqual(result["drive"]["status"], "available")
        self.assertEqual(result["artifact_storage"]["status"], "available")
        self.assertEqual(len(calls), 4)
        self.assertIn("drive", calls[-1])
        self.assertTrue(all("create" not in args and "update" not in args for args in calls))

    def test_invalid_grant_provides_safe_reauthorization_guidance(self):
        self.path.write_text(json.dumps(self.credential), encoding="utf-8")
        result = google_auth.health_check(
            lambda *args: (_ for _ in ()).throw(RuntimeError("invalid_grant: Failed to get token")),
            self.env({"google_sheet_id": "sheet-id", "google_drive_folder_id": "folder-id"}))
        self.assertEqual(result["sheets"]["category"], "invalid_credentials")
        self.assertIn("Reauthorize", result["sheets"]["message"])
        self.assertNotIn("TEST_SECRET", result["sheets"]["message"])
        self.assertNotIn("TEST_REFRESH", result["sheets"]["message"])

    def test_service_account_credentials_are_rejected_for_drive_and_sheets(self):
        self.path.write_text(json.dumps({"type": "service_account", "client_email": "agent@example.test",
                                         "private_key": "test-key", "token_uri": "https://example.test/token"}),
                             encoding="utf-8")
        env = {"GOOGLE_AUTH_MODE": "service_account", google_auth.ENV_CREDENTIALS: str(self.path),
               "google_sheet_id": "sheet-id", "google_drive_folder_id": "folder-id"}
        result = google_auth.health_check(lambda *args: {}, env)
        self.assertEqual(result["artifact_storage"]["status"], "unavailable")
        self.assertIn("require OAuth", result["artifact_storage"]["message"])

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
