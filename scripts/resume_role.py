"""The target role of a tailored resume -- used ONLY for its filename.

The job's title is normalized (seniority and level stripped, spelling unified), e.g.

    "Senior Java Developer"        -> "Java Developer"
    "Full-Stack Developer II"      -> "Full Stack Developer"
    "We're Hiring: Java Developer | 4+ Years" -> "Java Developer"

and becomes the export name, Akhil_Dalali_Java_Developer.pdf. The resume's header never
changes with the job: fixed_header() puts the master resume's name and generic
headline ("Software Engineer | Java | Spring Boot | ...") on every tailored resume.
The role never adds a claim -- a technology the master resume doesn't have, or a
seniority/leadership word it doesn't use, is dropped from it. If no role noun is left
the role falls back to "Software Engineer", the candidate's actual title.
Pure functions, no I/O.
"""
import re

import no_fabrication as nf

FALLBACK_ROLE = "Software Engineer"
ROLE_NOUNS = {"developer", "engineer", "programmer", "consultant", "analyst", "specialist",
              "tester", "administrator", "scientist"}
_SENIORITY = {"senior", "sr", "junior", "jr", "lead", "principal", "staff", "associate", "mid",
              "mid-level", "midlevel", "entry", "entry-level", "experienced", "trainee", "intern",
              "internship", "chief", "head", "expert", "level", "fresher", "graduate"}
_NOISE = {"remote", "hybrid", "onsite", "on-site", "contract", "temporary", "permanent", "urgent",
          "immediate", "joiner", "joiners", "opening", "openings", "hiring", "we're", "were", "we"}
_LEVEL = re.compile(r"^(?:i{1,3}|iv|v|l\d+|\d+)$", re.I)
_SEGMENT_SPLIT = re.compile(r"\s+[-–—|]\s+|[|(),:;\[\]]| @ | at ")
_SPELLING = ((re.compile(r"\bfull[\s-]*stack\b", re.I), "Full Stack"),
             (re.compile(r"\bback[\s-]*end\b", re.I), "Backend"),
             (re.compile(r"\bfront[\s-]*end\b", re.I), "Frontend"),
             (re.compile(r"\bdev\b", re.I), "Developer"))
_SEPARATORS = {"/", "&", "+", "-", "and", ","}


def _has_noun(text):
    return any(w.lower() in ROLE_NOUNS for w in re.split(r"[\s/]+", text))


def _base_casing(word, base_md):
    """The master resume's own capitalized spelling of a word ('JAVA' -> 'Java'), if it has one.
    Lowercase prose uses ('backend services') don't count -- a title is title-cased."""
    for m in re.finditer(r"(?<![\w+#.])" + re.escape(word) + r"(?![\w+#])", base_md, re.I):
        if not m.group(0).islower():
            return m.group(0)
    return None


def _case(word, base_md):
    cased = _base_casing(word, base_md)
    if cased and cased.lower() not in ROLE_NOUNS:
        return cased
    if word.islower() or (word.isupper() and len(word) > 3) or word.lower() in ROLE_NOUNS:
        return word[:1].upper() + word[1:].lower()
    return word


