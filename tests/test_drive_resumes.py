"""Google Drive persistence of generated resumes: upload, idempotency, failure handling, the
tracker never receiving a local path. `gws` is replaced by an in-memory FakeDrive -- nothing
external is touched.
"""
import os
import unittest
from unittest import mock

from fixtures import ANALYSIS, BASE, JD_TEXT, FakeDrive, TempOutput, reorder_bullets
import drive_resumes as dr
import jd_analysis as ja
import resume_store
import tailoring_service as ts
import tracker_service
import write_sheet

PDF_URL = "https://drive.google.com/file/d/ABC123/view"


def make_resume(company="Acme", title="Java Dev", fmts=("pdf",), versions=1):
    rid = resume_store.create(source="manual", job={"title": title, "company": company, "link": "manual:abc123def456",
                                                    "description": JD_TEXT},
                              analysis=ANALYSIS, match=ja.compute_match(ANALYSIS, BASE), provider="test")
    for n in range(1, versions + 1):
        resume_store.add_version(rid, reorder_bullets(BASE), "generated",
                                 {"ok": True, "problems": [], "warnings": [], "ats": {"ok": True, "checks": []}, "attempts": 1})
        for fmt in fmts:
            resume_store.version_path(rid, n, fmt).write_bytes(b"%PDF-fake" if fmt == "pdf" else b"PK-fake")
            resume_store.mark_export(rid, n, fmt)
    return rid


