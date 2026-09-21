"""Username/password authentication for the dashboard (standard library only).

    browser --POST /api/auth/login {username, password}--> dashboard_server
        -> rate-limit check -> PBKDF2 verify against DASHBOARD_PASSWORD_HASH
        -> opaque random session id, sent as an HttpOnly cookie; only its SHA-256 is stored server-side

Credentials live in the Modal secret `job-apply-agent-dashboard-secrets`:
    DASHBOARD_USERNAME        the one account
    DASHBOARD_PASSWORD_HASH   pbkdf2-sha256:<iterations>:<salt>:<hash>   (scripts/create_dashboard_password_hash.py)
    DASHBOARD_ALLOWED_HOSTS   unchanged Host-header allow-list
The plaintext password is never stored, logged or returned, and neither is the hash.

Sessions are server-side and opaque (nothing in the cookie to forge or decode). They are kept in memory
and mirrored to a small file on the output Volume so a scale-to-zero restart doesn't sign you out. A
session is bound to the current password hash, so resetting the password signs every old session out.

Single-container by design (same as dashboard_tasks): the login rate limiter is in memory, so it is
per-container protection, not distributed rate limiting.

Without DASHBOARD_USERNAME/DASHBOARD_PASSWORD_HASH the dashboard runs open, but only on loopback
(local development); dashboard_server.make_server refuses to bind anywhere else.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie

ALGORITHM = "pbkdf2-sha256"
ITERATIONS = 600_000          # OWASP 2023 guidance for PBKDF2-HMAC-SHA256
MIN_ITERATIONS = 100_000      # a stored hash weaker than this is treated as a configuration error
MAX_ITERATIONS = 10_000_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 10
MAX_INPUT_LENGTH = 1024

COOKIE_NAME = "jobagent_session"
SECURE_COOKIE_NAME = "__Host-" + COOKIE_NAME  # __Host- pins the cookie to this exact origin over HTTPS
DEFAULT_SESSION_HOURS = 12
MAX_SESSIONS = 20

MAX_FAILURES = 5              # per (client, username) within WINDOW_SECONDS
MAX_CLIENT_FAILURES = 15      # per client across usernames
WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60

_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
_TRUTHY = ("1", "true", "yes", "on")


class ConfigError(RuntimeError):
    """Dashboard authentication is missing or malformed. The message never contains a value."""


# ---------------------------------------------------------------------------
# password hashing
# ---------------------------------------------------------------------------

def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password, iterations=ITERATIONS, salt=None):
    """`pbkdf2-sha256:<iterations>:<salt>:<hash>` -- unique random salt, no shell-special characters."""
    salt = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    return f"{ALGORITHM}:{int(iterations)}:{_b64(salt)}:{_b64(digest)}"


def parse_hash(stored):
    """(iterations, salt, digest) or None when `stored` isn't a well-formed hash."""
    try:
        algo, iterations, salt, digest = str(stored or "").strip().split(":")
        iterations = int(iterations)
        if algo != ALGORITHM or not 1 <= iterations <= MAX_ITERATIONS:
            return None
        salt, digest = _unb64(salt), _unb64(digest)
        return (iterations, salt, digest) if salt and len(digest) == 32 else None
    except (ValueError, TypeError):
        return None


def verify_password(password, stored):
    """Constant-time check. Always does the full PBKDF2 work, so timing doesn't say whether the
    hash was well-formed; a malformed stored hash simply never verifies."""
    parsed = parse_hash(stored)
    if parsed is None:
        hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"\0" * SALT_BYTES, MIN_ITERATIONS)
        return False
    iterations, salt, expected = parsed
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def password_problem(password):
    """Why a *new* password isn't acceptable (None when it is). Used by the setup/reset utilities."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if password.strip() != password or len(set(password)) < 4:
        return "Avoid leading/trailing spaces and repeated-character passwords."
    return None


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def username(env=None):
    return ((env if env is not None else os.environ).get("DASHBOARD_USERNAME") or "").strip()


def password_hash(env=None):
    return ((env if env is not None else os.environ).get("DASHBOARD_PASSWORD_HASH") or "").strip()


def configured(env=None):
    """True when sign-in is enabled (both credentials present). Validity is config_problems()'s job."""
    return bool(username(env) and password_hash(env))


