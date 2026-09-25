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


def contact_line():
    return " ".join(os.environ.get("RESUME_CONTACT_LINE", "").split())


def require_contact_line():
    """The contact line, or a clear configuration error -- never a silent resume without one."""
    line = contact_line()
    if not line:
        raise ContactConfigError(MISSING_MESSAGE)
    return line


def with_contact(markdown):
    """Put the contact line in the resume's header block (before the first "## " section).
    It is joined onto an existing LinkedIn line so the header stays a single contact row;
    otherwise it goes in as its own line under the name/headline. Idempotent."""
    line = contact_line()
    if not line or line in markdown:
        return markdown
    lines = markdown.split("\n")
    end = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), len(lines))
    for i in range(end):
        if "linkedin.com" in lines[i].lower():
            lines[i] = f"{line} | {lines[i].strip()}"
            return "\n".join(lines)
    at = 1
    while at < end and lines[at].strip() and not lines[at].startswith("#"):
        at += 1  # past the name + headline lines
    lines[at:at] = ["", line] if at < end and not lines[at].strip() else [line]
    return "\n".join(lines)


def has_email(markdown):
    """True if the exported resume will show an email -- in the text or via the contact line."""
    return bool(EMAIL_RE.search(markdown) or EMAIL_RE.search(contact_line()))
