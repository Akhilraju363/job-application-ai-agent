"""Scoped runs (--job-ids-file): only the listed jobs may be scored or tailored; everything else in
the cumulative stores (cached LinkedIn jobs, older Naukri jobs) is left alone and nothing historical
is rewritten. No LLM, network, Drive or Sheet is used: the stage scripts run against a temporary copy
of the repository in which a correct scope leaves nothing for an LLM to do."""
import fixtures  # noqa: F401 -- must be first: disables .env loading, puts scripts/ on sys.path
from fixtures import BASE

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import job_scope
import score_jobs

ROOT = Path(__file__).resolve().parent.parent
NAUKRI = "https://www.naukri.com/job-listings-java-developer-acme-bengaluru-111111111111"
NAUKRI_OLD = "https://www.naukri.com/job-listings-java-developer-oldco-chennai-222222222222"
LINKEDIN = "https://www.linkedin.com/jobs/view/4300000001"


def job(link, title="Java Developer", source="Naukri", **extra):
    return {"title": title, "company": "Acme", "link": link, "description": "Spring Boot " * 20,
            "source": source, **extra}


def scored(link, qualified=True, **extra):
    return {**job(link, **extra), "score": 9 if qualified else 5, "reasoning": "x", "matched_must_haves": [],
            "missing_must_haves": [], "qualified": qualified}


def write_scope(directory, links, run_id="run-1", ids=None):
    path = Path(directory) / "scope.json"
    ids = ids or [f"id-{i}" for i in range(len(links))]
    path.write_text(json.dumps({"run_id": run_id, "jobs": [{"id": i, "link": l} for i, l in zip(ids, links)]}),
                    encoding="utf-8")
    return path


class TestScopeFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_flag_env_and_unscoped(self):
        self.assertIsNone(job_scope.scope_path([], {}))  # unscoped: previous behaviour
        self.assertEqual(job_scope.scope_path(["--job-ids-file", "a.json"], {}), "a.json")
        self.assertEqual(job_scope.scope_path(["--job-ids-file=b.json"], {}), "b.json")
        self.assertEqual(job_scope.scope_path([], {"JOB_IDS_FILE": "c.json"}), "c.json")
        self.assertEqual(job_scope.scope_path(["--job-ids-file", "a.json"], {"JOB_IDS_FILE": "c.json"}), "a.json")
        for argv in (["--job-ids-file"], ["--job-ids-file="], ["--job-ids-file", " "]):
            with self.subTest(argv), self.assertRaises(job_scope.ScopeError):
                job_scope.scope_path(argv, {})

    def test_load_canonicalizes_and_keeps_caller_ids(self):
        path = write_scope(self.dir, [NAUKRI + "?src=x", NAUKRI, LINKEDIN], ids=["111", "dup", "li"])
        scope = job_scope.load_scope(path)
        self.assertEqual(scope.run_id, "run-1")
        self.assertEqual(scope.ids, {NAUKRI: "111", LINKEDIN: "li"})

    def test_empty_scope_is_valid_and_means_nothing(self):
        scope = job_scope.load_scope(write_scope(self.dir, []))
        self.assertEqual(scope.links, set())
        to_score, report = job_scope.select_for_scoring([job(LINKEDIN, source="LinkedIn")], set(), scope)
        self.assertEqual((to_score, report["to_score"]), ([], []))

    def test_missing_or_malformed_files_are_errors(self):
        bad = Path(self.dir) / "bad.json"
        link = json.dumps(NAUKRI)
        for content in ("{not json", "[]", '{"jobs": "x"}', '{"run_id": "r", "jobs": [{"id": "1"}]}',
                        '{"run_id": "r", "jobs": [{"link": "ftp://x"}]}',
                        '{"jobs": [{"id": "1", "link": %s}]}' % link,  # no run_id: reports could be mixed up
                        '{"run_id": "  ", "jobs": []}', '{"run_id": 7, "jobs": []}'):
            bad.write_text(content, encoding="utf-8")
            with self.subTest(content), self.assertRaises(job_scope.ScopeError):
                job_scope.load_scope(bad)
        with self.assertRaises(job_scope.ScopeError):
            job_scope.load_scope(Path(self.dir) / "absent.json")


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.scope = job_scope.load_scope(write_scope(self.dir, [NAUKRI, NAUKRI_OLD, "https://www.naukri.com/x-999"],
                                                      ids=["111", "222", "999"]))

    def test_scoring_selects_only_unscored_scope_members(self):
        raw = [job(NAUKRI), job(NAUKRI_OLD), job(LINKEDIN, source="LinkedIn"), job(NAUKRI + "?dup=1")]
        to_score, report = job_scope.select_for_scoring(raw, {NAUKRI_OLD}, self.scope)
        self.assertEqual([j["link"] for j in to_score], [NAUKRI])
        self.assertEqual(report["to_score"], ["111"])
        self.assertEqual(report["already_scored"], ["222"])
        self.assertEqual(report["missing"], ["999"])  # reported, never replaced by another job
        finished = job_scope.finish_scoring(report, to_score, [scored(NAUKRI_OLD)], self.scope)
        self.assertEqual((finished["newly_scored"], finished["failed"]), ([], ["111"]))

    def test_scored_job_no_longer_in_raw_jobs_is_already_scored_not_missing(self):
        _, report = job_scope.select_for_scoring([], {NAUKRI}, self.scope)
        self.assertEqual((report["already_scored"], report["to_score"]), (["111"], []))
        self.assertEqual(report["missing"], ["222", "999"])

    def test_tailoring_selects_only_qualified_scope_members(self):
        jobs = [scored(NAUKRI), scored(NAUKRI_OLD, qualified=False), scored(LINKEDIN, source="LinkedIn")]
        qualified, report = job_scope.select_for_tailoring(jobs, self.scope)
        self.assertEqual([j["link"] for j in qualified], [NAUKRI])
        self.assertEqual((report["qualified"], report["not_qualified"], report["not_scored"]), (["111"], ["222"], ["999"]))

    def test_only_scoped_jobs_reach_the_llm(self):
        raw = [job(NAUKRI, title="Scoped"), job(LINKEDIN, title="Cached LinkedIn", source="LinkedIn")]
        to_score, _ = job_scope.select_for_scoring(raw, set(), self.scope)
        prompts = []

        def fake_llm(prompt, title, json_mode=False):
            prompts.append(title)
            return json.dumps({"score": 9, "reasoning": "fit"})
        with mock.patch.object(score_jobs, "call_llm", fake_llm):
            result = score_jobs.score_jobs(to_score, BASE)
        self.assertEqual(prompts, ["Scoped"])
        self.assertEqual([j["link"] for j in result], [NAUKRI])

    def test_report_is_per_run(self):
        out = Path(self.dir)
        job_scope.write_report(out, "score", {"newly_scored": ["111"]}, self.scope)
        job_scope.write_report(out, "tailor", {"newly_tailored": []}, self.scope)
        report = json.loads((out / job_scope.REPORT_NAME).read_text(encoding="utf-8"))
        self.assertEqual((report["run_id"], set(report)), ("run-1", {"run_id", "scope_file", "ids", "score", "tailor"}))
        next_run = job_scope.load_scope(write_scope(self.dir, [NAUKRI], run_id="run-2"))
        job_scope.write_report(out, "score", {"newly_scored": []}, next_run)
        report = json.loads((out / job_scope.REPORT_NAME).read_text(encoding="utf-8"))
        self.assertEqual((report["run_id"], "tailor" in report), ("run-2", False))


