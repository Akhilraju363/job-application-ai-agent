"""The single resume-tailoring service.

Both dashboard entry points funnel through `tailor()`:

    manual JD  ----\\
                     >--> tailor() --> analyze -> match -> generate -> validate -> persist
    scraped job ---/

It reuses, rather than reimplements, the existing pieces: tailor_job.tailor_text (the
pipeline's prompt), validate_resume.validate (sections + placeholders), llm.call_llm (the
provider chain / Ollama), and adds the no-fabrication and ATS checks.

Hard rule enforcement: a generated resume that fails verification is retried once with the
violations fed back to the model. If it still fails, the service falls back to a
reorder-only version of the master resume (`conservative_resume`), which cannot add a claim,
and labels it as such. Only a version that passes verification can be exported or logged to
the tracker; a user edit that fails verification is stored but stays blocked until fixed.
"""
import hashlib
import re

import activity
import jd_analysis
import no_fabrication as nf
import paths
import resume_store
from job_links import canonical_link
from validate_resume import validate

STAGES = [
    ("received", "Job description received"),
    ("analyze", "Analyzing job description"),
    ("match", "Matching master resume"),
    ("generate", "Generating tailored resume"),
    ("validate", "ATS + fact validation"),
    ("prepare", "Preparing downloads"),
]
MAX_ATTEMPTS = 2


def _strip_fences(text):
    t = text.strip()
    m = re.match(r"^```[a-zA-Z]*\n(.*?)\n```$", t, re.S)
    return (m.group(1) if m else t).strip() + "\n"


