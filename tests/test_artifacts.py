import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fixtures import ROOT
import artifacts
import artifact_storage


class GCSArtifactMirrorTests(unittest.TestCase):
    def test_push_and_pull_use_date_stamped_gcs_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            source = output / "scored_jobs.json"
            source.write_text('[{"score": 8}]', encoding="utf-8")
            sidecar = output / ".artifact_sync.json"
            current = datetime.now(timezone.utc)
            with mock.patch.object(artifacts, "OUTPUT_DIR", output), \
                    mock.patch.object(artifacts, "SIDECAR", sidecar), \
                    mock.patch.object(artifacts, "_DISABLED", False), \
                    mock.patch.object(artifacts, "_RUN_DATE", "2026-11-11"), \
                    mock.patch.object(artifact_storage, "backend", return_value="gcs"), \
                    mock.patch.object(artifact_storage, "upload_file") as upload, \
                    mock.patch.object(artifact_storage, "object_metadata",
                                      return_value={"updated": current, "metadata": {}}), \
                    mock.patch.object(artifact_storage, "download_bytes", return_value=b'[{"score": 9}]') as download:
                artifacts.push("scored_jobs.json")
                self.assertEqual(source.read_text(encoding="utf-8"), '[{"score": 8}]')
                self.assertEqual(upload.call_args.args[1], "pipeline-artifacts/scored_jobs-2026-11-11.json")
                artifacts._save_sidecar({})
                source.unlink()
                artifacts.pull("scored_jobs.json")
            self.assertEqual(source.read_bytes(), b'[{"score": 9}]')
            download.assert_called_once_with("pipeline-artifacts/scored_jobs-2026-11-11.json")

    def test_disabled_artifact_sync_does_not_contact_storage(self):
        with mock.patch.object(artifacts, "_DISABLED", True), \
                mock.patch.object(artifact_storage, "backend") as backend:
            artifacts.pull("raw_jobs.json")
            artifacts.push("raw_jobs.json")
        backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
