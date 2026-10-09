"""Evidence-grounded tailoring: the model rewords individual master-resume bullets, code builds the resume.

A small local model asked to regenerate a whole resume pulls job-description skills into it, moves
bullets between employers and drops roles -- and one such claim anywhere rejects the whole document,
losing every safe change with it. Here the model never sees the document to rewrite:

    master bullets --(stable ids E<employer>.B<bullet>)--> one JSON call: {"bullets": [{id, text}]}
      --> each rewrite checked against ITS OWN source bullet (check_rewrite) --> accepted or original kept
      --> master with accepted rewrites, bullets/skills re-ordered for the job (deterministic)

A rewrite is accepted only if it adds nothing the source bullet doesn't already say: no technology,
skill or job-description term the source lacks (a job requirement is not evidence of experience), no
technology dropped, no new number, no new leadership/ownership/impact/scale word, no goal turned
into an achieved result ("to improve X" -> "improved X"), not merely the source with words cut out
(deleting a detail is not an improvement), and most of its words come from the source
(and most of the source's words are kept). Anything unclear keeps the
original. Header, Summary, employment lines, Education and Certifications are the master's. The
assembled resume still goes through the full no-fabrication check (tailoring_service.verify).
"""
import json
import re

import no_fabrication as nf

BULLET_PROMPT = """You rewrite existing resume bullets so each one reads better for one job application.

Each bullet below is a verified fact about the candidate. For each bullet, write a restructured
version of the sentence that says exactly the same thing:
- put first what matters most for this job: a technology the bullet names or the work it describes
- restructure, don't just swap words: change the order of the parts, turn wordy phrases into
  direct verbs ("Performed debugging and profiling" -> "Debugged and profiled"), merge clauses
- keep every detail: every technology, every descriptive word (e.g. "scalable", "secure"), every
  responsibility and every purpose -- never delete a detail to shorten the sentence
- reuse the bullet's own words; the job is only a guide to what to put first, never evidence:
  never add a technology, tool, skill, API, platform or method the bullet does not name, and
  never add numbers, metrics, outcomes, achievements, scale, users or business impact
- never add or upgrade responsibility: no leading, owning, managing, mentoring, architecting,
  driving or designing unless the bullet already says so
- keep a goal a goal: "to improve X" must not become "improved X" or "improving X"
- one sentence, plain text, no markdown, about the same length
Return a bullet unchanged only if it is unrelated to this job, or if every restructured version
would change its facts.

Examples (not the candidate's bullets):
[X.B1] Was responsible for writing REST endpoints in Node.js for the billing module, ensuring proper input validation.
-> {"id": "X.B1", "text": "Wrote Node.js REST endpoints for the billing module, ensuring proper input validation."}
[X.B2] Worked on the reporting service, writing SQL queries and building secure PDF exports to support finance users.
-> {"id": "X.B2", "text": "Wrote SQL queries and built secure PDF exports for the reporting service to support finance users."}
Not acceptable: "Created REST endpoints in Node.js ..." (one word swapped), "Wrote REST endpoints for billing."
(details deleted), "Led Node.js and GraphQL billing APIs, cutting errors by 30%" (adds leadership, a
technology and a metric).

JOB TITLE: __TITLE__
WHAT THIS JOB VALUES THAT THE CANDIDATE HAS: __MATCHED__

BULLETS:
__BULLETS__

Now restructure each job-related bullet above -- do not copy it word for word, keep every fact, add
nothing. Respond with ONLY JSON in this exact shape, one entry for every id above, ids unchanged:
{"bullets": [{"id": "E1.B1", "text": "..."}]}
"""

MIN_PRECISION = nf.MIN_BULLET_OVERLAP  # share of the rewrite's words taken from the source bullet
MIN_RECALL = 0.6                       # share of the source bullet's words the rewrite keeps
MAX_GROWTH = 1.4                       # rewrite length vs source (chars), beyond a small allowance

