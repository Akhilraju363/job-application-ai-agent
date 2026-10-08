"""Fixed header + role-based filename: the header (name, headline, contact row) is the same on
every tailored resume whatever the job; the job title, normalized and never a claim the master
resume can't back, only names the exported file."""
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
CANONICAL_LINKEDIN = "linkedin.com/in/akhildalali-320204233"
# Same shape and order as the real RESUME_CONTACT_LINE; the real phone/email stay out of this public repo.
CONTACT = f"+91 90000 00000 | name@example.com | {CANONICAL_LINKEDIN} | Bengaluru, Karnataka, India"
FIXED_HEADER = f"# AKHIL DALALI\n{STATIC_HEADLINE}\n\n{CONTACT}\n\n## Summary"
ROLES = {"Java Developer": "Java_Developer", "Senior Java Developer": "Java_Developer",
         "Python Developer": "Python_Developer", "Python Backend Developer": "Python_Backend_Developer",
         "Full Stack Developer": "Full_Stack_Developer", "Backend Developer": "Backend_Developer",
         "Software Engineer": "Software_Engineer", "Angular Developer": "Angular_Developer",
         "Java + AWS Developer": "Java_+_AWS_Developer"}   # existing filename rule keeps "+" (C++)


def exported(md):
    """The markdown an export renders (fixed header + contact row), with the test contact line."""
    import tailor_job
    with mock.patch.dict(os.environ, {"RESUME_CONTACT_LINE": CONTACT}), \
            mock.patch.object(paths, "BASE_RESUME", paths.ROOT / "resume" / "base_resume.md"):
        return tailor_job.render_markdown(md).replace("\r", "")   # the master resume is CRLF


def header(md):
    return md[:md.index("## Summary") + len("## Summary")]


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

    def test_fixed_header_passes_no_fabrication(self):
        md = rr.fixed_header(reorder_bullets(REAL_BASE), REAL_BASE)
        self.assertEqual(nf.check_no_fabrication(REAL_BASE, md, ["Kafka"]), [])


class FixedHeader(unittest.TestCase):
    def test_exact_header(self):
        md = exported(rr.fixed_header(REAL_BASE, REAL_BASE))
        self.assertTrue(md.startswith(FIXED_HEADER), md[:200])
        lines = md.split("\n")
        self.assertEqual((lines[0], lines[1]), ("# AKHIL DALALI", STATIC_HEADLINE))
        for part in ("+91 90000 00000", "name@example.com", CANONICAL_LINKEDIN, "Bengaluru, Karnataka, India"):
            self.assertEqual(md.count(part), 1, part)
        self.assertEqual(md.count("linkedin.com/"), 1)
        self.assertEqual(md.count(CONTACT), 1)

    def test_any_role_headline_is_replaced(self):
        for rogue in ("Java Developer", "Python Developer", "Senior Backend Developer", "Chief Kubernetes Wrangler"):
            md = REAL_BASE.replace(STATIC_HEADLINE, rogue).replace("# AKHIL DALALI", "# Akhil Dalali, Python Developer")
            self.assertTrue(exported(rr.fixed_header(md, REAL_BASE)).startswith(FIXED_HEADER), rogue)

    def test_idempotent_and_inserted_when_missing(self):
        once = rr.fixed_header(REAL_BASE, REAL_BASE)
        self.assertEqual(rr.fixed_header(once, REAL_BASE), once)
        no_headline = REAL_BASE.replace(STATIC_HEADLINE + "\r\n", "").replace(STATIC_HEADLINE + "\n", "")
        self.assertEqual(rr.fixed_header(no_headline, REAL_BASE).replace("\r", ""), once.replace("\r", ""))

    def test_body_is_untouched(self):
        md = reorder_bullets(REAL_BASE)
        self.assertEqual(rr.fixed_header(md, REAL_BASE).split("## Summary")[1], md.split("## Summary")[1])

    def test_misspelled_linkedin_in_the_contact_line_becomes_the_canonical_one(self):
        typo = CONTACT.replace(CANONICAL_LINKEDIN, "linkedin.com/in/akhil-dalali-320204233")
        with mock.patch.dict(os.environ, {"RESUME_CONTACT_LINE": typo}):
            md = contact.with_contact(rr.fixed_header(REAL_BASE, REAL_BASE))
        self.assertIn(f"\n{CONTACT}", md)
        self.assertNotIn("akhil-dalali-320204233", md)


