import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import modal_app


class ModalGoogleCredentialTests(unittest.TestCase):
    def test_service_account_secret_is_materialized_outside_repository(self):
        credential = {"type": "service_account", "client_email": "agent@example.test",
                      "private_key": "TEST_ONLY_NOT_A_REAL_KEY", "token_uri": "https://example.test/token"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch("tempfile.mkdtemp", return_value=tmp), \
                mock.patch.dict(os.environ, {"GOOGLE_AUTH_MODE": "service_account",
                                              "GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps(credential)}, clear=True):
            modal_app.materialize_gws_credentials()
            path = Path(os.environ["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"])
            self.assertEqual(path.parent, Path(tmp))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), credential)
            self.assertEqual(os.environ["GOOGLE_AUTH_MODE"], "service_account")
            self.assertEqual(os.environ["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"], str(Path(tmp) / "gws-config"))

    def test_oauth_mode_is_explicit_and_does_not_inherit_service_account_identity(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("tempfile.mkdtemp", return_value=tmp), \
                mock.patch.dict(os.environ, {"GOOGLE_AUTH_MODE": "oauth",
                    "GOOGLE_SERVICE_ACCOUNT_JSON": '{"type":"service_account"}',
                    "client_id": "id", "client_secret": "secret", "refresh_token": "refresh",
                    "type": "authorized_user"}, clear=True):
            modal_app.materialize_gws_credentials()
            self.assertEqual(os.environ["GOOGLE_AUTH_MODE"], "oauth")
            self.assertEqual(json.loads(Path(os.environ["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"])
                                        .read_text(encoding="utf-8"))["type"], "authorized_user")
            import google_auth
            self.assertFalse(google_auth.is_service_account())


if __name__ == "__main__":
    unittest.main()
