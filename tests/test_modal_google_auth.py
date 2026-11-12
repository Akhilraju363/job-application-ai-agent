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
    def test_modal_defaults_to_personal_oauth_and_materializes_authorized_user_credentials(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("tempfile.mkdtemp", return_value=tmp), \
                mock.patch.dict(os.environ, {
                    "client_id": "id", "client_secret": "secret", "refresh_token": "refresh",
                    "type": "authorized_user"}, clear=True):
            modal_app.materialize_gws_credentials()
            self.assertEqual(os.environ["GOOGLE_AUTH_MODE"], "oauth")
            path = Path(os.environ["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"])
            self.assertEqual(path.parent, Path(tmp))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {
                "client_id": "id", "client_secret": "secret", "refresh_token": "refresh",
                "type": "authorized_user"})
            self.assertEqual(os.environ["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"], str(Path(tmp) / "gws-config"))
            for key in ("client_id", "client_secret", "refresh_token", "type"):
                self.assertNotIn(key, os.environ)

    def test_modal_oauth_requires_all_authorized_user_fields(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "client_id, client_secret, refresh_token, and type"):
                modal_app.materialize_gws_credentials()

    def test_modal_rejects_service_account_mode_for_personal_drive(self):
        with mock.patch.dict(os.environ, {"GOOGLE_AUTH_MODE": "service_account"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "must be oauth"):
                modal_app.materialize_gws_credentials()


if __name__ == "__main__":
    unittest.main()
