"""Private contact details, added to a resume only when it is exported.

resume/base_resume.md lives in a public repo, so it carries no email or location. The
contact line comes from RESUME_CONTACT_LINE (.env locally, the Modal secrets when hosted),
e.g. "name@example.com | Bengaluru, India". It is added after the no-fabrication check --
it is not tailored content, and the checker would read digits in an email as invented
numbers. Unset == resumes export exactly as stored.
"""
import os
import re

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
MISSING_MESSAGE = ("RESUME_CONTACT_LINE is not set, so exported resumes would have no email or location. "
                   "Add it to .env (local) or the job-apply-agent-secrets Modal secret, then restart the dashboard.")


class ContactConfigError(RuntimeError):
    pass


LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/[^\s|]*", re.I)
# Dash look-alikes (non-breaking/figure/en/em dash, minus) that turn a LinkedIn slug into a
# different, broken URL when pasted from a doc; the soft hyphen is invisible and just dropped.
_DASHES = {**dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d"), "-"),
           ord("\u00ad"): None}


def _clean_linkedin(text):
    return LINKEDIN_RE.sub(lambda m: m.group(0).translate(_DASHES), text)


def contact_line():
    return _clean_linkedin(" ".join(os.environ.get("RESUME_CONTACT_LINE", "").split()))


def require_contact_line():
    """The contact line, or a clear configuration error -- never a silent resume without one."""
    line = contact_line()
    if not line:
        raise ContactConfigError(MISSING_MESSAGE)
    return line


def with_contact(markdown):
    """Put the contact line in the resume's header block (before the first "## " section).
    It is merged with an existing LinkedIn line so the header stays a single contact row;
    otherwise it goes in as its own line under the name/headline. Idempotent.
    Exactly one LinkedIn URL, and always the resume's own (the base resume's canonical public
    URL): a LinkedIn segment in RESUME_CONTACT_LINE only fixes where it sits in the row, e.g.
    "phone | email | linkedin | location"; without one the URL goes last."""
    line = contact_line()
    if not line or line in markdown:
        return markdown
    lines = markdown.split("\n")
    end = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), len(lines))
    for i in range(end):
        m = LINKEDIN_RE.search(lines[i])
        if not m:
            continue
        url = m.group(0).translate(_DASHES)
        parts = [p.strip() for p in line.split("|") if p.strip()]
        slots = [k for k, p in enumerate(parts) if LINKEDIN_RE.search(p)]
        if slots:
            parts = [url if k == slots[0] else p for k, p in enumerate(parts) if k not in slots[1:]]
            parts += [p.strip() for p in lines[i].split("|")   # anything else already on that line
                      if p.strip() and not LINKEDIN_RE.search(p) and p.strip() not in parts]
        else:
            parts.append(_clean_linkedin(lines[i].strip()))
        lines[i] = " | ".join(parts)
        return "\n".join(lines)
    at = 1
    while at < end and lines[at].strip() and not lines[at].startswith("#"):
        at += 1  # past the name + headline lines
    lines[at:at] = ["", line] if at < end and not lines[at].strip() else [line]
    return "\n".join(lines)


def has_email(markdown):
    """True if the exported resume will show an email -- in the text or via the contact line."""
    return bool(EMAIL_RE.search(markdown) or EMAIL_RE.search(contact_line()))
