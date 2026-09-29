"""The no-fabrication verifier: faithful tailoring passes, every kind of invention is caught."""
import unittest

from fixtures import BASE, reorder_bullets
import no_fabrication as nf


def check(tailored, jd_terms=()):
    return nf.check_no_fabrication(BASE, tailored, jd_terms)


class Faithful(unittest.TestCase):
    def test_identical_resume_is_clean(self):
        self.assertEqual(check(BASE), [])

    def test_reordered_bullets_and_skills_are_clean(self):
        t = reorder_bullets(BASE).replace("- Languages: Java (Core & Advanced), Python, TypeScript, SQL",
                                          "- Languages: TypeScript, Java (Core & Advanced), SQL, Python")
        self.assertEqual(check(t), [])

    def test_light_rewording_is_clean(self):
        t = BASE.replace("Developed Java backend services and RESTful APIs using Spring Boot.",
                         "Built Java backend services and RESTful APIs using Spring Boot.")
        self.assertEqual(check(t), [])

    def test_summary_can_mirror_jd_language_for_skills_the_resume_has(self):
        t = BASE.replace("building Java and Angular applications", "building full stack Java and Angular applications")
        self.assertEqual(check(t, jd_terms=["Kafka"]), [])

    def test_omitting_a_role_is_allowed_but_reported(self):
        t = BASE[:BASE.index("### Software Engineer | Java | Microservices")] + BASE[BASE.index("## Education"):]
        self.assertEqual(check(t), [])
        self.assertEqual(nf.omitted_roles(BASE, t), ["Software Engineer | Java | Microservices — LTIMindtree"])


class Fabrication(unittest.TestCase):
    def assertFlags(self, tailored, needle, jd_terms=()):
        problems = check(tailored, jd_terms)
        self.assertTrue(any(needle.lower() in p.lower() for p in problems), f"expected {needle!r} in {problems}")

    def test_invented_technology_in_summary(self):
        self.assertFlags(BASE.replace("REST API design", "REST API design and Kafka streaming"), "kafka")

    def test_invented_skill_item(self):
        self.assertFlags(BASE.replace("- Tools: Git, SCSS", "- Tools: Git, SCSS, Terraform"), "terraform")

    def test_jd_missing_skill_injected(self):
        self.assertFlags(BASE.replace("healthcare and REST", "healthcare, distributed systems and REST"),
                         "distributed systems", jd_terms=["distributed systems"])

    def test_technology_moved_between_employers(self):
        # Python belongs to Zyter only; the Infinite role is the Java/Spring Boot one.
        t = BASE.replace("- Developed Java backend services and RESTful APIs using Spring Boot.",
                         "- Developed Python backend services and RESTful APIs using Spring Boot.")
        self.assertFlags(t, "python")

    def test_java_moved_into_the_python_role(self):
        t = BASE.replace("- Worked with PostgreSQL databases for API-driven data processing.",
                         "- Worked with Java and PostgreSQL databases for API-driven data processing.")
        self.assertFlags(t, "java")

    def test_role_heading_changed(self):
        self.assertFlags(BASE.replace("Zyter Technologies", "Zyter Technologies Inc"), "roles not in master")

    def test_new_employer(self):
        t = BASE.replace("## Education", "### Senior Engineer — Google\nJan 2020 - Dec 2021\n- Built things.\n\n## Education")
        self.assertFlags(t, "roles not in master")

    def test_role_dates_changed(self):
        self.assertFlags(BASE.replace("Feb 2025 - Dec 2025", "Feb 2023 - Dec 2025"), "dates changed")

    def test_roles_reordered(self):
        a, b = "### Software Engineer | Python", "### Software Engineer | Java | Angular"
        i, j, k = BASE.index(a), BASE.index(b), BASE.index("### Software Engineer | Java | Microservices")
        t = BASE[:i] + BASE[j:k] + BASE[i:j] + BASE[k:]
        self.assertFlags(t, "reordered")

    def test_invented_years_or_metrics(self):
        self.assertFlags(BASE.replace("4+ years", "8+ years"), "numbers")
        self.assertFlags(BASE.replace("query optimization.", "query optimization, cutting latency by 40%."), "numbers")

    def test_extra_bullet(self):
        t = BASE.replace("- Worked with microservices architecture for scalable application development.",
                         "- Worked with microservices architecture for scalable application development.\n- Mentored juniors.")
        self.assertFlags(t, "extra bullets")

    def test_bullet_not_traceable_to_master(self):
        t = BASE.replace("- Developed Java backend services and RESTful APIs using Spring Boot.",
                         "- Owned the payments platform end to end across five squads.")
        self.assertFlags(t, "not traceable")

    def test_leadership_claim(self):
        self.assertFlags(BASE.replace("Software Engineer with 4+", "Software Engineer who led a team with 4+"), "leadership")

    def test_education_and_certifications_locked(self):
        self.assertFlags(BASE.replace("Bachelor of Science", "Master of Science"), "education")
        self.assertFlags(BASE.replace("AWS Certified Cloud Practitioner", "AWS Certified Solutions Architect"), "certifications")

    def test_header_locked(self):
        self.assertFlags(BASE.replace("# Jane Roe", "# Jane Q. Roe"), "header")
        self.assertFlags(BASE.replace("jane@example.com", "jane@other.com"), "header")


