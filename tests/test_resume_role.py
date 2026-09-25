"""Target-role headline: normalized from the job title, never a claim the master resume can't
back, applied to the saved resume and every export, and used in the download filename."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also runnable as `python -m unittest tests/<file>`
import fixtures  # noqa: E402,F401 -- must be first of the project imports: disables .env loading

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from fixtures import ANALYSIS, BASE, JD_TEXT, TempOutput, reorder_bullets
import contact
import no_fabrication as nf
import paths
import resume_role as rr
import resume_store
import tailoring_service as ts

REAL_BASE = (paths.ROOT / "resume" / "base_resume.md").read_text(encoding="utf-8")
STATIC_HEADLINE = "Software Engineer | Java | Spring Boot | Angular | AWS"


def tailor(title, md_outputs=None):
    """tailor_resume() against the real master resume, LLMs mocked; returns the stored record."""
    with mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
            mock.patch("tailor_job.call_llm", side_effect=md_outputs or [reorder_bullets(REAL_BASE)] * 5):
        r = ts.tailor_resume(JD_TEXT, title, "Acme", "https://acme.example/jobs/1", "LinkedIn", base_md=REAL_BASE)
    return r, resume_store.get(r["resume"]["id"])


class TargetRole(unittest.TestCase):
    CASES = {
        "Java Developer": "Java Developer",
        "Senior Java Developer": "Java Developer",
        "Java Backend Developer": "Java Backend Developer",
        "Python Developer": "Python Developer",
        "Backend Developer": "Backend Developer",
        "Full Stack Developer": "Full Stack Developer",
        "Angular Developer": "Angular Developer",
    }

    def test_required_titles(self):
        for title, want in self.CASES.items():
            self.assertEqual(rr.target_role(title, REAL_BASE), want, title)

    def test_normalization(self):
        for title, want in {
            "JAVA DEVELOPER": "Java Developer",
            "Sr. Java Developer II": "Java Developer",
            "Lead Software Engineer": "Software Engineer",
            "Full-Stack Java Developer": "Full Stack Java Developer",
            "Back-End Engineer (Remote)": "Backend Engineer",
            "We're Hiring: Java Developer | 4+ Years": "Java Developer",
            "Java Dev": "Java Developer",
            "Java/Angular Developer": "Java/Angular Developer",
        }.items():
            self.assertEqual(rr.target_role(title, REAL_BASE), want, title)

    def test_unsupported_technology_is_dropped_not_claimed(self):
        self.assertEqual(rr.target_role("Java/J2EE Developer", REAL_BASE), "Java Developer")
        self.assertEqual(rr.target_role("Java & React Developer", REAL_BASE), "Java Developer")
        self.assertEqual(rr.target_role("Golang Developer", REAL_BASE), "Software Developer")
        self.assertEqual(rr.target_role("Kafka Developer", REAL_BASE, ["Kafka"]), "Software Developer")

    def test_generic_jd_keywords_never_strip_the_role(self):
        # Regression (live run): the JD analysis listed "Developer" as a keyword; the master resume
        # never says "developer", so the role collapsed to the Software Engineer fallback.
        keywords = ["Java", "Spring Boot", "REST APIs", "Microservices", "SQL", "Developer", "Technical",
                    "Backend", "4+ years", "Experience"]
        self.assertEqual(rr.target_role("Java Developer", REAL_BASE, keywords), "Java Developer")
        self.assertEqual(rr.target_role("Java Developer", REAL_BASE, ["Java Developer"]), "Java Developer")

    def test_unusable_titles_fall_back_to_the_real_title(self):
        for title in ("", "Java Architect", "Engineering Manager", "Principal"):
            self.assertEqual(rr.target_role(title, REAL_BASE), rr.FALLBACK_ROLE, repr(title))

    def test_role_passes_no_fabrication(self):
        for title in list(self.CASES) + ["Senior Java Developer", "Lead Java Architect", "Kafka Developer"]:
            md = rr.apply_role(REAL_BASE, rr.target_role(title, REAL_BASE, ["Kafka"]))
            self.assertEqual(nf.check_no_fabrication(REAL_BASE, md, ["Kafka"]), [], title)


class ApplyRole(unittest.TestCase):
    def test_replaces_the_static_headline_keeping_structure(self):
        md = rr.apply_role(REAL_BASE, "Java Developer")
        self.assertTrue(md.startswith("# Akhil Dalali\nJava Developer\n\nlinkedin.com/in/"), md[:80])
        self.assertNotIn(STATIC_HEADLINE, md)
        self.assertEqual(rr.apply_role(md, "Java Developer"), md)   # idempotent

    def test_inserted_when_the_headline_is_missing(self):
        md = "# Jane Roe\n\njane@example.com | Bengaluru, India\n\n## Summary\nText.\n"
        self.assertEqual(rr.apply_role(md, "Java Developer"),
                         "# Jane Roe\nJava Developer\n\njane@example.com | Bengaluru, India\n\n## Summary\nText.\n")

    def test_experience_is_untouched(self):
        md = rr.apply_role(REAL_BASE, "Python Developer")
        self.assertEqual(md.split("## Summary")[1], REAL_BASE.split("## Summary")[1])

    def test_with_contact_line_matches_the_required_layout(self):
        with mock.patch.dict(os.environ, {"RESUME_CONTACT_LINE": "name@example.com | Bengaluru, India"}):
            md = contact.with_contact(rr.apply_role(REAL_BASE, "Java Developer"))
        self.assertTrue(md.startswith("# Akhil Dalali\nJava Developer\n\n"
                                      "name@example.com | Bengaluru, India | linkedin.com/in/"), md[:120])

    def test_filename(self):
        md = rr.apply_role(REAL_BASE, "Java Developer")
        self.assertEqual(rr.resume_filename(md, "pdf"), "Akhil_Dalali_Java_Developer.pdf")
        self.assertEqual(rr.resume_filename(rr.apply_role(REAL_BASE, "Full Stack Developer"), "docx"),
                         "Akhil_Dalali_Full_Stack_Developer.docx")


class TailoredOutput(unittest.TestCase):
    def test_saved_resume_uses_the_target_role_not_the_static_headline(self):
        """Regression: the master's generic headline must not survive as the Java Developer headline."""
        with TempOutput():
            r, rec = tailor("Senior Java Developer")
        content = rec["versions"][-1]["markdown"]
        self.assertEqual(r["status"], "completed", r["verification"])
        self.assertEqual(rr.role_of(content), "Java Developer")
        self.assertNotIn(STATIC_HEADLINE, content)

    def test_live_analysis_with_generic_keywords_keeps_the_role(self):
        analysis = {**ANALYSIS, "required_skills": ["Java", "Spring Boot", "REST APIs", "Microservices", "SQL"],
                    "technologies": ["Java", "Spring Boot", "SQL"],
                    "keywords": ["Developer", "Technical", "Backend", "4+ years", "Experience"]}
        with TempOutput():
            with mock.patch("llm.call_llm", return_value=json.dumps(analysis)), \
                    mock.patch("tailor_job.call_llm", side_effect=[reorder_bullets(REAL_BASE)] * 5):
                r = ts.tailor_resume(JD_TEXT, "Java Developer", None, None, None, base_md=REAL_BASE)
        self.assertEqual(rr.role_of(r["resume"]["content"]), "Java Developer")
        self.assertEqual(r["status"], "completed", r["verification"])

    def test_reorder_only_fallback_gets_the_role_too(self):
        bad = REAL_BASE.replace("- Tools: Git, GitHub, GitLab, SCSS", "- Tools: Git, GitHub, GitLab, SCSS, Kafka")
        with TempOutput():
            r, rec = tailor("Python Developer", md_outputs=[bad] * 5)
        v = rec["versions"][-1]
        self.assertEqual(v["kind"], "conservative")
        self.assertEqual(rr.role_of(v["markdown"]), "Python Developer")
        self.assertTrue(v["validation"]["ok"], v["validation"]["problems"])

    def test_technology_employer_mapping_is_unchanged(self):
        with TempOutput():
            _, rec = tailor("Python Developer")
        roles = {r["heading"]: r["text"] for r in nf.parse_resume(rec["versions"][-1]["markdown"])["roles"]}
        infinite = next(t for h, t in roles.items() if "Infinite" in h)
        ltim = next(t for h, t in roles.items() if "LTIMindtree" in h)
        self.assertFalse(nf.has_term(infinite, "python") or nf.has_term(ltim, "python"))
        self.assertTrue(nf.has_term(next(t for h, t in roles.items() if "Zyter" in h), "fastapi"))

    def test_fixture_resume_with_contact_row_keeps_it(self):
        with TempOutput():
            with mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
                    mock.patch("tailor_job.call_llm", side_effect=[reorder_bullets(BASE)] * 5):
                r = ts.tailor_resume(JD_TEXT, "Java Developer", None, None, None, base_md=BASE)
        self.assertTrue(r["resume"]["content"].startswith("# Jane Roe\nJava Developer\n\njane@example.com"))
        self.assertEqual(r["status"], "completed", r["verification"])


if __name__ == "__main__":
    unittest.main()