# Word stems that turn a bullet into a bigger claim: leadership, ownership, impact, scale. A rewrite
# may use one only if its source bullet already does.
CLAIM_STEMS = (
    "lead", "led", "own", "manag", "mentor", "coach", "train", "supervis", "oversaw", "oversee", "head",
    "direct", "architect", "spearhead", "champion", "drove", "drive", "driv", "pioneer", "found",
    "establish", "initiat", "sole", "single-handed", "independent", "end-to-end", "team", "stakeholder",
    "increas", "reduc", "boost", "accelerat", "sav", "cut", "doubl", "tripl", "grew", "grow", "maximi",
    "minimi", "eliminat", "significant", "substantial", "dramatic", "award", "recogni", "revenue",
    "cost", "million", "thousand", "billion", "customer", "user", "enterprise-wide", "company-wide",
    "organization-wide", "large-scale", "high-traffic", "high-volume", "mission-critical", "critical",
)


# Benefit verbs. "to improve X" states a goal; "improved X" / "improving X" claims it happened. A
# rewrite may state a benefit as a result only if its source bullet already does (any of these verbs
# in a non-"to" form); otherwise it must keep the "to <verb>" form.
OUTCOME_STEMS = ("improv", "enhanc", "ensur")
_OUTCOME_RE = re.compile(r"(?<![a-z0-9])(to\s+)?(?:" + "|".join(OUTCOME_STEMS) + r")[a-z]*")


def _states_result(text):
    """True if a benefit verb is used as an achieved result rather than a "to ..." goal."""
    return any(not m.group(1) for m in _OUTCOME_RE.finditer(text.lower()))


def _words(text):
    """The bullet's words in order, ignoring case and punctuation."""
    return re.findall(r"[a-z0-9+#]+(?:[./-][a-z0-9+#]+)*", text.lower())


def _only_deletes(original, rewrite):
    """True if the rewrite is the source with content words cut out -- nothing reworded or reordered
    (connectives like "and"/"using" are ignored: cutting a list item often moves an "and")."""
    src, new = ([w for w in _words(t) if w not in nf._STOP] for t in (original, rewrite))
    rest = iter(src)
    return len(new) < len(src) and all(w in rest for w in new)


def _call(prompt, label):
    from llm import call_llm  # lazy, like tailoring_service: llm reads its provider config at import
    return call_llm(prompt, label, json_mode=True)


def source_bullets(base_md):
    """[{id, employer, text}] for every Experience bullet of the master, in master order. Ids are
    positional (E<employer>.B<bullet>, 1-based), stable for a given master version."""
    out = []
    for i, role in enumerate(nf.parse_resume(base_md)["roles"], 1):
        for j, text in enumerate(role["bullets"], 1):
            out.append({"id": f"E{i}.B{j}", "employer": role["heading"], "text": text})
    return out


def _claims(text):
    low = text.lower()
    return {s for s in CLAIM_STEMS if re.search(r"(?<![a-z0-9])" + re.escape(s), low)}


def check_rewrite(original, rewrite, universe):
    """Reasons a rewrite of `original` is not supported by it ([] = accepted). `universe` is every
    term that counts as a skill/technology claim (tech vocabulary, master skills, JD terms)."""
    if not isinstance(rewrite, str) or not rewrite.strip():
        return ["empty rewrite"]
    reasons = []
    if _only_deletes(original, rewrite):
        reasons.append("only deletes words from the source bullet (details dropped, nothing restructured)")
    if "\n" in rewrite.strip() or re.search(r"[*`|]|^\s*[-•#]", rewrite):
        reasons.append("not a single plain-text line")
    if len(rewrite) > max(len(original) * MAX_GROWTH, len(original) + 40):
        reasons.append("much longer than the source bullet")
    added = sorted(nf.terms_in(rewrite, universe) - nf.terms_in(original, universe))
    if added:
        reasons.append(f"terms not in the source bullet: {', '.join(added)}")
    dropped = sorted(nf.terms_in(original, nf.TECH_VOCAB) - nf.terms_in(rewrite, nf.TECH_VOCAB))
    if dropped:
        reasons.append(f"technologies dropped from the source bullet: {', '.join(dropped)}")
    numbers = sorted(nf._numbers(rewrite) - nf._numbers(original))
    if numbers:
        reasons.append(f"numbers not in the source bullet: {', '.join(numbers)}")
    claims = sorted(_claims(rewrite) - _claims(original)
                    | (nf.terms_in(rewrite, nf.LEADERSHIP_CLAIMS) - nf.terms_in(original, nf.LEADERSHIP_CLAIMS)))
    if claims:
        reasons.append(f"responsibility/impact words not in the source bullet: {', '.join(claims)}")
    if _states_result(rewrite) and not _states_result(original):
        reasons.append("states a goal of the source bullet as an achieved result")
    src, new = nf._tokens(original), nf._tokens(rewrite)
    if new and len(new & src) / len(new) < MIN_PRECISION:
        reasons.append("too many words not in the source bullet")
    if src and len(new & src) / len(src) < MIN_RECALL:
        reasons.append("drops too much of the source bullet's meaning")
    return reasons


