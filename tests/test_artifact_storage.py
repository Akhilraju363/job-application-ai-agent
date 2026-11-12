import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import artifact_storage


class ArtifactStorageTests(unittest.TestCase):
    def test_drive_is_the_default_and_only_supported_backend(self):
        self.assertEqual(artifact_storage.backend({}), "drive")
        self.assertEqual(artifact_storage.backend({"GOOGLE_AUTH_MODE": "oauth"}), "drive")
        self.assertEqual(artifact_storage.backend({"ARTIFACT_STORAGE_BACKEND": "drive"}), "drive")
        with self.assertRaisesRegex(artifact_storage.ArtifactStorageError, "GCS storage is not supported"):
            artifact_storage.backend({"ARTIFACT_STORAGE_BACKEND": "gcs"})

    def test_personal_drive_rejects_service_account_identity(self):
        with self.assertRaisesRegex(artifact_storage.ArtifactStorageError, "require OAuth"):
            artifact_storage.backend({"GOOGLE_AUTH_MODE": "service_account"})

    def test_drive_configuration_requires_parent_folder(self):
        with self.assertRaisesRegex(artifact_storage.ArtifactStorageError, "google_drive_folder_id"):
            artifact_storage.check_configuration({})
        artifact_storage.check_configuration({"google_drive_folder_id": "folder-id"})


if __name__ == "__main__":
    unittest.main()
