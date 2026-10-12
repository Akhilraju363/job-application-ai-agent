"""Naukri history in raw_jobs.json: earlier Naukri records are kept (merged, not replaced) when a new
handoff arrives, and keeping them never widens a scoped run -- only the run's scope is scored/tailored.

Temporary output dirs and a temporary copy of the repo only; Apify, Drive and LLMs are never called."""
import fixtures  # noqa: F401 -- must be first: disables .env loading, puts scripts/ on sys.path
from fixtures import BASE, TempOutput

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import artifacts
import job_scope
import scrape_jobs

ROOT = Path(__file__).resolve().parent.parent
OLD = "https://www.naukri.com/job-listings-software-java-developer-creative-hands-121026001994"
NEW = "https://www.naukri.com/job-listings-java-fullstack-angular-infosys-240826031604"
LI = "https://www.linkedin.com/jobs/view/4300000001"


def naukri(link, job_id, title="Java Developer", found_at="2026-10-12", **extra):
    return {"title": title, "company": "Acme", "link": link, "description": "Spring Boot " * 20,
            "source": "Naukri", "source_job_id": job_id, "found_at": found_at, **extra}


def linkedin(link=LI, **extra):
    return {"title": "Full Stack Engineer", "company": "LiCo", "link": link, "description": "Angular " * 20,
            "source": "LinkedIn", "found_at": "2026-10-11", **extra}


class TestMergeNaukriHistory(unittest.TestCase):
    def test_new_handoff_job_is_added_and_history_kept(self):
        records, conflicts, stats = scrape_jobs.merge_naukri_history([naukri(OLD, "121026001994")],
                                                                     [naukri(NEW, "240826031604")])
        self.assertEqual([r["link"] for r in records], [OLD, NEW])
        self.assertEqual((conflicts, stats["added"], stats["refreshed"]), ([], 1, 0))

    def test_same_job_is_refreshed_once_keeping_earliest_found_at_and_old_fields(self):
        old = naukri(OLD + "?src=x", "121026001994", title="Old title", found_at="2026-10-09", resume_note="kept")
        new = naukri(OLD, "121026001994", title="New title", found_at="2026-10-12")
        records, conflicts, stats = scrape_jobs.merge_naukri_history([old], [new])
        self.assertEqual(len(records), 1)  # no duplicate
        self.assertEqual((records[0]["title"], records[0]["found_at"], records[0]["resume_note"]),
                         ("New title", "2026-10-09", "kept"))  # re-import never makes it look newer
        self.assertEqual((conflicts, stats["refreshed"]), ([], 1))

    def test_record_without_id_matches_by_link(self):
        old = naukri(OLD, None)
        records, _, stats = scrape_jobs.merge_naukri_history([old], [naukri(OLD, "121026001994")])
        self.assertEqual((len(records), stats["refreshed"]), (1, 1))

    def test_repeats_inside_the_handoff_keep_the_first(self):
        first, same_link, same_id = (naukri(NEW, "240826031604", title="first"),
                                     naukri(NEW + "?x=1", "240826031604", title="dup link"),
                                     naukri(OLD, "240826031604", title="dup id"))
        records, _, stats = scrape_jobs.merge_naukri_history([], [first, same_link, same_id])
        self.assertEqual([r["title"] for r in records], ["first"])
        self.assertEqual(stats["duplicates_in_handoff"], 2)

    def test_conflicts_never_lose_the_earlier_record(self):
        cases = {
            "same link, different source_job_id": ([naukri(OLD, "111")], naukri(OLD, "222")),
            "same source_job_id, different link": ([naukri(OLD, "111")], naukri(NEW, "111")),
            "link and source_job_id match different records": ([naukri(OLD, "111"), naukri(NEW, "222")],
                                                              naukri(OLD, "222")),
        }
        for reason, (history, incoming) in cases.items():
            with self.subTest(reason):
                records, conflicts, stats = scrape_jobs.merge_naukri_history(history, [incoming])
                self.assertEqual(stats["conflicts"], 1)
                self.assertTrue(all(c["reason"] == reason for c in conflicts))
                kept = [r for r in records] + [c["displaced"] for c in conflicts]
                for earlier in history:  # every earlier record is still somewhere
                    self.assertIn(earlier, kept)
                self.assertIn(incoming, records)

    def test_untouched_history_is_not_made_fresh(self):
        old = naukri(OLD, "121026001994", found_at="2026-10-01")
        records, _, _ = scrape_jobs.merge_naukri_history([old], [naukri(NEW, "240826031604")])
        self.assertEqual(records[0], old)


