"""HTTP-level tests for the dashboard server. Real server on an ephemeral port; the tracker
(Google Sheet), LLM, gws and subprocesses are all mocked -- nothing external is touched.
"""
import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from contextlib import contextmanager

from fixtures import ANALYSIS, BASE, JD_TEXT, FakeDrive, TempOutput, reorder_bullets
import dashboard_data as dd
import dashboard_server as srv
import dashboard_tasks as tasks
import drive_resumes
import paths
import resume_store

LINK = "https://in.linkedin.com/jobs/view/java-dev-123?trackingId=abc"
KEY = dd.job_key(LINK)
SECRETS = {"apify_api_key": "apify-SECRET-123456", "GROQ_API_KEY": "gsk_SECRET_abcdef", "TELEGRAM_BOT_TOKEN": "123456:TG-SECRET",
           "google_sheet_id": "SHEETID_SECRET_1"}


def tracker_state(rows=(), available=True, error=None):
    return {"rows": list(rows), "available": available, "error": error, "stale": False, "configured": True}


class ServerCase(unittest.TestCase):
    tracker_rows = ()

    @classmethod
    def setUpClass(cls):
        cls.out = TempOutput()
        cls.dir = cls.out.__enter__()
        base_file = cls.dir / "base_resume.md"
        base_file.write_text(BASE, encoding="utf-8")
        cls.patches = [
            mock.patch.object(paths, "BASE_RESUME", base_file),
            mock.patch.dict(os.environ, {**SECRETS, "DASHBOARD_TOKEN": "", "google_drive_folder_id": "PARENT_FOLDER"}),
            mock.patch.object(srv.tracker, "snapshot", side_effect=lambda force=False: tracker_state(cls.tracker_rows)),
            # tests never reach real Google Drive: any un-mocked upload fails fast (see ResumeActions.fake_drive)
            mock.patch.object(drive_resumes, "_gws", side_effect=RuntimeError("no real Google Drive in tests")),
            mock.patch.object(drive_resumes, "RETRY_DELAY_SECONDS", 0),
        ]
        for p in cls.patches:
            p.start()
        raw = [{"title": "Java Dev", "company": "Acme", "link": LINK, "description": JD_TEXT, "posted_date": "2026-09-19",
                "found_at": "2026-09-19", "location": "Bengaluru", "source": "LinkedIn"},
               {"title": "Low Fit", "company": "Beta", "link": "https://in.linkedin.com/jobs/view/low-9", "description": JD_TEXT,
                "posted_date": "2026-09-18"}]
        (cls.dir / "raw_jobs.json").write_text(json.dumps(raw))
        (cls.dir / "scored_jobs.json").write_text(json.dumps([{**raw[0], "score": 9, "qualified": True, "reasoning": "good",
                                                                 "matched_must_haves": ["Java"], "missing_must_haves": ["Kafka"]},
                                                                {**raw[1], "score": 4, "qualified": False}]))
        (cls.dir / "tailored_jobs.json").write_text("[]")
        cls.server = srv.make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.out.__exit__(None, None, None)

    def call(self, method, path, body=None, headers=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = dict(headers or {})
        payload = None
        if method != "GET":
            h.setdefault("Content-Type", "application/json")
            payload = json.dumps(body if body is not None else {})
        conn.request(method, path, body=payload, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        if raw:
            return r.status, dict(r.getheaders()), data
        return r.status, dict(r.getheaders()), (json.loads(data) if data and r.getheader("Content-Type", "").startswith("application/json") else data)

    def wait_task(self, task_id, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            _, _, t = self.call("GET", f"/api/tasks/{task_id}")
            if t["status"] in ("done", "error"):
                return t
            time.sleep(0.05)
        self.fail("task did not finish")

    def make_resume(self, ok=True, source="manual", link="manual:abcdef123456"):
        rid = resume_store.create(source=source, job={"title": "Java Dev", "company": "Acme", "link": link, "description": JD_TEXT},
                                  analysis=ANALYSIS, match=dd.__dict__ and __import__("jd_analysis").compute_match(ANALYSIS, BASE), provider="test")
        resume_store.add_version(rid, reorder_bullets(BASE), "generated",
                                 {"ok": ok, "problems": [] if ok else ["bad"], "warnings": [], "ats": {"ok": ok, "checks": []}, "attempts": 1})
        return rid


class Security(ServerCase):
    def test_serves_the_app_shell_and_client_routes(self):
        for path in ("/", "/tailor-resume", "/applications", "/find-jobs?x=1"):
            s, h, body = self.call("GET", path, raw=True)
            self.assertEqual(s, 200, path)
            self.assertIn(b"<div id=\"app\">", body)
        s, h, _ = self.call("GET", "/js/app.js", raw=True)
        self.assertEqual((s, h["Content-Type"].split(";")[0]), (200, "text/javascript"))

    def test_security_headers(self):
        _, h, _ = self.call("GET", "/", raw=True)
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["X-Frame-Options"], "DENY")

    def test_no_path_traversal_or_dotfiles(self):
        for path in ("/../.env", "/../CLAUDE.md", "/js/../../.env", "/.env", "/js/nope.js"):
            with socket.create_connection(("127.0.0.1", self.port)) as sock:
                sock.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nConnection: close\r\n\r\n".encode())
                reply = b""
                while chunk := sock.recv(65536):
                    reply += chunk
            self.assertIn(b" 404 ", reply.split(b"\r\n")[0], path)
            self.assertNotIn(b"apify", reply.lower())

    def test_unknown_host_header_is_refused(self):
        s, _, _ = self.call("GET", "/api/me", headers={"Host": "evil.example.com"})
        self.assertEqual(s, 403)

    def test_state_changing_requests_need_json_and_same_origin(self):
        s, _, _ = self.call("POST", "/api/tailor", headers={"Content-Type": "text/plain"})
        self.assertEqual(s, 415)
        s, _, _ = self.call("POST", "/api/tailor", headers={"Origin": "https://evil.example.com"})
        self.assertEqual(s, 403)
        s, _, _ = self.call("POST", "/api/tailor", {"title": "x"}, headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(s, 400)  # passed the origin check, failed input validation

    def test_bad_json_and_oversize_bodies(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/api/tailor", body="{not json", headers={"Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 400)
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/api/tailor", body=b"x", headers={"Content-Type": "application/json", "Content-Length": str(srv.MAX_BODY + 1)})
        self.assertEqual(conn.getresponse().status, 413)

    def test_bearer_token_gates_the_api_when_configured(self):
        with mock.patch.dict(os.environ, {"DASHBOARD_TOKEN": "s3cret-token"}):
            self.assertEqual(self.call("GET", "/api/me")[0], 401)
            self.assertEqual(self.call("GET", "/api/me", headers={"Authorization": "Bearer wrong"})[0], 401)
            self.assertEqual(self.call("GET", "/api/me", headers={"Authorization": "Bearer s3cret-token"})[0], 200)
            s, _, body = self.call("GET", "/api/auth")
            self.assertEqual((s, body), (200, {"required": True}))
        self.assertEqual(self.call("GET", "/api/auth")[2], {"required": False})

    def test_refuses_to_bind_publicly_without_a_token(self):
        with mock.patch.dict(os.environ, {"DASHBOARD_TOKEN": ""}), self.assertRaises(SystemExit):
            srv.make_server("0.0.0.0", 0)

    def test_errors_do_not_leak_internals(self):
        with mock.patch.object(dd, "load_artifacts", side_effect=RuntimeError("secret path C:\\x")):
            with mock.patch("traceback.print_exc"):
                s, _, body = self.call("GET", "/api/jobs")
        self.assertEqual(s, 500)
        self.assertNotIn("secret path", json.dumps(body))

    def test_settings_and_sources_never_contain_secrets(self):
        for path in ("/api/settings", "/api/dashboard/sources", "/api/me"):
            _, _, raw = self.call("GET", path, raw=True)
            for value in SECRETS.values():
                self.assertNotIn(value.encode(), raw, path)


class DashboardEndpoints(ServerCase):
    def test_summary_overview_status_use_real_data(self):
        s, _, d = self.call("GET", "/api/dashboard/summary?range=30d")
        self.assertEqual(s, 200)
        self.assertEqual(d["metrics"]["jobs_found"]["current"], 2)
        s, _, o = self.call("GET", "/api/dashboard/overview?range=30d")
        self.assertEqual({x["key"]: x["value"] for x in o["series"]}["qualified"], 1)
        s, _, st = self.call("GET", "/api/dashboard/status")
        self.assertEqual((st["total"], len(st["statuses"])), (0, 5))

    def test_custom_and_invalid_ranges(self):
        self.assertEqual(self.call("GET", "/api/dashboard/summary?range=custom&from=2026-09-01&to=2026-09-30")[0], 200)
        for q in ("range=custom", "range=1y", "range=custom&from=2026-09-30&to=2026-09-01"):
            self.assertEqual(self.call("GET", f"/api/dashboard/summary?{q}")[0], 400, q)

    def test_latest_jobs_and_activity(self):
        _, _, d = self.call("GET", "/api/dashboard/jobs?limit=5")
        self.assertEqual([j["title"] for j in d["jobs"]], ["Java Dev", "Low Fit"])
        self.assertEqual(d["jobs"][0]["match_pct"], 90)
        self.assertNotIn("description", d["jobs"][0])
        self.assertEqual(self.call("GET", "/api/dashboard/activity")[2], {"events": []})

    def test_jobs_list_filters_and_detail(self):
        self.assertEqual(self.call("GET", "/api/jobs?filter=qualified")[2]["total"], 1)
        self.assertEqual(self.call("GET", "/api/jobs?filter=below")[2]["jobs"][0]["title"], "Low Fit")
        self.assertEqual(self.call("GET", "/api/jobs?q=acme")[2]["total"], 1)
        self.assertEqual(self.call("GET", "/api/jobs?filter=bogus")[0], 400)
        s, _, j = self.call("GET", f"/api/jobs/{KEY}")
        self.assertEqual((s, j["missing"], j["description"][:10]), (200, ["Kafka"], JD_TEXT[:10]))
        self.assertEqual(self.call("GET", "/api/jobs/" + "0" * 12)[0], 404)

    def test_tracker_outage_degrades_sections_instead_of_failing(self):
        with mock.patch.object(srv.tracker, "snapshot", return_value=tracker_state(available=False, error="gws: not signed in")):
            s, _, d = self.call("GET", "/api/dashboard/summary")
            self.assertEqual(s, 200)
            self.assertIsNone(d["metrics"]["applications_sent"])
            self.assertEqual(d["metrics"]["jobs_found"]["current"], 2)
            self.assertEqual(self.call("GET", "/api/dashboard/status")[2]["tracker"]["error"], "gws: not signed in")
            self.assertEqual(self.call("GET", "/api/dashboard/jobs")[0], 200)

    def test_unknown_route_and_method(self):
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/jobs")[0], 405)


class TailoringEntryPoints(ServerCase):
    """Manual JD and scraped job must go through the exact same service function."""

    def fake_service(self, description, title, company, url, source, job_id, user_id, *, pipeline_score=None,
                     resume_id=None, on_stage=None, base_md=None, reuse=True):
        for stage in ("received", "analyze", "match", "generate", "validate", "prepare"):
            on_stage(stage)
        rid = self.make_resume(source="scraped" if job_id else "manual", link=url or "manual:abcdef123456")
        return {"resume": {"id": rid}, "reused": False, "status": "completed"}

    def test_both_entry_points_call_the_same_service(self):
        with mock.patch.object(srv.tailoring_service, "tailor_resume", side_effect=self.fake_service) as svc:
            s, _, a = self.call("POST", "/api/tailor", {"title": "Java Dev", "company": "Acme", "url": "", "source": "Naukri",
                                                          "description": JD_TEXT})
            self.assertEqual(s, 200)
            self.wait_task(a["task_id"])
            s, _, b = self.call("POST", f"/api/jobs/{KEY}/tailor")
            self.assertEqual(s, 200)
            self.wait_task(b["task_id"])
        self.assertEqual(svc.call_count, 2)          # ONE function served both requests
        manual, scraped = svc.call_args_list
        m_desc, m_title, m_company, m_url, m_source, m_job_id, _ = manual.args
        s_desc, s_title, s_company, s_url, s_source, s_job_id, _ = scraped.args
        self.assertEqual((m_title, m_company, m_source, m_job_id), ("Java Dev", "Acme", "Naukri", None))
        self.assertEqual((s_title, s_company, s_url, s_source, s_job_id), ("Java Dev", "Acme", LINK, "LinkedIn", KEY))
        self.assertEqual(scraped.kwargs["pipeline_score"], 9)
        self.assertEqual(s_desc, JD_TEXT)
        self.assertEqual(manual.kwargs["reuse"], True)

    def test_progress_is_the_services_real_stage_list(self):
        with mock.patch.object(srv.tailoring_service, "tailor_resume", side_effect=self.fake_service):
            _, _, a = self.call("POST", "/api/tailor", {"title": "Java Dev", "company": "Acme", "description": JD_TEXT})
            t = self.wait_task(a["task_id"])
        self.assertEqual([s["key"] for s in t["stages"]], ["received", "analyze", "match", "generate", "validate", "prepare"])
        self.assertTrue(all(s["status"] == "done" for s in t["stages"]))
        self.assertIn("resume_id", t["result"])
        self.assertEqual(t["result"]["result"]["status"], "completed")   # structured result rides on the task

    def test_service_failures_become_task_errors_not_crashes(self):
        with mock.patch.object(srv.tailoring_service, "tailor_resume", side_effect=RuntimeError("boom")), mock.patch("traceback.print_exc"):
            _, _, a = self.call("POST", "/api/tailor", {"title": "Java Dev", "company": "Acme", "description": JD_TEXT})
            t = self.wait_task(a["task_id"])
        self.assertEqual(t["status"], "error")
        self.assertIn("boom", t["error"]["message"])

    def test_llm_provider_failure_is_reported_clearly(self):
        from llm import AllProvidersFailed
        err = AllProvidersFailed("all 3 provider(s) failed for 'x': groq (HTTP 429); openrouter (HTTP 404); gemini (TimeoutError)")
        with mock.patch.object(srv.tailoring_service, "tailor_resume", side_effect=err), mock.patch("traceback.print_exc"):
            _, _, a = self.call("POST", "/api/tailor", {"title": "Java Dev", "company": "Acme", "description": JD_TEXT})
            t = self.wait_task(a["task_id"])
        self.assertEqual((t["status"], t["error"]["code"]), ("error", "llm_unavailable"))
        self.assertIn("HTTP 429", t["error"]["message"])

    def test_missing_master_resume_fails_fast_with_the_clear_message(self):
        with mock.patch.object(paths, "BASE_RESUME", self.dir / "does-not-exist.md"), \
                mock.patch.object(srv.tailoring_service, "tailor_resume") as svc:
            s, _, r = self.call("POST", "/api/tailor", {"title": "Java Dev", "company": "Acme", "description": JD_TEXT})
            s2, _, r2 = self.call("POST", f"/api/jobs/{KEY}/tailor")
        self.assertEqual((s, s2), (409, 409))
        self.assertEqual(r["error"]["code"], "no_master_resume")
        self.assertIn("No master resume is configured", r["error"]["message"])
        svc.assert_not_called()                                           # no LLM spend without a master resume

    def test_jd_validation_errors(self):
        good = {"title": "Java Dev", "company": "Acme", "description": JD_TEXT}
        for bad in ({**good, "title": ""}, {**good, "title": "  "}, {**good, "description": ""}, {**good, "description": "short"},
                    {**good, "url": "javascript:alert(1)"}, {**good, "description": "x" * 40000}):
            self.assertEqual(self.call("POST", "/api/tailor", bad)[0], 400, bad.get("url") or bad["title"])

    def test_company_is_optional_and_title_is_required_with_a_clear_message(self):
        good = {"title": "JAVA DEVELOPER", "company": "", "description": JD_TEXT}
        with mock.patch.object(srv.tailoring_service, "tailor_resume", side_effect=self.fake_service):
            for ok in (good, {**good, "company": "   "}, {**good, "company": None}, {**good, "title": "java developer"}):
                self.assertEqual(self.call("POST", "/api/tailor", ok)[0], 200, ok)
        for bad in ({**good, "title": ""}, {**good, "title": "   ", "company": "Acme"}):
            s, _, r = self.call("POST", "/api/tailor", bad)
            self.assertEqual(s, 400)
            self.assertIn("Job title is required.", json.dumps(r))
            self.assertNotIn("company", json.dumps(r).lower())

    def test_scraped_job_without_description_is_rejected(self):
        (self.dir / "raw_jobs.json").write_text(json.dumps([{"title": "No JD", "company": "X", "link": "https://l.example/jobs/77"}]))
        (self.dir / "scored_jobs.json").write_text("[]")
        try:
            self.assertEqual(self.call("POST", f"/api/jobs/{dd.job_key('https://l.example/jobs/77')}/tailor")[0], 422)
        finally:
            raw = [{"title": "Java Dev", "company": "Acme", "link": LINK, "description": JD_TEXT, "posted_date": "2026-09-19",
                    "found_at": "2026-09-19", "location": "Bengaluru", "source": "LinkedIn"}]
            (self.dir / "raw_jobs.json").write_text(json.dumps(raw))
            (self.dir / "scored_jobs.json").write_text(json.dumps([{**raw[0], "score": 9, "qualified": True}]))

    def test_regenerate_uses_the_same_service_and_bypasses_reuse(self):
        rid = self.make_resume()
        with mock.patch.object(srv.tailoring_service, "tailor_resume", side_effect=self.fake_service) as svc:
            _, _, a = self.call("POST", f"/api/resumes/{rid}/regenerate")
            self.wait_task(a["task_id"])
        self.assertEqual(svc.call_args.kwargs["resume_id"], rid)
        self.assertEqual(svc.call_args.kwargs["reuse"], False)

    def test_structured_result_endpoint(self):
        rid = self.make_resume()
        s, _, r = self.call("GET", f"/api/resumes/{rid}/result")
        self.assertEqual(s, 200)
        self.assertEqual(set(r), {"status", "reused", "job", "jd_analysis", "match_analysis", "resume", "ats_validation", "verification"})
        self.assertEqual(r["resume"]["id"], rid)
        self.assertEqual(self.call("GET", "/api/resumes/" + "f" * 12 + "/result")[0], 404)


class ResumeActions(ServerCase):
    def test_get_list_and_edit(self):
        rid = self.make_resume()
        s, _, r = self.call("GET", f"/api/resumes/{rid}")
        self.assertEqual((s, r["versions"][0]["validation"]["ok"]), (200, True))
        self.assertIn(rid, [x["id"] for x in self.call("GET", "/api/resumes")[2]["resumes"]])
        s, _, r = self.call("PUT", f"/api/resumes/{rid}", {"markdown": reorder_bullets(BASE)})
        self.assertEqual((s, r["versions"][-1]["kind"], r["versions"][-1]["validation"]["ok"]), (200, "edited", True))
        s, _, r = self.call("PUT", f"/api/resumes/{rid}", {"markdown": BASE.replace("Java and Angular", "Java, Angular and Kafka")})
        self.assertFalse(r["versions"][-1]["validation"]["ok"])
        self.assertEqual(self.call("PUT", f"/api/resumes/{rid}", {"markdown": ""})[0], 400)
        self.assertEqual(self.call("GET", "/api/resumes/" + "f" * 12)[0], 404)
        self.assertEqual(self.call("GET", "/api/resumes/..%2f..%2fx")[0], 404)

    @contextmanager
    def fake_drive(self, **kw):
        """An in-memory Drive plus a fake Docs export that writes a local PDF/DOCX."""
        drive = FakeDrive(**kw)

        def fake_export(md_path, out_path, mime_type="application/pdf"):
            Path(out_path).write_bytes(b"%PDF-fake" if mime_type == "application/pdf" else b"PK-fake-docx")

        with mock.patch.object(drive_resumes, "_gws", side_effect=drive), \
                mock.patch("tailor_job.export_doc_file", side_effect=fake_export):
            yield drive

    def test_download_markdown_and_export_pdf_docx(self):
        rid = self.make_resume()
        s, h, body = self.call("GET", f"/api/resumes/{rid}/download?format=md", raw=True)
        self.assertEqual(s, 200)
        self.assertIn(b"# Jane Roe", body)
        self.assertIn("attachment", h["Content-Disposition"])
        self.assertEqual(self.call("GET", f"/api/resumes/{rid}/download?format=pdf")[0], 404)   # not exported yet

        exported = []

        def fake_export(md_path, out_path, mime_type="application/pdf"):
            exported.append((Path(md_path).name, Path(out_path).suffix, mime_type))
            Path(out_path).write_bytes(b"%PDF-fake" if mime_type == "application/pdf" else b"PK-fake-docx")

        with mock.patch("tailor_job.export_doc_file", side_effect=fake_export), \
                mock.patch.object(srv.drive_resumes, "upload_and_record", return_value={"url": "https://drive.google.com/file/d/X/view", "reused": False}):
            for fmt in ("pdf", "docx"):
                s, _, t = self.call("POST", f"/api/resumes/{rid}/export", {"format": fmt})
                self.assertEqual(s, 200)
                self.assertEqual(self.wait_task(t["task_id"])["status"], "done")
        self.assertEqual([e[1] for e in exported], [".pdf", ".docx"])
        self.assertIn("wordprocessingml", exported[1][2])                                          # docx uses the Word mime type
        s, h, body = self.call("GET", f"/api/resumes/{rid}/download?format=pdf", raw=True)
        self.assertEqual((s, body, h["Content-Type"]), (200, b"%PDF-fake", "application/pdf"))
        self.assertEqual(self.call("GET", f"/api/resumes/{rid}/download?format=docx", raw=True)[2], b"PK-fake-docx")
        self.assertEqual(self.call("POST", f"/api/resumes/{rid}/export", {"format": "exe"})[0], 400)

    def test_blocked_versions_cannot_be_exported_or_saved(self):
        rid = self.make_resume(ok=False)
        self.assertEqual(self.call("POST", f"/api/resumes/{rid}/export", {"format": "pdf"})[0], 409)
        self.assertEqual(self.call("POST", f"/api/resumes/{rid}/save-to-tracker")[0], 409)
        self.assertEqual(self.call("GET", f"/api/resumes/{rid}/download?format=pdf")[0], 409)

    def test_export_failure_is_reported_on_the_task(self):
        rid = self.make_resume()
        with mock.patch("tailor_job.export_doc_file", side_effect=RuntimeError("gws not signed in")), mock.patch("traceback.print_exc"):
            _, _, t = self.call("POST", f"/api/resumes/{rid}/export", {"format": "pdf"})
            done = self.wait_task(t["task_id"])
        self.assertEqual(done["status"], "error")
        self.assertIn("gws not signed in", done["error"]["message"])

    def test_save_to_tracker_reuses_the_tracker_service(self):
        rid = self.make_resume()
        with self.fake_drive(), mock.patch.object(srv.tracker, "save_job", return_value={"result": "added", "row": None}) as save:
            s, _, r = self.call("POST", f"/api/resumes/{rid}/save-to-tracker")
        self.assertEqual((s, r["result"]), (200, "added"))
        kw = save.call_args.kwargs
        self.assertEqual((kw["title"], kw["company"], kw["source"]), ("Java Dev", "Acme", "Manual JD"))
        self.assertEqual(kw["resume_id"], f"{rid}-v1")
        self.assertIsInstance(kw["match_pct"], int)
        self.assertTrue(kw["link"].startswith("manual:"))
        self.assertRegex(kw["resume_path"], r"^https://drive\.google\.com/file/d/ID\d+/view")   # Drive URL, not a local path
        self.assertNotIn("generated_resumes", kw["resume_path"])
        self.assertTrue(1 <= kw["score"] <= 10)
        self.assertEqual(self.call("GET", f"/api/resumes/{rid}")[2]["tracker"]["result"], "added")

    def test_scraped_resume_logs_the_pipeline_score_to_the_tracker(self):
        rid = self.make_resume(source="scraped", link=LINK)
        resume_store.update_meta(rid, job={"title": "Java Dev", "company": "Acme", "link": LINK, "description": JD_TEXT, "pipeline_score": 9})
        with self.fake_drive(), mock.patch.object(srv.tracker, "save_job", return_value={"result": "exists", "row": {}}) as save:
            self.call("POST", f"/api/resumes/{rid}/save-to-tracker")
        self.assertEqual((save.call_args.kwargs["score"], save.call_args.kwargs["source"]), (9, "LinkedIn"))

    def test_tracker_outage_on_save_is_a_clear_502(self):
        from tracker_service import TrackerError
        rid = self.make_resume()
        with self.fake_drive(), mock.patch.object(srv.tracker, "save_job", side_effect=TrackerError("gws: offline")):
            s, _, r = self.call("POST", f"/api/resumes/{rid}/save-to-tracker")
        self.assertEqual((s, r["error"]["code"]), (502, "tracker_unavailable"))

    # -- Google Drive ------------------------------------------------------------------------

    def export(self, rid, fmt):
        _, _, t = self.call("POST", f"/api/resumes/{rid}/export", {"format": fmt})
        return self.wait_task(t["task_id"])

    def test_export_uploads_to_drive_and_keeps_the_local_download(self):
        rid = self.make_resume()
        with self.fake_drive() as drive:
            pdf, docx = self.export(rid, "pdf"), self.export(rid, "docx")
        self.assertEqual((pdf["status"], docx["status"]), ("done", "done"))
        self.assertEqual([s["key"] for s in pdf["stages"]], ["export", "upload"])
        self.assertEqual(pdf["result"]["drive"]["status"], "uploaded")
        self.assertEqual(sorted(f["name"] for f in drive.resume_files()), [f"{rid}-v1.docx", f"{rid}-v1.pdf"])
        res = self.call("GET", f"/api/resumes/{rid}/result")[2]["resume"]
        self.assertEqual(res["drive_url"], pdf["result"]["drive"]["url"])           # PDF is the primary link
        self.assertIsNone(res["drive_error"])
        self.assertIn("format=pdf", res["pdf_url"])                                 # local downloads unchanged
        self.assertEqual(self.call("GET", f"/api/resumes/{rid}/download?format=docx", raw=True)[2], b"PK-fake-docx")

    def test_re_export_does_not_upload_a_second_copy(self):
        rid = self.make_resume()
        with self.fake_drive() as drive:
            first, again = self.export(rid, "pdf"), self.export(rid, "pdf")
        self.assertEqual(len(drive.resume_files()), 1)
        self.assertEqual((first["result"]["drive"]["reused"], again["result"]["drive"]["reused"]), (False, True))
        self.assertEqual(first["result"]["drive"]["url"], again["result"]["drive"]["url"])

    def test_drive_failure_on_export_is_reported_but_the_local_file_survives(self):
        rid = self.make_resume()
        with self.fake_drive(fail="network unreachable"):
            t = self.export(rid, "pdf")
        self.assertEqual(t["status"], "done")                                       # the export itself worked
        self.assertEqual(t["result"]["drive"]["status"], "failed")
        self.assertIn("Google Drive upload failed", t["result"]["drive"]["error"])
        self.assertNotIn("url", t["result"]["drive"])
        res = self.call("GET", f"/api/resumes/{rid}/result")[2]["resume"]
        self.assertEqual((res["drive_url"], res["drive_error"] is not None), (None, True))
        self.assertEqual(self.call("GET", f"/api/resumes/{rid}/download?format=pdf", raw=True)[2], b"%PDF-fake")

    def test_save_to_tracker_sends_only_the_drive_url_never_the_local_path(self):
        rid = self.make_resume()
        local = str(resume_store.version_path(rid, 1, "md"))
        with self.fake_drive() as drive, mock.patch.object(srv.tracker, "save_job", return_value={"result": "added", "row": None}) as save:
            s, _, r = self.call("POST", f"/api/resumes/{rid}/save-to-tracker")
        self.assertEqual(s, 200)
        (f,) = drive.resume_files()
        self.assertEqual(f["name"], f"{rid}-v1.pdf")                                 # the PDF was exported, then uploaded
        url = save.call_args.kwargs["resume_path"]
        self.assertEqual((url, r["resume_path"], r["drive_url"]), (f["webViewLink"],) * 3)
        self.assertNotIn(local, [url, r["resume_path"]])
        self.assertEqual(self.call("GET", f"/api/resumes/{rid}")[2]["tracker"]["drive_url"], url)

    def test_drive_failure_on_save_blocks_the_row_and_never_falls_back_to_a_local_path(self):
        rid = self.make_resume()
        with self.fake_drive(fail="network unreachable"), mock.patch.object(srv.tracker, "save_job") as save:
            s, _, r = self.call("POST", f"/api/resumes/{rid}/save-to-tracker")
        self.assertEqual((s, r["error"]["code"]), (502, "drive_upload_failed"))
        save.assert_not_called()                                                     # no row, no fake Drive claim
        self.assertTrue(resume_store.version_path(rid, 1, "md").exists())
        self.assertIsNone(self.call("GET", f"/api/resumes/{rid}")[2]["tracker"])

    def test_sheet_failure_after_upload_then_retry_reuses_the_drive_file(self):
        from tracker_service import TrackerError
        rid = self.make_resume()
        with self.fake_drive() as drive:
            with mock.patch.object(srv.tracker, "save_job", side_effect=TrackerError("sheets down")):
                self.assertEqual(self.call("POST", f"/api/resumes/{rid}/save-to-tracker")[0], 502)
            with mock.patch.object(srv.tracker, "save_job", return_value={"result": "added", "row": None}) as save:
                self.assertEqual(self.call("POST", f"/api/resumes/{rid}/save-to-tracker")[0], 200)
        self.assertEqual(len(drive.resume_files()), 1)                               # one Drive file across both attempts
        self.assertEqual(save.call_args.kwargs["resume_path"], drive.resume_files()[0]["webViewLink"])

    def test_manual_blank_company_and_scraped_company_both_upload(self):
        manual = self.make_resume()
        resume_store.update_meta(manual, job={"title": "JAVA DEVELOPER", "company": "Company Not Specified",
                                              "link": "manual:abcdef123456", "description": JD_TEXT})
        scraped = self.make_resume(source="scraped", link=LINK)
        resume_store.update_meta(scraped, job={"title": "Java Developer", "company": "ABC Technologies", "link": LINK,
                                               "description": JD_TEXT, "pipeline_score": 9})
        with self.fake_drive() as drive, mock.patch.object(srv.tracker, "save_job", return_value={"result": "added", "row": None}) as save:
            for rid in (manual, scraped):
                self.assertEqual(self.call("POST", f"/api/resumes/{rid}/save-to-tracker")[0], 200)
        self.assertEqual(sorted(f["name"] for f in drive.folders()),
                         ["ABC Technologies-java-developer", "Company Not Specified-java-developer"])
        self.assertEqual([c.kwargs["company"] for c in save.call_args_list], ["Company Not Specified", "ABC Technologies"])
        self.assertTrue(all(c.kwargs["resume_path"].startswith("https://drive.google.com/") for c in save.call_args_list))


class TrackerAndJobActions(ServerCase):
    def test_save_scraped_job_to_tracker(self):
        with mock.patch.object(srv.tracker, "save_job", return_value={"result": "added", "row": None}) as save:
            s, _, r = self.call("POST", f"/api/jobs/{KEY}/save")
        self.assertEqual((s, r["result"]), (200, "added"))
        self.assertEqual((save.call_args.kwargs["link"], save.call_args.kwargs["score"]), (LINK, 9))

    def test_status_update_validation_and_errors(self):
        self.assertEqual(self.call("PATCH", "/api/tracker/status", {"link": LINK, "status": "Hired!"})[0], 400)
        self.assertEqual(self.call("PATCH", "/api/tracker/status", {"status": "Applied"})[0], 400)
        with mock.patch.object(srv.tracker, "set_status", side_effect=LookupError("not in tracker")):
            self.assertEqual(self.call("PATCH", "/api/tracker/status", {"link": LINK, "status": "Applied"})[0], 404)
        with mock.patch.object(srv.tracker, "set_status", return_value={"row": 2, "status": "Applied"}) as ss:
            self.assertEqual(self.call("PATCH", "/api/tracker/status", {"link": LINK, "status": "Applied"})[0], 200)
        ss.assert_called_once_with(LINK, "Applied")

    def test_tracker_listing_and_status_filter(self):
        rows = [{"row": 2, "title": "A", "company": "X", "link": "l1", "score": "8", "resume_path": "", "status": "Applied",
                 "timestamp": "2026-09-01", "company_notes": "", "source": "", "status_updated": ""},
                {"row": 3, "title": "B", "company": "Y", "link": "l2", "score": "8", "resume_path": "", "status": "Offer",
                 "timestamp": "2026-09-01", "company_notes": "", "source": "", "status_updated": ""}]
        with mock.patch.object(srv.tracker, "snapshot", return_value=tracker_state(rows)):
            self.assertEqual(self.call("GET", "/api/tracker")[2]["total"], 2)
            self.assertEqual([r["title"] for r in self.call("GET", "/api/tracker?status=Offer")[2]["rows"]], ["B"])
            self.assertEqual(self.call("GET", "/api/tracker?status=Nope")[0], 400)


class PipelineActionsAndPreferences(ServerCase):
    class FakeProc:
        commands = []

        def __init__(self, cmd, **kw):
            type(self).commands.append(cmd)
            self.stdout = iter(["working...\n", "secret " + SECRETS["GROQ_API_KEY"] + " leaked?\n"])
            self.returncode = 0

        def wait(self):
            return 0

        def kill(self):
            pass

    def test_find_new_jobs_runs_the_existing_scripts_in_order_and_redacts_secrets(self):
        self.FakeProc.commands = []
        with mock.patch.object(tasks.subprocess, "Popen", self.FakeProc):
            s, _, a = self.call("POST", "/api/actions/find-jobs", {"mode": "find"})
            self.assertEqual(s, 200)
            t = self.wait_task(a["task_id"])
        self.assertEqual(t["status"], "done")
        self.assertEqual([Path(c[-1]).name for c in self.FakeProc.commands], ["scrape_jobs.py", "score_jobs.py"])
        self.assertTrue(all(Path(c[-1]).parent.name == "scripts" for c in self.FakeProc.commands))
        self.assertNotIn(SECRETS["GROQ_API_KEY"], json.dumps(t))
        self.assertIn("***", json.dumps(t))

    def test_full_pipeline_is_the_five_existing_steps(self):
        self.FakeProc.commands = []
        with mock.patch.object(tasks.subprocess, "Popen", self.FakeProc):
            _, _, a = self.call("POST", "/api/actions/find-jobs", {"mode": "full"})
            self.wait_task(a["task_id"])
        self.assertEqual([Path(c[-1]).name for c in self.FakeProc.commands],
                         ["scrape_jobs.py", "score_jobs.py", "tailor_job.py", "company_research.py", "write_sheet.py"])

    def test_failing_step_fails_the_task_and_stops(self):
        class Failing(self.FakeProc):
            def __init__(s, cmd, **kw):
                super().__init__(cmd, **kw)
                s.returncode = 1

            def wait(s):
                return 1

        Failing.commands = []
        with mock.patch.object(tasks.subprocess, "Popen", Failing):
            _, _, a = self.call("POST", "/api/actions/find-jobs", {"mode": "find"})
            t = self.wait_task(a["task_id"])
        self.assertEqual(t["status"], "error")
        self.assertIn("scrape_jobs.py", t["error"]["message"])
        self.assertEqual(len(Failing.commands), 1)

    def test_only_one_pipeline_run_at_a_time_and_mode_is_validated(self):
        self.assertEqual(self.call("POST", "/api/actions/find-jobs", {"mode": "rm -rf"})[0], 400)
        self.assertTrue(tasks.PIPELINE_LOCK.acquire(blocking=False))
        try:
            self.assertEqual(self.call("POST", "/api/actions/find-jobs", {"mode": "find"})[0], 409)
        finally:
            tasks.PIPELINE_LOCK.release()

    def test_preferences_roundtrip_and_validation(self):
        s, _, p = self.call("GET", "/api/preferences")
        self.assertEqual((s, p["preferences"]["keywords"]), (200, p["defaults"]["keywords"]))
        cap = p["limit_cap"]
        body = {"keywords": "Java Spring Boot", "location": "Chennai", "date_posted": "pastWeek", "limit": min(3, cap)}
        s, _, saved = self.call("PUT", "/api/preferences", body)
        self.assertEqual((s, saved["preferences"]["location"], saved["preferences"]["date_posted"]), (200, "Chennai", "pastWeek"))
        import scrape_jobs
        self.assertEqual(scrape_jobs.load_preferences()["keywords"], "Java Spring Boot")
        for bad in ({**body, "limit": cap + 1}, {**body, "limit": 0}, {**body, "limit": "x"}, {**body, "date_posted": "forever"},
                    {**body, "keywords": ""}, {**body, "location": "x" * 200}):
            self.assertEqual(self.call("PUT", "/api/preferences", bad)[0], 400, bad)
        (self.dir / "preferences.json").unlink()

    def test_scraper_defaults_are_unchanged_without_saved_preferences(self):
        import scrape_jobs
        prefs = scrape_jobs.load_preferences()
        self.assertEqual((prefs["keywords"], prefs["location"], prefs["date_posted"]),
                         ("Full Stack Java Spring Boot Angular AWS Developer", "India", "past24Hours"))
        self.assertEqual(prefs["limit"], scrape_jobs.effective_job_limit())


if __name__ == "__main__":
    unittest.main()
