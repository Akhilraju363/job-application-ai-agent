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


if __name__ == "__main__":
    unittest.main()