def config_problems(env=None, *, require=True):
    """Human-readable problems (variable names only, never values). Empty list == good.
    With require=False an entirely unconfigured environment is fine (local, open, loopback-only)."""
    env = env if env is not None else os.environ
    user, phash = username(env), password_hash(env)
    if not require and not user and not phash:
        return []
    problems = []
    if not user:
        problems.append("DASHBOARD_USERNAME is missing")
    if not phash:
        problems.append("DASHBOARD_PASSWORD_HASH is missing")
    else:
        parsed = parse_hash(phash)
        if parsed is None:
            problems.append("DASHBOARD_PASSWORD_HASH is not a valid password hash "
                            "(generate one with scripts/create_dashboard_password_hash.py)")
        elif parsed[0] < MIN_ITERATIONS:
            problems.append("DASHBOARD_PASSWORD_HASH uses too few iterations "
                            "(regenerate it with scripts/create_dashboard_password_hash.py)")
    return problems


def require_config(env=None, *, require=True):
    problems = config_problems(env, require=require)
    if problems:
        raise ConfigError("Dashboard authentication is not configured: " + "; ".join(problems) + ".")


def fingerprint(env=None):
    """Identifies the current password hash without exposing it: sessions bind to it, so changing
    the password invalidates every existing session."""
    return hashlib.sha256(b"session-binding:" + password_hash(env).encode("utf-8")).hexdigest()[:16]


def session_lifetime_seconds(env=None):
    try:
        hours = float((env if env is not None else os.environ).get("DASHBOARD_SESSION_HOURS") or DEFAULT_SESSION_HOURS)
    except ValueError:
        hours = DEFAULT_SESSION_HOURS
    return int(max(0.25, min(hours, 24 * 30)) * 3600)


def _flag(name, env=None):
    return ((env if env is not None else os.environ).get(name) or "").strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# request helpers (cookie + client address)
# ---------------------------------------------------------------------------

def request_is_secure(headers, env=None):
    """HTTPS to the user: forced by DASHBOARD_COOKIE_SECURE (Modal), or reported by a trusted proxy."""
    if _flag("DASHBOARD_COOKIE_SECURE", env):
        return True
    return _flag("DASHBOARD_TRUST_PROXY", env) and (headers.get("X-Forwarded-Proto") or "").split(",")[-1].strip().lower() == "https"


