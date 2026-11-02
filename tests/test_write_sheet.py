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
        update.assert_called_once_with("sheet", "Jobs!M7", [["Shortlist"]])

    def test_update_range_keeps_user_entered_default_and_supports_raw(self):
        with mock.patch.object(write_sheet, "gws") as gws:
            write_sheet.update_range("sheet", "Interviews!A2:U2", [["=1+1"]], value_input_option="RAW")
        params = json.loads(gws.call_args.args[5])
        self.assertEqual(params["valueInputOption"], "RAW")

        with mock.patch.object(write_sheet, "gws") as gws:
            write_sheet.update_range("sheet", "Jobs!A2:M2", [["=1+1"]])
        params = json.loads(gws.call_args.args[5])
        self.assertEqual(params["valueInputOption"], "USER_ENTERED")

    def test_delete_row_deletes_only_the_requested_data_row(self):
        link = "https://example.com/jobs/123"
        with mock.patch.object(write_sheet, "gws") as gws, \
                mock.patch.object(write_sheet, "read_tracker", return_value=[{"row": 7, "link": link}]):
            gws.return_value = {"sheets": [{"properties": {"title": "Jobs", "sheetId": 702182959}}]}
            write_sheet.delete_row("sheet", 7, expected_link=link)
        args = gws.call_args_list[-1].args
        self.assertEqual(args[:4], ("sheets", "spreadsheets", "batchUpdate", "--params"))
        self.assertEqual(json.loads(args[4]), {"spreadsheetId": "sheet"})
        request = json.loads(args[6])["requests"][0]["deleteDimension"]
        self.assertEqual(request["range"], {"sheetId": 702182959, "dimension": "ROWS", "startIndex": 6, "endIndex": 7})

    def test_delete_row_refuses_the_header(self):
        with self.assertRaises(ValueError):
            write_sheet.delete_row("sheet", 1, expected_link="https://example.com/jobs/123")

    def test_delete_row_refuses_missing_or_ambiguous_stable_ids(self):
        link = "https://example.com/jobs/123"
        metadata = {"sheets": [{"properties": {"title": "Jobs", "sheetId": 99}}]}
        with mock.patch.object(write_sheet, "gws", side_effect=[metadata, {"values": [write_sheet.HEADERS]}]) as gws:
            with self.assertRaises(LookupError):
                write_sheet.delete_row("sheet", 2, expected_link=link)
        self.assertEqual(gws.call_count, 2)

        duplicates = [
            {"row": 2, "link": link},
            {"row": 4, "link": link + "?trackingId=duplicate"},
        ]
        with mock.patch.object(write_sheet, "gws", return_value=metadata) as gws, \
                mock.patch.object(write_sheet, "read_tracker", return_value=duplicates):
            with self.assertRaises(write_sheet.AmbiguousTrackerRowError):
                write_sheet.delete_row("sheet", 2, expected_link=link)
        self.assertEqual(gws.call_count, 1)

    def test_delete_row_uses_current_position_when_record_moved(self):
        link = "https://www.linkedin.com/jobs/view/123?trackingId=latest"
        values = [write_sheet.HEADERS,
                  ["Other", "A", "https://example.com/jobs/1"],
                  ["Other", "B", "https://example.com/jobs/2"],
                  ["Target", "C", link]]
        metadata = {"sheets": [{"properties": {"title": "Jobs", "sheetId": 702182959}}]}
        with mock.patch.object(write_sheet, "gws", side_effect=[metadata, {"values": values}, {}]) as gws:
            write_sheet.delete_row("sheet", 3, expected_link="https://www.linkedin.com/jobs/view/123")
        delete_request = json.loads(gws.call_args_list[-1].args[6])["requests"][0]["deleteDimension"]
        self.assertEqual(delete_request["range"]["startIndex"], 3)

    def test_schema_migration_adds_only_missing_suitability_header_and_validation(self):
        with mock.patch.object(write_sheet, "gws", side_effect=[
            {"values": [write_sheet.HEADERS[:12]]}, {},
        ]) as gws, mock.patch.object(write_sheet, "update_range") as update:
            write_sheet.ensure_extra_headers("sheet")
        update.assert_called_once_with("sheet", "Jobs!M1", [["Suitability"]])
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
        self.assertEqual(write_sheet.TRACKER_RANGE, "Jobs!A:M")
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

    def test_tracker_delete_matches_one_canonical_link_and_invalidates(self):
        tracker = tracker_service.Tracker()
        rows = [
            {"row": 2, "title": "Keep", "company": "A", "link": "https://example.com/jobs/1"},
            {"row": 3, "title": "Remove", "company": "B",
             "link": "https://www.linkedin.com/jobs/view/123?trackingId=old"},
        ]
        with mock.patch.dict("os.environ", {"google_sheet_id": "spreadsheet-id"}), \
                mock.patch.object(write_sheet, "read_tracker", return_value=rows), \
                mock.patch.object(write_sheet, "delete_row") as delete:
            result = tracker.delete_job("https://www.linkedin.com/jobs/view/123?trackingId=new")
        self.assertEqual(result, {"deleted": True})
        delete.assert_called_once_with("spreadsheet-id", 3, expected_link="https://www.linkedin.com/jobs/view/123")

    def test_tracker_delete_rejects_missing_and_duplicate_canonical_links(self):
        tracker = tracker_service.Tracker()
        link = "https://www.linkedin.com/jobs/view/123?trackingId=old"
        base = {"row": 2, "title": "Role", "company": "Acme", "link": link}
        with mock.patch.dict("os.environ", {"google_sheet_id": "spreadsheet-id"}), \
                mock.patch.object(write_sheet, "read_tracker", return_value=[]), \
                mock.patch.object(write_sheet, "delete_row") as delete:
            with self.assertRaises(LookupError):
                tracker.delete_job(link)
        delete.assert_not_called()

        from tracker_service import TrackerConflict
        with mock.patch.dict("os.environ", {"google_sheet_id": "spreadsheet-id"}), \
                mock.patch.object(write_sheet, "read_tracker", return_value=[
                    base, {**base, "row": 3, "link": "https://www.linkedin.com/jobs/view/123?trackingId=duplicate"},
                ]):
            with self.assertRaises(TrackerConflict):
                tracker.delete_job(link)


if __name__ == "__main__":
    unittest.main()