def _universe(base_md, analysis, match):
    jd = [t for k in ("required_skills", "preferred_skills", "programming_languages", "frameworks", "databases",
                      "cloud", "tools", "technologies", "keywords") for t in (analysis.get(k) or [])]
    return (set(nf.TECH_VOCAB) | nf.parse_resume(base_md)["skill_keys"]
            | {t.lower() for t in jd + list(match.get("missing_skills") or []) if t and len(t) > 1})


def _matched_terms(match):
    s = match["skills"]
    return list(dict.fromkeys(s["required_matched"] + s["preferred_matched"] + s["technologies_matched"]))


def build_prompt(job, bullets, matched):
    lines = []
    for b in bullets:
        hits = [t for t in matched if nf.has_term(b["text"], t)]
        lines.append(f"[{b['id']}] {b['text']}" + (f"  (matches the job: {', '.join(hits)})" if hits else ""))
    return (BULLET_PROMPT.replace("__TITLE__", str(job.get("title")))
            .replace("__MATCHED__", ", ".join(matched) or "(none)")
            .replace("__BULLETS__", "\n".join(lines)))


def _parse(raw):
    """{id: text} from the model's JSON; anything malformed is simply absent."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    items = data.get("bullets") if isinstance(data, dict) else data
    out = {}
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and isinstance(it.get("id"), str) and isinstance(it.get("text"), str):
            out.setdefault(it["id"].strip(), it["text"].strip().lstrip("-• ").strip())
    return out


def apply(base_md, accepted):
    """The master with the accepted rewrites ({id: text}) put in place of their source bullets.
    Positional: a rewrite can only land on its own bullet under its own employer."""
    lines, out, section, emp, n = base_md.split("\n"), [], "", 0, 0
    for ln in lines:
        if ln.startswith("## "):
            section = ln[3:].strip().lower()
        elif section == "experience" and ln.startswith("### "):
            emp, n = emp + 1, 0
        elif section == "experience" and emp and ln.startswith("- "):
            n += 1
            text = accepted.get(f"E{emp}.B{n}")
            if text is not None:
                ln = "- " + text
        out.append(ln)
    return "\n".join(out)


def tailor(job, base_md, analysis, match):
    """Returns (markdown with accepted rewrites in master order, report). The caller re-orders and
    verifies. LLM errors propagate unchanged (same as the full-rewrite path); a malformed response
    just means no rewrites. The report has counts plus, per bullet, id/employer/status/original/text/
    reasons -- stored with the version, never logged."""
    bullets = source_bullets(base_md)
    raw = _call(build_prompt(job, bullets, _matched_terms(match)), job.get("title"))
    proposed, universe = _parse(raw), _universe(base_md, analysis, match)
    accepted, rows = {}, []
    for b in bullets:
        text = proposed.get(b["id"])
        if text is None:
            status, reasons = "missing", ["no rewrite returned for this bullet"]
        elif _words(text) == _words(b["text"]):  # identical or punctuation-only: keep the original
            status, reasons = "unchanged", []
        else:
            reasons = check_rewrite(b["text"], text, universe)
            status = "rejected" if reasons else "rewritten"
            if not reasons:
                accepted[b["id"]] = nf.norm_ws(text)
        rows.append({"id": b["id"], "employer": b["employer"], "status": status, "original": b["text"],
                     "text": accepted.get(b["id"], b["text"]),
                     **({"proposed": text, "reasons": reasons} if status == "rejected" else {})})
    count = {k: sum(r["status"] == k for r in rows) for k in ("rewritten", "rejected", "unchanged", "missing")}
    unknown = sorted(set(proposed) - {b["id"] for b in bullets})
    return apply(base_md, accepted), {"strategy": "bullets", **count, "unknown_ids": unknown[:20], "bullets": rows}
