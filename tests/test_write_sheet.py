import json
import subprocess
import unittest
from unittest import mock

import fixtures  # noqa: F401
import write_sheet
import tracker_service


class GwsCommandTests(unittest.TestCase):
    def test_sheets_mutation_classifier_covers_api_and_gws_write_forms(self):
        writes = (
            ("sheets", "+append", "--spreadsheet", "sid"),
            ("sheets", "spreadsheets", "values", "append"),
            ("sheets", "spreadsheets", "values", "update"),
            ("sheets", "spreadsheets", "values", "batchUpdate"),
            ("sheets", "spreadsheets", "values", "clear"),
            ("sheets", "spreadsheets", "values", "batchClear"),
            ("sheets", "spreadsheets", "batchUpdate"),  # includes row and sheet deletion requests
            ("sheets", "spreadsheets", "create"),
        )
        for command in writes:
            with self.subTest(command=command):
                self.assertTrue(write_sheet.is_sheets_write_operation(command))
        for command in (("sheets", "+read", "--spreadsheet", "sid"),
                        ("sheets", "spreadsheets", "get"),
                        ("drive", "files", "create")):
            with self.subTest(command=command):
                self.assertFalse(write_sheet.is_sheets_write_operation(command))

    def test_all_sheets_mutations_are_blocked_when_opt_in_is_false(self):
        writes = (
            ("sheets", "+append", "--spreadsheet", "sid"),
            ("sheets", "spreadsheets", "values", "append"),
            ("sheets", "spreadsheets", "values", "update"),
            ("sheets", "spreadsheets", "values", "batchUpdate"),
            ("sheets", "spreadsheets", "values", "clear"),
            ("sheets", "spreadsheets", "batchUpdate"),
        )
        with mock.patch.object(write_sheet.subprocess, "run") as run, \
                mock.patch.dict("os.environ", {"GOOGLE_SHEETS_WRITE_ENABLED": "false"}):
            for command in writes:
                with self.subTest(command=command), self.assertRaisesRegex(RuntimeError, "writes are disabled"):
                    write_sheet.gws(*command)
        run.assert_not_called()

    def test_read_only_sheets_calls_work_with_writes_disabled(self):
        command = subprocess.CompletedProcess([], 0, stdout='{"values": []}', stderr="")
        with mock.patch.object(write_sheet.subprocess, "run", return_value=command) as run, \
                mock.patch.dict("os.environ", {"GOOGLE_SHEETS_WRITE_ENABLED": "false"}):
            self.assertEqual(write_sheet.gws("sheets", "+read", "--spreadsheet", "sid"), {"values": []})
        run.assert_called_once()

    def test_oauth_writes_are_also_blocked_when_opt_in_is_false(self):
        with mock.patch.object(write_sheet.subprocess, "run") as run, \
                mock.patch.dict("os.environ", {"GOOGLE_SHEETS_WRITE_ENABLED": "false"}):
            with self.assertRaisesRegex(RuntimeError, "writes are disabled"):
                write_sheet.gws("sheets", "spreadsheets", "values", "append")
        run.assert_not_called()

    def test_oauth_write_succeeds_after_explicit_opt_in(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(tuple(command))
            return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

        with mock.patch.object(write_sheet.subprocess, "run", side_effect=fake_run), \
                mock.patch.dict("os.environ", {"google_sheet_id": "existing-sheet",
                                               "GOOGLE_SHEETS_WRITE_ENABLED": "true"}):
            write_sheet.gws("sheets", "+append", "--spreadsheet", "existing-sheet", "--json-values", "[]")
        calls = [c[len(write_sheet.GWS_CMD):] for c in calls]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ("sheets", "+append"))

    def test_write_opt_in_must_be_explicit(self):
        with mock.patch.object(write_sheet.subprocess, "run") as run, \
                mock.patch.dict("os.environ", {"google_sheet_id": "existing-sheet",
                                               "GOOGLE_SHEETS_WRITE_ENABLED": "false"}):
            with self.assertRaisesRegex(RuntimeError, "writes are disabled"):
                write_sheet.gws("sheets", "+append", "--spreadsheet", "existing-sheet", "--json-values", "[]")
        run.assert_not_called()

    def test_tracker_id_is_required_and_no_replacement_is_created(self):
        with mock.patch.dict("os.environ", {"google_sheet_id": ""}), \
                mock.patch.object(write_sheet, "gws") as gws:
            with self.assertRaisesRegex(RuntimeError, "existing tracker spreadsheet"):
                write_sheet.get_or_create_sheet_id()
        gws.assert_not_called()

    def test_tracker_read_without_id_does_not_search_drive(self):
        with mock.patch.dict("os.environ", {"google_sheet_id": "  "}), \
                mock.patch.object(write_sheet, "find_sheet_by_title") as find_sheet:
            tracker = tracker_service.Tracker()
            self.assertFalse(tracker.snapshot()["configured"])
        find_sheet.assert_not_called()

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

    def test_delete_rows_resolves_stable_links_and_batches_descending_positions(self):
        first = "https://example.com/jobs/first"
        second = "https://example.com/jobs/second"
        missing = "https://example.com/jobs/missing"
        rows = [
            {"row": 2, "title": "Unrelated", "company": "Keep", "link": "https://example.com/jobs/keep"},
            {"row": 4, "title": "First", "company": "A", "link": first},
            {"row": 9, "title": "Second", "company": "B", "link": second},
        ]
        metadata = {"sheets": [{"properties": {"title": "Jobs", "sheetId": 702182959}}]}
        with mock.patch.object(write_sheet, "gws", side_effect=[metadata, {}]) as gws, \
                mock.patch.object(write_sheet, "read_tracker", return_value=rows):
            outcomes = write_sheet.delete_rows("sheet", [first, second, missing])
        requests = json.loads(gws.call_args_list[-1].args[6])["requests"]
        ranges = [request["deleteDimension"]["range"] for request in requests]
        self.assertEqual([r["startIndex"] for r in ranges], [8, 3])
        self.assertTrue(all(r["sheetId"] == 702182959 and r["dimension"] == "ROWS" for r in ranges))
        self.assertEqual([outcome["status"] for outcome in outcomes], ["deleted", "deleted", "failed"])
        self.assertEqual(outcomes[-1]["code"], "not_found")
        self.assertEqual([outcome["title"] for outcome in outcomes[:2]], ["First", "Second"])
        self.assertEqual(gws.call_count, 2)

    def test_delete_rows_reports_missing_and_ambiguous_without_mutating(self):
        first = "https://www.linkedin.com/jobs/view/123"
        missing = "https://example.com/jobs/missing"
        metadata = {"sheets": [{"properties": {"title": "Jobs", "sheetId": 12}}]}
        rows = [{"row": 2, "link": first}, {"row": 3, "link": first + "?trackingId=duplicate"}]
        with mock.patch.object(write_sheet, "gws", return_value=metadata) as gws, \
                mock.patch.object(write_sheet, "read_tracker", return_value=rows):
            outcomes = write_sheet.delete_rows("sheet", [missing, first])
        self.assertEqual([result["code"] for result in outcomes], ["not_found", "tracker_conflict"])
        self.assertEqual(gws.call_count, 1)

    def test_delete_rows_rejects_empty_or_duplicate_links(self):
        with mock.patch.object(write_sheet, "gws") as gws:
            for links in ([], ["https://example.com/jobs/1", "https://example.com/jobs/1?utm_source=x"]):
                with self.assertRaises(ValueError):
                    write_sheet.delete_rows("sheet", links)
        gws.assert_not_called()

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
                write_sheet.gws("sheets", "spreadsheets", "get", "--spreadsheet", "spreadsheet-id")
        with mock.patch.object(write_sheet.subprocess, "run", return_value=completed):
            try:
                write_sheet.gws("sheets", "spreadsheets", "get", "--spreadsheet", "spreadsheet-id")
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

    def test_tracker_bulk_delete_invalidates_after_success_and_preserves_partial_outcomes(self):
        tracker = tracker_service.Tracker()
        tracker._cache = (1, [{"link": "old"}])
        links = ["https://example.com/jobs/1", "https://example.com/jobs/2"]
        outcomes = [
            {"status": "deleted", "row": 2, "title": "Role 1", "company": "A", "link": links[0]},
            {"status": "failed", "code": "not_found", "message": "Application not found"},
        ]
        with mock.patch.dict("os.environ", {"google_sheet_id": "spreadsheet-id"}), \
                mock.patch.object(write_sheet, "delete_rows", return_value=outcomes) as delete, \
                mock.patch("activity.log_event") as event:
            result = tracker.delete_jobs(links)
        delete.assert_called_once_with("spreadsheet-id", links)
        self.assertEqual(result, outcomes)
        self.assertIsNone(tracker._cache)
        event.assert_called_once()

    def test_tracker_bulk_delete_fails_closed_when_tracker_is_unavailable(self):
        tracker = tracker_service.Tracker()
        with mock.patch.dict("os.environ", {"google_sheet_id": "spreadsheet-id"}), \
                mock.patch.object(write_sheet, "delete_rows", side_effect=RuntimeError("offline")) as delete:
            with self.assertRaises(tracker_service.TrackerError):
                tracker.delete_jobs(["https://example.com/jobs/1"])
        delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