class RunScrapeCase(unittest.TestCase):
    def setUp(self):
        self.out = TempOutput()
        self.dir = self.out.__enter__()
        self.addCleanup(self.out.__exit__, None, None, None)
        self.cache, self.handoff = self.dir / "raw_jobs.json", self.dir / "naukri_jobs.json"
        for patcher in (mock.patch.object(scrape_jobs, "CACHE_PATH", self.cache), mock.patch.object(artifacts, "pull"),
                        mock.patch.object(artifacts, "push"),
                        mock.patch.object(scrape_jobs, "scrape_jobs", return_value=[linkedin(LI + "9")])):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.apify = scrape_jobs.scrape_jobs

    def env(self, **values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cache_with(self, jobs, age):
        self.cache.write_text(json.dumps(jobs), encoding="utf-8")
        os.utime(self.cache, (time.time() - age, time.time() - age))

    def handoff_with(self, jobs):
        self.handoff.write_text(json.dumps(jobs), encoding="utf-8")
        self.env(NAUKRI_JOBS_PATH=str(self.handoff))

    def raw(self):
        return json.loads(self.cache.read_text(encoding="utf-8"))


class TestRunScrapeKeepsHistory(RunScrapeCase):
    def test_previous_naukri_jobs_stay_and_linkedin_is_unchanged(self):
        self.env(JOB_SOURCES="naukri")
        history = [linkedin(), naukri(OLD, "121026001994", found_at="2026-10-11")]
        self.cache_with(history, age=60)
        self.handoff_with([naukri(NEW, "240826031604")])
        jobs = scrape_jobs.run_scrape()
        self.apify.assert_not_called()
        self.assertEqual(jobs, [history[0], history[1], naukri(NEW, "240826031604")])
        self.assertEqual(self.raw(), jobs)

    def test_rerun_with_the_same_handoff_adds_nothing(self):
        self.env(JOB_SOURCES="naukri")
        self.cache_with([linkedin(), naukri(OLD, "121026001994")], age=60)
        self.handoff_with([naukri(NEW, "240826031604")])
        first = scrape_jobs.run_scrape()
        self.assertEqual(scrape_jobs.run_scrape(), first)

    def test_stale_linkedin_cache_still_keeps_naukri_history(self):
        self.env(JOB_SOURCES="naukri")
        self.cache_with([linkedin(), naukri(OLD, "121026001994")], age=7 * 3600)
        self.handoff_with([naukri(NEW, "240826031604")])
        jobs = scrape_jobs.run_scrape()
        self.assertEqual([j["link"] for j in jobs], [OLD, NEW])  # stale LinkedIn cache expires as before
        self.assertEqual(self.cache.stat().st_mtime, 0)

    def test_linkedin_scrape_keeps_naukri_history(self):
        self.env(JOB_SOURCES="", NAUKRI_JOBS_PATH="")
        self.cache_with([linkedin(), naukri(OLD, "121026001994")], age=7 * 3600)
        jobs = scrape_jobs.run_scrape()
        self.apify.assert_called_once()
        self.assertEqual([j["link"] for j in jobs], [LI + "9", OLD])

    def test_conflicting_earlier_record_is_kept_in_the_conflicts_file(self):
        self.env(JOB_SOURCES="naukri")
        old = naukri(OLD, "121026001994", title="earlier")
        self.cache_with([linkedin(), old], age=60)
        self.handoff_with([naukri(OLD, "999999999999", title="current")])
        jobs = scrape_jobs.run_scrape()
        self.assertEqual([j["title"] for j in jobs if j["source"] == "Naukri"], ["current"])
        lines = (self.dir / "raw_jobs_conflicts.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["displaced"], old)
        scrape_jobs.run_scrape()  # append-only: a later run never clears it
        self.assertGreaterEqual(len((self.dir / "raw_jobs_conflicts.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_unreadable_cache_is_copied_aside_not_lost(self):
        self.env(JOB_SOURCES="naukri")
        self.cache.write_text("{not json", encoding="utf-8")
        self.handoff_with([naukri(NEW, "240826031604")])
        jobs = scrape_jobs.run_scrape(force=True)
        self.assertEqual([j["link"] for j in jobs], [NEW])
        copies = list(self.dir.glob("raw_jobs.unreadable-*.json"))
        self.assertEqual([c.read_text(encoding="utf-8") for c in copies], ["{not json"])

    def test_recent_malformed_cache_recovers_without_a_second_parse(self):
        self.env(JOB_SOURCES="naukri")
        old = naukri(OLD, "121026001994")
        self.cache.write_text("{not json", encoding="utf-8")
        self.handoff_with([naukri(NEW, "240826031604")])
        jobs = scrape_jobs.run_scrape()
        self.assertEqual([j["source_job_id"] for j in jobs], ["240826031604"])
        self.assertEqual((self.dir / "raw_jobs.json").read_text(encoding="utf-8"),
                         json.dumps(jobs, indent=2))
        self.assertEqual([c.read_text(encoding="utf-8")
                          for c in self.dir.glob("raw_jobs.unreadable-*.json")], ["{not json"])
        self.assertNotIn(old, jobs)

    def test_old_malformed_cache_forces_linkedin_refresh_and_keeps_handoff(self):
        self.env(JOB_SOURCES="", NAUKRI_JOBS_PATH="")
        self.cache.write_text("{not json", encoding="utf-8")
        self.handoff_with([naukri(NEW, "240826031604")])
        jobs = scrape_jobs.run_scrape()
        self.apify.assert_called_once()
        self.assertEqual([j["link"] for j in jobs], [LI + "9", NEW])

    def test_object_cache_is_backed_up_and_recovered_from_handoff(self):
        self.env(JOB_SOURCES="naukri")
        original = '{"jobs": []}'
        self.cache.write_text(original, encoding="utf-8")
        self.handoff_with([naukri(NEW, "240826031604")])
        jobs = scrape_jobs.run_scrape()
        self.assertEqual([j["source_job_id"] for j in jobs], ["240826031604"])
        self.assertEqual([c.read_text(encoding="utf-8")
                          for c in self.dir.glob("raw_jobs.unreadable-*.json")], [original])

    def test_unreadable_cache_without_handoff_fails_clearly(self):
        self.env(JOB_SOURCES="naukri", NAUKRI_JOBS_PATH="")
        self.cache.write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "could not be recovered"):
            scrape_jobs.run_scrape()


class TestHistoryNeverWidensTheScope(unittest.TestCase):
    """The real scrape, score and tailor scripts in a temporary repo copy. Historical Naukri records
    that are unscored, or qualified but untailored, stay in raw_jobs.json yet are not processed."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="jobagent-history-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        shutil.copytree(ROOT / "scripts", self.root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "resume").mkdir()
        (self.root / "resume" / "base_resume.md").write_text(BASE, encoding="utf-8")
        self.out = self.root / "output"
        self.out.mkdir()

    def write(self, name, data):
        (self.out / name).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def read(self, name):
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def run_stage(self, script, *args, **env_extra):
        env = {k: v for k, v in os.environ.items() if not k.lower().startswith(("llm_", "open_router", "groq", "gemini"))}
        env.update(PYTHON_DOTENV_DISABLED="1", artifact_sync="0", google_drive_folder_id="test-folder",
                   JOB_AGENT_OUTPUT_DIR=str(self.out), **env_extra)
        env.pop("JOB_IDS_FILE", None)
        return subprocess.run([sys.executable, str(self.root / "scripts" / script), *args], cwd=self.root, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=120)

    def test_history_stays_stored_but_only_the_scope_is_processed(self):
        unscored_old = naukri(OLD, "121026001994")  # earlier export whose scoring failed
        qualified_old = naukri("https://www.naukri.com/job-listings-x-121026001987", "121026001987")
        current = naukri(NEW, "240826031604")
        self.write("raw_jobs.json", [linkedin(), unscored_old, qualified_old])
        os.utime(self.out / "raw_jobs.json", (time.time() - 60, time.time() - 60))
        scored_current = {**current, "score": 9, "qualified": True, "reasoning": "x",
                          "matched_must_haves": [], "missing_must_haves": []}
        scored_old = {**qualified_old, "score": 9, "qualified": True, "reasoning": "x",
                      "matched_must_haves": [], "missing_must_haves": []}
        self.write("scored_jobs.json", [scored_old, scored_current])  # current already scored: no LLM needed
        flagged = {**qualified_old, "status": "flagged_error", "reason": "earlier failure"}
        self.write("tailored_jobs.json", [{**current, "status": "saved", "resume_id": "r1"}, flagged])
        handoff = self.root / "naukri_jobs.json"
        handoff.write_text(json.dumps([current]), encoding="utf-8")
        scope = self.root / "scope.json"
        scope.write_text(json.dumps({"run_id": "run-1", "jobs": [{"id": "240826031604", "link": NEW}]}), encoding="utf-8")
        before_scored, before_tailored = self.read("scored_jobs.json"), self.read("tailored_jobs.json")

        scrape = self.run_stage("scrape_jobs.py", JOB_SOURCES="naukri", NAUKRI_JOBS_PATH=str(handoff))
        self.assertEqual(scrape.returncode, 0, scrape.stderr[-2000:])
        self.assertEqual([j["link"] for j in self.read("raw_jobs.json")], [LI, OLD, qualified_old["link"], NEW])
        for script in ("score_jobs.py", "tailor_job.py"):
            result = self.run_stage(script, "--job-ids-file", str(scope))
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr[-2000:]}")
        self.assertEqual(self.read("scored_jobs.json"), before_scored)  # the unscored old job was not scored
        self.assertEqual(self.read("tailored_jobs.json"), before_tailored)  # nor the qualified old one tailored
        report = self.read(job_scope.REPORT_NAME)
        named = {i for section in ("score", "tailor") for v in report[section].values() if isinstance(v, list) for i in v}
        self.assertEqual(named, {"240826031604"})  # only this run's id appears anywhere in the report
        self.assertEqual((report["score"]["newly_scored"], report["tailor"]["newly_tailored"]), ([], []))

    def test_missing_or_invalid_scope_still_fails_closed_with_history_present(self):
        self.write("raw_jobs.json", [linkedin(), naukri(OLD, "121026001994")])
        self.write("scored_jobs.json", [])
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"jobs": [{"id": "1", "link": NEW}]}), encoding="utf-8")  # no run_id
        for args in (["--job-ids-file", str(bad)], ["--job-ids-file", str(self.root / "missing.json")]):
            result = self.run_stage("score_jobs.py", *args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ScopeError", result.stderr)
        self.assertEqual(self.read("scored_jobs.json"), [])


if __name__ == "__main__":
    unittest.main()