def client_ip(headers, peer, env=None):
    """The address rate limiting keys on. Behind Modal's proxy every connection has the proxy as its
    peer, so with DASHBOARD_TRUST_PROXY the *rightmost* X-Forwarded-For entry (the one the proxy
    itself appended -- never the client-controlled left side) is used."""
    if _flag("DASHBOARD_TRUST_PROXY", env):
        forwarded = [p.strip() for p in (headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
        if forwarded:
            return forwarded[-1][:64]
    return str(peer)[:64]


def build_cookie(session_id, secure, max_age):
    name = SECURE_COOKIE_NAME if secure else COOKIE_NAME
    return f"{name}={session_id}; Path=/; Max-Age={int(max_age)}; HttpOnly; SameSite=Strict" + ("; Secure" if secure else "")


def clear_cookie(secure):
    name = SECURE_COOKIE_NAME if secure else COOKIE_NAME
    return f"{name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict" + ("; Secure" if secure else "")


def read_session_id(cookie_header):
    if not cookie_header:
        return None
    try:
        jar = SimpleCookie()
        jar.load(cookie_header)
    except Exception:  # noqa: BLE001 -- a malformed Cookie header is just "no session"
        return None
    for name in (SECURE_COOKIE_NAME, COOKIE_NAME):
        if name in jar and _SESSION_ID.match(jar[name].value):
            return jar[name].value
    return None


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def _sid_hash(session_id):
    return hashlib.sha256(session_id.encode("ascii")).hexdigest()


class SessionStore:
    """Opaque server-side sessions: {sha256(session id): {u, iat, exp, pv}}. `path_fn` returns the
    mirror file (or None to stay memory-only); it is called each time so tests and
    JOB_AGENT_OUTPUT_DIR can repoint it. Mirroring is best-effort -- a failed write never blocks sign-in."""

    def __init__(self, path_fn=None, clock=time.time):
        self._path_fn, self._clock = path_fn, clock
        self._lock = threading.Lock()
        self._sessions, self._loaded_from = {}, object()

    def _path(self):
        try:
            return self._path_fn() if self._path_fn else None
        except Exception:  # noqa: BLE001
            return None

    def _load_if_needed(self):
        path = self._path()
        if path == self._loaded_from:
            return
        self._loaded_from, self._sessions = path, {}
        if path is None:
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
                self._sessions = {k: v for k, v in data["sessions"].items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass

    def _save(self):
        path = self._path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "sessions": self._sessions}), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def _prune(self):
        now = self._clock()
        for key in [k for k, v in self._sessions.items() if v.get("exp", 0) <= now]:
            del self._sessions[key]
        while len(self._sessions) > MAX_SESSIONS:  # oldest first
            del self._sessions[min(self._sessions, key=lambda k: self._sessions[k].get("iat", 0))]

    def create(self, user, bind, lifetime):
        session_id = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._load_if_needed()
            self._sessions[_sid_hash(session_id)] = {"u": user, "iat": now, "exp": now + lifetime, "pv": bind}
            self._prune()
            self._save()
        return session_id

    def validate(self, session_id, user, bind):
        """The session's record, or None (unknown, expired, another account, or password changed)."""
        if not session_id or not _SESSION_ID.match(session_id):
            return None
        key = _sid_hash(session_id)
        with self._lock:
            self._load_if_needed()
            record = self._sessions.get(key)
            if record is None:
                return None
            if record.get("exp", 0) <= self._clock() or record.get("u") != user or record.get("pv") != bind:
                del self._sessions[key]
                self._save()
                return None
            return {"username": record["u"], "expires": record["exp"]}

    def destroy(self, session_id):
        if not session_id or not _SESSION_ID.match(session_id):
            return None
        with self._lock:
            self._load_if_needed()
            record = self._sessions.pop(_sid_hash(session_id), None)
            if record is not None:
                self._save()
            return record

    def clear(self):
        with self._lock:
            self._load_if_needed()
            self._sessions = {}
            self._save()

    def count(self):
        with self._lock:
            self._load_if_needed()
            self._prune()
            return len(self._sessions)


# ---------------------------------------------------------------------------
# brute-force protection
# ---------------------------------------------------------------------------

class LoginLimiter:
    """Failed-attempt counters per (client, username) and per client, with a fixed cooldown once a limit
    is hit. In memory, one container -- not distributed. A success clears that (client, username) pair.
    Keyed on the client address rather than the username alone so a stranger can't lock the owner out."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._failures, self._blocked_until = {}, {}

    @staticmethod
    def _keys(client, user):
        return (f"pair:{client}:{user.strip().lower()[:64]}", f"client:{client}")

    def blocked(self, client, user):
        """True while any of this client's counters is in cooldown."""
        now = self._clock()
        with self._lock:
            return any(self._blocked_until.get(k, 0) > now for k in self._keys(client, user))

    def failure(self, client, user):
        now = self._clock()
        pair, whole = self._keys(client, user)
        with self._lock:
            for key, limit in ((pair, MAX_FAILURES), (whole, MAX_CLIENT_FAILURES)):
                recent = [t for t in self._failures.get(key, []) if now - t < WINDOW_SECONDS] + [now]
                self._failures[key] = recent
                if len(recent) >= limit:
                    self._blocked_until[key] = now + LOCKOUT_SECONDS
                    self._failures[key] = []
            if len(self._failures) > 2000:  # bound memory under a spray of distinct clients
                for key in list(self._failures)[:500]:
                    self._failures.pop(key, None)
                    self._blocked_until.pop(key, None)

    def success(self, client, user):
        pair, _ = self._keys(client, user)
        with self._lock:
            self._failures.pop(pair, None)
            self._blocked_until.pop(pair, None)

    def reset(self):
        with self._lock:
            self._failures.clear()
            self._blocked_until.clear()


# ---------------------------------------------------------------------------
# shared by the setup / reset utilities
# ---------------------------------------------------------------------------

def collect_credentials(prompt_user, prompt_password, *, default_user=""):
    """Prompt (via the injected callables, so it is testable) for a username and a confirmed password;
    returns (username, password_hash). Raises ValueError with a safe message on bad input.
    The plaintext password exists only in this function's locals."""
    user = (prompt_user(f"Dashboard username{f' [{default_user}]' if default_user else ''}: ") or default_user).strip()
    if not user or ":" in user or len(user) > 64:
        raise ValueError("The username must be 1-64 characters and cannot contain ':'.")
    password = prompt_password("Dashboard password: ")
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    if password != prompt_password("Confirm password: "):
        raise ValueError("The passwords do not match.")
    return user, hash_password(password)
