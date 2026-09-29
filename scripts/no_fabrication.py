"""No-fabrication verifier: compares a tailored resume against the master resume.

The tailoring prompt says "reorder and reword only" -- this module is the enforcement,
because a prompt is a request and this is a check. It is deliberately conservative: a
false positive costs one regenerate, a false negative puts an invented claim in front of
a recruiter. Pure functions, no I/O, no LLM.

What is verified (each returns human-readable violations, empty list == clean):
  * header: name, headline + contact line unchanged
  * structure: exactly the master's sections, in the master's order
  * experience: same employer headings, job-title lines and "dates | location" lines, same
    order (dash/whitespace differences ignored); no extra bullets; every
    bullet must be a rewording of a bullet under the *same* employer
  * technologies: nothing from a broad tech vocabulary (or the JD's missing skills) may
    appear unless the master resume already has it; nothing from the Skills list may move
    into an employer whose master section doesn't mention it (e.g. Python into the
    Java/Spring Boot role)
  * skills section: items must be a subset of the master's items
  * education / certifications: unchanged
  * numbers (years, metrics, dates): none that aren't in the master resume
  * seniority/leadership words ("led", "managed", "senior"...) not in the master resume

Known limit: free-text claims in the Summary that use no tech term, number, or
seniority word are not machine-checkable; the tailoring prompt plus the human preview
cover those.
"""
import re
from functools import lru_cache

TECH_VOCAB = sorted({
    # languages
    "java", "python", "typescript", "javascript", "kotlin", "scala", "golang", "rust", "ruby",
    "php", "c#", "c++", "perl", "groovy", "bash", "powershell", "sql", "pl/sql",
    # jvm / backend
    "spring boot", "spring", "spring cloud", "spring security", "spring mvc", "hibernate", "jpa",
    "jdbc", "maven", "gradle", "junit", "mockito", "testng", "jersey", "quarkus", "micronaut",
    "struts", "servlet", "jsp", "j2ee", "ejb", "soap", "grpc", "graphql", "openapi", "swagger",
    "fastapi", "django", "flask", "nestjs", "node.js", "nodejs", ".net", "asp.net",
    "rails", "laravel",
    # frontend
    "angular", "angularjs", "react", "react.js", "redux", "vue", "vue.js", "svelte", "next.js",
    "nuxt", "rxjs", "ngrx", "jquery", "html", "css", "scss", "sass", "tailwind", "bootstrap",
    "angular material", "webpack", "vite", "jest", "jasmine", "karma", "cypress", "playwright",
    "selenium", "storybook", "nx",
    # data
    "mysql", "postgresql", "postgres", "oracle", "mongodb", "redis", "cassandra", "dynamodb",
    "elasticsearch", "kafka", "rabbitmq", "activemq", "sqs", "sns", "snowflake", "bigquery",
    "hadoop", "spark", "airflow", "databricks", "sql server", "mssql", "sqlite", "neo4j",
    "power bi", "tableau",
    # cloud / devops
    "aws", "azure", "gcp", "google cloud", "docker", "kubernetes", "k8s", "terraform",
    "ansible", "jenkins", "github actions", "gitlab ci", "circleci", "helm", "openshift",
    "lambda", "ec2", "s3", "rds", "eks", "ecs", "cloudformation", "cloudwatch", "prometheus",
    "grafana", "splunk", "datadog", "new relic", "nginx", "linux", "ci/cd", "git", "github",
    "gitlab", "bitbucket", "jira", "sonarqube",
    # patterns / practice
    "microservices", "restful", "kafka streams", "oauth", "jwt", "saml", "keycloak",
    "tdd", "bdd", "devops", "serverless", "event-driven", "domain-driven design",
    # ai
    "machine learning", "tensorflow", "pytorch", "llm", "langchain",
})

LEADERSHIP_CLAIMS = ["led", "leading", "lead", "leads", "managed", "manager", "mentor", "mentored",
                     "mentoring", "architect", "architected", "principal", "staff engineer",
                     "director", "founder", "co-founder", "spearheaded", "head of"]