class Parsing(unittest.TestCase):
    def test_skill_split_respects_parentheses(self):
        self.assertEqual(nf.split_items("Java (Core, Advanced), SQL"), ["Java (Core, Advanced)", "SQL"])

    def test_term_matching_is_whole_word(self):
        self.assertTrue(nf.has_term("Java and Spring", "java"))
        self.assertFalse(nf.has_term("JavaScript only", "java"))
        self.assertFalse(nf.has_term("PostgreSQL", "sql"))
        self.assertTrue(nf.has_term("C# and .NET", "c#"))


# The master resume's role layout: "### Employer", a job-title line, then "dates | location".
MASTER = """# JANE ROE
Software Engineer | Java

## Summary
Engineer with Java experience.

## Skills
- Languages: Java, Python

## Experience

### Acme Corp
Software Engineer — Backend Development
Jan 2022 – Jun 2024 | Chennai, India
- Built REST APIs in Java for enterprise applications.
- Tuned database queries for faster data access.

## Education
### B.Sc. Computer Science
State University | 2015 – 2018
"""


class MasterLayout(unittest.TestCase):
    def test_title_and_date_lines_are_parsed_separately(self):
        role = nf.parse_resume(MASTER)["roles"][0]
        self.assertEqual(role["heading"], "Acme Corp")
        self.assertEqual(role["title"], "Software Engineer — Backend Development")
        self.assertEqual(role["date"], "Jan 2022 – Jun 2024 | Chennai, India")
        self.assertEqual(role["label"], "Software Engineer — Backend Development — Acme Corp")

    def test_faithful_copy_and_dash_variants_are_clean(self):
        self.assertEqual(nf.check_no_fabrication(MASTER, MASTER), [])
        hyphens = MASTER.replace("—", "-").replace("–", "-")
        self.assertEqual(nf.check_no_fabrication(MASTER, hyphens), [])

    def test_job_title_line_is_locked(self):
        v = nf.check_no_fabrication(MASTER, MASTER.replace("Software Engineer — Backend", "Senior Engineer — Backend"))
        self.assertTrue(any("job title changed" in x for x in v), v)

    def test_dates_still_checked_with_a_title_line(self):
        v = nf.check_no_fabrication(MASTER, MASTER.replace("Jan 2022", "Jan 2021"))
        self.assertTrue(any("dates changed" in x for x in v), v)

    def test_old_layout_without_title_line_still_parses(self):
        role = nf.parse_resume("## Experience\n### Engineer — Acme\nJan 2022 - Present\n- x")["roles"][0]
        self.assertEqual((role["title"], role["date"]), (None, "Jan 2022 - Present"))


class ExportLayout(unittest.TestCase):
    def test_master_layout_blocks(self):
        import format_resume_doc as frd
        blocks = frd.parse(MASTER)
        kinds = [k for k, _ in blocks]
        self.assertEqual(kinds[:2], ["NAME", "HEADLINE"])
        self.assertIn(("SECTION", "PROFESSIONAL SUMMARY"), blocks)
        self.assertIn(("SECTION", "TECHNICAL SKILLS"), blocks)
        self.assertIn(("SECTION", "PROFESSIONAL EXPERIENCE"), blocks)
        self.assertIn(("SKILL", "Languages: Java, Python"), blocks)
        self.assertIn(("POSITION", "Software Engineer — Backend Development"), blocks)
        self.assertIn(("DATE", "Jan 2022 – Jun 2024 | Chennai, India"), blocks)
        self.assertIn(("DATE", "State University | 2015 – 2018"), blocks)

    def test_header_centered_and_sections_ruled(self):
        import format_resume_doc as frd
        reqs = frd.build_requests(frd.parse(MASTER))
        paras = [r["updateParagraphStyle"]["paragraphStyle"] for r in reqs if "updateParagraphStyle" in r]
        self.assertEqual(sum(1 for p in paras if p.get("alignment") == "CENTER"), 2)
        self.assertEqual(sum(1 for p in paras if "borderBottom" in p), 4)
        bullets = [r for r in reqs if "createParagraphBullets" in r]
        self.assertEqual(len(bullets), 1)  # skills are labelled lines, not bullets


if __name__ == "__main__":
    unittest.main()