class Filename(unittest.TestCase):
    def test_role_names_the_file_only(self):
        md = rr.fixed_header(REAL_BASE, REAL_BASE)
        for title, role in ROLES.items():
            for ext in ("pdf", "docx"):
                self.assertEqual(rr.export_filename(md, ext, title), f"Akhil_Dalali_{role}.{ext}", title)

    def test_legacy_role_headline_still_names_the_file(self):
        legacy = REAL_BASE.replace(STATIC_HEADLINE, "Java Developer")
        self.assertEqual(rr.resume_filename(legacy, "pdf"), "Akhil_Dalali_Java_Developer.pdf")
        self.assertEqual(rr.resume_filename(REAL_BASE, "pdf"), "Akhil_Dalali.pdf")   # generic headline adds nothing


class TailoredOutput(unittest.TestCase):
    def test_every_role_gets_the_same_header_and_its_own_filename(self):
        for title in ("Java Developer", "Python Developer", "Full Stack Developer", "Chief Kubernetes Wrangler"):
            # the model tries to retitle the resume; the header stays fixed anyway
            rogue = reorder_bullets(REAL_BASE).replace(STATIC_HEADLINE, title)
            with TempOutput():
                r, rec = tailor(title, md_outputs=[rogue] * 5)
            content = rec["versions"][-1]["markdown"]
            self.assertEqual(r["status"], "completed", (title, r["verification"]))
            self.assertTrue(exported(content).startswith(FIXED_HEADER), (title, content[:120]))
            self.assertEqual(content.split("\n")[:2], ["# AKHIL DALALI", STATIC_HEADLINE])
        self.assertEqual(rr.export_filename(content, "pdf", "Python Developer"), "Akhil_Dalali_Python_Developer.pdf")

    def test_live_analysis_with_generic_keywords_keeps_the_header(self):
        analysis = {**ANALYSIS, "required_skills": ["Java", "Spring Boot", "REST APIs", "Microservices", "SQL"],
                    "technologies": ["Java", "Spring Boot", "SQL"],
                    "keywords": ["Developer", "Technical", "Backend", "4+ years", "Experience"]}
        with TempOutput():
            with mock.patch("llm.call_llm", return_value=json.dumps(analysis)), \
                    mock.patch("tailor_job.call_llm", side_effect=[reorder_bullets(REAL_BASE)] * 5):
                r = ts.tailor_resume(JD_TEXT, "Java Developer", None, None, None, base_md=REAL_BASE)
        self.assertEqual(rr.role_of(r["resume"]["content"]), STATIC_HEADLINE)
        self.assertEqual(r["status"], "completed", r["verification"])

    def test_reorder_only_fallback_gets_the_fixed_header_too(self):
        bad = REAL_BASE.replace("- Tools: Git, GitHub, GitLab, SCSS", "- Tools: Git, GitHub, GitLab, SCSS, Kafka")
        with TempOutput():
            r, rec = tailor("Python Developer", md_outputs=[bad] * 5)
        v = rec["versions"][-1]
        self.assertEqual(v["kind"], "conservative")
        self.assertEqual(v["markdown"].split("\n")[:2], ["# AKHIL DALALI", STATIC_HEADLINE])
        self.assertTrue(v["validation"]["ok"], v["validation"]["problems"])

    def test_technology_employer_mapping_is_unchanged(self):
        with TempOutput():
            _, rec = tailor("Python Developer")
        roles = {r["heading"]: r["text"] for r in nf.parse_resume(rec["versions"][-1]["markdown"])["roles"]}
        infinite = next(t for h, t in roles.items() if "Infinite" in h)
        ltim = next(t for h, t in roles.items() if "LTIMindtree" in h)
        self.assertFalse(nf.has_term(infinite, "python") or nf.has_term(ltim, "python"))
        self.assertTrue(nf.has_term(next(t for h, t in roles.items() if "Zyter" in h), "python"))

    def test_fixture_resume_with_contact_row_keeps_it(self):
        with TempOutput():
            with mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
                    mock.patch("tailor_job.call_llm", side_effect=[reorder_bullets(BASE)] * 5):
                r = ts.tailor_resume(JD_TEXT, "Java Developer", None, None, None, base_md=BASE)
        self.assertTrue(r["resume"]["content"].startswith(
            "# Jane Roe\nSoftware Engineer | Java | Spring Boot | Angular\n\njane@example.com"))
        self.assertEqual(r["status"], "completed", r["verification"])


if __name__ == "__main__":
    unittest.main()
