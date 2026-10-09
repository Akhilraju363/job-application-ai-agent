"""Employment history is copied from the master resume, never from the model.

A model rewrite may only change the *bullets* inside each role. Every role's protected lines --
the "### Employer" heading, the job-title line and the "dates | location" line -- and the order
of the roles are the master resume's. restore() puts them back deterministically after
generation (the model has moved locations onto title lines, rewritten locations and reordered
roles despite the prompt), the same way resume_role.fixed_header() does for the header.

restore() only acts when it is unambiguous: every master employer appears exactly once, and
every non-bullet line in a role block is in its metadata position (between the heading and the
first bullet) or is made only of date ranges and the master's own metadata text. Otherwise it
leaves the text untouched and no_fabrication.check_employment() rejects it, naming the employer
and field. It never adds, removes or rewords a bullet. Pure functions, no I/O.
"""
import re

import no_fabrication as nf

MAX_METADATA_LINES = 3      # heading-to-first-bullet lines treated as the role's title/date lines
MAX_METADATA_CHARS = 160    # longer than any title/date line -- prose, not metadata: don't touch it


def _experience_bounds(lines):
    """(start, end) line indexes of the Experience section body, or None."""
    start = next((i for i, ln in enumerate(lines) if ln.startswith("## ") and ln[3:].strip().lower() == "experience"),
                 None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start + 1, end


def _blocks(body):
    """Experience body -> (preamble lines, [(heading, raw heading line, [lines])])."""
    pre, blocks = [], []
    for ln in body:
        if ln.startswith("### "):
            blocks.append((nf.norm_ws(ln[4:]), ln.rstrip(), []))
        elif blocks:
            blocks[-1][2].append(ln)
        else:
            pre.append(ln)
    return pre, blocks


def master_roles(base_md):
    """[(heading line, [protected metadata lines], [bullet lines])] for each master role, in order."""
    lines = base_md.replace("\r\n", "\n").split("\n")
    bounds = _experience_bounds(lines)
    if bounds is None:
        return []
    return [(raw, [ln.rstrip() for ln in block if ln.strip() and not ln.startswith("- ")],
             [ln.rstrip() for ln in block if ln.startswith("- ")])
            for _, raw, block in _blocks(lines[bounds[0]:bounds[1]])[1]]


def protected_lines(base_md):
    """The master's protected employment lines, in order -- shown verbatim to the model."""
    return "\n\n".join("\n".join([heading, *meta]) for heading, meta, _ in master_roles(base_md))


def _plain(line):
    return nf._dashless(re.sub(r"[*_`]", "", line))


def _metadata_like(line, known_segments):
    """A stray line made only of date ranges and the master's own title/location text."""
    parts = [p.strip() for p in _plain(line).split("|") if p.strip()]
    return bool(parts) and all(nf._YEAR.search(p) or p in known_segments for p in parts)


def restore(markdown, base_md):
    """Return (markdown, notes). On success the Experience section has the master's roles in the
    master's order, each with the master's exact heading, title and date lines followed by the
    bullets the model wrote for that employer; notes name what was put back ([] = nothing).
    When restoring isn't unambiguous, returns (markdown unchanged, None)."""
    roles = master_roles(base_md)
    lines = markdown.replace("\r\n", "\n").split("\n")
    bounds = _experience_bounds(lines)
    if not roles or bounds is None:
        return markdown, None
    pre, blocks = _blocks(lines[bounds[0]:bounds[1]])
    master_heads = [nf.norm_ws(h[4:]) for h, _, _ in roles]
    if sorted(h for h, _, _ in blocks) != sorted(master_heads):
        return markdown, None  # missing, duplicated or unknown employer: nothing safe to restore

    known = {p.strip() for h, meta, _ in roles for ln in (h[4:], *meta) for p in _plain(ln).split("|") if p.strip()}
    by_head, notes = {}, []
    for (heading, _, block), (raw_heading, meta, _) in zip(sorted(blocks, key=lambda b: master_heads.index(b[0])),
                                                           roles):
        content = [ln for ln in block if ln.strip()]
        first_bullet = next((i for i, ln in enumerate(content) if ln.startswith("- ")), len(content))
        zone, rest = content[:first_bullet], content[first_bullet:]
        if len(zone) > MAX_METADATA_LINES or any(len(ln) > MAX_METADATA_CHARS for ln in zone):
            return markdown, None
        stray = [ln for ln in rest if not ln.startswith("- ")]
        if any(not _metadata_like(ln, known) for ln in stray):
            return markdown, None  # unrecognised text after the bullets -- don't guess what it is
        if [ln.rstrip() for ln in zone] != meta or stray:
            notes.append(f"employment details restored for {heading[:50]!r}")
        by_head[heading] = [raw_heading, *meta, *(ln.rstrip() for ln in rest if ln.startswith("- "))]

    if [h for h, _, _ in blocks] != master_heads:
        notes.insert(0, "experience order restored to the master resume's order")
    body = list(pre)
    for h in master_heads:
        if body and body[-1].strip():
            body.append("")
        body += by_head[h]
    body.append("")
    return "\n".join(lines[:bounds[0]] + body + lines[bounds[1]:]), notes
