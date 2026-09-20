"""Dashboard read models: KPIs, period comparison, overview, status counts, activity, sources."""
import json
import os
import unittest
from datetime import date
from unittest import mock

import fixtures  # noqa: F401 -- puts scripts/ on sys.path
import dashboard_data as dd

TODAY = date(2026, 9, 20)


def job(i, **kw):
    return {"title": f"Job {i}", "company": f"Co {i}", "link": f"https://l.example/jobs/{i}?trackingId=x{i}",
            "description": "desc", "posted_date": kw.pop("posted", None), **kw}


def row(i, status="Not Applied", ts="2026-09-18", upd="", **kw):
    return {"row": i + 1, "title": f"Job {i}", "company": f"Co {i}", "link": f"https://l.example/jobs/{i}?trackingId=y{i}",
            "score": "8", "resume_path": "", "status": status, "timestamp": ts, "company_notes": "",
            "source": "LinkedIn", "status_updated": upd, **kw}


def tracker(rows, available=True, error=None):
    return {"rows": rows, "available": available, "error": error, "stale": False, "configured": True}


def resume_meta(created, ok=True):
    return {"id": "a" * 12, "created_at": created, "versions": [{"n": 1, "validation": {"ok": ok}}]}


class Jobs(unittest.TestCase):
    def test_scored_fields_override_raw_and_state_precedence(self):
        raw = [job(1, posted="2026-09-19"), job(2, posted="2026-09-19"), job(3, posted="2026-09-19"), job(4)]
        scored = [{**job(1), "score": 9, "matched_must_haves": ["Java"], "missing_must_haves": [], "reasoning": "r"},
                  {**job(2), "score": 5}, {**job(3), "score": "8"}]
        tailored = [{"link": job(1)["link"], "status": "saved", "resume_link": "https://drive/x"}]
        jobs = {j["title"]: j for j in dd.build_jobs(raw, scored, tailored, [row(1, "Applied")])}
        self.assertEqual(jobs["Job 1"]["state"], "Applied")            # tracker beats tailored
        self.assertEqual((jobs["Job 1"]["score"], jobs["Job 1"]["match_pct"]), (9, 90))
        self.assertTrue(jobs["Job 1"]["tailored"])
        self.assertEqual(jobs["Job 2"]["state"], "Below cutoff")
        self.assertEqual(jobs["Job 3"]["state"], "Qualified")
        self.assertEqual(jobs["Job 4"]["state"], "Unscored")
        self.assertIsNone(jobs["Job 4"]["match_pct"])

    def test_tracking_params_do_not_duplicate_jobs(self):
        a, b = job(1), {**job(1), "link": "https://l.example/jobs/1?trackingId=OTHER"}
        self.assertEqual(len(dd.build_jobs([a], [b], [], [])), 1)
        self.assertEqual(dd.job_key(a["link"]), dd.job_key(b["link"]))

    def test_public_job_omits_description_unless_asked(self):
        j = dd.build_jobs([job(1)], [], [], [])[0]
        self.assertNotIn("description", dd.public_job(j))
        self.assertEqual(dd.public_job(j, with_description=True)["description"], "desc")

    def test_empty(self):
        self.assertEqual(dd.build_jobs([], [], [], []), [])


class Periods(unittest.TestCase):
    def test_presets_and_previous_period(self):
        p = dd.parse_range("7d", today=TODAY)
        self.assertEqual((p["start"], p["end"], p["days"]), (date(2026, 9, 14), TODAY, 7))
        self.assertEqual((p["prev_start"], p["prev_end"]), (date(2026, 9, 7), date(2026, 9, 13)))
        self.assertEqual(dd.parse_range("90d", today=TODAY)["days"], 90)

    def test_custom_range_validation(self):
        self.assertEqual(dd.parse_range("custom", "2026-09-01", "2026-09-10", TODAY)["days"], 10)
        for args in (("custom", None, None), ("custom", "2026-09-10", "2026-09-01"), ("custom", "2024-01-01", "2026-09-01"),
                     ("custom", "nope", "2026-09-01"), ("weekly",)):
            with self.assertRaises(ValueError, msg=args):
                dd.parse_range(*args, today=TODAY)


