"""Shared filesystem locations for the dashboard layer.

`OUTPUT_DIR` is read at call time (`paths.OUTPUT_DIR`, never `from paths import OUTPUT_DIR`)
so tests can point it at a temp dir. `JOB_AGENT_OUTPUT_DIR` overrides it.
"""
import hashlib
import os
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.environ.get("JOB_AGENT_OUTPUT_DIR") or ROOT / "output")
BASE_RESUME = ROOT / "resume" / "base_resume.md"
WEB_DIR = ROOT / "web"
MAX_FILESYSTEM_COMPONENT_UNITS = 180
_INVALID_COMPONENT_CHARS = frozenset('<>:"/\\|?*')
_WINDOWS_DEVICE_NAME = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)

NO_MASTER_MESSAGE = ("No master resume is configured. Please configure your master resume "
                     "(resume/base_resume.md) before generating a tailored resume.")
REQUIRED_MASTER_SECTIONS = ("## Summary", "## Skills", "## Experience")


def _utf16_units(value):
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _truncate_utf16(value, max_units):
    result = []
    used = 0
    for char in value:
        units = 2 if ord(char) > 0xFFFF else 1
        if used + units > max_units:
            break
        result.append(char)
        used += units
    return "".join(result)


def safe_path_component(value, *, collision_key=None, fallback="item",
                        max_units=MAX_FILESYSTEM_COMPONENT_UNITS):
    """Return one readable, Windows-safe path component.

    Pass a stable collision_key for names derived from distinct records. Its digest
    keeps otherwise-equivalent sanitized names distinct without changing job IDs.
    """
    if max_units < 14:
        raise ValueError("max_units must leave room for a collision suffix")
    original = "" if value is None else str(value)
    safe = []
    for char in original:
        if char == ":":
            safe.append(" - ")
        elif char in _INVALID_COMPONENT_CHARS:
            safe.append("-")
        elif unicodedata.category(char) == "Cc":
            safe.append(" " if char.isspace() else "-")
        else:
            safe.append(char)
    component = re.sub(r"\s+", " ", "".join(safe)).strip(" .") or fallback
    is_device_name = bool(_WINDOWS_DEVICE_NAME.fullmatch(component))
    if is_device_name:
        component = f"_{component}"

    needs_suffix = collision_key is not None or is_device_name or _utf16_units(component) > max_units
    suffix = ""
    if needs_suffix:
        key = original if collision_key is None else str(collision_key)
        suffix = "-" + hashlib.sha256(key.encode("utf-8", errors="surrogatepass")).hexdigest()[:12]
        component = _truncate_utf16(component, max_units - _utf16_units(suffix)).rstrip(" .")
        component = component or _truncate_utf16(fallback, max_units - _utf16_units(suffix))
    return component + suffix


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
