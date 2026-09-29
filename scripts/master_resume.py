"""The master resume as structured, editable sections -- for the dashboard's Master Resume page.

resume/base_resume.md stays the ONLY source of truth. This module converts it to and from a
structured form the UI edits section by section; the Markdown file is what gets stored, in the
same canonical shape the rest of the pipeline reads (no_fabrication.parse_resume,
validate_resume, resume_role.fixed_header, format_resume_doc):

    # NAME
    Headline
    <blank>
    <public profile line(s), e.g. linkedin.com/in/...>   -- kept as stored, not editable here
    <blank>
    ## Summary
    <paragraph(s)>
    <blank>
    ## Skills
    - Label: item, item, ...
    <blank>
    ## Experience
    <blank>
    ### Employer
    Job title
    Dates | Location
    - bullet
    <blank>
    ## Education
    ### Degree
    Institution | Dates
    <blank>
    ## Certifications
    - certification

Private contact details (email, phone) are never stored here -- they come from
RESUME_CONTACT_LINE at export -- so a value that looks like one is rejected.
Pure parse/serialize/validate plus one atomic write to paths.BASE_RESUME; no other path is ever written.
"""
import os
import re
import tempfile

import no_fabrication as nf
import paths
import resume_role
from validate_resume import validate as validate_sections

SECTIONS = ("Summary", "Skills", "Experience", "Education", "Certifications")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(?<![\w-])\+?\d[\d\s().-]{8,}\d(?![\w-])")
MAX_FIELD = 600
MAX_ITEMS = 60


def looks_like_contact(value):
    """An email, or a run of 10+ digits (a phone number) -- '2015 - 2018' is a date, not a phone."""
    return bool(EMAIL_RE.search(value)) or any(
        len(re.sub(r"\D", "", m.group(0))) >= 10 for m in PHONE_RE.finditer(value))


class MasterFormatError(ValueError):
    """base_resume.md isn't in the canonical shape this editor can round-trip."""


class MasterConflict(RuntimeError):
    """The master changed since the editor loaded it (another tab / a git pull)."""


class ReadOnlyMaster(RuntimeError):
    """This deployment can't persist edits to the master (the hosted dashboard)."""


def read_only_reason():
    """Why the master can't be edited here, or None. On Modal, resume/ is baked into the image:
    a write would land in the container's temporary filesystem, vanish on restart and never
    reach the daily cron -- so the hosted dashboard is view-only."""
    if os.environ.get("MASTER_RESUME_READ_ONLY", "").strip().lower() in ("1", "true", "yes", "on"):
        return "Editing the master resume is disabled on this deployment (MASTER_RESUME_READ_ONLY)."
    if os.environ.get("MODAL_TASK_ID") or os.environ.get("MODAL_IS_REMOTE") == "1":
        return ("The hosted dashboard can't save the master resume: resume/base_resume.md is part of the "
                "deployed app, so an edit here would be lost on restart and never reach the daily run. "
                "Edit it in the local dashboard, commit, and redeploy.")
    return None


# ---------------------------------------------------------------------------
# parse / serialize
# ---------------------------------------------------------------------------

def _split_sections(md):
    header, sections, order, cur = [], {}, [], None
    for ln in md.replace("\r\n", "\n").split("\n"):
        if ln.startswith("## "):
            cur = ln[3:].strip()
            if cur in sections:
                raise MasterFormatError(f"Section '{cur}' appears twice")
            sections[cur], order = [], order + [cur]
        elif cur is None:
            header.append(ln)
        else:
            sections[cur].append(ln)
    unknown = [s for s in order if s not in SECTIONS]
    if unknown:
        raise MasterFormatError(f"Unsupported section(s) in the master resume: {', '.join(unknown)}")
    return header, sections, order


def _paragraphs(lines):
    paras, cur = [], []
    for ln in lines + [""]:
        if ln.strip():
            cur.append(ln.strip())
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    return paras


def _split_pipe(line):
    left, sep, right = line.partition(" | ")
    return (left.strip(), right.strip()) if sep else (line.strip(), "")