def target_role(title, base_md, jd_terms=()):
    """Normalize a job title into the resume's target role (see module docstring)."""
    segments = [s.strip() for s in _SEGMENT_SPLIT.split(title or "") if s and s.strip()]
    text = next((s for s in segments if _has_noun(s)), segments[0] if segments else "")
    for pattern, repl in _SPELLING:
        text = pattern.sub(repl, text)

    # Technologies the master resume doesn't have are claims -- drop them from the role.
    universe = set(nf.TECH_VOCAB) | {t.lower() for t in jd_terms if t}
    # A JD keyword naming a role ("Developer", "Java Developer") is positioning, not a technology
    # claim -- the technologies inside it are still checked on their own (TECH_VOCAB / JD tech terms).
    unsupported = {t for t in nf.terms_in(text, universe) - nf.terms_in(base_md, universe)
                   if not any(w in ROLE_NOUNS for w in re.split(r"[\s/]+", t))}
    for term in sorted(unsupported, key=len, reverse=True):
        text = re.sub(r"(?<![a-z0-9+#.])" + re.escape(term) + r"(?![a-z0-9+#])", " ", text, flags=re.I)

    words = []
    for w in re.split(r"\s+|(/)", text):
        if not w:
            continue
        w = w.strip(".,")
        low = w.lower()
        if (not w or low in _SENIORITY or low in _NOISE or _LEVEL.match(w) or re.search(r"\d", w)
                or (low in nf.LEADERSHIP_CLAIMS and not nf.has_term(base_md, low))):
            continue
        words.append(w)
    def joins_two_skills(i):  # a "/" or "&" survives only between two remaining words, before the role noun
        prev = words[i - 1].lower() if i > 0 else None
        nxt = words[i + 1].lower() if i + 1 < len(words) else None
        return all(x and x not in _SEPARATORS and x not in ROLE_NOUNS for x in (prev, nxt))

    words = [w for i, w in enumerate(words) if w.lower() not in _SEPARATORS or joins_two_skills(i)]

    role = " ".join(_case(w, base_md) for w in words).replace(" / ", "/")
    role = re.sub(r"\s+", " ", role).strip()
    if not _has_noun(role):
        return FALLBACK_ROLE
    if role.lower() in ROLE_NOUNS:  # "Golang Developer" minus the unsupported tech
        return f"Software {role}" if role.lower() in {"developer", "engineer"} else FALLBACK_ROLE
    return role


def _headline_index(lines):
    """Index of the headline (first plain line after '# Name', before any '## ' section), or None."""
    name = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), None)
    if name is None:
        return None, None
    for i in range(name + 1, len(lines)):
        ln = lines[i].strip()
        if lines[i].startswith("#"):
            break
        if not ln:
            continue
        if "@" in ln or "linkedin.com" in ln.lower():
            break  # contact row -- there's no headline
        return name, i
    return name, None


def fixed_header(markdown, base_md):
    """Give `markdown` the master resume's header: its name line and its headline, the
    same on every tailored resume whatever the job (the job role only names the file). Replaces
    whatever name/headline the markdown has -- a model rewrite or an older role headline. Idempotent."""
    base = base_md.replace("\r\n", "\n").split("\n")
    b_name, b_head = _headline_index(base)
    if b_name is None:
        return markdown
    name_line = base[b_name].strip()
    headline = base[b_head].strip() if b_head is not None else None
    lines = markdown.split("\n")
    name, head = _headline_index(lines)
    if name is None:
        return "\n".join([name_line] + ([headline] if headline else []) + [""] + lines)
    if headline and head is not None:
        lines[head] = headline
    elif headline:
        lines.insert(name + 1, headline)
    lines[name] = name_line
    return "\n".join(lines)


def role_of(markdown):
    lines = (markdown or "").split("\n")
    _, head = _headline_index(lines)
    return lines[head].strip() if head is not None else ""


def resume_filename(markdown, ext, fallback_name="Resume", role=""):
    """'Akhil_Dalali_Java_Developer.pdf' -- the name + target role of the resume being exported.
    Never a company or a version number. The header is fixed, so the role comes from the job
    (`role`); without one, a resume saved when headlines were roles still names its file by that
    headline, and a generic "A | B | C" headline adds nothing."""
    name = next((ln[2:].strip() for ln in (markdown or "").splitlines() if ln.startswith("# ")), "") or fallback_name
    name = name.title() if name.isupper() else name   # "AKHIL DALALI" header -> Akhil_Dalali file
    if not role:
        head = role_of(markdown)
        role = head if "|" not in head else ""
    stem = re.sub(r"[^\w+#]+", "_", f"{name} {role}").strip("_")[:120]
    return f"{stem or 'Resume'}.{ext}"


def export_filename(markdown, ext, job_title="", fallback_name="Resume"):
    """The one filename rule for every tailored-resume download and Drive upload."""
    return resume_filename(markdown, ext, fallback_name, target_role(job_title, markdown or "") if job_title else "")
