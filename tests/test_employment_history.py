"""Employment history is immutable: employers, job titles, date ranges, locations and their order
come from the master resume. Checked (no_fabrication.check_employment) and, after a model rewrite,
restored where that is unambiguous (employment_history.restore).

Every case runs for every role of the real master resume (read-only) and of a synthetic master
with different employers, so nothing depends on one employer's text.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402,F401 -- must be first of the project imports: disables .env loading

import json
import unittest
from unittest import mock

from fixtures import ANALYSIS, JD_TEXT, TempOutput
import employment_history as eh
import no_fabrication as nf
import paths
import tailor_job
import tailoring_service as ts

REAL = (paths.ROOT / "resume" / "base_resume.md").read_text(encoding="utf-8").replace("\r", "")

# A different shape on purpose: a role without a location, a single-word title, a title with "|".
SYNTH = """# SAM PATEL
Software Engineer | Java

## Summary
Software Engineer with 5+ years of experience building Java services and Angular applications.

## Skills
- Languages: Java, TypeScript, SQL
- Frameworks: Spring Boot, Angular

## Experience

### Northwind Systems
Senior Developer | Payments Platform
Mar 2023 – Present | Pune, India
- Built Spring Boot services in Java for payment processing.
- Wrote SQL queries for settlement reports.

### Contoso Labs
Engineer
Jul 2020 – Feb 2023
- Developed Angular screens in TypeScript for internal tools.

### Fabrikam Retail
Associate Engineer — Integrations
Jan 2019 – Jun 2020 | Hyderabad, India
- Integrated Java services with partner REST APIs.
- Maintained SQL schemas for order data.