def parse(md):
    """Canonical Markdown -> {name, headline, profile_lines, summary, skills, experience, education, certifications}."""
    header, sections, _ = _split_sections(md)
    name_i = next((i for i, l in enumerate(header) if l.startswith("# ")), None)
    if name_i is None:
        raise MasterFormatError("The master resume has no '# Name' line")
    rest = [l.strip() for l in header[name_i + 1:] if l.strip()]
    headline = ""
    if rest and "@" not in rest[0] and "linkedin.com" not in rest[0].lower():
        headline, rest = rest[0], rest[1:]

    skills = []
    for ln in sections.get("Skills", []):
        if not ln.strip():
            continue
        body = ln[2:] if ln.startswith("- ") else ln
        label, sep, items = body.partition(":")
        if not sep:
            raise MasterFormatError(f"Skill line without a 'Label:' prefix: {body[:60]!r}")
        skills.append({"label": label.strip(), "items": nf.norm_ws(items)})

    experience = []
    for ln in sections.get("Experience", []):
        if ln.startswith("### "):
            experience.append({"employer": ln[4:].strip(), "title": "", "dates": "", "location": "", "bullets": []})
        elif not ln.strip():
            continue
        elif not experience:
            raise MasterFormatError("Experience text before the first '### Employer' heading")
        elif ln.startswith("- "):
            experience[-1]["bullets"].append(ln[2:].strip())
        else:
            role = experience[-1]
            if YEAR_RE.search(ln) and not role["dates"]:
                role["dates"], role["location"] = _split_pipe(ln)
            elif not role["title"] and not role["dates"]:
                role["title"] = ln.strip()
            else:
                raise MasterFormatError(f"Unexpected line under {role['employer']!r}: {ln.strip()[:60]!r}")

    education = []
    for ln in sections.get("Education", []):
        if ln.startswith("### "):
            education.append({"degree": ln[4:].strip(), "institution": "", "dates": ""})
        elif ln.strip():
            if not education or education[-1]["institution"]:
                raise MasterFormatError(f"Unexpected education line: {ln.strip()[:60]!r}")
            education[-1]["institution"], education[-1]["dates"] = _split_pipe(ln)

    certifications = [ln[2:].strip() for ln in sections.get("Certifications", []) if ln.startswith("- ")]
    return {"name": header[name_i][2:].strip(), "headline": headline, "profile_lines": rest,
            "summary": "\n\n".join(_paragraphs(sections.get("Summary", []))), "skills": skills,
            "experience": experience, "education": education, "certifications": certifications}


def serialize(r):
    """Structured sections -> the canonical Markdown (see the module docstring)."""
    out = [f"# {r['name']}"] + ([r["headline"]] if r.get("headline") else []) + [""]
    if r.get("profile_lines"):
        out += list(r["profile_lines"]) + [""]
    out += ["## Summary"]
    for i, para in enumerate(p for p in re.split(r"\n\s*\n", r["summary"]) if p.strip()):
        out += ([""] if i else []) + [nf.norm_ws(para)]
    out += ["", "## Skills"] + [f"- {s['label']}: {s['items']}" for s in r["skills"]]
    out += ["", "## Experience", ""]
    for i, e in enumerate(r["experience"]):
        date_line = f"{e['dates']} | {e['location']}" if e.get("location") else e["dates"]
        out += ([""] if i else []) + [f"### {e['employer']}"] + ([e["title"]] if e.get("title") else []) \
            + [date_line] + [f"- {b}" for b in e["bullets"]]
    out += ["", "## Education"]
    for i, ed in enumerate(r["education"]):
        line = f"{ed['institution']} | {ed['dates']}" if ed.get("dates") else ed["institution"]
        out += ([""] if i else []) + [f"### {ed['degree']}"] + ([line] if line else [])
    out += ["", "## Certifications"] + [f"- {c}" for c in r["certifications"]]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _clean(value):
    return nf.norm_ws(value) if isinstance(value, str) else None