class Summary(unittest.TestCase):
    def setUp(self):
        self.p = dd.parse_range("7d", today=TODAY)  # 2026-09-14 .. 2026-09-20; previous 09-07 .. 09-13
        self.jobs = dd.build_jobs(
            [job(i, posted=d) for i, d in enumerate(["2026-09-19", "2026-09-18", "2026-09-15", "2026-09-10"], start=1)],
            [{**job(1), "score": 9}, {**job(2), "score": 4}], [], [])

    def test_kpis_are_computed_from_the_data_with_period_comparison(self):
        rows = [row(1, "Applied", "2026-09-01", "2026-09-19"), row(2, "Interviewing", "2026-09-01", "2026-09-18"),
                row(3, "Applied", "2026-09-01", "2026-09-09"), row(4, "Rejected", "2026-09-01", "2026-09-19")]
        s = dd.summary(self.jobs, [resume_meta("2026-09-19T10:00:00+00:00"), resume_meta("2026-09-19T10:00:00+00:00", ok=False)],
                       tracker(rows), self.p)["metrics"]
        self.assertEqual(s["jobs_found"], {"current": 3, "previous": 1, "change_pct": 200, "trend": "up"})
        self.assertEqual(s["resumes_tailored"]["current"], 1)     # the blocked resume isn't counted
        self.assertEqual(s["applications_sent"]["current"], 2)     # Applied + Interviewing this week; Rejected excluded
        self.assertEqual(s["applications_sent"]["previous"], 1)
        self.assertEqual(s["interviews"]["current"], 1)

    def test_no_data_yields_zeros_not_fake_numbers(self):
        s = dd.summary([], [], tracker([]), self.p)["metrics"]
        for m in s.values():
            self.assertEqual((m["current"], m["previous"], m["change_pct"], m["trend"]), (0, 0, None, "flat"))

    def test_new_trend_when_previous_period_was_empty(self):
        m = dd.summary(self.jobs[:1], [], tracker([]), self.p)["metrics"]["jobs_found"]
        self.assertEqual((m["current"], m["previous"], m["trend"]), (1, 0, "new"))

    def test_unavailable_tracker_marks_only_tracker_metrics_unavailable(self):
        s = dd.summary(self.jobs, [], tracker([], available=False, error="gws failed"), self.p)
        self.assertIsNone(s["metrics"]["applications_sent"])
        self.assertIsNone(s["metrics"]["interviews"])
        self.assertEqual(s["metrics"]["jobs_found"]["current"], 3)
        self.assertEqual(s["tracker"]["error"], "gws failed")

    def test_status_change_date_beats_logged_date(self):
        rows = [row(1, "Applied", ts="2026-08-01", upd="2026-09-19")]
        self.assertEqual(dd.summary([], [], tracker(rows), self.p)["metrics"]["applications_sent"]["current"], 1)
        rows = [row(1, "Applied", ts="2026-09-19", upd="")]      # never changed via the dashboard: logged date
        self.assertEqual(dd.summary([], [], tracker(rows), self.p)["metrics"]["applications_sent"]["current"], 1)

    def test_pipeline_tailored_jobs_are_dated_by_tailored_at_then_tracker_date(self):
        raw = [job(1), job(2)]
        tailored = [{"link": job(1)["link"], "status": "saved", "tailored_at": "2026-09-19T08:00:00+00:00"},
                    {"link": job(2)["link"], "status": "saved"}]
        jobs = dd.build_jobs(raw, [{**job(1), "score": 8}, {**job(2), "score": 8}], tailored, [])
        self.assertEqual(dd.summary(jobs, [], tracker([]), self.p)["metrics"]["resumes_tailored"]["current"], 1)
        jobs = dd.build_jobs(raw, [{**job(1), "score": 8}, {**job(2), "score": 8}], tailored, [row(2, ts="2026-09-16")])
        self.assertEqual(dd.summary(jobs, [], tracker([row(2, ts="2026-09-16")]), self.p)["metrics"]["resumes_tailored"]["current"], 2)


