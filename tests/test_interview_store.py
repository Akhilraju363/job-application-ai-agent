import fixtures  # noqa: F401
import json
import re
import unittest
from unittest import mock

import interview_store


class InterviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.rows = [interview_store.HEADERS]
        self.update_calls = []
        self.gws = mock.Mock(side_effect=self._gws)
        self.patches = [
            mock.patch.object(interview_store, "_spreadsheet_id", return_value="sheet"),
            mock.patch.object(interview_store, "_ensure_tab"),
            mock.patch.object(interview_store, "_tab", return_value=99),
            mock.patch.object(interview_store.write_sheet, "gws", self.gws),
            mock.patch.object(interview_store.write_sheet, "update_range", side_effect=self._update),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()

    def _gws(self, *args):
        if args[:3] == ("sheets", "+read", "--spreadsheet"):
            return {"values": [list(row) for row in self.rows]}
        if args[:4] == ("sheets", "spreadsheets", "values", "append"):
            body = json.loads(args[-1])
            self.rows.extend(body["values"])
            return {}
        if args[:3] == ("sheets", "spreadsheets", "batchUpdate"):
            body = json.loads(args[-1])
            request = body["requests"][0]
            if "deleteDimension" in request:
                index = request["deleteDimension"]["range"]["startIndex"]
                del self.rows[index]
            return {}
        return {}

    def _update(self, _sid, cell_range, values, value_input_option="USER_ENTERED"):
        self.update_calls.append((_sid, cell_range, values, value_input_option))
        match = re.search(r"A(\d+):U\d+", cell_range)
        if match:
            self.rows[int(match.group(1)) - 1] = values[0]

    def body(self):
        return {"company": "Acme", "job_title": "Java Engineer", "interview_date": "2026-11-05",
                "interview_time": "10:30", "interview_round": "Technical", "mode": "Online",
                "meeting_url": "https://example.test/meet", "reminder_enabled": True,
                "reminder_minutes_before": 60}

    def test_crud_persists_and_filters(self):
        row = interview_store.create_interview(self.body())
        self.assertEqual(interview_store.get_interview(row["id"])["company"], "Acme")
        self.assertEqual(len(interview_store.list_interviews(date_from="2026-11-01", company="ac")), 1)
        changed = interview_store.update_interview(row["id"], {"status": "Completed", "outcome": "Selected"})
        self.assertEqual(changed["status"], "Completed")
        self.assertEqual(interview_store.get_interview(row["id"])["outcome"], "Selected")
        interview_store.delete_interview(row["id"])
        self.assertIsNone(interview_store.get_interview(row["id"]))

    def test_formula_like_text_is_written_as_raw_for_create_and_update(self):
        body = self.body()
        body["company"] = "=1+1"
        row = interview_store.create_interview(body)
        append_call = next(
            call for call in self.gws.call_args_list
            if call.args[:4] == ("sheets", "spreadsheets", "values", "append")
        )
        self.assertEqual(json.loads(append_call.args[5])["valueInputOption"], "RAW")
        self.assertEqual(json.loads(append_call.args[7])["values"][0][2], "=1+1")

        interview_store.update_interview(row["id"], {"notes": "=1+1"})
        self.assertEqual(self.update_calls[-1][3], "RAW")
        self.assertEqual(self.update_calls[-1][2][0][14], "=1+1")

    def test_rejects_invalid_dates_times_url_and_status(self):
        for key, value in (("interview_date", "05/11/2026"), ("interview_time", "9:00"),
                           ("meeting_url", "not-a-url"), ("status", "Unknown")):
            body = self.body()
            body[key] = value
            with self.assertRaises(interview_store.InterviewError):
                interview_store.create_interview(body)

    def test_reminder_boolean_normalization_accepts_only_supported_values(self):
        body = self.body()
        for value, expected in (
            (True, True), (False, False), ("true", True), ("false", False),
            (1, True), (0, False), ("1", True), ("0", False),
            ("yes", True), ("no", False), ("", False), (None, False),
        ):
            with self.subTest(value=value):
                normalized = interview_store._normalise({**body, "reminder_enabled": value})
                self.assertEqual(normalized["reminder_enabled"], expected)
        body_without_reminder = self.body()
        body_without_reminder.pop("reminder_enabled")
        self.assertFalse(interview_store._normalise(body_without_reminder)["reminder_enabled"])
        existing = interview_store._normalise(self.body())
        self.assertTrue(interview_store._normalise({}, existing=existing, partial=True)["reminder_enabled"])
        for value in ("enabled", "maybe", 2, -1, 1.0, []):
            with self.subTest(invalid=value), self.assertRaisesRegex(interview_store.InterviewError, "must be a boolean"):
                interview_store._normalise({**body, "reminder_enabled": value})

    def test_malformed_sheet_id_is_explicit_error(self):
        bad = interview_store.HEADERS.copy()
        bad[0] = "not-an-id"
        self.rows.append(bad)
        with self.assertRaises(interview_store.InterviewError):
            interview_store.list_interviews()

    def test_update_preserves_existing_fields_and_delete_is_scoped(self):
        first = interview_store.create_interview({**self.body(), "application_id": "app-1", "notes": "Keep this note"})
        second = interview_store.create_interview({**self.body(), "company": "Other"})
        updated = interview_store.update_interview(first["id"], {"company": "Acme Updated"})
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["notes"], "Keep this note")
        self.assertEqual(updated["application_id"], "app-1")
        interview_store.delete_interview(first["id"])
        self.assertIsNone(interview_store.get_interview(first["id"]))
        self.assertIsNotNone(interview_store.get_interview(second["id"]))
        with self.assertRaises(KeyError):
            interview_store.delete_interview(first["id"])

    def test_delete_uses_dynamic_sheet_id_and_zero_based_data_row(self):
        row = interview_store.create_interview(self.body())
        interview_store.delete_interview(row["id"])
        delete_calls = [
            call for call in self.gws.call_args_list
            if call.args[:3] == ("sheets", "spreadsheets", "batchUpdate")
            and "deleteDimension" in json.loads(call.args[-1])["requests"][0]
        ]
        self.assertEqual(len(delete_calls), 1)
        request = json.loads(delete_calls[0].args[-1])["requests"][0]["deleteDimension"]
        self.assertEqual(request["range"], {
            "sheetId": 99, "dimension": "ROWS", "startIndex": 1, "endIndex": 2,
        })

    def test_delete_resolves_interview_row_after_it_moves(self):
        row = interview_store.create_interview(self.body())

        def insert_blank_row(_sid, create=True):
            self.rows.insert(1, [""] * len(interview_store.HEADERS))
            return 99

        interview_store._tab.side_effect = insert_blank_row
        interview_store.delete_interview(row["id"])
        delete_call = next(
            call for call in self.gws.call_args_list
            if call.args[:3] == ("sheets", "spreadsheets", "batchUpdate")
            and "deleteDimension" in json.loads(call.args[-1])["requests"][0]
        )
        request = json.loads(delete_call.args[-1])["requests"][0]["deleteDimension"]
        self.assertEqual(request["range"]["startIndex"], 2)
        self.assertIsNone(interview_store.get_interview(row["id"]))

    def test_delete_surfaces_sheet_failure(self):
        row = interview_store.create_interview(self.body())
        self.gws.side_effect = lambda *args: (_ for _ in ()).throw(RuntimeError("Sheets unavailable")) if (
            args[:3] == ("sheets", "spreadsheets", "batchUpdate")
            and "deleteDimension" in json.loads(args[-1])["requests"][0]
        ) else self._gws(*args)
        with self.assertRaises(interview_store.StorageError):
            interview_store.delete_interview(row["id"])
        self.assertIsNotNone(interview_store.get_interview(row["id"]))

    def test_duplicate_id_in_sheet_is_rejected(self):
        row = interview_store.create_interview(self.body())
        self.rows.append(interview_store._to_sheet(row))
        with self.assertRaises(interview_store.InterviewError):
            interview_store.list_interviews()


if __name__ == "__main__":
    unittest.main()
