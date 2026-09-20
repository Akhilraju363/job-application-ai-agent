"""The shared tailoring service: JD -> analysis -> match -> tailor -> verify -> store.

All LLM calls are mocked (llm.call_llm for JD extraction, tailor_job.call_llm for the resume
itself) -- nothing here touches a provider or spends quota.
"""
import json
import unittest
from unittest import mock

from fixtures import ANALYSIS, BASE, JD_TEXT, TempOutput, reorder_bullets
import jd_analysis as ja
import no_fabrication as nf
import resume_store
import tailoring_service as ts

JOB = {"title": "Full Stack Java Developer", "company": "Acme", "link": "https://acme.example/jobs/1",
       "description": JD_TEXT}
FAKE_KAFKA = BASE.replace("REST API design", "REST API design and Kafka streaming")
FAKE_ROLE = BASE.replace("Zyter Technologies", "Zyter Technologies Ltd")


def run(outputs, analysis=ANALYSIS, job=JOB, **kw):
    """Run tailor() with mocked LLMs inside a temp output dir; returns (record, stages, tailor_mock)."""
    stages = []
    with TempOutput():
        with mock.patch("llm.call_llm", return_value=json.dumps(analysis)), \
                mock.patch("tailor_job.call_llm", side_effect=outputs) as t:
            rec = ts.tailor(job, source=kw.pop("source", "manual"), on_stage=stages.append, base_md=BASE, **kw)
        return rec, stages, t


class HappyPath(unittest.TestCase):
    def test_full_flow_and_real_stage_order(self):
        rec, stages, t = run([reorder_bullets(BASE)])
        v = rec["versions"][-1]
        self.assertEqual(stages, ["analyze", "match", "generate", "validate", "prepare"])
        self.assertTrue(v["validation"]["ok"])
        self.assertEqual(v["kind"], "generated")
        self.assertEqual(v["validation"]["attempts"], 1)
        self.assertEqual(t.call_count, 1)
        self.assertEqual(rec["source"], "manual")
        self.assertIsNotNone(rec["match"]["keywords"]["tailored_pct"])

    def test_missing_skills_are_reported_and_told_to_the_model_but_never_added(self):
        rec, _, t = run([reorder_bullets(BASE)])
        self.assertIn("Kafka", rec["match"]["missing_skills"])
        prompt = t.call_args.args[0]
        self.assertIn("MISSING REQUIREMENTS:", prompt)
        self.assertIn("Kafka", prompt.split("MISSING REQUIREMENTS:")[1].split("JOB DESCRIPTION")[0])
        self.assertNotIn("kafka", rec["versions"][-1]["markdown"].lower())

    def test_code_fences_around_the_resume_are_stripped(self):
        rec, _, _ = run(["```markdown\n" + BASE + "```"])
        self.assertTrue(rec["versions"][-1]["validation"]["ok"])
        self.assertFalse(rec["versions"][-1]["markdown"].startswith("```"))

    def test_scraped_and_manual_records_differ_only_by_source(self):
        a, _, _ = run([BASE], source="scraped", job_key="abc123abc123")
        b, _, _ = run([BASE], source="manual")
        self.assertEqual((a["source"], a["job"]["job_key"]), ("scraped", "abc123abc123"))
        self.assertEqual(b["source"], "manual")
        self.assertEqual(a["match"]["overall"], b["match"]["overall"])


