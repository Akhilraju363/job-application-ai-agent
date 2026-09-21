"""Centralised application logging (standard library only).

    from logging_config import configure_logging, get_logger, bind
    configure_logging("dashboard")            # once, in each entrypoint (never on import)
    logger = get_logger("tailoring")          # named component logger, in every module
    with bind(resume_id=rid):                 # context that rides on every record in this scope
        logger.info("Tailoring started", extra={"job_id": key})

Two sinks, both configured here and nowhere else:

  * console (stderr)  -- what Modal captures as runtime logs. Always on; readable key=value lines.
  * rotating files    -- <output>/logs/application.log (everything, JSON lines), errors.log
                         (ERROR+), and one file per area (dashboard/pipeline/tailoring/drive/
                         tracker). On Modal <output> is the dashboard Volume at /app/output.

Logging is observability, never control flow: a file that can't be created or written degrades
to console-only, and no handler error ever propagates into the application.

This is developer/operator diagnostics. The user-facing history lives in activity.py and is
deliberately separate. Never log secrets, prompts, model output, a full JD or a full resume --
`redact()` is a safety net (env secret values, bearer tokens, key=value credentials), not a
licence to log them.

Environment: LOG_LEVEL (INFO), LOG_MAX_BYTES (10 MB), LOG_BACKUP_COUNT (5), LOG_DIR
(<output>/logs), LOG_TO_FILE (1; 0/false disables files), LOG_CTX_<KEY> (context inherited from a
parent process, e.g. LOG_CTX_TASK_ID).
"""
import contextvars
import json
import logging
import os
import re
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

CONTEXT_KEYS = ("request_id", "task_id", "resume_id", "job_id", "company", "operation")
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# logger-name root -> per-area file. Anything unlisted still lands in application.log.
COMPONENT_FILES = {
    "dashboard": "dashboard", "auth": "dashboard",
    "pipeline": "pipeline", "scraper": "pipeline", "scoring": "pipeline", "research": "pipeline",
    "tailoring": "tailoring", "jd_analysis": "tailoring", "verification": "tailoring",
    "resume": "tailoring", "llm": "tailoring",
    "drive": "drive",
    "tracker": "tracker",
}
COMPONENTS = tuple(sorted(COMPONENT_FILES))

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
MAX_MESSAGE_CHARS = 2000
MAX_FIELD_CHARS = 300

_HANDLER_TAG = "_job_agent_handler"
_context = contextvars.ContextVar("log_context", default={})
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


# ---------------------------------------------------------------------------
# context (request / task / resume / job ids)
# ---------------------------------------------------------------------------

def current_context():
    return dict(_context.get())


@contextmanager
def bind(**values):
    """Attach ids to every record emitted (in this thread/async scope) inside the block."""
    clean = {k: v for k, v in values.items() if k in CONTEXT_KEYS and v not in (None, "")}
    token = _context.set({**_context.get(), **clean})
    try:
        yield
    finally:
        _context.reset(token)


def set_context(**values):
    """Unscoped variant for a process-wide id (a child process inheriting its parent's task id)."""
    clean = {k: v for k, v in values.items() if k in CONTEXT_KEYS and v not in (None, "")}
    _context.set({**_context.get(), **clean})


def new_request_id():
    return uuid.uuid4().hex[:12]


def safe_request_id(value):
    """The client-supplied X-Request-ID if it is short and boring, else None."""
    value = (value or "").strip()
    return value if _SAFE_REQUEST_ID.match(value) else None


def env_context():
    """LOG_CTX_TASK_ID=... style ids handed down by a parent process."""
    return {k: os.environ[f"LOG_CTX_{k.upper()}"] for k in CONTEXT_KEYS if os.environ.get(f"LOG_CTX_{k.upper()}")}


def child_env(**extra_ctx):
    """Environment for a subprocess so its records carry this process's ids."""
    env = {f"LOG_CTX_{k.upper()}": str(v) for k, v in {**current_context(), **extra_ctx}.items()
           if k in CONTEXT_KEYS and v not in (None, "")}
    return {**os.environ, **env}


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------

_SECRET_ENV_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)
_PATTERNS = (
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}"), r"\1 ***"),
    (re.compile(r"(?i)\b(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
                r"token|secret|password|cookie|session|(?:__host-)?jobagent_session)(\"?\s*[:=]\s*\"?)([^\s\",;&]+)"), r"\1\2***"),
    (re.compile(r"\bpbkdf2-sha256:\d+:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+"), "***"),  # a stored password hash
    (re.compile(r"\bgsk_[A-Za-z0-9]{10,}|\bsk-[A-Za-z0-9_-]{16,}|\bAIza[A-Za-z0-9_-]{20,}"
                r"|\bya29\.[A-Za-z0-9._-]{20,}|\b1//[A-Za-z0-9._-]{20,}"), "***"),
)


def redact(text):
    """Mask secret env values and credential-shaped strings. Safety net only."""
    text = str(text)
    for name, value in os.environ.items():
        if value and len(value) >= 8 and _SECRET_ENV_NAME.search(name):
            text = text.replace(value, "***")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

def _fields(record):
    """Context ids first (in a stable order), then any other `extra=` fields."""
    merged = {**_context.get()}
    extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS and not k.startswith("_")}
    merged.update(extras)
    ordered = {k: merged[k] for k in CONTEXT_KEYS if merged.get(k) not in (None, "")}
    ordered.update({k: v for k, v in sorted(merged.items()) if k not in ordered and v is not None})
    return {k: _clean(v) for k, v in ordered.items()}


def _clean(value):
    if isinstance(value, (bool, int, float)):
        return value
    return redact(value)[:MAX_FIELD_CHARS]


