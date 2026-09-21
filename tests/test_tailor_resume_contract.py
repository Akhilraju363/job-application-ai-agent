"""The tailor_resume() contract: one entry point, structured result, transparent ATS score,
duplicate prevention, master-resume errors, no fabrication, exports and tracker save.
LLM / gws / Google Sheets are mocked throughout.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import ANALYSIS, BASE, JD_TEXT, TempOutput, reorder_bullets
import jd_analysis as ja
import no_fabrication as nf
import paths
import resume_store
import tailor_job
import tailoring_service as ts
import tracker_service
import write_sheet

KAFKA_INJECTED = BASE.replace("REST API design", "REST API design and Kafka streaming").replace(
    "- Tools: Git, SCSS", "- Tools: Git, SCSS, Kafka")


def run(md_outputs=None, analysis=ANALYSIS, base=BASE, **kw):
    """tailor_resume() with mocked LLMs in a temp output dir; returns (result, tailor_llm_mock, analysis_llm_mock)."""
    args = dict(job_description=JD_TEXT, job_title="Full Stack Java Developer", company="Acme",
                job_url="https://acme.example/jobs/1", source="Naukri")
    args.update(kw)
    with mock.patch("llm.call_llm", return_value=json.dumps(analysis)) as a_llm, \
            mock.patch("tailor_job.call_llm", side_effect=md_outputs or [reorder_bullets(BASE)] * 5) as t_llm:
        return ts.tailor_resume(base_md=base, **args), t_llm, a_llm


class StructuredResult(unittest.TestCase):
    def test_result_has_the_documented_shape(self):
        with TempOutput():
            r, _, _ = run()
        self.assertEqual(set(r), {"status", "reused", "job", "jd_analysis", "match_analysis", "resume", "ats_validation", "verification"})
        self.assertEqual(r["status"], "completed")
        self.assertEqual((r["job"]["title"], r["job"]["company"], r["job"]["url"], r["job"]["source"]),
                         ("Full Stack Java Developer", "Acme", "https://acme.example/jobs/1", "Naukri"))
        for key in ("required_skills", "preferred_skills", "programming_languages", "frameworks", "databases", "cloud", "tools",
                    "technologies", "responsibilities", "experience_requirements", "keywords", "education", "certifications"):
            self.assertIsInstance(r["jd_analysis"][key], list, key)
        m = r["match_analysis"]
        for key in ("overall_match", "skills_match", "experience_match", "keyword_match", "matching_skills", "missing_skills",
                    "relevant_experience", "relevant_projects", "relevant_responsibilities", "relevant_achievements"):
            self.assertIn(key, m)
        self.assertEqual(set(m["matching_skills"]) >= {"Java", "Spring Boot", "Angular"}, True)
        self.assertIn("Kafka", m["missing_skills"])
        res = r["resume"]
        self.assertEqual((res["version"], res["kind"]), (1, "generated"))
        self.assertIn("# Jane Roe", res["content"])
        self.assertEqual(len(res["master_resume_version"]), 12)
        self.assertEqual((res["pdf_url"], res["docx_url"]), (None, None))    # nothing exported yet -- no fake URLs
        self.assertEqual(r["verification"]["ok"], True)

    def test_manual_jd_without_url_gets_a_stable_synthetic_link_and_no_fake_url(self):
        with TempOutput():
            r, _, _ = run(job_url=None)
            rec = resume_store.get(r["resume"]["id"])
        self.assertTrue(rec["job"]["link"].startswith("manual:"))
        self.assertIsNone(r["job"]["url"])
        self.assertEqual(rec["job"]["source"], "Naukri")

    def test_export_urls_appear_only_after_a_real_export(self):
        with TempOutput():
            r, _, _ = run()
            rid = r["resume"]["id"]
            resume_store.mark_export(rid, 1, "pdf")
            after = ts.to_result(resume_store.get(rid))
        self.assertIn(f"/api/resumes/{rid}/download?format=pdf", after["resume"]["pdf_url"])
        self.assertIsNone(after["resume"]["docx_url"])


class OneServiceTwoEntryPoints(unittest.TestCase):
    def test_manual_and_scraped_converge_on_the_same_core_with_identical_job_shape(self):
        with mock.patch.object(ts, "tailor", return_value={"id": "abcdef012345"}) as core, \
                mock.patch.object(ts, "to_result", side_effect=lambda rec: {"resume": {"id": rec["id"]}}), \
                mock.patch.object(paths, "read_base_resume", return_value=BASE):
            ts.tailor_resume(JD_TEXT, "Java Dev", "Acme", "https://acme.example/1", "Naukri", None)
            ts.tailor_resume(JD_TEXT, "Java Dev", "Acme", "https://acme.example/1", "LinkedIn", "abc123abc123", pipeline_score=9)
        self.assertEqual(core.call_count, 2)
        (m_job,), m_kw = core.call_args_list[0].args, core.call_args_list[0].kwargs
        (s_job,), s_kw = core.call_args_list[1].args, core.call_args_list[1].kwargs
        self.assertEqual((m_kw["source"], s_kw["source"]), ("manual", "scraped"))
        self.assertEqual(s_kw["job_key"], "abc123abc123")
        self.assertEqual(set(m_job) - {"source"}, set(s_job) - {"pipeline_score", "source"})   # same job structure either way
        self.assertEqual(s_job["pipeline_score"], 9)


class JDAnalysis(unittest.TestCase):
    def test_categories_and_equivalent_terms(self):
        a = ja.parse_analysis({"required_skills": ["Spring Boot", "SpringBoot", "spring-boot", "Java", "JavaScript"],
                               "frameworks": ["Spring Boot"], "cloud": ["AWS"], "databases": ["Postgres", "PostgreSQL"],
                               "education": ["B.E."], "certifications": ["AWS SAA"], "experience_requirements": ["5+ years Java"]})
        self.assertEqual(a["required_skills"], ["Spring Boot", "Java", "JavaScript"])      # merged, but Java != JavaScript
        self.assertEqual(a["databases"], ["Postgres"])
        self.assertEqual((a["education"], a["certifications"]), (["B.E."], ["AWS SAA"]))
        self.assertIn("AWS", a["technologies"])
        self.assertEqual(a["experience_notes"], "5+ years Java")

    def test_unrelated_terms_are_never_merged(self):
        for x, y in (("Java", "JavaScript"), ("Angular", "AngularJS"), ("SQL", "MySQL"), ("C", "C++"), ("React", "React Native")):
            self.assertNotEqual(ja.canonical_key(x), ja.canonical_key(y), (x, y))
        for x, y in (("Spring Boot", "SpringBoot"), ("K8s", "Kubernetes"), ("REST API", "RESTful APIs"), ("Node.js", "NodeJS")):
            self.assertEqual(ja.canonical_key(x), ja.canonical_key(y), (x, y))

    def test_spelling_variants_match_the_master_resume(self):
        self.assertTrue(ja.term_present("Spring-Boot", BASE))
        self.assertTrue(ja.term_present("SpringBoot", BASE))
        self.assertFalse(ja.term_present("JavaScript", BASE))          # resume has Java + TypeScript, not JavaScript


class ATSValidation(unittest.TestCase):
    def report(self, md=None, title="Full Stack Java Developer", unsupported=()):
        md = md or reorder_bullets(BASE)
        match = ja.compute_match(ANALYSIS, BASE, md)
        checks = ts.ats_checks(md, ANALYSIS, match)["checks"]
        return ts.ats_validation(md, BASE, ANALYSIS, match, title, checks, unsupported)

    def test_score_is_the_documented_weighted_average_of_measurable_components(self):
        r = self.report()
        self.assertEqual(sum(c["weight"] for c in r["components"]), 100)
        used = [(c["weight"], c["score"]) for c in r["components"] if c["score"] is not None]
        self.assertEqual(r["score"], round(sum(w * s for w, s in used) / sum(w for w, _ in used)))
        self.assertTrue(0 <= r["score"] <= 100)
        self.assertIn("not a", r["disclaimer"].lower())                # no guarantee claimed
        self.assertEqual(self.report()["score"], r["score"])           # reproducible

    def test_missing_keywords_are_reported_and_split_by_whether_the_master_resume_has_them(self):
        r = self.report()
        self.assertIn("Kafka", r["missing_keywords"])
        self.assertIn("Kafka", r["not_in_master_resume"])              # cannot be added honestly
        self.assertEqual(r["required_skill_coverage"], 75)             # 3 of 4 required skills present
        self.assertNotIn("Java", r["missing_keywords"])

    def test_supported_but_omitted_keywords_produce_a_warning(self):
        r = self.report(md=BASE.replace("Frameworks: Spring Boot, Hibernate, Angular, RxJS, FastAPI", "Frameworks: Hibernate, RxJS, FastAPI")
                        .replace("Java and Angular applications", "applications").replace("Spring Boot", "Hibernate"))
        self.assertTrue(any("not surfaced" in w for w in r["warnings"]), r["warnings"])

    def test_duplicate_keyword_stuffing_is_flagged(self):
        stuffed = BASE + "\n" + " ".join(["Java"] * 8) + "\n"
        self.assertIn("Java", self.report(md=stuffed)["duplicate_keywords"])
        self.assertEqual(self.report()["duplicate_keywords"], [])   # an honest resume repeating its core stack is fine

    def test_formatting_and_structure_problems_lower_the_score_and_raise_issues(self):
        good = self.report()
        bad = self.report(md=BASE + "\n| a | b |\n")
        self.assertLess(bad["score"], good["score"])
        self.assertTrue(any("Plain single-column" in i for i in bad["issues"]))
        no_edu = self.report(md=BASE.replace("## Education", "## Studies"))
        self.assertLess([c for c in no_edu["components"] if c["name"] == "Structure"][0]["score"], 100)

    def test_unsupported_claims_hit_integrity_and_issues(self):
        r = self.report(unsupported=["technologies/skills not in master resume: kafka"])
        self.assertEqual([c["score"] for c in r["components"] if c["name"].startswith("Integrity")], [75])
        self.assertTrue(any(i.startswith("Unsupported claim") for i in r["issues"]))

    def test_job_title_relevance(self):
        self.assertEqual(ts._title_relevance("Senior Java Developer", BASE)[0], 50)     # java yes, developer no
        self.assertEqual(ts._title_relevance("Java Software Engineer", BASE)[0], 100)
        self.assertEqual(ts._title_relevance("Sr.", BASE), (None, []))


class NoFabricationEndToEnd(unittest.TestCase):
    """Acceptance test C: a technology the master resume lacks is reported as missing and never added."""

    def test_model_that_adds_the_missing_technology_is_rejected_and_it_stays_out(self):
        with TempOutput():
            r, t_llm, _ = run(md_outputs=[KAFKA_INJECTED, KAFKA_INJECTED])
        content = r["resume"]["content"]
        self.assertNotIn("kafka", content.lower())
        self.assertEqual(r["resume"]["kind"], "conservative")
        self.assertIn("Kafka", r["match_analysis"]["missing_skills"])
        self.assertIn("Kafka", r["ats_validation"]["not_in_master_resume"])
        self.assertEqual(nf.check_no_fabrication(BASE, content), [])
        self.assertEqual(t_llm.call_count, 2)

    def test_employer_technologies_are_not_swapped(self):
        swapped = BASE.replace("- Developed Java backend services and RESTful APIs using Spring Boot.",
                               "- Developed Python backend services and RESTful APIs using FastAPI.")
        with TempOutput():
            r, _, _ = run(md_outputs=[swapped, swapped])
        infinite = r["resume"]["content"].split("Infinite Computer Solutions")[1].split("###")[0]
        self.assertIn("Java backend services", infinite)
        self.assertNotIn("FastAPI", infinite)


class DuplicatePrevention(unittest.TestCase):
    def test_same_job_jd_and_master_resume_reuses_the_existing_resume(self):
        with TempOutput():
            first, t1, a1 = run()
            second, t2, a2 = run()
            self.assertFalse(first["reused"])
            self.assertTrue(second["reused"])
            self.assertEqual(second["resume"]["id"], first["resume"]["id"])
            self.assertEqual((t2.call_count, a2.call_count), (0, 0))              # no LLM spend at all
            self.assertEqual(len(resume_store.list_all()), 1)

    def test_changed_jd_or_changed_master_resume_creates_a_new_resume(self):
        with TempOutput():
            first, _, _ = run()
            other_jd, _, _ = run(job_description=JD_TEXT + " Also: GraphQL.")
            new_master, _, _ = run(base=BASE.replace("- Tools: Git, SCSS", "- Tools: Git, SCSS, Jira"),
                                   md_outputs=[reorder_bullets(BASE.replace("- Tools: Git, SCSS", "- Tools: Git, SCSS, Jira"))])
            ids = {first["resume"]["id"], other_jd["resume"]["id"], new_master["resume"]["id"]}
            self.assertEqual(len(ids), 3)
            self.assertNotEqual(first["resume"]["master_resume_version"], new_master["resume"]["master_resume_version"])

    def test_regenerate_creates_v2_v3_never_reuses(self):
        with TempOutput():
            first, _, _ = run()
            rid = first["resume"]["id"]
            second, t, _ = run(resume_id=rid, reuse=False)
            third, _, _ = run(resume_id=rid, reuse=False)
            self.assertEqual([second["resume"]["version"], third["resume"]["version"]], [2, 3])
            self.assertEqual(second["resume"]["id"], rid)
            self.assertEqual(t.call_count, 1)

    def test_a_blocked_resume_is_never_reused(self):
        with TempOutput():
            first, _, _ = run()
            rid = first["resume"]["id"]
            ts.revalidate_edit(rid, KAFKA_INJECTED, base_md=BASE)                    # latest version now fails verification
            again, t, _ = run()
            self.assertFalse(again["reused"])
            self.assertEqual(t.call_count, 1)


class Errors(unittest.TestCase):
    def test_missing_master_resume_has_the_documented_message_and_spends_no_llm_calls(self):
        with TempOutput(), mock.patch.object(paths, "BASE_RESUME", Path(tempfile.gettempdir()) / "no-such-resume.md"), \
                mock.patch("llm.call_llm") as a_llm, mock.patch("tailor_job.call_llm") as t_llm:
            with self.assertRaises(paths.MasterResumeError) as cm:
                ts.tailor_resume(JD_TEXT, "Dev", "Acme", None, None, None)
        self.assertEqual(str(cm.exception), "No master resume is configured. Please configure your master resume "
                                            "(resume/base_resume.md) before generating a tailored resume.")
        a_llm.assert_not_called()
        t_llm.assert_not_called()

    def test_empty_or_unusable_master_resume(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "base.md"
            for content, needle in (("", "No master resume"), ("# Jane\nhello", "missing required section")):
                f.write_text(content, encoding="utf-8")
                with mock.patch.object(paths, "BASE_RESUME", f), self.assertRaises(paths.MasterResumeError) as cm:
                    paths.read_base_resume()
                self.assertIn(needle, str(cm.exception))

    def test_empty_short_and_invalid_jd_are_rejected_before_any_llm_call(self):
        with TempOutput(), mock.patch("llm.call_llm") as a_llm:
            for bad in (dict(job_description=""), dict(job_description="too short"), dict(job_title=""), dict(job_title="   "),
                        dict(job_url="javascript:alert(1)")):
                args = dict(job_description=JD_TEXT, job_title="Dev", company="Acme", job_url=None, source=None, job_id=None)
                args.update(bad)
                with self.assertRaises(ja.InputError, msg=str(bad)):
                    ts.tailor_resume(base_md=BASE, **args)
            a_llm.assert_not_called()

    def test_llm_provider_failure_propagates_and_stores_nothing(self):
        from llm import AllProvidersFailed
        with TempOutput():
            with mock.patch("llm.call_llm", side_effect=AllProvidersFailed("all 3 provider(s) failed")), \
                    self.assertRaises(AllProvidersFailed):
                ts.tailor_resume(JD_TEXT, "Dev", "Acme", None, None, None, base_md=BASE)
            self.assertEqual(resume_store.list_all(), [])

    def test_resume_not_found(self):
        with TempOutput():
            with self.assertRaises(KeyError):
                resume_store.get("0123456789ab")


class Exports(unittest.TestCase):
    """PDF and DOCX come from the same Docs export the pipeline uses; the scratch Doc is always deleted."""

    def run_export(self, mime, export_rc=0):
        calls = []

        def fake_gws(*args, **kw):
            calls.append(args)
            return {"documentId": "DOC1"} if args[:3] == ("docs", "documents", "create") else {}

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "resume.out"
            md = Path(d) / "r.md"
            md.write_text(BASE, encoding="utf-8")
            runs = []

            def fake_run(cmd, **kw):
                runs.append(cmd)
                is_export = "export" in cmd
                if is_export and export_rc == 0:
                    out.write_bytes(b"bytes")
                return mock.Mock(returncode=export_rc if is_export else 0, stderr="export boom" if export_rc else "")

            with mock.patch.object(tailor_job, "gws", side_effect=fake_gws), mock.patch.object(tailor_job.subprocess, "run", side_effect=fake_run):
                try:
                    tailor_job.export_doc_file(md, out, mime)
                    err = None
                except RuntimeError as e:
                    err = e
            return calls, runs, err, out.exists()

    def test_pdf_and_docx_use_their_mime_types_and_clean_up(self):
        docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        for mime in ("application/pdf", docx):
            calls, runs, err, exists = self.run_export(mime)
            self.assertIsNone(err)
            self.assertTrue(exists)
            export_cmd = next(c for c in runs if "export" in c)
            self.assertIn(mime, export_cmd[export_cmd.index("--params") + 1])
            self.assertEqual(calls[0][:3], ("docs", "documents", "create"))
            self.assertEqual(calls[-1][:3], ("drive", "files", "delete"))               # scratch Doc removed

    def test_export_failure_raises_and_still_deletes_the_scratch_doc(self):
        calls, _, err, exists = self.run_export("application/pdf", export_rc=1)
        self.assertIn("export failed", str(err))
        self.assertFalse(exists)
        self.assertEqual(calls[-1][:3], ("drive", "files", "delete"))


class TrackerSave(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {"google_sheet_id": "SHEET1"})
        self.env.start()
        self.tr = tracker_service.Tracker()
        self.rows = []
        self.appended, self.updates = [], []
        self.patches = [
            mock.patch.object(write_sheet, "read_tracker", side_effect=lambda sid: list(self.rows)),
            mock.patch.object(write_sheet, "ensure_extra_headers"),
            mock.patch.object(write_sheet, "append_rows", side_effect=lambda sid, rows: self.appended.extend(rows)),
            mock.patch.object(write_sheet, "update_range", side_effect=lambda sid, rng, vals: self.updates.append((rng, vals))),
            mock.patch("activity.log_event"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()

    def test_row_carries_job_source_score_resume_id_and_match(self):
        out = self.tr.save_job(title="Java Dev", company="Acme", link="https://acme.example/1", score=8,
                               resume_path="output/generated_resumes/x/v1.pdf", source="Naukri",
                               resume_id="abcdef012345-v1", match_pct=79)
        self.assertEqual(out["result"], "added")
        (row,) = self.appended
        self.assertEqual(len(row), len(write_sheet.HEADERS))
        got = dict(zip(write_sheet.HEADERS, row))
        self.assertEqual((got["Job Title"], got["Company"], got["Job Link"], got["Fit Score"], got["Status"], got["Source"]),
                         ("Java Dev", "Acme", "https://acme.example/1", "8", "Not Applied", "Naukri"))
        self.assertEqual((got["Resume Path"], got["Resume ID"], got["Match %"]), ("output/generated_resumes/x/v1.pdf", "abcdef012345-v1", "79"))
        self.assertRegex(got["Timestamp"], r"^\d{4}-\d{2}-\d{2}$")

    def test_no_duplicate_rows_and_missing_resume_path_is_filled_in(self):
        self.rows = [{"row": 5, "title": "Java Dev", "company": "Acme", "link": "https://acme.example/1?trackingId=zzz", "resume_path": "",
                      "status": "Applied"}]
        out = self.tr.save_job(title="Java Dev", company="Acme", link="https://acme.example/1?trackingId=NEW", score=8, resume_path="p.md")
        self.assertEqual(out["result"], "exists")
        self.assertEqual(self.appended, [])
        self.assertEqual(self.updates, [("Sheet1!E5", [["p.md"]])])

    def test_sheet_failure_is_a_tracker_error_with_a_useful_message(self):
        with mock.patch.object(write_sheet, "append_rows", side_effect=RuntimeError("gws sheets +append failed: not signed in")):
            with self.assertRaises(tracker_service.TrackerError) as cm:
                self.tr.save_job(title="T", company="C", link="https://x.example/1", score=8)
        self.assertIn("not signed in", str(cm.exception))

    def test_reader_pads_old_eight_column_rows_and_parses_new_columns(self):
        values = [write_sheet.HEADERS[:8],
                  ["Old", "Co", "https://o.example/1", "8", "https://drive/x", "Applied", "2026-09-01", "notes"],
                  ["New", "Co2", "https://n.example/2", "9", "p.md", "Not Applied", "2026-09-19", "", "Naukri", "", "abc-v2", "81"]]
        with mock.patch.object(write_sheet, "gws", return_value={"values": values}), \
                mock.patch.object(write_sheet, "read_tracker", wraps=None) as _:
            pass
        with mock.patch.object(write_sheet, "gws", return_value={"values": values}):
            for p in self.patches[:1]:
                p.stop()
            try:
                rows = write_sheet.read_tracker("SHEET1")
            finally:
                self.patches[0].start()
        self.assertEqual((rows[0]["source"], rows[0]["resume_id"], rows[0]["status"]), ("", "", "Applied"))
        self.assertEqual((rows[1]["source"], rows[1]["resume_id"], rows[1]["match_pct"], rows[1]["row"]), ("Naukri", "abc-v2", "81", 3))

    def test_existing_pipeline_rows_are_unchanged_apart_from_trailing_columns(self):
        row = write_sheet.build_row({"title": "T", "company": "C", "link": "L", "score": 8, "resume_link": "R", "company_notes": "N"}, "2026-09-20")
        self.assertEqual(row[:8], ["T", "C", "L", "8", "R", "Not Applied", "2026-09-20", "N"])
        self.assertEqual(row[8:], ["LinkedIn", "", "", ""])


class OptionalCompany(unittest.TestCase):
    """Manual JDs may omit the employer: stored as "Company Not Specified", never guessed, never in the resume."""

    def test_blank_company_completes_the_whole_flow_with_the_fallback(self):
        for blank in ("", "   ", None):
            with TempOutput():
                r, t_llm, a_llm = run(company=blank, job_title="JAVA DEVELOPER")
                self.assertEqual((r["status"], r["job"]["title"], r["job"]["company"]),
                                 ("completed", "JAVA DEVELOPER", "Company Not Specified"), repr(blank))
                self.assertTrue(r["verification"]["ok"])
                self.assertNotIn("Company Not Specified", r["resume"]["content"])   # metadata, not an employer
                self.assertNotIn("Company Not Specified", a_llm.call_args.args[0])  # nor shown to either model as one
                self.assertNotIn("Company Not Specified", t_llm.call_args.args[0])
                self.assertIn("COMPANY: (not specified)", t_llm.call_args.args[0])

    def test_no_company_is_invented(self):
        with TempOutput():
            r, _, _ = run(company="", job_title="Java Developer",
                          job_description="Looking for Java Developers to join in Bangalore! " + JD_TEXT)
        self.assertEqual(r["job"]["company"], "Company Not Specified")
        for wrong in ("Bangalore", "LinkedIn", "Java Developer", "Recruiter", "Unknown"):
            self.assertNotIn(wrong, r["job"]["company"])

    def test_real_company_is_used_when_supplied(self):
        with TempOutput():
            r, t_llm, _ = run(company="ABC Technologies")
        self.assertEqual(r["job"]["company"], "ABC Technologies")
        self.assertIn("COMPANY: ABC Technologies", t_llm.call_args.args[0])

    def test_placeholder_leaking_into_the_resume_is_blocked_by_verification(self):
        leaked = BASE.replace("REST API design", "REST API design at Company Not Specified")
        self.assertIn("Company Not Specified", leaked)
        v = ts.verify(leaked, BASE, analysis=ANALYSIS)
        self.assertFalse(v["ok"])
        self.assertTrue(any("Company Not Specified" in p for p in v["problems"]), v["problems"])

    def test_storage_and_ids_are_valid_without_a_company(self):
        with TempOutput():
            r, _, _ = run(company="", job_url=None)
            rec = resume_store.get(r["resume"]["id"])
        self.assertRegex(r["resume"]["id"], r"^[0-9a-f]{12}")
        self.assertEqual(rec["job"]["company"], "Company Not Specified")
        self.assertTrue(rec["job"]["link"].startswith("manual:"))

    def test_duplicate_prevention_still_reuses_a_blank_company_resume(self):
        with TempOutput():
            first, _, _ = run(company="")
            second, t_llm, _ = run(company="")
            third, _, _ = run(company="Company Not Specified")
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        t_llm.assert_not_called()
        self.assertEqual(second["resume"]["id"], first["resume"]["id"])
        self.assertEqual(third["resume"]["id"], first["resume"]["id"])   # same job identity (link-keyed), unchanged

    def test_tracker_row_has_the_fallback_company(self):
        row = write_sheet.build_row({"title": "JAVA DEVELOPER", "company": ja.normalize_job("JAVA DEVELOPER", "", "", JD_TEXT)["company"],
                                     "link": "manual:abc", "score": 8}, "2026-09-21")
        self.assertEqual(row[:3], ["JAVA DEVELOPER", "Company Not Specified", "manual:abc"])


if __name__ == "__main__":
    unittest.main()