class DriveCase(unittest.TestCase):
    def setUp(self):
        self.out = TempOutput()
        self.dir = self.out.__enter__()
        self.drive = FakeDrive()
        self.patches = [mock.patch.object(dr, "_gws", side_effect=self.drive),
                        mock.patch.object(dr, "RETRY_DELAY_SECONDS", 0),
                        mock.patch.dict(os.environ, {"google_drive_folder_id": "PARENT"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.out.__exit__(None, None, None)


class Upload(DriveCase):
    def test_uploads_the_pdf_into_the_per_job_folder_and_returns_the_drive_link(self):
        rid = make_resume()
        r = dr.upload_resume(rid, 1, "pdf")
        (f,) = self.drive.resume_files()
        (folder,) = self.drive.folders()
        self.assertEqual((f["name"], f["appProperties"]), ("Jane_Roe_Java_Developer.pdf", {"resume_key": f"{rid}-v1-pdf"}))
        self.assertEqual((folder["name"], folder["parents"], f["parents"]), ("Acme-java-dev", ["PARENT"], [folder["id"]]))
        self.assertEqual(r["url"], f["webViewLink"])                       # Google's own link, used as given
        self.assertTrue(r["url"].startswith("https://drive.google.com/file/d/"))
        self.assertEqual((r["file_id"], r["reused"]), (f["id"], False))
        up = self.drive.uploads[0]
        self.assertEqual((up["content_type"], up["cwd"]), ("application/pdf", str(resume_store.version_path(rid, 1).parent.resolve())))

    def test_falls_back_to_the_standard_view_url_only_if_google_gives_no_link(self):
        self.assertEqual(dr._link({"id": "XYZ"}), "https://drive.google.com/file/d/XYZ/view")
        self.assertEqual(dr._link({"id": "XYZ", "webViewLink": "https://drive.google.com/file/d/XYZ/view?usp=drivesdk"}),
                         "https://drive.google.com/file/d/XYZ/view?usp=drivesdk")

    def test_pdf_and_docx_are_both_uploaded_with_their_own_mime_type_and_key(self):
        rid = make_resume(fmts=("pdf", "docx"))
        pdf, docx = dr.upload_resume(rid, 1, "pdf"), dr.upload_resume(rid, 1, "docx")
        self.assertNotEqual(pdf["file_id"], docx["file_id"])
        self.assertEqual([(u["name"], u["content_type"]) for u in self.drive.uploads],
                         [("Jane_Roe_Java_Developer.pdf", "application/pdf"),
                          ("Jane_Roe_Java_Developer.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")])
        self.assertEqual(len(self.drive.folders()), 1)                    # one job folder for both

    def test_blank_company_manual_jd_uploads_under_company_not_specified(self):
        job = ja.normalize_job("JAVA DEVELOPER", "", "", JD_TEXT)
        rid = make_resume(company=job["company"], title=job["title"])
        dr.upload_resume(rid, 1, "pdf")
        self.assertEqual(self.drive.folders()[0]["name"], "Company Not Specified-java-developer")

    def test_scraped_job_with_a_real_company_uploads_under_that_company(self):
        rid = make_resume(company="ABC Technologies", title="Java Developer")
        dr.upload_resume(rid, 1, "pdf")
        self.assertEqual(self.drive.folders()[0]["name"], "ABC Technologies-java-developer")

    def test_folder_names_are_safe(self):
        self.assertEqual(dr.folder_name({"company": "A/B\\C", "title": "!!!"}), "A B C-job")
        self.assertEqual(dr.folder_name({"company": "", "title": "Java Dev"}), "Company Not Specified-java-dev")


class Idempotency(DriveCase):
    def test_same_resume_version_and_format_reuses_the_existing_drive_file(self):
        rid = make_resume()
        first, second = dr.upload_resume(rid, 1, "pdf"), dr.upload_resume(rid, 1, "pdf")
        self.assertEqual(len(self.drive.resume_files()), 1)
        self.assertEqual(len(self.drive.folders()), 1)
        self.assertEqual((first["reused"], second["reused"]), (False, True))
        self.assertEqual((first["url"], first["file_id"]), (second["url"], second["file_id"]))
        self.assertEqual(len(self.drive.uploads), 1)

    def test_a_new_version_is_a_new_file(self):
        rid = make_resume(versions=2)
        v1, v2 = dr.upload_resume(rid, 1, "pdf"), dr.upload_resume(rid, 2, "pdf")
        self.assertNotEqual(v1["file_id"], v2["file_id"])
        self.assertEqual(sorted(f["name"] for f in self.drive.resume_files()), ["Jane_Roe_Java_Developer.pdf"] * 2)   # no version in the name; keyed by appProperties

    def test_identity_is_independent_of_the_folder(self):
        rid = make_resume(company="Acme")
        first = dr.upload_resume(rid, 1, "pdf")
        resume_store.update_meta(rid, job={"title": "Java Dev", "company": "Renamed Co", "link": "manual:abc123def456"})
        again = dr.upload_resume(rid, 1, "pdf")
        self.assertEqual((again["file_id"], again["reused"]), (first["file_id"], True))
        self.assertEqual(len(self.drive.resume_files()), 1)

    def test_a_create_whose_response_was_lost_is_not_duplicated_by_the_retry(self):
        rid = make_resume()
        self.drive.fail_after_create = 1                                   # file lands on Drive, gws then errors out
        r = dr.upload_resume(rid, 1, "pdf")
        self.assertEqual(len(self.drive.resume_files()), 1)
        self.assertEqual((r["reused"], r["file_id"]), (True, self.drive.resume_files()[0]["id"]))

    def test_retry_after_a_sheet_failure_reuses_the_drive_file(self):
        rid = make_resume()
        with mock.patch.object(write_sheet, "append_rows", side_effect=RuntimeError("sheets down")), \
                mock.patch.object(write_sheet, "ensure_extra_headers"), mock.patch.object(write_sheet, "read_tracker", return_value=[]), \
                mock.patch.dict(os.environ, {"google_sheet_id": "SID"}), mock.patch("activity.log_event"):
            tr = tracker_service.Tracker()
            for attempt in range(2):
                url = dr.upload_and_record(rid, 1, "pdf")["url"]
                with self.assertRaises(tracker_service.TrackerError):
                    tr.save_job(title="Java Dev", company="Acme", link="manual:abc123def456", score=8, resume_path=url)
        self.assertEqual(len(self.drive.resume_files()), 1)                # two attempts, one Drive file


class Failures(DriveCase):
    def test_failure_is_reported_records_no_url_and_keeps_the_local_file(self):
        rid = make_resume()
        self.drive.fail = "network unreachable"
        with self.assertRaises(dr.DriveUploadError) as cm:
            dr.upload_and_record(rid, 1, "pdf")
        self.assertIn("Google Drive upload failed: network unreachable", str(cm.exception))
        self.assertEqual(self.drive.resume_files(), [])
        rec = resume_store.get(rid)["versions"][0]
        self.assertEqual(rec["drive"]["pdf"]["status"], "failed")
        self.assertNotIn("url", rec["drive"]["pdf"])
        self.assertTrue(resume_store.version_path(rid, 1, "pdf").exists())  # local artifact untouched
        self.assertTrue(resume_store.version_path(rid, 1, "md").exists())
        result = ts.to_result(resume_store.get(rid))["resume"]
        self.assertIsNone(result["drive_url"])
        self.assertIn("network unreachable", result["drive_error"])

    def test_transient_failure_is_retried_once_then_succeeds(self):
        rid = make_resume()
        real = FakeDrive()
        calls = {"n": 0}

        def flaky(args, cwd=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("HTTP 503 backend error")
            return real(args, cwd)

        with mock.patch.object(dr, "_gws", side_effect=flaky):
            self.assertFalse(dr.upload_resume(rid, 1, "pdf")["reused"])
        self.assertEqual(len(real.resume_files()), 1)

    def test_expired_auth_is_not_retried_and_says_how_to_reauthenticate(self):
        rid = make_resume()
        self.drive.fail = "invalid_grant: Token has been expired or revoked"
        with self.assertRaises(dr.DriveUploadError) as cm:
            dr.upload_resume(rid, 1, "pdf")
        self.assertIn("gws auth login", str(cm.exception))
        self.assertEqual(len(self.drive.calls), 1)                         # no pointless retry of a dead login

    def test_missing_parent_folder_config_is_a_clear_error_and_creates_nothing(self):
        rid = make_resume()
        with mock.patch.dict(os.environ, {"google_drive_folder_id": ""}), self.assertRaises(dr.DriveUploadError) as cm:
            dr.upload_resume(rid, 1, "pdf")
        self.assertIn("google_drive_folder_id", str(cm.exception))
        self.assertEqual(self.drive.files, [])

    def test_missing_local_file_or_bad_format_never_reaches_drive(self):
        rid = make_resume(fmts=())
        for fmt in ("pdf", "exe"):
            with self.assertRaises(dr.DriveUploadError):
                dr.upload_resume(rid, 1, fmt)
        self.assertEqual(self.drive.calls, [])

    def test_a_later_success_replaces_the_recorded_failure(self):
        rid = make_resume()
        self.drive.fail = "boom"
        with self.assertRaises(dr.DriveUploadError):
            dr.upload_and_record(rid, 1, "pdf")
        self.drive.fail = None
        dr.upload_and_record(rid, 1, "pdf")
        result = ts.to_result(resume_store.get(rid))["resume"]
        self.assertTrue(result["drive_url"].startswith("https://drive.google.com/"))
        self.assertIsNone(result["drive_error"])


class SheetValue(DriveCase):
    """The tracker's Resume column is the Drive URL -- never the local path."""

    LOCAL = "output/generated_resumes/e4b10170c3fe/v1.md"

    def setUp(self):
        super().setUp()
        self.rows, self.appended, self.updates = [], [], []
        self.tp = [mock.patch.dict(os.environ, {"google_sheet_id": "SID"}),
                   mock.patch.object(write_sheet, "ensure_extra_headers"),
                   mock.patch.object(write_sheet, "read_tracker", side_effect=lambda sid: self.rows),
                   mock.patch.object(write_sheet, "append_rows", side_effect=lambda sid, rows: self.appended.extend(rows)),
                   mock.patch.object(write_sheet, "update_range", side_effect=lambda sid, rng, vals: self.updates.append((rng, vals))),
                   mock.patch("activity.log_event")]
        for p in self.tp:
            p.start()
        self.tr = tracker_service.Tracker()

    def tearDown(self):
        for p in self.tp:
            p.stop()
        super().tearDown()

    def test_sheet_receives_the_drive_url_and_not_the_local_path(self):
        self.tr.save_job(title="Java Dev", company="Acme", link="https://acme.example/1", score=8, resume_path=PDF_URL,
                         resume_id="e4b10170c3fe-v1", match_pct=71)
        (row,) = self.appended
        got = dict(zip(write_sheet.HEADERS, row))
        self.assertEqual(got["Resume Path"], PDF_URL)
        self.assertNotIn(self.LOCAL, row)
        self.assertFalse(any("output/generated_resumes" in c or "\\Users\\" in c for c in row))
        self.assertEqual((got["Resume ID"], got["Match %"], got["Status"]), ("e4b10170c3fe-v1", "71", "Not Applied"))

    def test_a_local_path_is_refused_and_nothing_is_written(self):
        for bad in (self.LOCAL, r"C:\Users\me\output\v1.pdf", "/app/output/v1.pdf", "/root/output/v1.pdf", "v1.md"):
            with self.assertRaises(ValueError, msg=bad):
                self.tr.save_job(title="T", company="C", link="https://x.example/1", score=8, resume_path=bad)
        self.assertEqual((self.appended, self.updates), ([], []))

    def test_a_legacy_local_path_in_an_existing_row_is_replaced_by_the_drive_url(self):
        self.rows = [{"row": 5, "title": "T", "company": "C", "link": "https://x.example/1", "resume_path": self.LOCAL, "status": "Applied"}]
        self.assertEqual(self.tr.save_job(title="T", company="C", link="https://x.example/1", score=8, resume_path=PDF_URL)["result"], "exists")
        self.assertEqual((self.appended, self.updates), ([], [("Sheet1!E5", [[PDF_URL]])]))

    def test_an_existing_drive_link_is_not_overwritten(self):
        self.rows = [{"row": 5, "title": "T", "company": "C", "link": "https://x.example/1", "resume_path": PDF_URL, "status": "Applied"}]
        self.tr.save_job(title="T", company="C", link="https://x.example/1", score=8,
                         resume_path="https://drive.google.com/file/d/OTHER/view")
        self.assertEqual(self.updates, [])


if __name__ == "__main__":
    unittest.main()