def _message(record):
    try:
        text = record.getMessage()
    except Exception:  # noqa: BLE001 -- a bad %-format must not lose the record
        text = str(record.msg)
    return redact(text)[:MAX_MESSAGE_CHARS]


def _traceback(record, formatter):
    if record.exc_info:
        return redact(formatter.formatException(record.exc_info))
    if record.exc_text:
        return redact(record.exc_text)
    return ""


def _utc(record):
    return datetime.fromtimestamp(record.created, timezone.utc)


class ConsoleFormatter(logging.Formatter):
    """2026-09-21 21:20:01 INFO [dashboard] request_id=abc method=GET message="..." + traceback."""

    def format(self, record):
        fields = " ".join(f"{k}={v if isinstance(v, (bool, int, float)) else json.dumps(v, ensure_ascii=False)}"
                          for k, v in _fields(record).items())
        line = (f"{_utc(record):%Y-%m-%d %H:%M:%S} {record.levelname} [{record.name}] "
                f"{fields + ' ' if fields else ''}message={json.dumps(_message(record), ensure_ascii=False)}")
        tb = _traceback(record, self)
        return f"{line}\n{tb}" if tb else line


class JsonFormatter(logging.Formatter):
    """One JSON object per line -- what /api/logs parses."""

    RESERVED = {"timestamp", "level", "component", "message", "exception"}

    def format(self, record):
        entry = {"timestamp": _utc(record).isoformat(timespec="seconds"), "level": record.levelname,
                 "component": record.name, "message": _message(record)}
        entry.update({k: v for k, v in _fields(record).items() if k not in self.RESERVED})
        tb = _traceback(record, self)
        if tb:
            entry["exception"] = tb[:6000]
        return json.dumps(entry, ensure_ascii=False)


class _ComponentFilter(logging.Filter):
    def __init__(self, area):
        super().__init__()
        self.area = area

    def filter(self, record):
        return COMPONENT_FILES.get(record.name.split(".")[0]) == self.area


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def get_logger(name):
    """Named component logger (see COMPONENT_FILES). Configuration lives only in configure_logging()."""
    return logging.getLogger(name)


def default_log_dir():
    import paths  # read at call time: tests (and JOB_AGENT_OUTPUT_DIR) repoint OUTPUT_DIR

    return Path(os.environ.get("LOG_DIR") or paths.OUTPUT_DIR / "logs")


def _int_env(name, default, minimum):
    try:
        return max(minimum, int(os.environ.get(name, "")))
    except ValueError:
        return default


def _level(value):
    name = (value or os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    return name if name in LEVELS else "INFO"


def _files_enabled(to_file):
    if to_file is not None:
        return to_file
    return os.environ.get("LOG_TO_FILE", "1").strip().lower() not in ("0", "false", "no", "off")


def _tag(handler):
    setattr(handler, _HANDLER_TAG, True)
    return handler


def configure_logging(service="app", *, level=None, log_dir=None, to_file=None, to_console=True, stream=None):
    """Install the console + rotating-file handlers on the root logger. Idempotent (a second call
    replaces the first call's handlers). Returns the log directory in use, or None when file
    logging is off/unavailable. Never raises."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_TAG, False)]:
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
    logging.raiseExceptions = False  # a failing sink must never surface as an application error
    root.setLevel(_level(level))
    for noisy in ("urllib3", "requests", "concurrent"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    set_context(**env_context())

    if to_console:
        console = _tag(logging.StreamHandler(stream or sys.stderr))
        console.setFormatter(ConsoleFormatter())
        root.addHandler(console)

    directory, problem = None, None
    if _files_enabled(to_file):
        try:
            directory = _install_file_handlers(root, Path(log_dir) if log_dir else default_log_dir())
        except Exception as e:  # noqa: BLE001 -- unwritable volume, read-only disk, bad LOG_DIR ...
            problem = f"{type(e).__name__}: {e}"
    _install_excepthook(to_console)
    log = get_logger("dashboard" if service == "dashboard" else "pipeline")
    if problem:
        log.warning("File logging unavailable, continuing with console only", extra={"reason": problem})
    log.info("Logging initialized", extra={"service": service, "log_dir": str(directory) if directory else "console-only",
                                          "log_level": logging.getLevelName(root.level)})
    return directory


def _install_excepthook(console_logging):
    """Uncaught exceptions in a script (a pipeline step crashing) reach errors.log with a traceback."""
    if sys.excepthook is not sys.__excepthook__:
        return  # already installed (or someone else's hook): don't stack

    def hook(exc_type, exc, tb):
        logged = False
        if not issubclass(exc_type, KeyboardInterrupt):
            try:
                get_logger("pipeline").critical("Uncaught exception", exc_info=(exc_type, exc, tb))
                logged = True
            except Exception:  # noqa: BLE001
                pass
        if not (logged and console_logging):  # the console handler already printed the traceback
            sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


def _install_file_handlers(root, directory):
    directory.mkdir(parents=True, exist_ok=True)
    max_bytes = _int_env("LOG_MAX_BYTES", DEFAULT_MAX_BYTES, 1024)
    backups = _int_env("LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT, 1)

    def make(name, delay=True):
        handler = _tag(RotatingFileHandler(directory / name, maxBytes=max_bytes, backupCount=backups,
                                           encoding="utf-8", delay=delay))
        handler.setFormatter(JsonFormatter())
        return handler

    application = make("application.log", delay=False)  # opens now: proves the directory is writable
    root.addHandler(application)
    errors = make("errors.log")
    errors.setLevel(logging.ERROR)
    root.addHandler(errors)
    for area in sorted(set(COMPONENT_FILES.values())):
        handler = make(f"{area}.log")
        handler.addFilter(_ComponentFilter(area))
        root.addHandler(handler)
    return directory