class Verification(unittest.TestCase):
    def test_fabricated_output_is_retried_with_the_reasons_fed_back(self):
        rec, stages, t = run([FAKE_KAFKA, reorder_bullets(BASE)])
        v = rec["versions"][-1]
        self.assertTrue(v["validation"]["ok"])
        self.assertEqual(v["validation"]["attempts"], 2)
        self.assertEqual(stages.count("generate"), 2)
        retry_prompt = t.call_args_list[1].args[0]
        self.assertIn("PREVIOUS ATTEMPT WAS REJECTED", retry_prompt)
        self.assertIn("kafka", retry_prompt.lower())
        self.assertNotIn("kafka", v["markdown"].lower())

    def assertReorderOnly(self, md):
        """Same skill items and same experience bullets as the master resume -- only order differs."""
        a, b = nf.parse_resume(BASE), nf.parse_resume(md)
        self.assertEqual(sorted(a["skill_items"]), sorted(b["skill_items"]))
        for ra, rb in zip(a["roles"], b["roles"]):
            self.assertEqual((ra["heading"], ra["date"]), (rb["heading"], rb["date"]))
            self.assertEqual(sorted(ra["bullets"]), sorted(rb["bullets"]))
        for name in ("summary", "education", "certifications"):
            self.assertEqual(a["sections"][name], b["sections"][name])

    def test_two_failed_rewrites_fall_back_to_a_reorder_only_resume(self):
        rec, _, t = run([FAKE_KAFKA, FAKE_ROLE])
        v = rec["versions"][-1]
        self.assertEqual(t.call_count, 2)
        self.assertEqual(v["kind"], "conservative")
        self.assertTrue(v["validation"]["ok"])
        self.assertIn("re-orders", v["validation"]["notice"])
        self.assertTrue(v["validation"]["llm_problems"])
        self.assertEqual(nf.check_no_fabrication(BASE, v["markdown"]), [])
        self.assertNotIn("kafka", v["markdown"].lower())
        self.assertReorderOnly(v["markdown"])

    def test_conservative_resume_puts_jd_relevant_items_first_without_rewording(self):
        analysis = {**ANALYSIS, "required_skills": ["Python", "FastAPI"], "preferred_skills": [],
                    "technologies": ["Python", "FastAPI"], "keywords": []}
        match = ja.compute_match(analysis, BASE)
        md = ts.conservative_resume(BASE, analysis, match)
        self.assertIn("- Languages: Python, Java (Core & Advanced), TypeScript, SQL", md)
        self.assertIn("FastAPI", md.split("- Frameworks:")[1].splitlines()[0].split(",")[0])
        self.assertEqual(nf.check_no_fabrication(BASE, md), [])
        self.assertReorderOnly(md)

    def test_missing_required_section_blocks(self):
        v = ts.verify(BASE.replace("## Summary", "## About"), BASE)
        self.assertFalse(v["ok"])
        self.assertTrue(any("Summary" in p for p in v["problems"]))

    def test_ats_checks_flag_tables_and_html(self):
        v = ts.verify(BASE + "\n| a | b |\n", BASE)
        self.assertFalse(v["ok"])
        self.assertFalse(ts.verify(BASE + "\n<b>bold</b>\n", BASE)["ok"])

    def test_omitted_role_is_a_warning_not_a_block(self):
        t = BASE[:BASE.index("### Software Engineer | Java | Microservices")] + BASE[BASE.index("## Education"):]
        v = ts.verify(t, BASE)
        self.assertTrue(v["ok"])
        self.assertTrue(any("LTIMindtree" in w for w in v["warnings"]))


class Storage(unittest.TestCase):
    def test_regenerate_adds_a_version_to_the_same_resume(self):
        with TempOutput():
            with mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
                    mock.patch("tailor_job.call_llm", side_effect=[BASE, reorder_bullets(BASE)]):
                first = ts.tailor(JOB, source="manual", base_md=BASE)
                second = ts.tailor(JOB, source="manual", base_md=BASE, resume_id=first["id"])
            self.assertEqual(second["id"], first["id"])
            self.assertEqual([v["n"] for v in second["versions"]], [1, 2])
            self.assertEqual(second["versions"][1]["kind"], "regenerated")
            self.assertEqual(len(resume_store.list_all()), 1)

    def test_edit_is_reverified_and_a_bad_edit_stays_blocked(self):
        with TempOutput():
            with mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
                    mock.patch("tailor_job.call_llm", return_value=BASE):
                rec = ts.tailor(JOB, source="manual", base_md=BASE)
            good = ts.revalidate_edit(rec["id"], reorder_bullets(BASE), base_md=BASE)
            self.assertEqual((good["versions"][-1]["kind"], good["versions"][-1]["validation"]["ok"]), ("edited", True))
            bad = ts.revalidate_edit(rec["id"], FAKE_KAFKA, base_md=BASE)
            self.assertFalse(bad["versions"][-1]["validation"]["ok"])
            self.assertEqual(len(bad["versions"]), 3)

    def test_unusable_jd_stores_nothing(self):
        empty = {k: ([] if isinstance(v, list) else None if k == "experience_years_min" else "") for k, v in ANALYSIS.items()}
        with TempOutput():
            with mock.patch("llm.call_llm", return_value=json.dumps(empty)), \
                    mock.patch("tailor_job.call_llm") as t, self.assertRaises(ja.InputError):
                ts.tailor(JOB, source="manual", base_md=BASE)
            t.assert_not_called()
            self.assertEqual(resume_store.list_all(), [])

    def test_resume_ids_cannot_traverse_the_filesystem(self):
        with TempOutput():
            for bad in ("../x", "..", "0" * 11, "g" * 12, "abc/def"):
                with self.assertRaises(KeyError):
                    resume_store.get(bad)


if __name__ == "__main__":
    unittest.main()