_STOP = frozenset("a an and are as at be by for from in into is it of on or the to with using use "
                  "used across per via their its this that these those while within over".split())


def norm_ws(s):
    return re.sub(r"\s+", " ", s).strip()


_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")


def _dashless(s):
    """Compare dates/titles without caring which dash the model typed ('2026 – Present' == '2026 - Present')."""
    return None if s is None else norm_ws(s.translate(_DASHES))


@lru_cache(maxsize=4096)
def _term_re(term):
    return re.compile(r"(?<![a-z0-9+#.])" + re.escape(term.lower()) + r"(?![a-z0-9+#])")


def has_term(text, term):
    """Case-insensitive whole-word presence ('java' does not match 'javascript')."""
    return bool(_term_re(term).search(text.lower()))


def terms_in(text, terms):
    return {t for t in terms if has_term(text, t)}


def split_items(line):
    """Comma-split that respects parentheses: 'Java (Core, Advanced), SQL' -> 2 items."""
    items, depth, cur = [], 0, []
    for ch in line:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur))
    return [norm_ws(i) for i in items if norm_ws(i)]


def item_key(item):
    return norm_ws(re.sub(r"\(.*?\)", "", item)).lower()


def parse_resume(md):
    header, sections, cur = [], {}, None
    for ln in md.splitlines():
        if ln.startswith("## "):
            cur = ln[3:].strip().lower()
            sections[cur] = []
        elif cur is None:
            header.append(ln)
        else:
            sections[cur].append(ln)

    # A role is "### Employer", an optional job-title line, then the "dates | location" line
    # (the first line with a year). Older "### Title — Employer" resumes have no title line.
    roles, role = [], None
    for ln in sections.get("experience", []):
        if ln.startswith("### "):
            role = {"heading": norm_ws(ln[4:]), "title": None, "date": None, "bullets": [], "lines": [ln]}
            roles.append(role)
        elif role is not None:
            role["lines"].append(ln)
            if ln.startswith("- "):
                role["bullets"].append(ln[2:].strip())
            elif ln.strip() and role["date"] is None:
                if _YEAR.search(ln) or role["title"] is not None:
                    role["date"] = norm_ws(ln)
                else:
                    role["title"] = norm_ws(ln)
    for r in roles:
        r["text"] = "\n".join(r["lines"])
        r["label"] = f"{r['title']} — {r['heading']}" if r["title"] else r["heading"]

    skill_items = []
    for ln in sections.get("skills", []):
        body = ln[2:] if ln.startswith("- ") else ln
        if ":" in body:
            body = body.split(":", 1)[1]
        skill_items.extend(split_items(body))

    return {"header": header, "sections": sections, "roles": roles, "skill_items": skill_items,
            "skill_keys": {item_key(i) for i in skill_items if len(item_key(i)) > 1}}


def _tokens(s):
    toks = (t.strip(".") for t in re.findall(r"[a-z0-9+#./]+", s.lower()))
    return {t for t in toks if t and t not in _STOP}


def _numbers(text):
    return {n.rstrip(".,") for n in re.findall(r"\d[\d,.]*%?", text)}


def _lines_of(section):
    return [norm_ws(l.lstrip("- ")) for l in section if l.strip()]


MIN_BULLET_OVERLAP = 0.6


def omitted_roles(base_md, tailored_md):
    """Role headings present in the master resume but absent from the tailored one."""
    kept = {r["heading"] for r in parse_resume(tailored_md)["roles"]}
    return [r["heading"] for r in parse_resume(base_md)["roles"] if r["heading"] not in kept]