class Charts(unittest.TestCase):
    def test_overview_series(self):
        p = dd.parse_range("30d", today=TODAY)
        jobs = dd.build_jobs([job(1, posted="2026-09-19"), job(2, posted="2026-09-01")], [{**job(1), "score": 9}], [], [])
        o = dd.overview(jobs, [], tracker([row(1, "Interviewing", upd="2026-09-19")]), p)
        self.assertEqual({s["key"]: s["value"] for s in o["series"]},
                         {"found": 2, "qualified": 1, "tailored": 0, "applied": 1, "interviews": 1})

    def test_overview_with_tracker_down_reports_null_not_zero(self):
        o = dd.overview([], [], tracker([], available=False, error="x"), dd.parse_range("7d", today=TODAY))
        self.assertEqual({s["key"]: s["value"] for s in o["series"]}["applied"], None)

    def test_status_counts(self):
        rows = [row(1, "Applied"), row(2, "Applied"), row(3, "Offer"), row(4, "Weird")]
        c = dd.status_counts(tracker(rows))
        self.assertEqual(c["total"], 4)
        self.assertEqual({s["status"]: s["count"] for s in c["statuses"]},
                         {"Not Applied": 1, "Applied": 2, "Interviewing": 0, "Offer": 1, "Rejected": 0})


class Activity(unittest.TestCase):
    def test_backfills_pipeline_resumes_without_duplicating_logged_ones(self):
        tailored = [{"link": "L1", "title": "T1", "company": "C1", "status": "saved", "tailored_at": "2026-09-05T10:00:00+00:00"},
                    {"link": "L2", "title": "T2", "company": "C2", "status": "saved"},
                    {"link": "L3", "title": "T3", "company": "C3", "status": "flagged_error"}]
        logged = [{"kind": "resume_tailored", "ts": "2026-09-19T10:00:00+00:00", "message": "logged", "link": "L1"}]
        feed = dd.activity_feed(logged, tailored, [], limit=10)
        self.assertEqual([e["message"] for e in feed], ["logged"])       # L1 logged; L2 has no date; L3 not saved
        feed = dd.activity_feed([], tailored, [], limit=10)
        self.assertEqual([e["message"] for e in feed], ["Resume tailored for C1 - T1"])
        self.assertTrue(feed[0]["date_only"])

    def test_sorted_newest_first_and_limited(self):
        ev = [{"kind": "x", "ts": f"2026-09-{d:02d}T00:00:00+00:00", "message": str(d)} for d in (3, 9, 5)]
        self.assertEqual([e["message"] for e in dd.activity_feed(ev, [], [], limit=2)], ["9", "5"])

    def test_activity_log_roundtrip_never_raises(self):
        import activity
        from fixtures import TempOutput
        with TempOutput() as out:
            activity.log_event("jobs_found", "Found 3 jobs", count=3)
            (out / "activity_log.jsonl").write_text((out / "activity_log.jsonl").read_text() + "not json\n")
            events = activity.read_events()
            self.assertEqual([e["message"] for e in events], ["Found 3 jobs"])
        with mock.patch("activity._log_path", side_effect=OSError("disk gone")):
            activity.log_event("x", "y")  # must not raise


class Sources(unittest.TestCase):
    SECRETS = {"apify_api_key": "apify-SECRET-123456", "GROQ_API_KEY": "gsk_SECRET_abcdef", "open_router_apikey": "sk-or-SECRET-xyz",
               "GEMINI_API_KEY": "AIzaSECRETgemini", "google_sheet_id": "SHEETID_SECRET_1", "google_drive_folder_id": "DRIVEID_SECRET_1",
               "TELEGRAM_BOT_TOKEN": "123456:TELEGRAM-SECRET", "TELEGRAM_CHAT_ID": "99887766", "DASHBOARD_TOKEN": "tok-SECRET-9"}

    def test_reports_configuration_without_exposing_any_secret(self):
        with mock.patch.dict(os.environ, self.SECRETS):
            out = dd.sources()
        blob = json.dumps(out)
        for value in self.SECRETS.values():
            self.assertNotIn(value, blob)
        linkedin = out["job_sources"][0]
        self.assertEqual((linkedin["name"], linkedin["via"], linkedin["active"]), ("LinkedIn", "Apify", True))

    def test_inactive_when_not_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            out = dd.sources()
        self.assertFalse(out["job_sources"][0]["active"])
        self.assertFalse(any(s["active"] for s in out["services"] if s["id"] in ("sheets", "drive", "telegram")))


if __name__ == "__main__":
    unittest.main()
