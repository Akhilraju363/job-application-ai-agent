"""Shared filesystem locations for the dashboard layer.

`OUTPUT_DIR` is read at call time (`paths.OUTPUT_DIR`, never `from paths import OUTPUT_DIR`)
so tests can point it at a temp dir. `JOB_AGENT_OUTPUT_DIR` overrides it.
"""
import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.environ.get("JOB_AGENT_OUTPUT_DIR") or ROOT / "output")
BASE_RESUME = ROOT / "resume" / "base_resume.md"
WEB_DIR = ROOT / "web"

NO_MASTER_MESSAGE = ("No master resume is configured. Please configure your master resume "
                     "(resume/base_resume.md) before generating a tailored resume.")
REQUIRED_MASTER_SECTIONS = ("## Summary", "## Skills", "## Experience")


class MasterResumeError(OSError):
    """The master resume is missing, empty, or unusable. An OSError subclass so existing
    `except OSError` handlers keep working."""


def read_base_resume():
    """The master resume text (the single source of truth for every tailored resume)."""
    try:
        text = BASE_RESUME.read_text(encoding="utf-8")
    except OSError as e:
        raise MasterResumeError(NO_MASTER_MESSAGE) from e
    if not text.strip():
        raise MasterResumeError(NO_MASTER_MESSAGE)
    missing = [s for s in REQUIRED_MASTER_SECTIONS if s not in text]
    if missing:
        raise MasterResumeError("The master resume is missing required section(s): "
                                + ", ".join(m[3:] for m in missing) + ". Fix resume/base_resume.md and try again.")
    return text


def master_resume_version(text=None):
    """Short content hash: identifies which master resume a tailored resume was built from."""
    return hashlib.sha1((text if text is not None else read_base_resume()).encode("utf-8")).hexdigest()[:12]