def check_no_fabrication(base_md, tailored_md, jd_terms=()):
    """Return a list of violation strings; [] means the tailored resume is faithful."""
    base, tail = parse_resume(base_md), parse_resume(tailored_md)
    v = []

    # header: name, headline and contact line must be verbatim (the job role never replaces
    # the headline -- it only names the exported file)
    base_head = [norm_ws(l) for l in base["header"] if l.strip()]
    tail_head = {norm_ws(l) for l in tail["header"] if l.strip()}
    headline = [l for l in base_head[1:2] if "@" not in l and "linkedin.com" not in l.lower()]
    for line in base_head[:1] + headline + [l for l in base_head if "@" in l]:
        if line not in tail_head:
            v.append(f"header line changed or removed: {line[:60]!r}")

    # structure: the master's sections, in the master's order -- no added (Projects,
    # Achievements, ...), dropped or reordered sections
    if list(tail["sections"]) != list(base["sections"]):
        v.append(f"resume sections must match the master resume exactly: {list(tail['sections'])} "
                 f"!= {list(base['sections'])}")

    # experience structure: no added/changed role headings, original order kept. Omitting a
    # role is not fabrication (tailoring may drop an irrelevant one) -- verify() surfaces it
    # as a warning instead.
    base_heads = [r["heading"] for r in base["roles"]]
    tail_heads = [r["heading"] for r in tail["roles"]]
    extra = [h for h in tail_heads if h not in base_heads]
    if extra:
        v.append(f"experience roles not in master resume (titles and employers must be unchanged): {extra}")
    elif tail_heads != [h for h in base_heads if h in tail_heads]:
        v.append("experience roles were reordered (keep the master resume's order)")
    base_by_head = {r["heading"]: r for r in base["roles"]}
    for r in tail["roles"]:
        b = base_by_head.get(r["heading"])
        if b is None:
            continue
        if _dashless(r["title"]) != _dashless(b["title"]):
            v.append(f"job title changed for {r['heading'][:50]!r}: {r['title']!r} != {b['title']!r}")
        if _dashless(r["date"]) != _dashless(b["date"]):
            v.append(f"dates changed for {r['heading'][:50]!r}: {r['date']!r} != {b['date']!r}")
        if len(r["bullets"]) > len(b["bullets"]):
            v.append(f"extra bullets added under {r['heading'][:50]!r}")
        base_bullet_toks = [_tokens(x) for x in b["bullets"]]
        for bullet in r["bullets"]:
            toks = _tokens(bullet)
            best = max((len(toks & bt) / len(toks) for bt in base_bullet_toks if toks), default=0)
            if best < MIN_BULLET_OVERLAP:
                v.append(f"bullet not traceable to master resume under {r['heading'][:40]!r}: "
                         f"{bullet[:80]!r}")

    # education / certifications carried over unchanged
    for name in ("education", "certifications"):
        if ([_dashless(l) for l in _lines_of(base["sections"].get(name, []))]
                != [_dashless(l) for l in _lines_of(tail["sections"].get(name, []))]):
            v.append(f"{name} section differs from master resume")

    # skills subset
    extra_skills = sorted({item_key(i) for i in tail["skill_items"]} - base["skill_keys"]
                          - {item_key(i) for i in base["skill_items"]})
    if extra_skills:
        v.append(f"skills not in master resume: {', '.join(extra_skills)}")

    # technologies introduced anywhere
    universe = set(TECH_VOCAB) | {t.lower() for t in jd_terms if t}
    introduced = sorted(terms_in(tailored_md, universe) - terms_in(base_md, universe))
    if introduced:
        v.append(f"technologies/skills not in master resume: {', '.join(introduced)}")

    # technologies moved between employers
    movable = terms_in(base_md, set(TECH_VOCAB) | base["skill_keys"])
    for r in tail["roles"]:
        b = base_by_head.get(r["heading"])
        if b is None:
            continue
        moved = sorted(t for t in terms_in(r["text"], movable) if not has_term(b["text"], t))
        if moved:
            v.append(f"{', '.join(moved)} attributed to {r['heading'][:50]!r} but the master resume "
                     "doesn't list it under that employer")

    # numbers
    new_nums = sorted(_numbers(tailored_md) - _numbers(base_md))
    if new_nums:
        v.append(f"numbers not in master resume: {', '.join(new_nums)}")

    # seniority / leadership words
    claims = sorted(terms_in(tailored_md, LEADERSHIP_CLAIMS) - terms_in(base_md, LEADERSHIP_CLAIMS))
    if claims:
        v.append(f"seniority/leadership claims not in master resume: {', '.join(claims)}")

    return v
