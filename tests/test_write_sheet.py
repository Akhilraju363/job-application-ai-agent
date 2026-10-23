import json
import subprocess
import unittest
from unittest import mock

import fixtures  # noqa: F401
import write_sheet
import tracker_service


class GwsCommandTests(unittest.TestCase):
    def test_build_row_defaults_both_tracker_fields_independently(self):
        row = write_sheet.build_row({"title": "T", "company": "C", "link": "L", "score": 8}, "2026-10-23")
        self.assertEqual(row[5], "Not Applied")
        self.assertEqual(row[12], "Not reviewed")

    def test_suitability_update_writes_only_column_m(self):
        with mock.patch.object(write_sheet, "update_range") as update:
            write_sheet.set_suitability("sheet", 7, "Shortlist")
        update.assert_called_once_with("sheet", "Sheet1!M7", [["Shortlist"]])

    def test_schema_migration_adds_only_missing_suitability_header_and_validation(self):
        with mock.patch.object(write_sheet, "gws", side_effect=[
            {"values": [write_sheet.HEADERS[:12]]}, {},
        ]) as gws, mock.patch.object(write_sheet, "update_range") as update:
            write_sheet.ensure_extra_headers("sheet")
        update.assert_called_once_with("sheet", "Sheet1!M1", [["Suitability"]])
        request = json.loads(gws.call_args_list[-1].args[-1])["requests"]
        self.assertEqual(request[0]["setDataValidation"]["range"]["startColumnIndex"], 5)
        self.assertEqual(request[1]["setDataValidation"]["range"]["startColumnIndex"], 12)

    def test_append_uses_supported_helper_and_json_values(self):
        rows = [["Java & Angular", "Marsh", "https://www.linkedin.com/jobs/view/123", "8", "",
                 "Not Applied", "2026-10-21", "", "LinkedIn", "", "", "80", "Not reviewed"]]
        with mock.patch.object(write_sheet, "gws") as gws:
            write_sheet.append_rows("spreadsheet-id", rows)

        args = gws.call_args.args
        self.assertEqual(args[:4], ("sheets", "+append", "--spreadsheet", "spreadsheet-id"))
        self.assertEqual(args[4], "--json-values")
        self.assertEqual(json.loads(args[5]), rows)
        self.assertEqual(args[5], json.dumps(rows, ensure_ascii=False, separators=(",", ":")))

    def test_append_rejects_rows_that_do_not_match_tracker_headers(self):
        with self.assertRaisesRegex(ValueError, "13 values"):
            write_sheet.append_rows("spreadsheet-id", [["too", "short"]])

    def test_gws_failure_contains_exit_code_stdout_and_stderr(self):
        completed = subprocess.CompletedProcess(
            args=["gws"], returncode=17, stdout='{"error":"details"}', stderr="permission denied"
        )
        with mock.patch.object(write_sheet.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, r"exit code 17"):
                write_sheet.gws("sheets", "+append", "--spreadsheet", "spreadsheet-id")
        with mock.patch.object(write_sheet.subprocess, "run", return_value=completed):
            try:
                write_sheet.gws("sheets", "+append", "--spreadsheet", "spreadsheet-id")
            except RuntimeError as exc:
                message = str(exc)
        self.assertIn('stdout: {"error":"details"}', message)
        self.assertIn("stderr: permission denied", message)

    def test_tracker_range_and_header_mapping_are_explicit(self):
        self.assertEqual(write_sheet.TRACKER_RANGE, "Sheet1!A:M")
        self.assertEqual(write_sheet.HEADERS[0:4], ["Job Title", "Company", "Job Link", "Fit Score"])
        self.assertEqual(write_sheet.HEADERS[8:], ["Source", "Status Updated", "Resume ID", "Match %", "Suitability"])

    def test_tracker_does_not_append_a_canonical_duplicate(self):
        tracker = tracker_service.Tracker()
        existing = [{"row": 7, "link": "https://www.linkedin.com/jobs/view/123?trackingId=old",
                     "resume_path": "https://drive.google.com/file/d/existing/view"}]
        with mock.patch.dict("os.environ", {"google_sheet_id": "spreadsheet-id"}), \
                mock.patch.object(write_sheet, "ensure_extra_headers"), \
                mock.patch.object(write_sheet, "read_tracker", return_value=existing), \
                mock.patch.object(write_sheet, "append_rows") as append, \
                mock.patch("activity.log_event"):
            result = tracker.save_job(
                title="Java & Angular", company="Marsh",
                link="https://www.linkedin.com/jobs/view/123?trackingId=new", score=8,
                resume_path="https://drive.google.com/file/d/new/view",
            )
        self.assertEqual(result["result"], "exists")
        append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
