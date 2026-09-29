"""Master Resume editor: scripts/master_resume.py (parse/serialize/validate/save) and the
GET/PUT /api/master-resume endpoints. A temp copy of the real master is used -- the repo's
resume/base_resume.md is never written by these tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also runnable as `python -m unittest tests/<file>`
import fixtures  # noqa: E402,F401 -- must be first of the project imports: disables .env loading

import copy
import json
import os
import unittest
from unittest import mock

from fixtures import ANALYSIS, JD_TEXT, auth_env, reorder_bullets
from test_dashboard_server import ServerCase
import activity
import master_resume as mr
import no_fabrication as nf
import paths
import resume_role
import tailoring_service as ts

REAL = (paths.ROOT / "resume" / "base_resume.md").read_text(encoding="utf-8")
LF = REAL.replace("\r\n", "\n")


class Format(unittest.TestCase):
    def test_real_master_round_trips_byte_for_byte(self):
        self.assertEqual(mr.serialize(mr.parse(REAL)), LF)

    def test_each_section_is_structured_separately(self):
        r = mr.parse(REAL)
        self.assertEqual((r["name"], r["headline"]), ("AKHIL DALALI", "Software Engineer | Java | Spring Boot | Angular | AWS"))
        self.assertEqual([s["label"] for s in r["skills"]][:3], ["Languages", "Backend", "Frontend"])
        zyter = r["experience"][0]
        self.assertEqual((zyter["employer"], zyter["dates"], zyter["location"]),
                         ("Zyter Technologies India Private Limited", "Jan 2026 – Present", "Bengaluru, India"))
        self.assertEqual(len(zyter["bullets"]), 5)
        self.assertEqual(r["education"], [{"degree": "Bachelor of Commerce in Computer Applications",
                                           "institution": "Sri Venkateswara University", "dates": "2015 – 2018"}])
        self.assertEqual(len(r["certifications"]), 3)

    def test_valid_master_has_no_errors(self):
        self.assertEqual(mr.validate(mr.normalize(mr.parse(REAL))), [])

    def test_unknown_section_is_not_silently_dropped(self):
        with self.assertRaises(mr.MasterFormatError):
            mr.parse(LF.replace("## Education", "## Projects\n- x\n\n## Education"))

    def test_field_errors(self):
        r = mr.normalize(mr.parse(REAL))
        r["experience"][0]["title"] = ""
        r["experience"][1]["dates"] = "Present"
        r["certifications"][0] = " "
        msgs = {(e["section"], e["index"], e["field"]): e["message"] for e in mr.validate(r)}
        self.assertEqual(msgs[("experience", 0, "title")], "Job title cannot be empty.")
        self.assertIn("year", msgs[("experience", 1, "dates")])
        self.assertEqual(msgs[("certifications", 0, None)], "Certification cannot be empty.")

    def test_contact_details_are_rejected(self):
        for value in ("reach me at someone@example.com", "call +91 98765 43210"):
            r = mr.normalize(mr.parse(REAL))
            r["summary"] = value
            self.assertTrue(any("Contact details" in e["message"] for e in mr.validate(r)), value)

    def test_date_ranges_are_not_mistaken_for_phone_numbers(self):
        r = mr.normalize(mr.parse(REAL))
        r["education"][0]["dates"] = "2015 - 2018"
        self.assertEqual(mr.validate(r), [])


class MasterApi(ServerCase):
    def setUp(self):
        self.master = paths.BASE_RESUME            # the temp file ServerCase patched in
        self.master.write_text(REAL, encoding="utf-8")
        self.addCleanup(self.master.write_text, fixtures.BASE, encoding="utf-8")   # other suites expect the fixture

    def get(self):
        s, _, body = self.call("GET", "/api/master-resume")
        self.assertEqual(s, 200, body)
        return body

    def put(self, resume, version=None, **extra):
        return self.call("PUT", "/api/master-resume", {"resume": resume, "expected_version": version or self.get()["version"], **extra})

    def test_get_returns_structured_master_and_metadata(self):
        b = self.get()
        self.assertEqual(b["version"], paths.master_resume_version(REAL))
        self.assertTrue(b["editable"])
        self.assertEqual(b["resume"]["experience"][2]["employer"], "LTIMindtree")
        self.assertIn("updated_at", b)
        self.assertEqual(b["contact"]["configured"], True)

    def test_valid_edit_is_saved_in_canonical_markdown_and_changes_the_version(self):
        before = self.get()
        r = copy.deepcopy(before["resume"])
        r["certifications"].append("Test Certification")
        r["experience"][2]["bullets"][0] = r["experience"][2]["bullets"][0].replace("enterprise-grade", "enterprise")
        s, _, out = self.put(r, before["version"])
        self.assertEqual(s, 200, out)
        self.assertTrue(out["changed"])
        self.assertNotEqual(out["version"], before["version"])
        self.assertEqual(out["previous_version"], before["version"])
        text = self.master.read_text(encoding="utf-8")
        self.assertTrue(text.replace("\r\n", "\n").endswith("- Oracle Database for Developer\n- Test Certification\n"))
        self.assertIn("for enterprise applications.", text)
        self.assertEqual(self.get()["resume"]["certifications"][-1], "Test Certification")   # a reload sees it
        self.assertEqual(out["version"], paths.master_resume_version(text))
        self.assertTrue(any(e["kind"] == "master_resume_updated" for e in activity.read_events()))

    def test_saving_unchanged_master_keeps_the_version(self):
        b = self.get()
        s, _, out = self.put(b["resume"], b["version"])
        self.assertEqual((s, out["changed"], out["version"]), (200, False, b["version"]))
        self.assertEqual(self.master.read_text(encoding="utf-8"), REAL)

    def test_invalid_structure_is_rejected_with_field_errors(self):
        b = self.get()
        r = copy.deepcopy(b["resume"])
        r["experience"][0]["title"] = ""
        s, _, out = self.put(r, b["version"])
        self.assertEqual((s, out["error"]["code"]), (422, "validation_failed"))
        self.assertIn({"section": "experience", "index": 0, "field": "title", "message": "Job title cannot be empty."},
                      out["error"]["details"])
        self.assertEqual(self.master.read_text(encoding="utf-8"), REAL)

    def test_malformed_body_is_rejected(self):
        b = self.get()
        for bad in ({**b["resume"], "skills": "Java"}, {**b["resume"], "experience": [{"employer": 5}]}, "text", None):
            s, _, out = self.put(bad, b["version"])
            self.assertEqual(s, 400, (bad, out))
        s, _, out = self.call("PUT", "/api/master-resume", {"resume": b["resume"]})
        self.assertEqual(s, 400)
        self.assertEqual(self.master.read_text(encoding="utf-8"), REAL)

    def test_contact_information_cannot_be_persisted(self):
        b = self.get()
        r = copy.deepcopy(b["resume"])
        r["summary"] += " Email: akhil@example.com"
        s, _, out = self.put(r, b["version"])
        self.assertEqual(s, 422)
        # the public profile line is kept as stored -- the client can't smuggle contact details in through it
        r = copy.deepcopy(b["resume"])
        r["profile_lines"] = ["someone@example.com | +91 98765 43210"]
        s, _, out = self.put(r, b["version"])
        self.assertEqual((s, out["changed"]), (200, False))
        text = self.master.read_text(encoding="utf-8")
        self.assertNotIn("@", text)
        self.assertNotIn(os.environ["RESUME_CONTACT_LINE"], text)

    def test_no_client_supplied_path_or_filename(self):
        b = self.get()
        for extra in ({"path": "../../evil.md"}, {"filename": "x.md"}, {"file": "/etc/passwd"}):
            s, _, out = self.put(b["resume"], b["version"], **extra)
            self.assertEqual((s, out["error"]["code"]), (400, "invalid_input"), extra)
        self.assertFalse((paths.ROOT / "evil.md").exists())
        self.assertEqual(self.master.read_text(encoding="utf-8"), REAL)

    def test_stale_version_is_a_conflict(self):
        b = self.get()
        r = copy.deepcopy(b["resume"])
        r["certifications"].append("Another")
        s, _, out = self.put(r, "0" * 12)
        self.assertEqual((s, out["error"]["code"]), (409, "conflict"))
        self.assertEqual(self.master.read_text(encoding="utf-8"), REAL)

    def test_hosted_dashboard_is_read_only(self):
        b = self.get()
        r = copy.deepcopy(b["resume"])
        r["certifications"].append("Another")
        with mock.patch.dict(os.environ, {"MODAL_TASK_ID": "ta-123"}):
            self.assertFalse(self.get()["editable"])
            s, _, out = self.put(r, b["version"])
        self.assertEqual((s, out["error"]["code"]), (409, "read_only"))
        self.assertEqual(self.master.read_text(encoding="utf-8"), REAL)

    def test_authentication_required(self):
        with mock.patch.dict(os.environ, auth_env()):
            for method, body in (("GET", None), ("PUT", {"resume": {}, "expected_version": "0" * 12})):
                s, _, out = self.call(method, "/api/master-resume", body)
                self.assertEqual(s, 401, method)
            cookie = self.sign_in()
            s, _, out = self.call("GET", "/api/master-resume", headers={"Cookie": cookie})
            self.assertEqual(s, 200)

    def test_cross_site_writes_are_refused(self):
        b = self.get()
        r = copy.deepcopy(b["resume"])
        r["certifications"].append("Another")
        body = {"resume": r, "expected_version": b["version"]}
        for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            s, _, out = self.call("PUT", "/api/master-resume", body, headers=headers)
            self.assertEqual(s, 403, headers)
        s, _, _ = self.call("PUT", "/api/master-resume", body, headers={"Content-Type": "text/plain"})
        self.assertEqual(s, 415)
        self.assertEqual(self.master.read_text(encoding="utf-8"), REAL)

    def test_pipeline_reads_the_saved_master(self):
        b = self.get()
        r = copy.deepcopy(b["resume"])
        r["summary"] = r["summary"].replace("Results-driven", "Detail-oriented")
        s, _, out = self.put(r, b["version"])
        self.assertEqual(s, 200, out)
        saved = paths.read_base_resume()
        self.assertIn("Detail-oriented Software Engineer", saved)
        self.assertEqual(nf.check_no_fabrication(saved, saved), [])
        self.assertEqual(saved.replace("\r\n", "\n").split("\n")[:2], ["# AKHIL DALALI", "Software Engineer | Java | Spring Boot | Angular | AWS"])
        self.assertEqual(resume_role.export_filename(saved, "pdf", "Java Developer", "Akhil Dalali"), "Akhil_Dalali_Java_Developer.pdf")
        with mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
                mock.patch("tailor_job.call_llm", side_effect=[reorder_bullets(saved)] * 5):
            res = ts.tailor_resume(JD_TEXT, "Java Developer", "Acme", "https://acme.example/jobs/m1", "LinkedIn")
        self.assertEqual(res["status"], "completed", res["verification"])
        self.assertEqual(res["resume"]["master_resume_version"], out["version"])
        self.assertIn("Detail-oriented", res["resume"]["content"])


if __name__ == "__main__":
    unittest.main()
