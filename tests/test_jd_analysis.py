"""JD intake/sanitising, extraction coercion, and code-verified matching (incl. missing skills)."""
import json
import unittest
from unittest import mock

from fixtures import ANALYSIS, BASE, JD_TEXT
import jd_analysis as ja


class Sanitizing(unittest.TestCase):
    def test_strips_markup_and_control_chars(self):
        out = ja.sanitize_jd("<script>alert(1)</script>Build APIs\x00 with   Spring Boot.\r\n\r\n\r\n\r\nApply now. " * 3)
        self.assertNotIn("<script>", out)
        self.assertNotIn("\x00", out)
        self.assertNotIn("\r", out)
        self.assertNotIn("\n\n\n", out)

    def test_size_limits(self):
        with self.assertRaises(ja.InputError):
            ja.sanitize_jd("too short")
        with self.assertRaises(ja.InputError):
            ja.sanitize_jd("x" * (ja.MAX_JD_CHARS + 1))

    def test_url_must_be_http(self):
        self.assertEqual(ja.sanitize_url(""), "")
        self.assertEqual(ja.sanitize_url(" https://example.com/job/1 "), "https://example.com/job/1")
        for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://x.com/a", "https://a b.com"):
            with self.assertRaises(ja.InputError, msg=bad):
                ja.sanitize_url(bad)

    def test_title_required_and_single_line(self):
        for blank in ("", "   ", None):
            with self.assertRaises(ja.InputError, msg=repr(blank)) as cm:
                ja.normalize_job(blank, "Acme", "", JD_TEXT)
            self.assertEqual(str(cm.exception), "Job title is required.")
            with self.assertRaises(ja.InputError) as cm:
                ja.normalize_job(blank, "", "", JD_TEXT)
            self.assertEqual(str(cm.exception), "Job title is required.")
        job = ja.normalize_job("Dev\nEngineer", "Acme\x00 Corp", "", JD_TEXT)
        self.assertEqual((job["title"], job["company"]), ("Dev Engineer", "Acme Corp"))

    def test_job_title_accepts_any_capitalisation_and_is_preserved(self):
        for title in ("java developer", "Java Developer", "JAVA DEVELOPER", "JaVa DeVeLoPeR"):
            self.assertEqual(ja.normalize_job(title, "Acme", "", JD_TEXT)["title"], title)

    def test_company_is_optional_and_falls_back_deterministically(self):
        for blank in ("", "   ", None, "\x00 \n"):
            job = ja.normalize_job("JAVA DEVELOPER", blank, "", JD_TEXT)
            self.assertEqual(job["company"], "Company Not Specified", repr(blank))
        self.assertEqual(ja.normalize_job("Dev", " Acme Corp ", "", JD_TEXT)["company"], "Acme Corp")

    def test_fallback_is_never_shown_to_a_model_as_an_employer(self):
        self.assertEqual(ja.real_company({"company": ja.COMPANY_NOT_SPECIFIED}), "")
        self.assertEqual(ja.real_company({"company": "Acme"}), "Acme")
        job = ja.normalize_job("Dev", "", "", JD_TEXT)
        with mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)) as m:
            ja.analyze_jd(job)
        self.assertNotIn("Company Not Specified", m.call_args.args[0])
        self.assertNotIn("Company Not Specified", ja.ANALYSIS_PROMPT)


class Extraction(unittest.TestCase):
    def test_parse_analysis_coerces_junk(self):
        a = ja.parse_analysis({"required_skills": "Java", "preferred_skills": ["x", "X", " "], "technologies": None,
                               "experience_years_min": "five", "domain": 7, "keywords": ["A"] * 40})
        self.assertEqual(a["required_skills"], ["Java"])
        self.assertEqual(a["preferred_skills"], ["x"])
        self.assertEqual(a["technologies"], [])
        self.assertIsNone(a["experience_years_min"])
        self.assertEqual(len(a["keywords"]), 1)  # de-duplicated
        self.assertEqual(ja.parse_analysis("not a dict")["required_skills"], [])

    def test_analyze_jd_uses_the_provider_chain_and_rejects_empty_extraction(self):
        job = {"title": "Dev", "company": "Acme", "description": JD_TEXT}
        with mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)) as call:
            self.assertEqual(ja.analyze_jd(job)["required_skills"][0], "Java")
            self.assertTrue(call.call_args.kwargs["json_mode"])
            self.assertIn("untrusted DATA", call.call_args.args[0])
        with mock.patch("llm.call_llm", return_value="{}"), self.assertRaises(ja.InputError):
            ja.analyze_jd(job)


