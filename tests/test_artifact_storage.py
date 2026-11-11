import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import artifact_storage


class ArtifactStorageTests(unittest.TestCase):
    def test_backend_selection_is_explicit_for_service_accounts(self):
        service = {"GOOGLE_AUTH_MODE": "service_account"}
        with self.assertRaises(artifact_storage.ArtifactStorageError):
            artifact_storage.backend(service)
        self.assertEqual(artifact_storage.backend({**service, "ARTIFACT_STORAGE_BACKEND": "gcs"}), "gcs")
        with self.assertRaises(artifact_storage.ArtifactStorageError):
            artifact_storage.backend({**service, "ARTIFACT_STORAGE_BACKEND": "drive"})

    def test_resume_and_pipeline_keys_are_safe_and_stable(self):
        key = artifact_storage.resume_object_key("0123456789ab", 2, "Akhil_Developer.pdf", folder="Example Co")
        self.assertEqual(key, "generated_resumes/0123456789ab/Example-Co/v2/Akhil_Developer.pdf")
        self.assertEqual(artifact_storage.pipeline_object_key("scored_jobs-2026-11-11.json"),
                         "pipeline-artifacts/scored_jobs-2026-11-11.json")
        for bad in ("../resume.pdf", "resume.exe", "bad/name.pdf"):
            with self.assertRaises(artifact_storage.ArtifactStorageError):
                artifact_storage.resume_object_key("0123456789ab", 1, bad)

    def test_resume_filenames_with_role_symbols_spaces_and_unicode_round_trip_through_urls(self):
        filenames = ("Akhil_C++_Developer.pdf", "Akhil_C#_Developer.pdf",
                     "Senior_+_C#_Developer.docx", "Développeur_東京.pdf")
        with mock.patch.dict(os.environ, {"ARTIFACT_PUBLIC_BASE_URL": "https://agent.example"}):
            for filename in filenames:
                with self.subTest(filename=filename):
                    key = artifact_storage.resume_object_key("0123456789ab", 3, filename, "Example Co")
                    url = artifact_storage.public_url(key)
                    self.assertEqual(artifact_storage.decode_resume_object_key(urlsplit(url).path.split("/api/artifacts/", 1)[1]), key)

    def test_resume_object_keys_reject_separators_and_traversal(self):
        for filename in ("../resume.pdf", "..\\resume.pdf", "nested/name.pdf", "nested\\name.pdf", "bad\nname.pdf"):
            with self.subTest(filename=filename), self.assertRaises(artifact_storage.ArtifactStorageError):
                artifact_storage.resume_object_key("0123456789ab", 1, filename)
        for key in ("generated_resumes/../resume.pdf", "generated_resumes/0123456789ab/job/v1/%2Fsecret.pdf",
                    "generated_resumes/0123456789ab/job/v1/bad%ZZ.pdf"):
            with self.subTest(key=key), self.assertRaises(artifact_storage.ArtifactStorageError):
                artifact_storage.decode_resume_object_key(key)

    def test_public_url_uses_https_authenticated_dashboard_route(self):
        with mock.patch.dict(os.environ, {"ARTIFACT_PUBLIC_BASE_URL": "https://agent.example/"}):
            self.assertEqual(artifact_storage.public_url("generated_resumes/id/file.pdf"),
                             "https://agent.example/api/artifacts/generated_resumes/id/file.pdf")
        with mock.patch.dict(os.environ, {"ARTIFACT_PUBLIC_BASE_URL": "http://agent.example"}):
            with self.assertRaises(artifact_storage.ArtifactStorageError):
                artifact_storage.public_url("generated_resumes/id/file.pdf")

    def test_upload_preserves_content_type_and_private_metadata(self):
        class Blob:
            metadata = None
            def exists(self): return False
            def upload_from_filename(self, filename, content_type=None):
                self.upload = (filename, content_type, self.metadata)
        blob = Blob()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resume.pdf"
            path.write_bytes(b"pdf")
            credentials = Path(tmp) / "sa.json"
            credentials.write_text(json.dumps({"type": "service_account", "client_email": "test@example.com",
                                               "private_key": "test-only", "token_uri": "https://example.com/token"}))
            with mock.patch.dict(os.environ, {"GOOGLE_AUTH_MODE": "service_account",
                                              "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": str(credentials),
                                              "GCS_BUCKET": "private-bucket"}), \
                    mock.patch.object(artifact_storage, "_client") as client:
                client.return_value.bucket.return_value.blob.return_value = blob
                out = artifact_storage.upload_file(path, "generated_resumes/id/resume.pdf",
                                                   content_type="application/pdf", metadata={"resume_id": "id"})
        self.assertEqual(out["reused"], False)
        self.assertEqual(blob.upload[1], "application/pdf")
        self.assertEqual(blob.upload[2], {"resume_id": "id"})

    def test_download_reads_private_object_bytes(self):
        class Blob:
            def download_as_bytes(self):
                return b"%PDF-private"
        with mock.patch.object(artifact_storage, "_blob", return_value=Blob()) as get_blob:
            self.assertEqual(artifact_storage.download_bytes("generated_resumes/id/resume.pdf"),
                             b"%PDF-private")
        get_blob.assert_called_once_with("generated_resumes/id/resume.pdf")

    def test_configuration_requires_service_account_bucket_and_https_dashboard_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp) / "service-account.json"
            credentials.write_text('{"type":"service_account","client_email":"agent@example.com",'
                                  '"private_key":"not-a-real-key","token_uri":"https://oauth2.example"}',
                                  encoding="utf-8")
            env = {"GOOGLE_AUTH_MODE": "service_account",
                   "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": str(credentials),
                   "GCS_BUCKET": "private-bucket",
                   "ARTIFACT_PUBLIC_BASE_URL": "https://agent.example"}
            artifact_storage.check_configuration(env)
            with self.assertRaises(artifact_storage.ArtifactStorageError):
                artifact_storage.check_configuration({**env, "ARTIFACT_PUBLIC_BASE_URL": "http://agent.example"})


if __name__ == "__main__":
    unittest.main()
