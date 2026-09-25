import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also runnable as `python -m unittest tests/<file>`
import fixtures  # noqa: E402,F401 -- must be first of the project imports: disables .env loading

import os
import unittest
from unittest import mock

import contact
import no_fabrication as nf
import tailoring_service

LINE = "jane@example.com | Bengaluru, India"
MD = "# Jane Roe\nJava Developer\n\nlinkedin.com/in/jane-roe\n\n## Summary\nText.\n"


class WithContact(unittest.TestCase):
    def test_unset_leaves_resume_unchanged(self):
        with mock.patch.dict(os.environ, {"RESUME_CONTACT_LINE": ""}):
            self.assertEqual(contact.with_contact(MD), MD)
            self.assertFalse(contact.has_email(MD))

    def test_joined_onto_the_linkedin_line(self):
        with mock.patch.dict(os.environ, {"RESUME_CONTACT_LINE": LINE}):
            out = contact.with_contact(MD)
        self.assertIn(f"{LINE} | linkedin.com/in/jane-roe\n", out)
        self.assertEqual(out.count("jane@example.com"), 1)

    def test_own_line_under_headline_when_no_linkedin(self):
        md = "# Jane Roe\nJava Developer\n\n## Summary\nText.\n"
        with mock.patch.dict(os.environ, {"RESUME_CONTACT_LINE": LINE}):
            out = contact.with_contact(md)
        self.assertEqual(out, f"# Jane Roe\nJava Developer\n\n{LINE}\n\n## Summary\nText.\n")

    def test_idempotent(self):
        with mock.patch.dict(os.environ, {"RESUME_CONTACT_LINE": LINE}):
            once = contact.with_contact(MD)
            self.assertEqual(contact.with_contact(once), once)

    def test_never_inside_a_section(self):
        md = "# Jane Roe\n\n## Summary\nSee linkedin.com/in/x for more.\n"
        with mock.patch.dict(os.environ, {"RESUME_CONTACT_LINE": LINE}):
            out = contact.with_contact(md)
        self.assertLess(out.index(LINE), out.index("## Summary"))

    def test_configured_email_counts_for_ats_checks(self):
        with mock.patch.dict(os.environ, {"RESUME_CONTACT_LINE": LINE}):
            checks = {c["name"]: c["status"] for c in tailoring_service.ats_checks(MD)["checks"]}
        self.assertEqual(checks["Contact email present"], "pass")

    def test_stored_resume_still_passes_no_fabrication(self):
        # The contact line is export-only, so verification runs on markdown without it.
        base = MD + "\n## Experience\n"
        self.assertEqual(nf.check_no_fabrication(base, base), [])


if __name__ == "__main__":
    unittest.main()
