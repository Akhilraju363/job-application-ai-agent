import fixtures  # noqa: F401
import json
import os
import hashlib
import unittest
from unittest import mock

import naukri_handoff as handoff
import scrape_jobs


JOB = {"title": "Engineer", "company": "Example", "link": "https://naukri.example/1",
       "description": "Build software", "skills": ["Python"]}


class ValidationTests(unittest.TestCase):
    def test_valid_metadata_is_preserved(self):
        self.assertEqual(handoff.parse_json(json.dumps([JOB])), [JOB])

    def test_invalid_json_and_non_list(self):
        for raw in ("{", '{"jobs": []}'):
            with self.assertRaises(handoff.HandoffError):
                handoff.parse_json(raw)

    def test_required_fields_report_record_and_fields(self):
        with self.assertRaises(handoff.HandoffError) as caught:
            handoff.validate_jobs([{"title": "Engineer"}])
        self.assertEqual(caught.exception.details["invalid_records"][0]["missing"], ["company", "link", "description"])

    def test_empty_rejected_to_protect_existing_file(self):
        with self.assertRaises(handoff.HandoffError):
            handoff.validate_jobs([])

    def test_local_path_is_still_supported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "naukri.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump([JOB], f)
            self.assertEqual(len(scrape_jobs.load_naukri_jobs(path)), 1)


class DriveTests(unittest.TestCase):
    def test_missing_parent_reports_not_configured(self):
        with mock.patch.dict(os.environ, {"google_drive_folder_id": ""}):
            self.assertEqual(handoff.status()["status"], "not_configured")

    def test_missing_file_and_drive_failure_are_safe(self):
        with mock.patch.dict(os.environ, {"google_drive_folder_id": "parent"}), \
             mock.patch.object(handoff, "_folder", return_value=None):
            self.assertEqual(handoff.status()["status"], "file_missing")
            self.assertEqual(handoff.retrieve_jobs(), [])
        with mock.patch.dict(os.environ, {"google_drive_folder_id": "parent"}), \
             mock.patch.object(handoff, "_folder", side_effect=RuntimeError("private")):
            self.assertEqual(handoff.status()["status"], "unavailable")
            self.assertEqual(handoff.retrieve_jobs(), [])

    def test_modal_style_retrieval_validates_drive_jobs(self):
        with mock.patch.object(handoff, "_folder", return_value="folder"), \
             mock.patch.object(handoff, "_find", return_value=[{"id": "file"}]), \
             mock.patch.object(handoff, "_gws", return_value=[JOB]):
            self.assertEqual(handoff.retrieve_jobs(), [JOB])

    def test_invalid_upload_never_touches_drive(self):
        with mock.patch.object(handoff, "_folder") as folder:
            with self.assertRaises(handoff.HandoffError):
                handoff.import_jobs([])
            folder.assert_not_called()

    def test_reimported_identical_file_does_not_upload_again(self):
        payload = json.dumps([JOB], ensure_ascii=False, indent=2)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with mock.patch.object(handoff, "_folder", return_value="folder"), \
             mock.patch.object(handoff, "_find", return_value=[{"id": "file", "appProperties": {"sha256": digest,
                                                                                                 "lastSuccessfulImport": "then"}}]), \
             mock.patch.object(handoff, "_gws") as gws:
            result = handoff.import_jobs([JOB])
        self.assertTrue(result["unchanged"])
        gws.assert_not_called()

    def test_new_import_creates_only_the_dedicated_handoff_file(self):
        with mock.patch.object(handoff, "_folder", return_value="folder"), \
             mock.patch.object(handoff, "_find", return_value=[]), \
             mock.patch.object(handoff, "_gws", return_value={"id": "new-file"}) as gws:
            result = handoff.import_jobs([JOB])
        self.assertEqual(result["job_count"], 1)
        args = gws.call_args.args[0]
        self.assertEqual(args[:3], ["drive", "files", "create"])
        metadata = json.loads(args[args.index("--json") + 1])
        self.assertEqual((metadata["name"], metadata["parents"]), ("naukri_jobs.json", ["folder"]))
        self.assertIn("--upload", args)


if __name__ == "__main__":
    unittest.main()