def ats_checks(markdown, analysis=None, match=None):
    """Structural ATS checks. 'fail' blocks; 'warn' is advisory."""
    checks = []

    def add(name, status, detail=""):
        checks.append({"name": name, "status": status, "detail": detail})

    ok, reason = validate(markdown)
    add("Required sections (Summary, Skills, Experience) and no placeholder text",
        "pass" if ok else "fail", reason or "")
    lower = markdown.lower()
    add("Education section present", "pass" if "## education" in lower else "warn")
    add("Contact email present", "pass" if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", markdown) else "warn")
    add("Plain single-column text (no tables, images, HTML, code fences)",
        "fail" if re.search(r"^\s*\|.*\|\s*$|!\[|</?\w+[^>]*>|^```", markdown, re.M) else "pass")
    words = len(markdown.split())
    add("Length (about two pages or less)", "pass" if words <= 900 else "warn", f"{words} words")
    if match is not None and match["keywords"]["tailored_pct"] is not None:
        pct = match["keywords"]["tailored_pct"]
        add("JD keyword coverage", "pass" if pct >= 60 else "warn", f"{pct}% of JD keywords appear")
    return {"ok": not any(c["status"] == "fail" for c in checks), "checks": checks}


ATS_DISCLAIMER = ("A transparent heuristic over the measurable criteria below. It is not a "
                  "prediction of, or guarantee about, any employer's ATS.")
ATS_WEIGHTS = (("Required-skill coverage", 30), ("Keyword coverage", 25), ("Job-title relevance", 10),
               ("Structure", 15), ("Formatting", 10), ("Integrity (no unsupported claims)", 10))
_TITLE_STOP = frozenset("a an and or of the for to in at i ii iii iv sr jr senior junior lead principal "
                        "staff associate intern trainee".split())
DUPLICATE_MARGIN = 3  # a keyword is 'stuffed' if tailoring made it appear more than this many times MORE than in
# the master resume (an honest resume already repeats its core stack, so a fixed cap would nag).
DUPLICATE_MIN = 6


def _title_relevance(title, markdown):
    toks = [t.strip(".") for t in re.findall(r"[a-z0-9+#.]+", (title or "").lower())]
    toks = [t for t in toks if t and t not in _TITLE_STOP]
    if not toks:
        return None, []
    head = "\n".join(l for l in markdown.splitlines() if not l.startswith("- "))  # tagline, summary, role headings
    missing = [t for t in toks if not jd_analysis._word_in(head.lower(), t)]
    return round(100 * (len(toks) - len(missing)) / len(toks)), missing


def ats_validation(markdown, base_md, analysis, match, title="", checks=None, unsupported=()):
    """Measurable ATS report: weighted components, missing + duplicate keywords, issues and
    warnings. Every component is computed from the resume text, so the score is reproducible."""
    checks = checks or []
    lower = markdown.lower()
    universe = jd_analysis._keyword_universe(analysis) if analysis else []
    required = analysis["required_skills"] if analysis else []

    req_cov = jd_analysis._pct(sum(jd_analysis.term_present(t, markdown) for t in required), len(required))
    kw_cov = jd_analysis._pct(sum(jd_analysis.term_present(t, markdown) for t in universe), len(universe))
    title_pct, title_missing = _title_relevance(title, markdown)

    sections = [f"## {n}" in markdown for n in ("Summary", "Skills", "Experience", "Education")]
    email = bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", markdown))
    structure = round(100 * (sum(sections) + email) / 5)
    formatting = 100
    if re.search(r"^\s*\|.*\|\s*$|!\[|</?\w+[^>]*>|^```", markdown, re.M):
        formatting -= 50
    if len(markdown.split()) > 900:
        formatting -= 25
    if re.search(r"[\u2022\u25cf\u25aa\u25e6]", markdown):
        formatting -= 25
    integrity = max(0, 100 - 25 * len(unsupported))

    values = [req_cov, kw_cov, title_pct, structure, max(0, formatting), integrity]
    used = [(w, v) for (_, w), v in zip(ATS_WEIGHTS, values) if v is not None]
    score = round(sum(w * v for w, v in used) / sum(w for w, _ in used)) if used else 0
    components = [{"name": n, "weight": w, "score": v} for (n, w), v in zip(ATS_WEIGHTS, values)]

    def count(text, t):
        return len(nf._term_re(t.lower()).findall(text.lower()))

    duplicates = sorted((t for t in universe if count(markdown, t) > max(DUPLICATE_MIN, count(base_md, t) + DUPLICATE_MARGIN)),
                        key=str.lower)
    missing_kw = [t for t in universe if not jd_analysis.term_present(t, markdown)]
    supported_missing = [t for t in missing_kw if jd_analysis.term_present(t, base_md)]

    issues = [f"{c['name']}: {c['detail']}" if c["detail"] else c["name"] for c in checks if c["status"] == "fail"]
    issues += [f"Unsupported claim: {u}" for u in unsupported]
    warnings = [f"{c['name']}" + (f" ({c['detail']})" if c["detail"] else "") for c in checks if c["status"] == "warn"]
    warnings += [f"Keyword repeated far more than in your master resume ({count(markdown, t)} vs {count(base_md, t)}): {t}"
                 for t in duplicates]
    if supported_missing:
        warnings.append("In your master resume but not surfaced in this version: " + ", ".join(supported_missing))
    if title_missing:
        warnings.append("Job-title words not reflected in the headline/summary/role titles: " + ", ".join(title_missing))
    unverified = [t for t in missing_kw if t not in supported_missing]
    return {
        "ok": not any(c["status"] == "fail" for c in checks), "checks": checks, "score": score,
        "components": components, "keyword_coverage": kw_cov, "required_skill_coverage": req_cov,
        "missing_keywords": missing_kw, "not_in_master_resume": unverified, "duplicate_keywords": duplicates,
        "issues": issues, "warnings": warnings, "disclaimer": ATS_DISCLAIMER,
    }


def verify(markdown, base_md, jd_terms=(), analysis=None, match=None, title=""):
    """Full validation of one resume text -> {ok, problems, warnings, ats}. problems block
    export; warnings are shown but don't."""
    checks = ats_checks(markdown, analysis, match)["checks"]
    unsupported = nf.check_no_fabrication(base_md, markdown, jd_terms)
    problems = [f"{c['name']}: {c['detail']}" if c["detail"] else c["name"] for c in checks if c["status"] == "fail"]
    problems += unsupported
    placeholder = jd_analysis.COMPANY_NOT_SPECIFIED
    if placeholder.lower() in markdown.lower() and placeholder.lower() not in base_md.lower():
        problems.append(f'"{placeholder}" is a tracker placeholder, not an employer -- remove it from the resume')
    ats = ats_validation(markdown, base_md, analysis, match, title, checks, unsupported)
    warnings = [f"Omitted from this version: {r}" for r in nf.omitted_roles(base_md, markdown)]
    return {"ok": not problems, "problems": problems, "warnings": warnings, "ats": ats}


def conservative_resume(base_md, analysis, match):
    """Fact-safe fallback: the master resume with only its own Skills items and Experience
    bullets re-ordered so JD-relevant ones come first. No wording changes, so it cannot add
    a claim -- used when the model's rewrite keeps failing verification."""
    terms = (match["skills"]["required_matched"] + match["skills"]["preferred_matched"]
             + match["skills"]["technologies_matched"]
             + [k for k in analysis["keywords"] if jd_analysis.term_present(k, base_md)])

    def hits(text):
        return sum(1 for t in terms if jd_analysis.term_present(t, text, strict=True))

    out, section, bullets = [], "", []

    def flush():
        out.extend(sorted(bullets, key=lambda b: -hits(b)))  # stable: ties keep master order
        bullets.clear()

    for line in base_md.splitlines():
        if line.startswith("## "):
            flush()
            section = line[3:].strip().lower()
        elif line.startswith("### "):
            flush()
        if section == "experience" and line.startswith("- "):
            bullets.append(line)
            continue
        flush()
        if section == "skills" and line.startswith("- ") and ":" in line:
            head, items = line.split(":", 1)
            ordered = sorted(nf.split_items(items), key=lambda i: 0 if hits(i) else 1)
            line = f"{head}: {', '.join(ordered)}"
        out.append(line)
    flush()
    return "\n".join(out).strip() + "\n"


def _dedupe_key(job, master_version):
    jd = hashlib.sha1(job["description"].encode("utf-8")).hexdigest()
    return hashlib.sha1(f"{canonical_link(job['link'])}|{jd}|{master_version}".encode("utf-8")).hexdigest()


def tailor(job, *, source, resume_id=None, on_stage=None, base_md=None, job_key=None, reuse=True, user_id=None):
    """Generate (or regenerate, when resume_id is given) a tailored resume for one job.

    `job` is {title, company, link, description[, source, pipeline_score]}; already validated
    via jd_analysis.normalize_job. Returns the stored resume record (with markdown; `reused` is
    True when an identical job + JD + master-resume version already had a verified resume and no
    LLM call was made). `on_stage(key)` is called as each real stage begins.
    """
    import tailor_job  # the pipeline's prompt + PDF export; imported lazily (heavy, needs .env)
    from llm import PROVIDER_SUMMARY

    stage = on_stage or (lambda key: None)
    base_md = base_md or paths.read_base_resume()
    master_version = paths.master_resume_version(base_md)
    dedupe_key = _dedupe_key(job, master_version)

    if reuse and not resume_id:
        existing = resume_store.find_reusable(dedupe_key)
        if existing:
            stage("prepare")
            rec = resume_store.get(existing["id"])
            rec["reused"] = True
            return rec

    stage("analyze")
    analysis = jd_analysis.analyze_jd(job)

    stage("match")
    match = jd_analysis.compute_match(analysis, base_md)
    if match["overall"] is None:
        raise jd_analysis.InputError("Could not compute a match from this job description")
    tailor_input = {**job, "company": jd_analysis.real_company(job),  # placeholder is metadata, not an employer
                    "matched_must_haves": match["skills"]["required_matched"] + match["skills"]["technologies_matched"],
                    "missing_must_haves": match["missing_skills"]}

    def check(md, m):
        return verify(md, base_md, jd_terms=m["missing_skills"], analysis=analysis, match=m, title=job["title"])

    feedback, markdown, verdict, llm_problems, kind_override = "", "", None, [], None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        stage("generate")
        markdown = _strip_fences(tailor_job.tailor_text(tailor_input, base_md, feedback=feedback))
        stage("validate")
        match = jd_analysis.compute_match(analysis, base_md, markdown)
        verdict = check(markdown, match)
        if verdict["ok"]:
            break
        llm_problems = verdict["problems"]
        feedback = "- " + "\n- ".join(verdict["problems"])
    else:
        # Both model rewrites failed fact-checking. Fall back to a reorder-only version of the
        # master resume rather than leaving the user with nothing (or an unverified rewrite).
        markdown = conservative_resume(base_md, analysis, match)
        match = jd_analysis.compute_match(analysis, base_md, markdown)
        verdict = check(markdown, match)
        kind_override = "conservative"

    stage("prepare")
    validation = {**verdict, "attempts": attempt, "llm_problems": llm_problems if kind_override else []}
    if kind_override:
        validation["notice"] = (f"The model's rewrite failed fact-checking after {attempt} attempts "
                                "(it added claims your master resume doesn't support), so this version "
                                "only re-orders your existing skills and bullets. Nothing was reworded.")
    if resume_id and resume_store.exists(resume_id):
        resume_store.update_meta(resume_id, job={**job, "job_key": job_key}, analysis=analysis,
                                 master_version=master_version)
        rid, kind = resume_id, kind_override or "regenerated"
    else:
        rid = resume_store.create(source=source, job={**job, "job_key": job_key}, analysis=analysis,
                                  match=match, provider=PROVIDER_SUMMARY, dedupe_key=dedupe_key,
                                  master_version=master_version, user_id=user_id)
        kind = kind_override or "generated"
    resume_store.add_version(rid, markdown, kind, validation, match=match, analysis=analysis)

    label = f"{job['company']} - {job['title']}"
    activity.log_event("resume_tailored" if verdict["ok"] else "resume_blocked",
                       (f"Resume tailored for {label}" if verdict["ok"]
                        else f"Resume for {label} failed verification"),
                       company=job["company"], title=job["title"], link=job["link"] or None,
                       resume_id=rid, source=source)
    return resume_store.get(rid)


def tailor_resume(job_description, job_title=None, company=None, job_url=None, source=None, job_id=None,
                  user_id=None, *, pipeline_score=None, resume_id=None, on_stage=None, base_md=None, reuse=True):
    """THE entry point. A pasted JD (job_id=None) and a scraped job (job_id=<key>) both call this,
    so there is exactly one implementation of: validate input -> analyze JD -> match master
    resume -> tailor -> ATS/fact validation -> store. Returns to_result(record)."""
    stage = on_stage or (lambda key: None)
    stage("received")
    base_md = base_md or paths.read_base_resume()  # MasterResumeError before any LLM spend
    job = jd_analysis.normalize_job(job_title, company, job_url, job_description)
    job["source"] = jd_analysis._one_line(source, "Job source") or ("LinkedIn" if job_id else "Other")
    if not job["link"]:  # tracker rows are keyed by link -- give a JD with no URL a stable synthetic one
        seed = f"{job['title']}|{job['company']}|{job['description']}"
        job["link"] = "manual:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    if pipeline_score is not None:
        job["pipeline_score"] = pipeline_score
    rec = tailor(job, source="scraped" if job_id else "manual", resume_id=resume_id, on_stage=stage,
                 base_md=base_md, job_key=job_id, reuse=reuse, user_id=user_id)
    return to_result(rec)


def to_result(rec, version=None):
    """The structured response shape (job / jd_analysis / match_analysis / resume /
    ats_validation) built from a stored record -- the UI never has to parse generated text."""
    versions = rec["versions"]
    v = next((x for x in versions if x["n"] == version), versions[-1])
    a, m, val, job = rec["analysis"], rec["match"], v["validation"], rec["job"]
    ats = dict(val["ats"])
    ats.pop("ok", None)

    def url(fmt):
        return (f"/api/resumes/{rec['id']}/download?format={fmt}&version={v['n']}"
                if v.get("exports", {}).get(fmt) else None)

    matching = jd_analysis._clean_list(m["skills"]["required_matched"] + m["skills"]["preferred_matched"]
                                       + m["skills"]["technologies_matched"], 60)
    return {
        "status": "completed" if val["ok"] else "blocked",
        "reused": bool(rec.get("reused")),
        "job": {"title": job["title"], "company": job["company"], "job_id": job.get("job_key"),
                "url": None if str(job["link"]).startswith("manual:") else job["link"],
                "source": job.get("source"), "pipeline_score": job.get("pipeline_score")},
        "jd_analysis": {k: a.get(k) for k in (
            "required_skills", "preferred_skills", "programming_languages", "frameworks", "databases", "cloud",
            "tools", "technologies", "responsibilities", "experience_requirements", "experience_years_min",
            "domain", "keywords", "education", "certifications")},
        "match_analysis": {
            "overall_match": m["overall"], "skills_match": m["skills"]["pct"],
            "experience_match": m["experience"]["pct"],
            "keyword_match": m["keywords"]["tailored_pct"] if m["keywords"]["tailored_pct"] is not None
            else m["keywords"]["base_pct"],
            "keyword_match_before_tailoring": m["keywords"]["base_pct"],
            "matching_skills": matching, "missing_skills": m["missing_skills"],
            "relevant_experience": m["relevant_experience"], "relevant_projects": m["relevant_projects"],
            "relevant_responsibilities": m.get("relevant_responsibilities", []),
            "relevant_achievements": m.get("relevant_achievements", []),
            "notes": [n for n in (m.get("projects_note"), m.get("achievements_note")) if n]},
        "resume": {"id": rec["id"], "version": v["n"], "versions": len(versions), "kind": v["kind"],
                   "content": v.get("markdown", ""), "master_resume_version": rec.get("master_version"),
                   "pdf_url": url("pdf"), "docx_url": url("docx")},
        "ats_validation": {k: ats.get(k) for k in (
            "score", "keyword_coverage", "required_skill_coverage", "missing_keywords", "not_in_master_resume",
            "duplicate_keywords", "issues", "warnings", "components", "checks", "disclaimer")},
        "verification": {"ok": val["ok"], "problems": val["problems"], "warnings": val.get("warnings", []),
                         "notice": val.get("notice"), "attempts": val.get("attempts")},
    }


def revalidate_edit(rid, markdown, base_md=None):
    """Store a user edit as a new version, re-running the same verification."""
    base_md = base_md or paths.read_base_resume()
    record = resume_store.get(rid, with_markdown=False)
    markdown = markdown.strip() + "\n"
    if len(markdown) > 60_000:
        raise jd_analysis.InputError("Resume is too long")
    match = jd_analysis.compute_match(record["analysis"], base_md, markdown)
    verdict = verify(markdown, base_md, jd_terms=match["missing_skills"], analysis=record["analysis"],
                     match=match, title=record["job"]["title"])
    resume_store.add_version(rid, markdown, "edited", {**verdict, "attempts": 0}, match=match)
    return resume_store.get(rid)
