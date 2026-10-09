"""Evidence-grounded bullet tailoring (local models): each rewrite is checked against its own source
bullet, rejected rewrites keep the original, code assembles the resume, employment history is untouched."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402,F401 -- must be first of the project imports: disables .env loading

import json
import unittest
from unittest import mock

from fixtures import ANALYSIS, JD_TEXT, TempOutput
import bullet_tailoring as bt
import jd_analysis as ja
import no_fabrication as nf
import paths
import resume_store
import tailoring_service as ts

MASTER = (paths.ROOT / "resume" / "base_resume.md").read_text(encoding="utf-8").replace("\r", "")
BULLETS = {b["id"]: b for b in bt.source_bullets(MASTER)}
UNIVERSE = bt._universe(MASTER, ANALYSIS, ja.compute_match(ANALYSIS, MASTER))
JOB = {"title": "Senior Full Stack Developer", "company": "Acme", "link": "https://acme.example/jobs/7",
       "description": JD_TEXT}

LTI_API = "Designed and developed RESTful APIs and microservices using Java, Spring Boot, Hibernate, and JPA for enterprise-grade applications."
GOOD = {  # light rewordings that add nothing
    "LTI_API": "Designed and developed Java, Spring Boot, Hibernate, and JPA RESTful APIs and microservices for enterprise-grade applications.",
    "INF_REST": "Built and maintained Java REST APIs for the Trucare backend service, supporting secure data exchange between admin and client-facing applications.",
}


def ids(employer):
    return [i for i, b in BULLETS.items() if b["employer"] == employer]


def bid(text):
    return next(i for i, b in BULLETS.items() if b["text"] == text)


def check(original, rewrite):
    return bt.check_rewrite(original, rewrite, UNIVERSE)


def respond(rewrites):
    return json.dumps({"bullets": [{"id": i, "text": rewrites.get(i, b["text"])} for i, b in BULLETS.items()]})


INF_REST = next(b["text"] for b in BULLETS.values() if b["text"].startswith("Built and maintained REST APIs"))


class SourceBullets(unittest.TestCase):
    def test_every_master_bullet_has_a_stable_positional_id(self):
        roles = nf.parse_resume(MASTER)["roles"]
        self.assertEqual(len(BULLETS), sum(len(r["bullets"]) for r in roles))
        self.assertEqual(BULLETS["E1.B1"]["employer"], roles[0]["heading"])
        self.assertEqual(BULLETS[f"E3.B{len(roles[2]['bullets'])}"]["text"], roles[2]["bullets"][-1])

    def test_prompt_lists_ids_and_supported_terms_only(self):
        match = ja.compute_match(ANALYSIS, MASTER)
        prompt = bt.build_prompt(JOB, list(BULLETS.values()), bt._matched_terms(match))
        for i, b in BULLETS.items():
            self.assertIn(f"[{i}] {b['text']}", prompt)
        self.assertNotIn("Kafka", prompt)  # a JD requirement the master lacks is never offered as a term
        self.assertIn("never evidence", prompt)


class CheckRewrite(unittest.TestCase):
    def test_supported_wording_improvement_is_accepted(self):
        self.assertEqual(check(LTI_API, GOOD["LTI_API"]), [])
        self.assertEqual(check(INF_REST, GOOD["INF_REST"]), [])

    def test_unsupported_technology_is_rejected(self):
        for added in ("Terraform", "AWS RDS", "Kafka", "Kubernetes"):
            with self.subTest(added=added):
                r = check(INF_REST, INF_REST.replace("in Java", f"in Java with {added}"))
                self.assertTrue(any("terms not in the source bullet" in x for x in r), r)

    def test_jd_wording_the_source_does_not_use_is_rejected(self):
        # "REST APIs" -> "RESTful APIs" adds a term this bullet doesn't name (the 2026-10-09 failure)
        r = check(INF_REST, INF_REST.replace("REST APIs", "RESTful APIs"))
        self.assertTrue(any("restful" in x for x in r), r)

    def test_dropping_a_technology_is_rejected(self):
        r = check(LTI_API, LTI_API.replace(", Hibernate, and JPA", ""))
        self.assertTrue(any("technologies dropped" in x for x in r), r)

    def test_invented_metric_or_achievement_is_rejected(self):
        self.assertTrue(any("numbers" in x for x in check(LTI_API, LTI_API[:-1] + ", serving 2M requests a day.")))
        r = check(LTI_API, LTI_API[:-1] + ", significantly reducing costs.")
        self.assertTrue(any("responsibility/impact" in x for x in r), r)

    def test_invented_responsibility_is_rejected(self):
        for claim in ("while mentoring junior engineers", "and led the team", "and owned the platform"):
            with self.subTest(claim=claim):
                r = check(LTI_API, LTI_API[:-1] + f" {claim}.")
                self.assertTrue(any("responsibility/impact" in x or "leadership" in x for x in r), r)

    def test_responsibility_already_in_the_source_is_allowed(self):
        src = "Collaborated with cross-functional Agile teams to deliver feature releases on schedule with high code quality."
        self.assertEqual(check(src, "Delivered feature releases on schedule with high code quality, collaborating with cross-functional Agile teams."), [])

    def test_meaning_must_be_kept(self):
        src = "Created database indexes and implemented server-side pagination to improve query performance and data access efficiency."
        r = check(src, "Implemented server-side pagination.")
        self.assertTrue(any("meaning" in x for x in r), r)
        r = check(LTI_API, LTI_API + " Worked closely with product owners on roadmap planning and backlog grooming sessions.")
        self.assertTrue(r)

    def test_formatting_must_stay_plain(self):
        self.assertTrue(check(LTI_API, "**" + LTI_API + "**"))
        self.assertTrue(check(LTI_API, LTI_API + "\n- Another bullet."))
        self.assertTrue(check(LTI_API, ""))


def src(prefix):
    return next(b["text"] for b in BULLETS.values() if b["text"].startswith(prefix))


class MeaningfulParaphrase(unittest.TestCase):
    """Restructuring that keeps every fact is accepted; upgrading a goal into a result is not.
    Real examples come from the 2026-10-09 SourcingXPress isolated run (qwen2.5:7b)."""

    def test_restructured_rewrites_that_keep_every_fact_are_accepted(self):
        cases = {
            "Performed debugging": "Debugged, performance-profiled, and optimized code following software engineering best practices.",
            "Worked with MySQL": "Maintained data integrity and schemas in MySQL and Oracle databases and wrote optimized queries.",
            "Collaborated with cross-functional Agile teams to deliver feature": "Delivered feature releases on schedule with high code quality as part of cross-functional Agile teams.",
            "Built and maintained REST APIs": "Built and maintained Java REST APIs for the Trucare backend service, enabling secure data exchange between admin and client-facing applications.",
            "Implemented modular codebases": "Organized modular codebases in an Nx Monorepo architecture, improving maintainability, code reuse, and team collaboration.",
        }
        for prefix, rewrite in cases.items():
            with self.subTest(prefix=prefix):
                self.assertEqual(check(src(prefix), rewrite), [])

    def test_goal_kept_as_a_goal_is_accepted(self):
        original = src("Created database indexes")
        rewrite = ("To improve query performance and data access efficiency, created database indexes and "
                   "implemented server-side pagination.")
        self.assertEqual(check(original, rewrite), [])
        # accepted in the real run: "to improve" -> "to enhance" is still a goal
        self.assertEqual(check(src("Built dynamic, reusable Angular"), "Developed dynamic, reusable Angular components "
                               "with optimized data flow to enhance UI responsiveness and user experience."), [])

    def test_goal_restated_as_an_achieved_result_is_rejected(self):
        cases = {
            "Created database indexes": "Improved query performance and data access efficiency by creating database indexes and implementing server-side pagination.",
            "Built dynamic, reusable Angular": "Enhanced UI responsiveness and user experience by building dynamic, reusable Angular components with optimized data flow.",
            "Built dynamic, reusable Angular ": "Built dynamic, reusable Angular components with optimized data flow, improving UI responsiveness and user experience.",
        }
        for prefix, rewrite in cases.items():
            with self.subTest(rewrite=rewrite[:40]):
                r = check(src(prefix.strip()), rewrite)
                self.assertIn("states a goal of the source bullet as an achieved result", r)

    def test_benefit_already_stated_as_a_result_may_be_reworded(self):
        # the source already claims the result ("enhancing"), so "improving" adds nothing
        self.assertEqual(check(src("Implemented modular codebases"), "Implemented modular codebases using Nx Monorepo "
                               "architecture, improving maintainability, code reuse, and team collaboration."), [])

    def test_benefit_verb_added_to_a_bullet_without_one_is_rejected(self):
        r = check(LTI_API, LTI_API[:-1] + ", improving reliability.")
        self.assertIn("states a goal of the source bullet as an achieved result", r)

    def test_maintaining_to_managing_stays_blocked(self):
        # the real run's one rejection: "manag" is a responsibility stem; kept blocked by design
        r = check(src("Worked with MySQL"), "Worked with MySQL and Oracle databases, maintaining data integrity, "
                  "writing optimized queries, and managing database schemas.")
        self.assertTrue(any("responsibility/impact" in x and "manag" in x for x in r), r)

    def test_restructuring_cannot_smuggle_in_unsupported_claims(self):
        base = "Debugged, performance-profiled, and optimized code following software engineering best practices."
        original = src("Performed debugging")
        for bad, why in ((base[:-1] + " with Datadog.", "terms not in the source bullet"),
                         (base[:-1] + " across 40 services.", "numbers not in the source bullet"),
                         ("Owned " + base[0].lower() + base[1:], "responsibility/impact"),
                         (base[:-1] + ", cutting latency.", "responsibility/impact"),
                         ("Debugged and optimized code following software engineering best practices.", "meaning")):
            with self.subTest(why=why, bad=bad[:30]):
                self.assertTrue(any(why in x for x in check(original, bad)), check(original, bad))

    def test_every_technical_term_must_survive_a_restructure(self):
        original = src("Developed and maintained Trucare Admin")
        for tech in ("Angular Material", "RxJS", "TypeScript"):
            with self.subTest(tech=tech):
                rewrite = ("Built and maintained Trucare Admin healthcare applications with Angular, Angular Material, "
                           "RxJS, and TypeScript.").replace(", " + tech, "").replace(" and " + tech, "")
                r = check(original, rewrite)
                self.assertTrue(any("technologies dropped" in x and tech.lower() in x for x in r), r)

    def test_prompt_asks_for_restructuring_not_synonym_swaps_and_keeps_the_guards(self):
        prompt = bt.build_prompt(JOB, list(BULLETS.values()), [])
        for phrase in ("restructure, don't just swap words", "never delete a detail", "keep a goal a goal",
                       "never evidence", "never add or upgrade responsibility", "Return a bullet unchanged only if",
                       "ids unchanged"):
            self.assertIn(phrase, prompt)
        self.assertNotIn("X.B1", "".join(b["id"] for b in BULLETS.values()))  # the example ids are not real bullets
        self.assertNotIn("X.B2", "".join(b["id"] for b in BULLETS.values()))


class DeletionOnly(unittest.TestCase):
    """Cutting words out of a bullet loses a fact and improves nothing; the 2026-10-09 qwen2.5:7b probes
    produced exactly these and the overlap thresholds let them through."""

    REAL = {  # source prefix -> what the model returned
        "Built Python-based backend": "Built Python-based backend services and REST APIs to support the TruCare healthcare platform.",
        "Developed and maintained scalable Angular": "Developed and maintained Angular web applications for the healthcare domain using Angular Material, RxJS, and TypeScript.",
        "Implemented modular codebases": "Implemented modular codebases using Nx Monorepo architecture, enhancing maintainability and code reuse.",
    }

    def test_real_deletion_only_rewrites_are_rejected(self):
        for prefix, rewrite in self.REAL.items():
            with self.subTest(prefix=prefix):
                self.assertIn("only deletes words from the source bullet (details dropped, nothing restructured)",
                              check(src(prefix), rewrite))

    def test_rejected_deletion_keeps_the_full_original(self):
        original = src("Built Python-based backend")
        with mock.patch("bullet_tailoring._call", return_value=respond({bid(original): self.REAL["Built Python-based backend"]})):
            md, rep = bt.tailor(JOB, MASTER, ANALYSIS, ja.compute_match(ANALYSIS, MASTER))
        self.assertIn("- " + original, md)
        self.assertEqual((rep["rewritten"], rep["rejected"]), (0, 1))

    def test_restructure_that_also_shortens_is_not_a_deletion(self):
        self.assertFalse(bt._only_deletes(src("Performed debugging"), "Debugged, performance-profiled, and optimized "
                                          "code following software engineering best practices."))
        self.assertFalse(bt._only_deletes(LTI_API, GOOD["LTI_API"]))  # reordered, same words

    def test_punctuation_only_change_counts_as_unchanged(self):
        original = src("Developed and maintained scalable Angular")
        comma = original.replace(" domain using", " domain, using")
        with mock.patch("bullet_tailoring._call", return_value=respond({bid(original): comma})):
            md, rep = bt.tailor(JOB, MASTER, ANALYSIS, ja.compute_match(ANALYSIS, MASTER))
        row = next(r for r in rep["bullets"] if r["id"] == bid(original))
        self.assertEqual((row["status"], row["text"]), ("unchanged", original))
        self.assertEqual(md, MASTER)


class Assembly(unittest.TestCase):
    def test_one_rewrite_changes_exactly_one_line(self):
        out = bt.apply(MASTER, {bid(LTI_API): GOOD["LTI_API"]})
        diff = [(a, b) for a, b in zip(MASTER.split("\n"), out.split("\n")) if a != b]
        self.assertEqual(diff, [("- " + LTI_API, "- " + GOOD["LTI_API"])])

    def tailor(self, rewrites, raw=None):
        match = ja.compute_match(ANALYSIS, MASTER)
        with mock.patch("bullet_tailoring._call", return_value=raw if raw is not None else respond(rewrites)):
            return bt.tailor(JOB, MASTER, ANALYSIS, match)

    def test_rejected_rewrite_keeps_its_original_and_other_rewrites_survive(self):
        bad = INF_REST.replace("in Java", "in Java and Terraform")
        md, rep = self.tailor({bid(LTI_API): GOOD["LTI_API"], bid(INF_REST): bad})
        self.assertIn("- " + GOOD["LTI_API"], md)
        self.assertIn("- " + INF_REST, md)
        self.assertNotIn("Terraform", md)
        self.assertEqual((rep["rewritten"], rep["rejected"]), (1, 1))
        row = next(r for r in rep["bullets"] if r["id"] == bid(INF_REST))
        self.assertEqual((row["status"], row["text"], row["proposed"]), ("rejected", INF_REST, bad))

    def test_bullets_never_move_between_employers(self):
        # the model puts an LTIMindtree bullet under a Zyter id: rejected, Zyter keeps its own text
        zyter = ids(nf.parse_resume(MASTER)["roles"][0]["heading"])[0]
        md, rep = self.tailor({zyter: LTI_API})
        self.assertEqual(rep["rewritten"], 0)
        roles_before = [(r["heading"], r["bullets"]) for r in nf.parse_resume(MASTER)["roles"]]
        self.assertEqual([(r["heading"], r["bullets"]) for r in nf.parse_resume(md)["roles"]], roles_before)

    def test_unknown_and_duplicate_ids_cannot_add_or_override_bullets(self):
        raw = json.dumps({"bullets": [{"id": "E9.B1", "text": "Invented role bullet."},
                                      {"id": bid(LTI_API), "text": GOOD["LTI_API"]},
                                      {"id": bid(LTI_API), "text": LTI_API + " Led a team."}]})
        md, rep = self.tailor({}, raw=raw)
        self.assertEqual(rep["unknown_ids"], ["E9.B1"])
        self.assertIn("- " + GOOD["LTI_API"], md)
        self.assertNotIn("Invented role bullet", md)
        self.assertEqual(len(nf.parse_resume(md)["roles"]), len(nf.parse_resume(MASTER)["roles"]))

    def test_employment_metadata_is_never_touched(self):
        md, _ = self.tailor({bid(LTI_API): GOOD["LTI_API"], bid(INF_REST): GOOD["INF_REST"]})
        self.assertEqual(nf.check_employment(nf.parse_resume(MASTER), nf.parse_resume(md)), [])
        self.assertEqual(nf.check_no_fabrication(MASTER, md), [])

    def test_report_keeps_ids_employers_and_shape(self):
        bad = INF_REST.replace("in Java", "in Java and Terraform")
        md, rep = self.tailor({bid(LTI_API): GOOD["LTI_API"], bid(INF_REST): bad})
        self.assertEqual([(r["id"], r["employer"]) for r in rep["bullets"]],
                         [(b["id"], b["employer"]) for b in bt.source_bullets(MASTER)])
        self.assertEqual(set(rep), {"strategy", "rewritten", "rejected", "unchanged", "missing", "unknown_ids", "bullets"})
        self.assertEqual(rep["strategy"], "bullets")
        self.assertEqual([(r["heading"], r["title"], r["date"]) for r in nf.parse_resume(md)["roles"]],
                         [(r["heading"], r["title"], r["date"]) for r in nf.parse_resume(MASTER)["roles"]])

    def test_malformed_response_means_no_rewrites(self):
        for raw in ("not json", json.dumps({"bullets": "x"}), json.dumps([1, 2]), json.dumps({})):
            with self.subTest(raw=raw):
                md, rep = self.tailor({}, raw=raw)
                self.assertEqual(md, MASTER)
                self.assertEqual(rep["missing"], len(BULLETS))


class Service(unittest.TestCase):
    """tailoring_service.tailor() in local (bullet) mode."""

    def run_tailor(self, raw=None, error=None):
        call = mock.patch("bullet_tailoring._call", side_effect=error) if error else \
            mock.patch("bullet_tailoring._call", return_value=raw)
        with TempOutput(), mock.patch("tailoring_service.bullet_mode", return_value=True), \
                mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
                mock.patch("tailor_job.call_llm") as full, call as bullets:
            try:
                rec = ts.tailor(JOB, source="manual", base_md=MASTER)
            finally:
                stored = resume_store.list_all()
            return rec, full, bullets, stored

    def test_verified_rewrites_produce_a_generated_version_with_one_llm_call(self):
        rec, full, bullets, _ = self.run_tailor(respond({bid(LTI_API): GOOD["LTI_API"], bid(INF_REST): GOOD["INF_REST"]}))
        v = rec["versions"][-1]
        full.assert_not_called()                      # no whole-resume rewrite in local mode
        self.assertEqual(bullets.call_count, 1)
        self.assertEqual((v["kind"], v["validation"]["ok"]), ("generated", True))
        self.assertIn(GOOD["LTI_API"], v["markdown"])
        bt_rep = v["validation"]["bullet_tailoring"]
        self.assertEqual((bt_rep["rewritten"], bt_rep["applied"]), (2, True))
        self.assertEqual(nf.check_no_fabrication(MASTER, v["markdown"]), [])
        self.assertEqual([(r["heading"], r["title"], r["date"]) for r in nf.parse_resume(v["markdown"])["roles"]],
                         [(r["heading"], r["title"], r["date"]) for r in nf.parse_resume(MASTER)["roles"]])

    def test_no_safe_rewrite_falls_back_to_reorder_only(self):
        bad = {i: b["text"] + " Mentored a team of 5 engineers." for i, b in BULLETS.items()}
        rec, *_ = self.run_tailor(respond(bad))
        v = rec["versions"][-1]
        self.assertEqual((v["kind"], v["validation"]["ok"]), ("conservative", True))
        self.assertIn("No bullet rewrite could be verified", v["validation"]["notice"])
        self.assertNotIn("Mentored", v["markdown"])
        self.assertEqual((v["validation"]["bullet_tailoring"]["rejected"], v["validation"]["bullet_tailoring"]["applied"]),
                         (len(BULLETS), False))

    def test_invalid_response_falls_back_to_reorder_only(self):
        rec, *_ = self.run_tailor(json.dumps({"unexpected": True}))
        self.assertEqual(rec["versions"][-1]["kind"], "conservative")

    def test_llm_errors_propagate_like_the_full_rewrite_path(self):
        import llm
        for err in (llm.AllProvidersFailed("all providers failed: timeout"), TimeoutError("deadline")):
            with self.subTest(err=type(err).__name__):
                with self.assertRaises(type(err)):
                    self.run_tailor(error=err)

    def test_llm_error_stores_nothing(self):
        with TempOutput(), mock.patch("tailoring_service.bullet_mode", return_value=True), \
                mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
                mock.patch("bullet_tailoring._call", side_effect=RuntimeError("deadline")):
            with self.assertRaises(RuntimeError):
                ts.tailor(JOB, source="manual", base_md=MASTER)
            self.assertEqual(resume_store.list_all(), [])

    def test_cloud_mode_keeps_the_full_rewrite_path(self):
        with TempOutput(), mock.patch("tailoring_service.bullet_mode", return_value=False), \
                mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
                mock.patch("tailor_job.call_llm", return_value=MASTER) as full, \
                mock.patch("bullet_tailoring._call") as bullets:
            rec = ts.tailor(JOB, source="manual", base_md=MASTER)
        full.assert_called_once()
        bullets.assert_not_called()
        self.assertNotIn("bullet_tailoring", rec["versions"][-1]["validation"])


if __name__ == "__main__":
    unittest.main()
