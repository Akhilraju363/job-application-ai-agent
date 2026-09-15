"""Tests for the canonical-link dedup fix: LinkedIn (and similar) append a different
tracking query string to the same posting's URL on every fresh search result, which
defeated exact-string dedup across separate scrape runs and produced duplicate Drive
folders / Sheet rows for a job that had already been processed. See scripts/job_links.py.

Stdlib only (no pytest): python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from job_links import canonical_link  # noqa: E402


class CanonicalLink(unittest.TestCase):
    SAME_POSTING = (
        "https://in.linkedin.com/jobs/view/fullstack-developer-at-r-systems-4336955994"
        "?position=2&pageNum=0&refId=abc&trackingId=xyz",
        "https://in.linkedin.com/jobs/view/fullstack-developer-at-r-systems-4336955994"
        "?position=4&pageNum=0&refId=def&trackingId=uvw",
        "https://in.linkedin.com/jobs/view/fullstack-developer-at-r-systems-4336955994",
    )

    def test_strips_tracking_query_params(self):
        canon = {canonical_link(u) for u in self.SAME_POSTING}
        self.assertEqual(len(canon), 1)
        self.assertEqual(
            canon.pop(),
            "https://in.linkedin.com/jobs/view/fullstack-developer-at-r-systems-4336955994",
        )

    def test_different_postings_stay_different(self):
        a = canonical_link("https://in.linkedin.com/jobs/view/job-a-111?trackingId=x")
        b = canonical_link("https://in.linkedin.com/jobs/view/job-b-222?trackingId=x")
        self.assertNotEqual(a, b)

    def test_identity_for_plain_non_url_strings(self):
        # test_llm.py's Reconciliation tests use bare identifiers like "j1" -- must not
        # be mangled so those tests keep passing unchanged.
        self.assertEqual(canonical_link("j1"), "j1")

    def test_identity_for_url_with_no_query_string(self):
        url = "https://in.linkedin.com/jobs/view/fullstack-developer-at-r-systems-4336955994"
        self.assertEqual(canonical_link(url), url)

    def test_empty_and_none_pass_through(self):
        self.assertEqual(canonical_link(""), "")
        self.assertIsNone(canonical_link(None))


class ReconciliationCanonicalDedup(unittest.TestCase):
    """Regression test for the false-positive this fix could otherwise introduce: once
    tailor_job.py correctly skips a second raw-link variant of an already-saved
    canonical job, reconcile_qualified must not treat that skipped variant as an unmet
    qualified job (see modal_app.reconcile_qualified)."""

    def setUp(self):
        import modal_app
        self.reconcile = modal_app.reconcile_qualified

    def test_second_raw_link_variant_of_saved_job_is_not_unmet(self):
        variant_a, variant_b, _ = CanonicalLink.SAME_POSTING
        scored = [
            {"link": variant_a, "qualified": True},
            {"link": variant_b, "qualified": True},  # same posting, different trackingId
        ]
        tailored = [{"link": variant_a, "status": "saved"}]  # only variant_a was ever tailored
        unmet, _ = self.reconcile(scored, tailored)
        self.assertEqual(unmet, set())

    def test_genuinely_different_unsaved_job_is_still_flagged(self):
        variant_a, _, _ = CanonicalLink.SAME_POSTING
        other = "https://in.linkedin.com/jobs/view/some-other-job-999?trackingId=z"
        scored = [
            {"link": variant_a, "qualified": True},
            {"link": other, "qualified": True},
        ]
        tailored = [{"link": variant_a, "status": "saved"}]
        unmet, reasons = self.reconcile(scored, tailored)
        self.assertEqual(unmet, {canonical_link(other)})


if __name__ == "__main__":
    unittest.main()
