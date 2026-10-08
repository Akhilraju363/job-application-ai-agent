"""Verified local PDF artifacts: generated_resumes/<id>/v<n>.pdf published only from verified versions."""
import fixtures  # noqa: F401 -- must be first: disables .env loading, puts scripts/ on sys.path
from fixtures import TempOutput

import json
import unittest
from unittest import mock

import resume_artifacts
import resume_store
import tailor_job

LINK = "https://www.naukri.com/job-listings-java-full-stack-developer-infosys-hyderabad-2-to-4-years-120226011477"
MD = "# Candidate\n\n## Summary\nJava full stack engineer.\n\n## Skills\n- Java, Spring Boot, Angular\n\n## Experience\n- Built APIs\n"


def make_pdf(lines):
    """A small but real PDF (Helvetica text, xref, trailer) standing in for the Google Docs export."""
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    content = "BT /F1 11 Tf 50 800 Td 14 TL " + " ".join(f"({esc(line)}) '" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = b"%PDF-1.4\n%" + b"x" * 900 + b"\n"
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def fake_export(calls):
    def export(md_path, out_path):
        calls.append(md_path.name)
        lines = [l.lstrip("#- ").strip() for l in md_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        out_path.write_bytes(make_pdf(lines))
    return export


class ArtifactTestCase(unittest.TestCase):
    def setUp(self):
        self.out = TempOutput()
        self.out.__enter__()
        self.addCleanup(self.out.__exit__, None, None, None)

    def record(self, versions=((MD, True),), link=LINK):
        rid = resume_store.create(source="scraped", job={"title": "Java Dev", "company": "Infosys", "link": link,
                                                         "job_key": link, "source": "Naukri"},
                                  analysis={}, match={}, provider="test", master_version="abc")
        for markdown, ok in versions:
            resume_store.add_version(rid, markdown, "tailored", {"ok": ok, "problems": [] if ok else ["invented"]})
        return rid


class TestPublish(ArtifactTestCase):
    def test_publishes_verified_version_with_metadata(self):
        rid, calls = self.record(), []
        info = resume_artifacts.publish_verified_pdf(rid, export=fake_export(calls))
        pdf = resume_store.version_path(rid, 1, "pdf")
        self.assertTrue(pdf.exists())
        self.assertEqual(calls, ["v1.md"])
        self.assertEqual(resume_artifacts.check_pdf_structure(pdf), [])
        meta = resume_store.get(rid, with_markdown=False)
        version = meta["versions"][0]
        self.assertEqual(version["exports"]["pdf"], "v1.pdf")
        self.assertEqual(version["artifacts"]["pdf"], info)
        self.assertEqual((info["version"], info["verified"], info["job_key"], info["file"]), (1, True, LINK, "v1.pdf"))
        self.assertEqual(info["md_sha1"], resume_artifacts.md_sha1(MD))

    def test_idempotent(self):
        rid, calls = self.record(), []
        first = resume_artifacts.publish_verified_pdf(rid, export=fake_export(calls))
        second = resume_artifacts.publish_verified_pdf(rid, export=fake_export(calls))
        self.assertEqual((first, calls), (second, ["v1.md"]))

    def test_unverified_version_is_never_published(self):
        rid, calls = self.record(versions=((MD, False),)), []
        with self.assertRaises(resume_artifacts.ArtifactError):
            resume_artifacts.publish_verified_pdf(rid, export=fake_export(calls))
        with self.assertRaises(resume_artifacts.ArtifactError):
            resume_artifacts.publish_verified_pdf(rid, 1, export=fake_export(calls))
        self.assertFalse(resume_store.version_path(rid, 1, "pdf").exists())
        self.assertEqual(calls, [])

    def test_latest_verified_version_maps_to_its_own_markdown(self):
        rid, calls = self.record(versions=((MD, True), (MD + "- v2 bullet\n", True), (MD + "- bad\n", False))), []
        info = resume_artifacts.publish_verified_pdf(rid, export=fake_export(calls))
        self.assertEqual((info["version"], calls), (2, ["v2.md"]))
        self.assertEqual(info["md_sha1"], resume_artifacts.md_sha1(MD + "- v2 bullet\n"))
        self.assertFalse(resume_store.version_path(rid, 1, "pdf").exists())

    def test_invalid_export_is_not_published(self):
        rid = self.record()
        for bad in (b"", b"<html>Drive page</html>" * 100, make_pdf(["x"])[:-200]):
            with self.assertRaises(resume_artifacts.ArtifactError):
                resume_artifacts.publish_verified_pdf(rid, export=lambda md, out: out.write_bytes(bad))
            self.assertFalse(resume_store.version_path(rid, 1, "pdf").exists())
            self.assertEqual(list(resume_store.version_path(rid, 1, "pdf").parent.glob("*.tmp")), [])
        self.assertNotIn("pdf", resume_store.get(rid, with_markdown=False)["versions"][0]["exports"])

    def test_find_record_for_link(self):
        rid = self.record()
        self.record(link="https://www.naukri.com/job-listings-other-999999999999")
        self.assertEqual(resume_artifacts.find_record_for_link(LINK + "?src=x")["id"], rid)
        self.assertIsNone(resume_artifacts.find_record_for_link("https://www.naukri.com/job-listings-none-1"))

    def test_cli(self):
        rid = self.record()
        with mock.patch.object(tailor_job, "export_doc_file", side_effect=fake_export([])):
            self.assertEqual(resume_artifacts.main(["--link", LINK]), 0)
        self.assertTrue(resume_store.version_path(rid, 1, "pdf").exists())
        self.assertEqual(resume_artifacts.main(["--link", "https://www.naukri.com/job-listings-none-1"]), 1)


class TestVerifiedResumeRecord(unittest.TestCase):
    def test_want_record_returns_id_and_version(self):
        rec = {"id": "abcdef012345", "versions": [{"n": 2, "markdown": "# resume", "validation": {"ok": True, "problems": []}}]}
        job = {"title": "Java Developer", "company": "Infosys", "link": LINK, "source": "Naukri",
               "description": "Spring Boot and Angular, REST APIs, SQL. " * 3, "score": 9}
        with mock.patch("tailoring_service.tailor", return_value=rec), \
                mock.patch.object(tailor_job, "validate", return_value=(True, None)):
            self.assertEqual(tailor_job.verified_resume(job, "# master"), ("# resume", None))
            self.assertEqual(tailor_job.verified_resume(job, "# master", want_record=True),
                             ("# resume", None, "abcdef012345", 2))
        rec["versions"][0]["validation"] = {"ok": False, "problems": ["x"]}
        with mock.patch("tailoring_service.tailor", return_value=rec):
            self.assertEqual(tailor_job.verified_resume(job, "# master", want_record=True)[2:], (None, None))


class TestStructure(unittest.TestCase):
    def test_check_pdf_structure(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.pdf"
            good.write_bytes(make_pdf(["Summary", "Skills"]))
            self.assertEqual(resume_artifacts.check_pdf_structure(good), [])
            for name, data in (("empty", b""), ("html", b"<html>" * 400), ("cut", make_pdf(["a"])[:-300])):
                path = Path(tmp) / name
                path.write_bytes(data)
                self.assertTrue(resume_artifacts.check_pdf_structure(path), name)
            self.assertTrue(resume_artifacts.check_pdf_structure(Path(tmp) / "missing.pdf"))


if __name__ == "__main__":
    unittest.main()