class TestStageScripts(unittest.TestCase):
    """The real stage scripts, in a temporary copy of the repo (no network, no LLM, no Drive)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="jobagent-scope-"))
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

    def run_stage(self, script, *args):
        env = {k: v for k, v in os.environ.items() if not k.lower().startswith(("llm_", "open_router", "groq", "gemini"))}
        env.update(PYTHON_DOTENV_DISABLED="1", artifact_sync="0", google_drive_folder_id="test-folder",
                   JOB_AGENT_OUTPUT_DIR=str(self.out))
        env.pop("JOB_IDS_FILE", None)
        return subprocess.run([sys.executable, str(self.root / "scripts" / script), *args], cwd=self.root, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=120)

    def seed(self):
        """A scoped Naukri job already scored and tailored, plus a cached LinkedIn job that is
        unscored and a qualified-but-untailored LinkedIn job: an unscoped run would process both."""
        self.write("raw_jobs.json", [job(NAUKRI), job(LINKEDIN, source="LinkedIn")])
        self.write("scored_jobs.json", [scored(NAUKRI), scored("https://www.linkedin.com/jobs/view/4300000002",
                                                               source="LinkedIn")])
        self.write("tailored_jobs.json", [{**job(NAUKRI), "status": "saved", "resume_id": "r1"}])
        return {name: (self.out / name).read_text(encoding="utf-8")
                for name in ("raw_jobs.json", "scored_jobs.json", "tailored_jobs.json")}

    def test_scoped_score_and_tailor_leave_other_jobs_and_history_alone(self):
        before = self.seed()
        scope = write_scope(self.root, [NAUKRI], ids=["111111111111"])
        for script in ("score_jobs.py", "tailor_job.py"):
            result = self.run_stage(script, "--job-ids-file", str(scope))
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr[-2000:]}")
        for name, text in before.items():  # nothing outside the scope was scored, tailored or rewritten
            self.assertEqual(json.loads((self.out / name).read_text(encoding="utf-8")), json.loads(text), name)
        report = self.read(job_scope.REPORT_NAME)
        self.assertEqual(report["score"]["already_scored"], ["111111111111"])
        self.assertEqual((report["score"]["newly_scored"], report["score"]["missing"]), ([], []))
        self.assertEqual(report["tailor"]["already_tailored"], ["111111111111"])
        self.assertEqual((report["tailor"]["newly_tailored"], report["tailor"]["flagged"]), ([], []))

    def test_scoped_tailor_keeps_out_of_scope_records_it_did_not_process(self):
        """Unscoped runs drop flagged records because they retry them; a scoped run never retries
        out-of-scope jobs, so it must keep their history exactly as it was."""
        self.seed()
        flagged = {**job(LINKEDIN, source="LinkedIn"), "status": "flagged_error", "reason": "earlier failure"}
        tailored = self.read("tailored_jobs.json") + [flagged]
        self.write("tailored_jobs.json", tailored)
        scope = write_scope(self.root, [NAUKRI], ids=["111111111111"])
        self.assertEqual(self.run_stage("tailor_job.py", "--job-ids-file", str(scope)).returncode, 0)
        self.assertEqual(self.read("tailored_jobs.json"), tailored)

    def test_unknown_ids_are_reported_and_nothing_else_is_processed(self):
        before = self.seed()
        scope = write_scope(self.root, ["https://www.naukri.com/job-listings-x-999999999999"], ids=["999999999999"])
        for script in ("score_jobs.py", "tailor_job.py"):
            self.assertEqual(self.run_stage(script, "--job-ids-file", str(scope)).returncode, 0)
        report = self.read(job_scope.REPORT_NAME)
        self.assertEqual(report["score"]["missing"], ["999999999999"])
        self.assertEqual(report["tailor"]["not_scored"], ["999999999999"])
        self.assertEqual(self.read("scored_jobs.json"), json.loads(before["scored_jobs.json"]))

    def test_empty_scope_processes_nothing(self):
        before = self.seed()
        scope = write_scope(self.root, [])
        for script in ("score_jobs.py", "tailor_job.py"):
            self.assertEqual(self.run_stage(script, "--job-ids-file", str(scope)).returncode, 0)
        self.assertEqual(self.read("scored_jobs.json"), json.loads(before["scored_jobs.json"]))
        self.assertEqual(self.read("tailored_jobs.json"), json.loads(before["tailored_jobs.json"]))
        self.assertEqual(self.read(job_scope.REPORT_NAME)["score"]["to_score"], [])

    def test_bad_scope_file_stops_the_stage_instead_of_running_unscoped(self):
        before = self.seed()
        for args in (["--job-ids-file", str(self.root / "missing.json")], ["--job-ids-file"]):
            for script in ("score_jobs.py", "tailor_job.py"):
                result = self.run_stage(script, *args)
                self.assertNotEqual(result.returncode, 0, script)
                self.assertIn("ScopeError", result.stderr)
        for name, text in before.items():
            self.assertEqual(json.loads((self.out / name).read_text(encoding="utf-8")), json.loads(text), name)
        self.assertFalse((self.out / job_scope.REPORT_NAME).exists())

    def test_unscoped_run_is_unchanged_and_still_processes_cached_jobs(self):
        """Backward compatibility (and the control for the scoped tests): with no scope the stage
        still takes the cached LinkedIn job -- here it fails fast because no LLM is configured."""
        self.seed()
        result = self.run_stage("score_jobs.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scored 0/1", result.stderr)
        self.assertFalse((self.out / job_scope.REPORT_NAME).exists())

    def test_repeated_scoped_runs_report_the_same_without_new_work(self):
        self.seed()
        scope = write_scope(self.root, [NAUKRI], ids=["111111111111"])
        reports = []
        for _ in range(2):
            for script in ("score_jobs.py", "tailor_job.py"):
                self.assertEqual(self.run_stage(script, "--job-ids-file", str(scope)).returncode, 0)
            reports.append(self.read(job_scope.REPORT_NAME))
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(reports[1]["tailor"]["newly_tailored"], [])


if __name__ == "__main__":
    unittest.main()