def normalize(data):
    """Coerce the client's JSON into the structured shape (strings trimmed, whitespace collapsed).
    Raises ValueError on a malformed shape (wrong types / too many items)."""
    if not isinstance(data, dict):
        raise ValueError("resume must be an object")

    def text(v, multiline=False):
        if not isinstance(v, str):
            raise ValueError("every field must be text")
        if len(v) > (MAX_FIELD * 10 if multiline else MAX_FIELD):
            raise ValueError("a field is too long")
        return v.replace("\r\n", "\n").strip() if multiline else nf.norm_ws(v.replace("\n", " "))

    def items(v, fn):
        if not isinstance(v, list) or len(v) > MAX_ITEMS:
            raise ValueError("a list section is malformed or too long")
        return [fn(x) for x in v]

    def obj(keys, list_keys=()):
        def fn(x):
            if not isinstance(x, dict):
                raise ValueError("a section entry is malformed")
            o = {k: text(x.get(k, "")) for k in keys}
            for k in list_keys:
                o[k] = items(x.get(k, []), text)
            return o
        return fn

    return {"name": text(data.get("name", "")), "headline": text(data.get("headline", "")),
            "summary": text(data.get("summary", ""), multiline=True),
            "skills": items(data.get("skills", []), obj(("label", "items"))),
            "experience": items(data.get("experience", []), obj(("employer", "title", "dates", "location"), ("bullets",))),
            "education": items(data.get("education", []), obj(("degree", "institution", "dates"))),
            "certifications": items(data.get("certifications", []), text)}


def validate(r):
    """Field-level problems: [{section, index, field, message}] -- empty == valid."""
    errs = []

    def err(section, message, index=None, field=None):
        errs.append({"section": section, "index": index, "field": field, "message": message})

    def single(section, value, index=None, field=None, label="This field"):
        value = nf.norm_ws(value)
        if not value:
            err(section, f"{label} cannot be empty.", index, field)
        elif value.startswith(("#", "- ")):
            err(section, f"{label} can't start with '#' or '- ' (that would break the resume structure).", index, field)

    single("header", r["name"], field="name", label="Name")
    single("header", r["headline"], field="headline", label="Headline")
    if not r["summary"].strip():
        err("summary", "Professional summary cannot be empty.", field="summary")
    elif any(l.strip().startswith(("#", "- ")) for l in r["summary"].split("\n")):
        err("summary", "Summary lines can't start with '#' or '- '.", field="summary")

    if not r["skills"]:
        err("skills", "Add at least one skill group.")
    for i, s in enumerate(r["skills"]):
        single("skills", s["label"], i, "label", "Skill group name")
        if ":" in s["label"]:
            err("skills", "Skill group name can't contain ':'.", i, "label")
        if not nf.norm_ws(s["items"]):
            err("skills", "Skill group needs at least one skill.", i, "items")

    if not r["experience"]:
        err("experience", "Add at least one employer.")
    for i, e in enumerate(r["experience"]):
        single("experience", e["employer"], i, "employer", "Employer")
        single("experience", e["title"], i, "title", "Job title")
        if YEAR_RE.search(e["title"]):
            err("experience", "Job title can't contain a year (years belong in the dates).", i, "title")
        if not nf.norm_ws(e["dates"]):
            err("experience", "Experience date is required.", i, "dates")
        elif not YEAR_RE.search(e["dates"]):
            err("experience", "Dates must include a year, e.g. 'Jan 2022 – Jun 2024'.", i, "dates")
        if "|" in e["dates"]:
            err("experience", "Dates can't contain '|'.", i, "dates")
        if not nf.norm_ws(e["location"]):
            err("experience", "Location is required.", i, "location")
        elif "|" in e["location"]:
            err("experience", "Location can't contain '|'.", i, "location")
        if not e["bullets"]:
            err("experience", "Add at least one bullet.", i, "bullets")
        for j, b in enumerate(e["bullets"]):
            if not nf.norm_ws(b):
                err("experience", f"Bullet {j + 1} cannot be empty.", i, f"bullets.{j}")

    for i, ed in enumerate(r["education"]):
        single("education", ed["degree"], i, "degree", "Degree")
        single("education", ed["institution"], i, "institution", "Institution")
        if "|" in ed["institution"]:
            err("education", "Institution can't contain '|'.", i, "institution")
        if ed["dates"] and not YEAR_RE.search(ed["dates"]):
            err("education", "Dates must include a year.", i, "dates")
    for i, c in enumerate(r["certifications"]):
        if not nf.norm_ws(c):
            err("certifications", "Certification cannot be empty.", i)

    for section, value in _all_values(r):
        if looks_like_contact(value):
            err(section, "Contact details (email/phone) can't be stored in the public master resume -- "
                         "they come from RESUME_CONTACT_LINE at export.")
            break
    return errs


