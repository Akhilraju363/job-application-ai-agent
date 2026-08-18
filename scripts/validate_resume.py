"""Shared resume validation logic (PRD 3.4). Used by both the interactive
Claude Code hook (.claude/hooks/resume_hook.py) and the automated
tailor_job.py script, so the two paths can't drift apart.
"""

REQUIRED_SECTIONS = ["## Summary", "## Skills", "## Experience"]
PLACEHOLDER_MARKERS = ["[placeholder]", "todo", "tbd", "lorem ipsum", "{{", "}}"]


def validate(content):
    """Returns (ok: bool, reason: str | None)."""
    missing_sections = [s for s in REQUIRED_SECTIONS if s not in content]
    if missing_sections:
        return False, f"missing required section(s): {', '.join(missing_sections)}"

    lower_content = content.lower()
    found_placeholders = [m for m in PLACEHOLDER_MARKERS if m in lower_content]
    if found_placeholders:
        return False, f"placeholder text found: {', '.join(found_placeholders)}"

    return True, None