## Education
### B.Tech Computer Science
Example University | 2015 – 2019
"""

MASTERS = {"real master": REAL, "synthetic master": SYNTH}


def roles(md):
    return nf.parse_resume(md)["roles"]


def blocks(md):
    """The master's role blocks as text (heading through trailing blank line), plus the text
    before the first and after the last."""
    heads = [h for h, _, _ in eh.master_roles(md)]
    starts = [md.index(h + "\n") for h in heads]
    end = md.index("\n## ", starts[-1]) + 1
    return md[:starts[0]], [md[a:b] for a, b in zip(starts, starts[1:] + [end])], md[end:]


def with_roles(md, order):
    pre, bl, post = blocks(md)
    return pre + "".join(bl[i] for i in order) + post


def swap_line(md, old, new):
    assert f"\n{old}\n" in md, old
    return md.replace(f"\n{old}\n", f"\n{new}\n", 1)


def check(master, tailored):
    return nf.check_no_fabrication(master, tailored)


class ForEveryRole(unittest.TestCase):
    def each_role(self):
        for name, md in MASTERS.items():
            for r in roles(md):
                with self.subTest(master=name, employer=r["heading"]):
                    yield md, r

    def assertRejected(self, master, tailored, *needles):
        v = check(master, tailored)
        self.assertTrue(any(all(n in x for n in needles) for x in v), f"expected {needles} in {v}")

    def test_unchanged_master_passes(self):
        for name, md in MASTERS.items():
            with self.subTest(master=name):
                self.assertEqual(check(md, md), [])
                self.assertEqual(nf.check_employment(nf.parse_resume(md), nf.parse_resume(md)), [])

    def test_fields_are_parsed_separately(self):
        r = {x["heading"]: x for x in roles(REAL)}["LTIMindtree"]
        self.assertEqual((r["title"], r["dates"], r["location"]),
                         ("Software Engineer — Backend Development", "Jan 2022 – Jun 2024", "Chennai, India"))
        self.assertEqual(nf.split_date_line("Jul 2020 – Feb 2023"), ("Jul 2020 – Feb 2023", None))

    def test_changed_title_is_rejected(self):
        for md, r in self.each_role():
            self.assertRejected(md, swap_line(md, r["title"], "Principal " + r["title"]),
                                "job title changed", repr(r["heading"][:80]))

    def test_changed_title_capitalization_is_rejected(self):
        for md, r in self.each_role():
            self.assertRejected(md, swap_line(md, r["title"], r["title"].upper()), "job title changed")

    def test_changed_date_range_is_rejected(self):
        for md, r in self.each_role():
            changed = r["date"].replace(r["dates"], r["dates"].replace("20", "19", 1))
            self.assertRejected(md, swap_line(md, r["date"], changed), "dates changed", repr(r["heading"][:80]))

    def test_changed_location_is_rejected(self):
        for md, r in self.each_role():
            if r["location"]:
                self.assertRejected(md, swap_line(md, r["date"], f"{r['dates']} | Mumbai, India"),
                                    "location changed", repr(r["heading"][:80]), "Mumbai, India")
            else:
                self.assertRejected(md, swap_line(md, r["date"], f"{r['dates']} | Mumbai, India"),
                                    "location added", repr(r["heading"][:80]))

    def test_location_removed_is_rejected(self):
        for md, r in self.each_role():
            if r["location"]:
                self.assertRejected(md, swap_line(md, r["date"], r["dates"]), "location missing")

    def test_location_appended_to_title_line_is_rejected(self):
        for md, r in self.each_role():
            loc = r["location"] or "Bengaluru, India"
            self.assertRejected(md, swap_line(md, r["title"], f"{r['title']} | {loc}"),
                                "job title changed", "appended to the title line")

    def test_location_moved_from_date_line_to_title_line_is_rejected(self):
        # The SourcingXPress failure: "Title | Location" then a bare date range.
        for md, r in self.each_role():
            if not r["location"]:
                continue
            t = swap_line(swap_line(md, r["title"], f"{r['title']} | {r['location']}"), r["date"], r["dates"])
            v = check(md, t)
            self.assertTrue(any("location moved" in x and "title line" in x for x in v), v)
            self.assertTrue(any("job title changed" in x for x in v), v)
            self.assertFalse(any("dates changed" in x for x in v), v)  # the dates themselves are right

    def test_location_appended_to_date_line_is_rejected(self):
        for md, r in self.each_role():
            self.assertRejected(md, swap_line(md, r["date"], f"{r['date']} | Remote"), "location")

    def test_date_line_layout_change_is_rejected(self):
        for md, r in self.each_role():
            if r["location"]:
                self.assertRejected(md, swap_line(md, r["date"], f"{r['location']} | {r['dates']}"),
                                    "date line reformatted")

    def test_title_merged_into_date_line_is_rejected(self):
        for md, r in self.each_role():
            t = md.replace(f"\n{r['title']}\n{r['date']}\n", f"\n{r['title']} | {r['date']}\n", 1)
            self.assertRejected(md, t, "merged into the date line")

    def test_removed_employer_is_rejected(self):
        for name, md in MASTERS.items():
            for i, r in enumerate(roles(md)):
                with self.subTest(master=name, employer=r["heading"]):
                    order = [j for j in range(len(roles(md))) if j != i]
                    self.assertRejected(md, with_roles(md, order), "employer missing", repr(r["heading"][:80]))

    def test_duplicated_employer_is_rejected(self):
        for name, md in MASTERS.items():
            n = len(roles(md))
            for i, r in enumerate(roles(md)):
                with self.subTest(master=name, employer=r["heading"]):
                    order = list(range(n)) + [i]
                    self.assertRejected(md, with_roles(md, order), "employer duplicated", repr(r["heading"][:80]))

    def test_reordered_employers_are_rejected(self):
        for name, md in MASTERS.items():
            n = len(roles(md))
            for i in range(n - 1):
                with self.subTest(master=name, swap=i):
                    order = list(range(n))
                    order[i], order[i + 1] = order[i + 1], order[i]
                    self.assertRejected(md, with_roles(md, order), "reordered")

    def test_fabricated_employer_is_rejected(self):
        for name, md in MASTERS.items():
            with self.subTest(master=name):
                pre, bl, post = blocks(md)
                fake = "### Globex Corporation\nSoftware Engineer\nJan 2018 – Dec 2018 | Delhi, India\n- Built APIs.\n\n"
                self.assertRejected(md, pre + "".join(bl) + fake + post, "not in master resume", "Globex")

    def test_text_inside_a_role_block_is_rejected(self):
        for md, r in self.each_role():
            t = md.replace(r["date"] + "\n", r["date"] + "\nTech stack: Kubernetes and Kafka at scale\n", 1) \
                if not r["bullets"] else md.replace("- " + r["bullets"][-1], "- " + r["bullets"][-1] +
                                                    "\nOwned the platform roadmap", 1)
            self.assertRejected(md, t, "unexpected text under", repr(r["heading"][:80]))

    def test_legitimate_summary_and_skills_tailoring_passes(self):
        t = REAL.replace("Results-driven Software Engineer", "Full-stack Software Engineer", 1) \
                .replace("- Databases: MySQL, Oracle, PostgreSQL", "- Databases: PostgreSQL, MySQL, Oracle")
        self.assertEqual(check(REAL, t), [])
        t = SYNTH.replace("building Java services", "building scalable Java services") \
                 .replace("- Frameworks: Spring Boot, Angular", "- Frameworks: Angular, Spring Boot")
        self.assertEqual(check(SYNTH, t), [])

    def test_unsupported_metrics_and_achievements_still_rejected(self):
        bullet = roles(REAL)[0]["bullets"][0]
        t = REAL.replace(bullet, bullet[:-1] + ", cutting latency by 45%.")
        self.assertTrue(any("numbers not in master resume" in x for x in check(REAL, t)))
        t = REAL.replace(bullet, "Won the company-wide innovation award for the payments platform.")
        self.assertTrue(any("not traceable" in x for x in check(REAL, t)))


def sourcingxpress_style(md):
    """The model failure seen on 2026-10-09: roles out of order and every location moved
    from its date line onto the title line."""
    out = md
    for r in roles(md):
        if r["location"]:
            out = swap_line(swap_line(out, r["title"], f"{r['title']} | {r['location']}"), r["date"], r["dates"])
    n = len(roles(md))
    return with_roles(out, [n - 2] + [i for i in range(n) if i != n - 2])


class Restore(unittest.TestCase):
    def test_master_is_untouched(self):
        for name, md in MASTERS.items():
            with self.subTest(master=name):
                self.assertEqual(eh.restore(md, md), (md, []))

    def test_moved_locations_and_order_are_restored_exactly(self):
        for name, md in MASTERS.items():
            with self.subTest(master=name):
                broken = sourcingxpress_style(md)
                self.assertNotEqual(check(md, broken), [])
                fixed, notes = eh.restore(broken, md)
                self.assertEqual(fixed, md)
                self.assertEqual(check(md, fixed), [])
                self.assertIn("experience order restored to the master resume's order", notes)

    def test_bullets_are_kept_with_their_employer_and_never_changed(self):
        reworded = REAL.replace("Developed and maintained scalable Angular web applications",
                                "Built and maintained scalable Angular web applications")
        fixed, notes = eh.restore(sourcingxpress_style(reworded), REAL)
        self.assertEqual([(r["heading"], r["bullets"]) for r in roles(fixed)],
                         [(r["heading"], r["bullets"]) for r in roles(reworded)])
        self.assertEqual(check(REAL, fixed), [])

    def test_rewritten_title_is_restored(self):
        for md, r in ((md, r) for md in MASTERS.values() for r in roles(md)):
            with self.subTest(employer=r["heading"]):
                fixed, notes = eh.restore(swap_line(md, r["title"], "Senior " + r["title"]), md)
                self.assertEqual(fixed, md)
                self.assertEqual(len(notes), 1)

    def test_refuses_when_an_employer_is_missing_duplicated_or_unknown(self):
        n = len(roles(REAL))
        pre, bl, post = blocks(REAL)
        cases = {"missing": with_roles(REAL, range(1, n)), "duplicated": with_roles(REAL, [*range(n), 0]),
                 "unknown": REAL.replace("### LTIMindtree", "### Infosys")}
        for case, md in cases.items():
            with self.subTest(case=case):
                self.assertEqual(eh.restore(md, REAL), (md, None))
                self.assertNotEqual(check(REAL, md), [])

    def test_refuses_when_text_after_the_bullets_is_not_metadata(self):
        last = roles(REAL)[0]["bullets"][-1]
        md = REAL.replace(last, last + "\nOwned the platform roadmap end to end", 1)
        self.assertEqual(eh.restore(md, REAL), (md, None))
        self.assertTrue(any("unexpected text" in x for x in check(REAL, md)))

    def test_refuses_when_prose_sits_where_the_metadata_goes(self):
        r = roles(REAL)[0]
        md = REAL.replace(r["date"] + "\n", r["date"] + "\n" + "Owned delivery of the platform. " * 8 + "\n", 1)
        self.assertEqual(eh.restore(md, REAL), (md, None))

    def test_date_line_moved_below_bullets_is_restored(self):
        r = roles(REAL)[2]
        md = REAL.replace(r["date"] + "\n", "", 1).replace(r["bullets"][-1], r["bullets"][-1] + "\n" + r["date"], 1)
        self.assertNotEqual(check(REAL, md), [])
        self.assertEqual(eh.restore(md, REAL), (REAL, [f"employment details restored for {r['heading'][:50]!r}"]))

    def test_protected_lines_come_from_the_current_master(self):
        block = eh.protected_lines(SYNTH)
        self.assertEqual(block.split("\n\n")[1], "### Contoso Labs\nEngineer\nJul 2020 – Feb 2023")
        for r in roles(REAL):
            self.assertIn(f"### {r['heading']}\n{r['title']}\n{r['date']}", eh.protected_lines(REAL))


JOB = {"title": "Senior Full Stack Developer", "company": "Acme", "link": "https://acme.example/jobs/42",
       "description": JD_TEXT}


class Pipeline(unittest.TestCase):
    def run_tailor(self, outputs):
        with TempOutput(), mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
                mock.patch("tailor_job.call_llm", side_effect=outputs) as llm:
            rec = ts.tailor(JOB, source="manual", base_md=REAL)
            return rec, llm

    def test_prompt_states_the_rules_and_lists_the_protected_lines(self):
        with mock.patch("tailor_job.call_llm", return_value="") as llm:
            tailor_job.tailor_text(JOB, REAL)
        prompt = llm.call_args[0][0]
        self.assertIn("EMPLOYMENT HISTORY IS IMMUTABLE", prompt)
        self.assertIn("authoritative source of truth", prompt)
        self.assertIn(eh.protected_lines(REAL), prompt)
        self.assertNotIn("__EMPLOYMENT__", prompt)

    def test_misplaced_metadata_is_restored_and_verified_on_the_first_attempt(self):
        rec, llm = self.run_tailor([sourcingxpress_style(REAL)])
        v = rec["versions"][-1]
        self.assertEqual(llm.call_count, 1)
        self.assertEqual((v["kind"], v["validation"]["ok"]), ("generated", True))
        self.assertEqual([(r["heading"], r["title"], r["date"]) for r in roles(v["markdown"])],
                         [(r["heading"], r["title"], r["date"]) for r in roles(REAL)])
        self.assertTrue(any("Corrected from the master resume" in w for w in v["validation"]["warnings"]))

    def test_unrestorable_output_is_rejected_then_falls_back(self):
        dropped = with_roles(REAL, range(1, len(roles(REAL))))
        rec, llm = self.run_tailor([dropped, dropped])
        v = rec["versions"][-1]
        self.assertEqual(llm.call_count, 2)
        self.assertEqual(v["kind"], "conservative")
        self.assertTrue(any("employer missing" in p for p in v["validation"]["llm_problems"]))
        self.assertEqual([(r["heading"], r["title"], r["date"]) for r in roles(v["markdown"])],
                         [(r["heading"], r["title"], r["date"]) for r in roles(REAL)])

    def test_user_edits_are_verified_as_written_never_restored(self):
        with TempOutput(), mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)), \
                mock.patch("tailor_job.call_llm", return_value=REAL):
            rec = ts.tailor(JOB, source="manual", base_md=REAL)
            edited = ts.revalidate_edit(rec["id"], sourcingxpress_style(REAL), base_md=REAL)
        v = edited["versions"][-1]
        self.assertFalse(v["validation"]["ok"])
        self.assertTrue(any("location moved" in p for p in v["validation"]["problems"]))


if __name__ == "__main__":
    unittest.main()