class Matching(unittest.TestCase):
    def test_aliases_and_plurals(self):
        self.assertTrue(ja.term_present("SpringBoot", BASE))
        self.assertTrue(ja.term_present("REST APIs", BASE))          # resume says RESTful APIs
        self.assertTrue(ja.term_present("Microservice", BASE))
        self.assertFalse(ja.term_present("Kafka", BASE))
        self.assertFalse(ja.term_present("React", BASE))

    def test_generic_phrase_does_not_vouch_for_parenthesised_specifics(self):
        # the resume mentions healthcare but neither HL7 nor FHIR
        self.assertFalse(ja.term_present("Healthcare domain experience (HL7/FHIR)", BASE))
        self.assertTrue(ja.term_present("Healthcare", BASE))
        # specifics that ARE supported still match; examples after a single-word outer don't gate it
        self.assertTrue(ja.term_present("Unit testing (JUnit, Mockito)", BASE + "\n- Wrote tests with JUnit."))
        self.assertTrue(ja.term_present("AWS (EC2, S3, RDS)", BASE))
        self.assertTrue(ja.term_present("Java (8+)", BASE))
        self.assertFalse(ja.term_present("Kafka (event streaming)", BASE))

    def test_filler_and_spelling_do_not_cause_false_gaps(self):
        self.assertTrue(ja.term_present("CI/CD pipelines", BASE))
        self.assertTrue(ja.term_present("SQL query optimisation", BASE))       # resume: "query optimization"
        self.assertFalse(ja.term_present("Data pipelines", BASE))              # 'data' alone is not supported

    def test_lenient_vs_strict_phrase_matching(self):
        # "microservices" and "design" both appear in the resume, but never as that phrase
        self.assertTrue(ja.term_present("Microservices design", BASE))
        self.assertFalse(ja.term_present("Microservices design", BASE, strict=True))

    def test_match_finds_gaps_and_never_credits_missing_skills(self):
        m = ja.compute_match(ANALYSIS, BASE)
        self.assertEqual(m["skills"]["required_matched"], ["Java", "Spring Boot", "Angular"])
        self.assertEqual(m["skills"]["required_missing"], ["Kafka"])
        self.assertIn("Kafka", m["missing_skills"])
        self.assertIn("Kubernetes", m["missing_skills"])
        self.assertEqual(m["skills"]["pct"], 75)
        self.assertNotIn("Kafka", m["emphasis"])

    def test_experience_match_uses_the_years_the_resume_states(self):
        self.assertEqual(ja.resume_years(BASE), 4.0)
        self.assertEqual(ja.compute_match({**ANALYSIS, "experience_years_min": 3}, BASE)["experience"]["pct"], 100)
        self.assertEqual(ja.compute_match({**ANALYSIS, "experience_years_min": 8}, BASE)["experience"]["pct"], 50)
        self.assertIsNone(ja.compute_match({**ANALYSIS, "experience_years_min": None}, BASE)["experience"]["pct"])

    def test_keyword_coverage_before_and_after_tailoring(self):
        tailored = BASE.replace("Experienced in healthcare", "Experienced in Agile healthcare")
        m = ja.compute_match(ANALYSIS, BASE, tailored)
        self.assertIsNotNone(m["keywords"]["tailored_pct"])
        self.assertGreaterEqual(m["keywords"]["tailored_pct"], m["keywords"]["base_pct"])
        self.assertIn("Kafka", m["keywords"]["missing_in_tailored"])
        self.assertIsNone(ja.compute_match(ANALYSIS, BASE)["keywords"]["tailored_pct"])

    def test_relevant_experience_is_per_employer(self):
        only_python = {**ANALYSIS, "required_skills": ["Python", "FastAPI"], "preferred_skills": [],
                       "technologies": ["Python", "FastAPI"], "keywords": []}
        m = ja.compute_match(only_python, BASE)
        roles = {r["role"]: r["matched_terms"] for r in m["relevant_experience"]}
        self.assertEqual(len(roles), 1)
        self.assertIn("Zyter", next(iter(roles)))

    def test_no_projects_section_means_no_projects_invented(self):
        m = ja.compute_match(ANALYSIS, BASE)
        self.assertEqual(m["relevant_projects"], [])
        self.assertIn("no Projects section", m["projects_note"])
        with_projects = BASE.replace("## Education", "## Projects\n### Job Agent\n- Built a pipeline.\n\n## Education")
        self.assertEqual(ja.compute_match(ANALYSIS, with_projects)["relevant_projects"], ["Job Agent", "Built a pipeline."])

    def test_scores_bounded_and_fit_score_derived(self):
        m = ja.compute_match(ANALYSIS, BASE)
        self.assertTrue(0 <= m["overall"] <= 100)
        self.assertEqual(m["fit_score"], max(1, min(10, round(m["overall"] / 10))))
        empty = {k: ([] if isinstance(v, list) else None if k == "experience_years_min" else "") for k, v in ANALYSIS.items()}
        self.assertIsNone(ja.compute_match(empty, BASE)["overall"])

    def test_technologies_are_the_fallback_when_no_required_skills(self):
        a = {**ANALYSIS, "required_skills": []}
        self.assertEqual(ja.compute_match(a, BASE)["skills"]["pct"], round(100 * 6 / 8))


if __name__ == "__main__":
    unittest.main()