def _all_values(r):
    yield "header", r["name"]
    yield "header", r["headline"]
    yield "summary", r["summary"]
    for s in r["skills"]:
        yield "skills", f"{s['label']} {s['items']}"
    for e in r["experience"]:
        yield "experience", " ".join([e["employer"], e["title"], e["dates"], e["location"], *e["bullets"]])
    for ed in r["education"]:
        yield "education", " ".join(ed.values())
    for c in r["certifications"]:
        yield "certifications", c


def check_markdown(md, r):
    """The serialized master must read back exactly as the pipeline will read it."""
    ok, reason = validate_sections(md)
    if not ok:
        return [f"Invalid master resume structure: {reason}"]
    problems = []
    roles = nf.parse_resume(md)["roles"]
    want = [(e["employer"], e["title"], nf.norm_ws(f"{e['dates']} | {e['location']}")) for e in r["experience"]]
    if [(x["heading"], x["title"], x["date"]) for x in roles] != want:
        problems.append("Invalid master resume structure: the experience entries don't read back cleanly.")
    if resume_role.fixed_header(md, md) != md:
        problems.append("Invalid master resume structure: the header doesn't read back cleanly.")
    if parse(md) != {**r, "profile_lines": parse(md)["profile_lines"]}:
        problems.append("Invalid master resume structure: the sections don't read back cleanly.")
    return problems


# ---------------------------------------------------------------------------
# read / save
# ---------------------------------------------------------------------------

def load():
    """{resume, markdown, version, updated_at} for the stored master."""
    md = paths.read_base_resume()
    return {"resume": parse(md), "markdown": md, "version": paths.master_resume_version(md),
            "updated_at": os.path.getmtime(paths.BASE_RESUME)}


def save(data, expected_version):
    """Validate + persist an edited master. Returns {version, previous_version, changed, markdown}.
    Raises ValueError (malformed), InvalidMaster (field errors), MasterConflict, ReadOnlyMaster."""
    reason = read_only_reason()
    if reason:
        raise ReadOnlyMaster(reason)
    current = paths.read_base_resume()
    current_version = paths.master_resume_version(current)
    if expected_version != current_version:
        raise MasterConflict("The master resume changed since you opened it. Reload to see the latest version.")
    r = normalize(data)
    errors = validate(r)
    r["profile_lines"] = parse(current)["profile_lines"]   # the public profile line is kept as stored
    md = serialize(r)
    if not errors:
        errors = [{"section": "structure", "index": None, "field": None, "message": m} for m in check_markdown(md, r)]
    if errors:
        raise InvalidMaster(errors)
    if md == current.replace("\r\n", "\n"):
        return {"version": current_version, "previous_version": current_version, "changed": False, "markdown": current}
    newline = "\r\n" if "\r\n" in current else "\n"
    _atomic_write(md.replace("\n", newline))
    saved = paths.read_base_resume()
    return {"version": paths.master_resume_version(saved), "previous_version": current_version,
            "changed": True, "markdown": saved}


class InvalidMaster(ValueError):
    def __init__(self, errors):
        super().__init__(errors[0]["message"] if errors else "Invalid master resume")
        self.errors = errors


def _atomic_write(text):
    target = paths.BASE_RESUME   # the one file this module ever writes; never a client-supplied path
    fd, tmp = tempfile.mkstemp(prefix=".base_resume.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
