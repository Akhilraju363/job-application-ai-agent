"""Master Resume = single source of truth.

base_resume.md -> tailored copy -> validation -> fixed master layout -> private contact -> PDF/DOCX.
Never JD -> invented career fact, never previous resume -> new resume, never job role -> header.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also runnable as `python -m unittest tests/<file>`
import fixtures  # noqa: E402,F401 -- must be first of the project imports: disables .env loading

import hashlib
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from fixtures import ANALYSIS, JD_TEXT, FakeDrive, TempOutput, reorder_bullets
import drive_resumes
import no_fabrication as nf
import paths
import resume_role as rr
import resume_store
import tailoring_service as ts

MASTER_PATH = paths.ROOT / "resume" / "base_resume.md"
MASTER = MASTER_PATH.read_text(encoding="utf-8").replace("\r", "")
NAME, HEADLINE = "# AKHIL DALALI", "Software Engineer | Java | Spring Boot | Angular | AWS"
LINKEDIN = "linkedin.com/in/akhil-dalali-320204233"
# Same shape as the real RESUME_CONTACT_LINE; the real phone/email never go in this public repo.
CONTACT = f"name@example.com | +91 90000 00000 | {LINKEDIN} | Bengaluru, Karnataka, India"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"\+?\d[\d\s-]{8,}\d")


def real_master():
    return mock.patch.object(paths, "BASE_RESUME", MASTER_PATH)


def contact_env():
    return mock.patch.dict(os.environ, {"RESUME_CONTACT_LINE": CONTACT})


def tailor(title, outputs=None, **kw):
    """tailor_resume() against the real master, LLMs mocked. Returns (result, stored record)."""
    with mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
            mock.patch("tailor_job.call_llm", side_effect=outputs or [reorder_bullets(MASTER)] * 5):
        r = ts.tailor_resume(JD_TEXT, title, "Acme", f"https://acme.example/jobs/{abs(hash(title))}",
                             "LinkedIn", base_md=MASTER, **kw)
    return r, resume_store.get(r["resume"]["id"])


def check(tailored, jd_terms=()):
    return nf.check_no_fabrication(MASTER, tailored, jd_terms)


class MasterSource(unittest.TestCase):
    def test_every_tailoring_call_starts_from_the_master(self):
        import tailor_job
        seen = []
        real = tailor_job.tailor_text

        def spy(job, resume_text, feedback=""):
            seen.append(resume_text)
            return real(job, resume_text, feedback)

        with TempOutput(), mock.patch.object(tailor_job, "tailor_text", side_effect=spy):
            r, _ = tailor("Java Developer")
            # a regenerate of an existing resume still starts from the master, not the old version
            tailor("Java Developer", resume_id=r["resume"]["id"], reuse=False)
        self.assertTrue(seen)
        self.assertTrue(all(text == MASTER for text in seen))

    def test_master_file_is_never_modified_by_tailoring_or_export_rendering(self):
        import tailor_job
        before = hashlib.sha1(MASTER_PATH.read_bytes()).hexdigest()
        with TempOutput(), real_master(), contact_env():
            _, rec = tailor("Python Developer")
            tailor_job.render_markdown(rec["versions"][-1]["markdown"])
        self.assertEqual(hashlib.sha1(MASTER_PATH.read_bytes()).hexdigest(), before)

    def test_generated_resume_has_no_facts_absent_from_master(self):
        with TempOutput():
            r, rec = tailor("Full Stack Developer")
        self.assertEqual(r["status"], "completed", r["verification"])
        self.assertEqual(check(rec["versions"][-1]["markdown"], ANALYSIS["required_skills"]), [])


class FixedHeader(unittest.TestCase):
    def test_header_is_the_master_header_for_every_role(self):
        for title in ("Java Developer", "Python Developer", "Full Stack Developer", "Chief Blockchain Ninja"):
            # even a model that swaps the headline for the job title gets the master header back
            hijacked = reorder_bullets(MASTER).replace(HEADLINE, title)
            with TempOutput():
                _, rec = tailor(title, outputs=[hijacked] * 5)
            md = rec["versions"][-1]["markdown"]
            self.assertEqual(md.split("\n")[:2], [NAME, HEADLINE], title)

    def test_role_never_replaces_software_engineer(self):
        v = check(MASTER.replace(HEADLINE, "Java Developer | Java | Spring Boot | Angular | AWS"))
        self.assertTrue(any("header line changed" in x for x in v), v)


class Filename(unittest.TestCase):
    def test_role_names_the_file_only(self):
        for title, want in (("Java Developer", "Akhil_Dalali_Java_Developer.pdf"),
                            ("Python Developer", "Akhil_Dalali_Python_Developer.pdf"),
                            ("Senior Full Stack Developer", "Akhil_Dalali_Full_Stack_Developer.pdf"),
                            ("Backend Developer", "Akhil_Dalali_Backend_Developer.pdf")):
            self.assertEqual(rr.export_filename(MASTER, "pdf", title, "Akhil Dalali"), want)
            self.assertEqual(rr.fixed_header(MASTER, MASTER), MASTER)  # the header is untouched


class NoFabrication(unittest.TestCase):
    def assertFlags(self, tailored, needle, jd_terms=()):
        v = check(tailored, jd_terms)
        self.assertTrue(any(needle in x for x in v), f"expected {needle!r} in {v}")

    def test_master_against_itself_is_clean(self):
        self.assertEqual(check(MASTER), [])

    def test_harmless_formatting_is_clean(self):
        self.assertEqual(check(MASTER.replace("–", "-").replace("—", "-").replace("\n- ", "\n-  ")), [])

    def test_jd_only_technology(self):
        for tech in ("Kafka", "React", "Terraform", "FastAPI", "Azure"):
            self.assertFlags(MASTER.replace("Microservices, RESTful APIs, Nx Monorepo",
                                            f"Microservices, RESTful APIs, Nx Monorepo, {tech}"),
                             "not in master resume", jd_terms=[tech])

    def test_new_or_changed_employer(self):
        self.assertFlags(MASTER.replace("### LTIMindtree", "### Infosys"), "not in master resume")

    def test_changed_job_title(self):
        self.assertFlags(MASTER.replace("Software Engineer — Backend Development",
                                        "Senior Software Engineer — Backend Development"), "job title changed")

    def test_changed_dates(self):
        self.assertFlags(MASTER.replace("Jan 2022 – Jun 2024", "Jan 2021 – Jun 2024"), "dates changed")

    def test_changed_location(self):
        self.assertFlags(MASTER.replace("Jun 2024 | Chennai, India", "Jun 2024 | Pune, India"), "location changed")

    def test_changed_education(self):
        self.assertFlags(MASTER.replace("Sri Venkateswara University", "Anna University"), "education")

    def test_changed_or_new_certification(self):
        self.assertFlags(MASTER.replace("- AWS Certifications", "- AWS Solutions Architect Professional"),
                         "certifications")
        self.assertFlags(MASTER.rstrip("\n") + "\n- Certified Kubernetes Administrator\n", "certifications")

    def test_new_bullet(self):
        self.assertFlags(MASTER.replace("### Overture Rede\nCredit Control Assistant\nMar 2020 – May 2020 | Bengaluru, India\n",
                                        "### Overture Rede\nCredit Control Assistant\nMar 2020 – May 2020 | Bengaluru, India\n"
                                        "- Reconciled ledgers and prepared monthly finance reports.\n"),
                         "extra bullets")

    def test_new_number_or_metric(self):
        self.assertFlags(MASTER.replace("to improve query performance", "to improve query performance by 40%"),
                         "numbers not in master resume")

    def test_technology_moved_between_employers(self):
        # Spring Boot is LTIMindtree's; it must not appear under Zyter
        self.assertFlags(MASTER.replace("Built Python-based backend services and REST APIs",
                                        "Built Python-based and Spring Boot backend services and REST APIs"),
                         "attributed to")

    def test_added_removed_or_reordered_sections(self):
        self.assertFlags(MASTER.replace("## Education", "## Projects\n- Built a job agent.\n\n## Education"),
                         "sections must match")
        self.assertFlags(re.sub(r"## Certifications.*", "", MASTER, flags=re.S), "sections must match")

    def test_leadership_claim(self):
        self.assertFlags(MASTER.replace("Collaborated with cross-functional Agile teams to deliver feature",
                                        "Led cross-functional Agile teams to deliver feature"), "leadership")


class SafeFallback(unittest.TestCase):
    FABRICATED = MASTER.replace("Microservices, RESTful APIs, Nx Monorepo", "Microservices, RESTful APIs, Nx Monorepo, Kafka")

    def test_failed_fact_check_falls_back_to_master_content(self):
        with TempOutput():
            r, rec = tailor("Java Developer", outputs=[self.FABRICATED] * 5)
        v = rec["versions"][-1]
        self.assertEqual(v["kind"], "conservative")
        self.assertTrue(v["validation"]["ok"], v["validation"]["problems"])
        self.assertNotIn("Kafka", v["markdown"])
        self.assertEqual(check(v["markdown"]), [])

    def test_scheduled_pipeline_uses_the_same_verified_path(self):
        import tailor_job
        job = {"title": "Java Developer", "company": "Acme", "link": "https://acme.example/jobs/pipe",
               "description": JD_TEXT, "score": 9}
        with TempOutput(), mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
                mock.patch("tailor_job.call_llm", side_effect=[self.FABRICATED] * 5):
            text, reason = tailor_job.verified_resume(job, MASTER)
        self.assertIsNone(reason)
        self.assertNotIn("Kafka", text)
        self.assertEqual(check(text), [])

    def test_pipeline_never_exports_an_unverified_resume(self):
        import tailor_job
        job = {"title": "Java Developer", "company": "Acme", "link": "https://acme.example/jobs/x",
               "description": JD_TEXT, "score": 9}
        bad = {"versions": [{"markdown": self.FABRICATED, "validation": {"ok": False, "problems": ["Kafka"]}}]}
        with mock.patch.object(ts, "tailor", return_value=bad):
            text, reason = tailor_job.verified_resume(job, MASTER)
        self.assertIsNone(text)
        self.assertIn("no-fabrication", reason)


class Contact(unittest.TestCase):
    def test_master_has_no_private_contact(self):
        self.assertIsNone(EMAIL_RE.search(MASTER))
        self.assertIsNone(PHONE_RE.search(MASTER))

    def test_contact_is_injected_only_at_export_exactly_once(self):
        import tailor_job
        with TempOutput(), real_master(), contact_env():
            _, rec = tailor("Java Developer")
            stored = rec["versions"][-1]["markdown"]
            out = tailor_job.render_markdown(stored)
        self.assertNotIn("name@example.com", stored)   # stored resume: no contact
        self.assertEqual(out.count("name@example.com"), 1)
        self.assertEqual(out.count("+91 90000 00000"), 1)
        self.assertEqual(out.count(LINKEDIN), 1)
        self.assertEqual(len(re.findall(r"linkedin\.com/", out)), 1)
        self.assertEqual(out.split("\n")[:2], [NAME, HEADLINE])


class Export(unittest.TestCase):
    def test_pdf_docx_and_markdown_share_one_rendered_content(self):
        import tailor_job
        rendered = {}

        def fake_export(md_path, doc_id, out_path, mime):
            rendered[mime] = Path(md_path).read_text(encoding="utf-8")

        with TempOutput(), real_master(), contact_env(), \
                mock.patch.object(tailor_job, "gws", return_value={"documentId": "DOC"}), \
                mock.patch.object(tailor_job, "_export_doc", side_effect=fake_export):
            _, rec = tailor("Java Developer")
            src = resume_store.version_path(rec["id"], 1, "md")
            for mime in ("application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
                tailor_job.export_doc_file(src, src.with_suffix(".x"), mime)
            md_download = tailor_job.render_markdown(src.read_text(encoding="utf-8"))
        pdf, docx = rendered.values()
        self.assertEqual(pdf, docx)
        self.assertEqual(pdf, md_download)

    def test_drive_uploads_the_exported_file_itself(self):
        drive = FakeDrive()
        with TempOutput(), mock.patch.object(drive_resumes, "_gws", side_effect=drive), \
                mock.patch.dict(os.environ, {"google_drive_folder_id": "ROOT"}):
            _, rec = tailor("Java Developer")
            pdf = resume_store.version_path(rec["id"], 1, "pdf")
            pdf.write_bytes(b"%PDF-1.4 exported")
            drive_resumes.upload_resume(rec["id"], 1, "pdf")
        create = next(c for c in drive.calls if "--upload" in c)
        self.assertEqual(create[create.index("--upload") + 1], pdf.name)


class MasterVersion(unittest.TestCase):
    def test_changing_the_master_changes_its_version(self):
        self.assertNotEqual(paths.master_resume_version(MASTER),
                            paths.master_resume_version(MASTER.replace("4+ years", "4+ years ")))

    def test_resume_from_an_older_master_is_detected(self):
        with TempOutput(), tempfile.TemporaryDirectory() as tmp:
            master = Path(tmp) / "base_resume.md"
            master.write_text(MASTER, encoding="utf-8")
            with mock.patch.object(paths, "BASE_RESUME", master):
                r, rec = tailor("Java Developer")
                self.assertTrue(r["resume"]["master_resume_current"])
                master.write_text(MASTER.replace("Oracle Database for Developer", "Oracle Database for Developers"),
                                  encoding="utf-8")
                self.assertFalse(ts.is_current_master(rec))
                self.assertFalse(ts.to_result(rec)["resume"]["master_resume_current"])


if __name__ == "__main__":
    unittest.main()
