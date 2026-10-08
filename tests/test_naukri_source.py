"""Naukri source: the Auto_job_apply JSON handoff merged into raw_jobs.json, plus the source labels
that follow a job through tailoring and the tracker. LinkedIn behaviour must stay unchanged."""
import fixtures  # noqa: F401 -- must be first: disables .env loading, puts scripts/ on sys.path
from fixtures import TempOutput

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import artifacts
import scrape_jobs
import tailor_job
import write_sheet
import dashboard_data as dd

NAUKRI_LINK = "https://www.naukri.com/job-listings-java-full-stack-developer-infosys-hyderabad-2-to-4-years-120226011477"
LINKEDIN_LINK = "https://www.linkedin.com/jobs/view/4000000001"


def naukri_record(link=NAUKRI_LINK, **overrides):
    record = {
        "title": "Java Full Stack Developer", "company": "Infosys", "link": link,
        "description": "Build Spring Boot microservices and Angular front ends. " * 3,
        "posted_date": "2026-10-05", "location": "Hyderabad", "source": "Naukri", "found_at": "2026-10-07",
        "source_job_id": "120226011477", "dedup_key": "naukri:id:120226011477", "skills": ["Java"],
    }
    record.update(overrides)
    return record


def linkedin_job(link=LINKEDIN_LINK):
    return {"title": "Full Stack Engineer", "company": "Acme", "link": link, "description": "Java Angular",
            "posted_date": "2026-10-06", "location": "India", "source": "LinkedIn", "found_at": "2026-10-07"}


