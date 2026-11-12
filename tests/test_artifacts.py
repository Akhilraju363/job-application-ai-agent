import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import ROOT
import artifacts
import artifact_storage


class DriveArtifactMirrorTests(unittest.TestCase):
    def test_push_and_pull_use_google_drive_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            source = output / "scored_jobs.json"
            source.write_text('[{"score": 8}]', encoding="utf-8")
            sidecar = output / ".artifact_sync.json"
            state = {"checked": False, "folder_id": None, "file_ids": {}}
            with mock.patch.object(artifacts, "OUTPUT_DIR", output), \
                    mock.patch.object(artifacts, "SIDECAR", sidecar), \
                    mock.patch.object(artifacts, "_DISABLED", False), \
                    mock.patch.object(artifacts, "_RUN_DATE", "2026-11-11"), \
                    mock.patch.object(artifacts, "_state", state), \
                    mock.patch.object(artifacts, "_PARENT_ID", "parent-id"), \
                    mock.patch.object(artifact_storage, "backend", return_value="drive"), \
                    mock.patch.object(artifacts, "_folder_id", return_value="folder-id"), \
                    mock.patch.object(artifacts, "_remote_meta", side_effect=[
                        None, {"id": "drive-file", "modifiedTime": "2026-11-11T00:00:00Z"}]), \
                    mock.patch.object(artifacts, "_json", return_value={"id": "drive-file", "modifiedTime": "2026-11-11T00:00:00Z"}) as gws, \
                    mock.patch.object(artifacts, "_run", return_value='[{"score": 9}]') as get:
                artifacts.push("scored_jobs.json")
                create_args = gws.call_args.args[0]
                self.assertEqual(create_args[:3], ["drive", "files", "create"])
                self.assertIn("--upload", create_args)
                self.assertEqual(json.loads(create_args[create_args.index("--json") + 1])["name"],
                                 "scored_jobs-2026-11-11.json")
                sidecar.unlink()
                source.unlink()
                artifacts.pull("scored_jobs.json")
            self.assertEqual(source.read_text(encoding="utf-8"), '[{"score": 9}]')
            self.assertEqual(json.loads(get.call_args.args[0][-1])["fileId"], "drive-file")

    def test_disabled_artifact_sync_does_not_contact_storage(self):
        with mock.patch.object(artifacts, "_DISABLED", True), \
                mock.patch.object(artifact_storage, "backend") as backend:
            artifacts.pull("raw_jobs.json")
            artifacts.push("raw_jobs.json")
        backend.assert_not_called()

    def test_gcs_configuration_fails_instead_of_falling_back_to_drive(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "raw_jobs.json").write_text("[]", encoding="utf-8")
            with mock.patch.object(artifacts, "OUTPUT_DIR", output), \
                    mock.patch.object(artifacts, "_DISABLED", False), \
                    mock.patch.object(artifact_storage, "backend",
                                      side_effect=artifact_storage.ArtifactStorageError("GCS storage is not supported")), \
                    mock.patch.object(artifacts, "_run") as gws:
                with self.assertRaisesRegex(artifact_storage.ArtifactStorageError, "GCS storage is not supported"):
                    artifacts.push("raw_jobs.json")
            gws.assert_not_called()


if __name__ == "__main__":
    unittest.main()