class NaukriSourceTestCase(unittest.TestCase):
    def setUp(self):
        self.out = TempOutput()
        self.dir = self.out.__enter__()
        self.addCleanup(self.out.__exit__, None, None, None)
        self.cache = self.dir / "raw_jobs.json"
        self.handoff = self.dir / "naukri_jobs.json"
        for patcher in (
            mock.patch.object(scrape_jobs, "CACHE_PATH", self.cache),
            mock.patch.object(artifacts, "pull"),
            mock.patch.object(artifacts, "push"),
            mock.patch.object(scrape_jobs, "scrape_jobs", return_value=[linkedin_job()]),
        ):
            self.addCleanup(patcher.stop)
            patcher.start()
        self.apify = scrape_jobs.scrape_jobs

    def env(self, **values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_handoff(self, records):
        self.handoff.write_text(json.dumps(records), encoding="utf-8")
        self.env(NAUKRI_JOBS_PATH=str(self.handoff))

    def write_cache(self, jobs, age_seconds):
        self.cache.write_text(json.dumps(jobs), encoding="utf-8")
        stamp = time.time() - age_seconds
        os.utime(self.cache, (stamp, stamp))

    def raw(self):
        return json.loads(self.cache.read_text(encoding="utf-8"))


class TestLoadNaukriJobs(NaukriSourceTestCase):
    def test_no_path_or_missing_file_means_no_jobs(self):
        self.env(NAUKRI_JOBS_PATH="")
        self.assertEqual(scrape_jobs.load_naukri_jobs(), [])
        self.assertEqual(scrape_jobs.load_naukri_jobs(str(self.dir / "missing.json")), [])

    def test_loads_records_and_keeps_extra_fields(self):
        self.write_handoff([naukri_record()])
        [job] = scrape_jobs.load_naukri_jobs()
        self.assertEqual(job["source"], "Naukri")
        self.assertEqual(job["source_job_id"], "120226011477")
        self.assertEqual(job["dedup_key"], "naukri:id:120226011477")
        for field in ("title", "company", "link", "description", "location", "posted_date", "found_at"):
            self.assertIn(field, job)

    def test_invalid_records_are_skipped(self):
        self.write_handoff([naukri_record(), naukri_record(description=""), "junk", {"title": "x"}])
        self.assertEqual(len(scrape_jobs.load_naukri_jobs()), 1)

    def test_non_list_file_fails_loudly(self):
        self.write_handoff({"jobs": []})
        with self.assertRaises(ValueError):
            scrape_jobs.load_naukri_jobs()

    def test_job_limit_caps_records(self):
        self.write_handoff([naukri_record(link=f"{NAUKRI_LINK[:-3]}{i:03d}") for i in range(5)])
        self.assertEqual(len(scrape_jobs.load_naukri_jobs(limit=2)), 2)


class TestMerge(NaukriSourceTestCase):
    def test_canonical_link_dedupe_keeps_base_first(self):
        base = [linkedin_job(), naukri_record(title="already here")]
        extra = [naukri_record(link=NAUKRI_LINK + "?src=jobsearchDesk#apply"), naukri_record(link=NAUKRI_LINK + "-x")]
        merged = scrape_jobs.merge_jobs(base, extra)
        self.assertEqual([j["title"] for j in merged], ["Full Stack Engineer", "already here", "Java Full Stack Developer"])

    def test_job_sources_config(self):
        self.env(JOB_SOURCES="")
        self.assertEqual(scrape_jobs.job_sources(), ("linkedin", "naukri"))
        self.env(JOB_SOURCES=" Naukri ")
        self.assertEqual(scrape_jobs.job_sources(), ("naukri",))
        self.env(JOB_SOURCES="naukri,indeed")
        with self.assertRaises(ValueError):
            scrape_jobs.job_sources()


class TestRunScrape(NaukriSourceTestCase):
    def test_linkedin_only_behaviour_unchanged_without_handoff(self):
        self.env(JOB_SOURCES="", NAUKRI_JOBS_PATH="")
        jobs = scrape_jobs.run_scrape()
        self.apify.assert_called_once()
        self.assertEqual(jobs, [linkedin_job()])
        self.assertEqual(self.raw(), [linkedin_job()])

    def test_cache_hit_without_naukri_does_not_rewrite(self):
        self.env(JOB_SOURCES="", NAUKRI_JOBS_PATH="")
        self.write_cache([linkedin_job()], age_seconds=60)
        before = self.cache.stat().st_mtime
        self.assertEqual(scrape_jobs.run_scrape(), [linkedin_job()])
        self.apify.assert_not_called()
        self.assertEqual(self.cache.stat().st_mtime, before)

    def test_fresh_scrape_merges_naukri(self):
        self.env(JOB_SOURCES="")
        self.write_handoff([naukri_record()])
        jobs = scrape_jobs.run_scrape()
        self.apify.assert_called_once()
        self.assertEqual([j["source"] for j in jobs], ["LinkedIn", "Naukri"])
        self.assertEqual(self.raw(), jobs)

    def test_cache_hit_still_merges_naukri_and_keeps_cache_age(self):
        self.env(JOB_SOURCES="")
        self.write_cache([linkedin_job(), naukri_record(link=NAUKRI_LINK[:-3] + "999", title="stale export")], 600)
        before = self.cache.stat().st_mtime
        self.write_handoff([naukri_record()])
        jobs = scrape_jobs.run_scrape()
        self.apify.assert_not_called()
        self.assertEqual([(j["source"], j["title"]) for j in jobs],
                         [("LinkedIn", "Full Stack Engineer"), ("Naukri", "Java Full Stack Developer")])
        self.assertAlmostEqual(self.cache.stat().st_mtime, before, delta=1)

    def test_naukri_only_never_calls_apify(self):
        self.env(JOB_SOURCES="naukri")
        self.write_handoff([naukri_record()])
        jobs = scrape_jobs.run_scrape(force=True)
        self.apify.assert_not_called()
        self.assertEqual([j["source"] for j in jobs], ["Naukri"])
        self.assertEqual(self.cache.stat().st_mtime, 0)  # no LinkedIn data: next LinkedIn run scrapes

    def test_naukri_only_keeps_fresh_linkedin_cache(self):
        self.env(JOB_SOURCES="naukri")
        self.write_cache([linkedin_job()], age_seconds=60)
        self.write_handoff([naukri_record()])
        jobs = scrape_jobs.run_scrape()
        self.apify.assert_not_called()
        self.assertEqual([j["source"] for j in jobs], ["LinkedIn", "Naukri"])

    def test_rerun_does_not_duplicate_naukri_jobs(self):
        self.env(JOB_SOURCES="naukri")
        self.write_handoff([naukri_record()])
        self.write_cache([linkedin_job()], age_seconds=60)
        scrape_jobs.run_scrape()
        jobs = scrape_jobs.run_scrape()
        self.assertEqual(len([j for j in jobs if j["source"] == "Naukri"]), 1)


class TestSourceLabels(unittest.TestCase):
    def _verified_source(self, job):
        captured = {}

        def fake_tailor(norm, **kwargs):
            captured.update(norm)
            return {"versions": [{"markdown": "# resume", "validation": {"ok": True, "problems": []}}]}

        with mock.patch("tailoring_service.tailor", side_effect=fake_tailor), \
                mock.patch.object(tailor_job, "validate", return_value=(True, None)):
            self.assertEqual(tailor_job.verified_resume(job, "# master"), ("# resume", None))
        return captured["source"]

    def test_tailor_uses_job_source(self):
        base = {"title": "Java Developer", "company": "Infosys", "link": NAUKRI_LINK,
                "description": "Spring Boot microservices and Angular, REST APIs, SQL. " * 3, "score": 9}
        self.assertEqual(self._verified_source({**base, "source": "Naukri"}), "Naukri")
        self.assertEqual(self._verified_source(base), "LinkedIn")

    def test_sheet_rows_use_job_source_and_dedupe(self):
        saved = [
            {"title": "A", "company": "X", "link": NAUKRI_LINK + "?x=1", "score": 9, "source": "Naukri"},
            {"title": "A", "company": "X", "link": NAUKRI_LINK, "score": 9, "source": "Naukri"},
            {"title": "B", "company": "Y", "link": LINKEDIN_LINK, "score": 8},
            {"title": "C", "company": "Z", "link": "https://www.linkedin.com/jobs/view/1", "score": 8},
        ]
        rows = write_sheet.build_new_rows(saved, {"https://www.linkedin.com/jobs/view/1"}, "2026-10-07")
        self.assertEqual([(r[0], r[8]) for r in rows], [("A", "Naukri"), ("B", "LinkedIn")])
        self.assertEqual(len(rows[0]), len(write_sheet.HEADERS))

    def test_dashboard_lists_naukri_after_linkedin(self):
        with mock.patch.dict(os.environ, {"NAUKRI_JOBS_PATH": str(Path(tempfile.gettempdir()) / "n.json")}):
            sources = dd.sources()["job_sources"]
        self.assertEqual([s["id"] for s in sources], ["linkedin", "naukri"])
        self.assertTrue(sources[1]["active"])
        with mock.patch.dict(os.environ, {"NAUKRI_JOBS_PATH": ""}):
            self.assertFalse(dd.sources()["job_sources"][1]["active"])


if __name__ == "__main__":
    unittest.main()
