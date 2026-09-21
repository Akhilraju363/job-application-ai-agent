"""Username/password authentication: hashing, sessions, rate limiting, the login/logout API, route
protection, CSRF/host checks, logging hygiene, and the setup/reset utilities.

Stdlib only:  python -m unittest discover -s tests -v
Hashes here use 1000 PBKDF2 rounds (see fixtures.py) so the suite stays fast; the algorithm and
format are the production ones. Nothing touches Modal, Google, or an LLM provider.
"""
import io
import json
import logging
import os
import re
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from fixtures import AUTH_PASSWORD, AUTH_USER, ROOT, auth_env
import create_dashboard_password_hash as create_script
import dashboard_auth as da
import dashboard_server as srv
import reset_dashboard_password as reset_script
from test_dashboard_server import ServerCase

WRONG = "definitely-not-the-password"


class Hashing(unittest.TestCase):
    def test_hash_format_is_salted_pbkdf2_and_never_contains_the_password(self):
        h = da.hash_password(AUTH_PASSWORD, iterations=1000)
        algo, iterations, salt, digest = h.split(":")
        self.assertEqual((algo, iterations), ("pbkdf2-sha256", "1000"))
        self.assertNotIn(AUTH_PASSWORD, h)
        self.assertEqual(len(da._unb64(salt)), da.SALT_BYTES)
        self.assertEqual(len(da._unb64(digest)), 32)
        self.assertNotRegex(h, r"[\s$'\"`;&|<>]")  # safe to paste into a shell

    def test_each_hash_uses_a_fresh_salt(self):
        a, b = da.hash_password(AUTH_PASSWORD, iterations=1000), da.hash_password(AUTH_PASSWORD, iterations=1000)
        self.assertNotEqual(a, b)
        self.assertTrue(da.verify_password(AUTH_PASSWORD, a) and da.verify_password(AUTH_PASSWORD, b))

    def test_default_work_factor_is_high(self):
        self.assertGreaterEqual(da.ITERATIONS, 600_000)
        self.assertNotEqual(da.hash_password("x" * 12, iterations=1000)[:6], "sha256")

    def test_verify_accepts_the_right_password_only(self):
        h = da.hash_password(AUTH_PASSWORD, iterations=1000)
        self.assertTrue(da.verify_password(AUTH_PASSWORD, h))
        for bad in (WRONG, "", AUTH_PASSWORD + " ", AUTH_PASSWORD.upper()):
            self.assertFalse(da.verify_password(bad, h), repr(bad))

    def test_malformed_stored_hashes_never_verify(self):
        for bad in ("", None, "plain", AUTH_PASSWORD, "pbkdf2-sha256:x:y:z", "pbkdf2-sha256:1000:AAAA:BBBB", "md5:1:2:3",
                    "pbkdf2-sha256:0:AAAAAAAAAAAAAAAAAAAAAA:" + "A" * 43):
            self.assertFalse(da.verify_password(AUTH_PASSWORD, bad), repr(bad))
        self.assertFalse(da.verify_password(AUTH_PASSWORD, hash_of_other := da.hash_password("another-password-1", iterations=1000)))
        self.assertTrue(hash_of_other)

    def test_comparison_is_constant_time_by_construction(self):
        src = (ROOT / "scripts" / "dashboard_auth.py").read_text(encoding="utf-8")
        self.assertIn("hmac.compare_digest(actual, expected)", src)
        self.assertNotRegex(src, r"hashlib\.sha256\(password")  # never a bare fast hash of the password

    def test_password_policy(self):
        self.assertIsNotNone(da.password_problem("short"))
        self.assertIsNotNone(da.password_problem("aaaaaaaaaaaaaa"))
        self.assertIsNotNone(da.password_problem(" leading-space-password"))
        self.assertIsNone(da.password_problem(AUTH_PASSWORD))


class Configuration(unittest.TestCase):
    def test_problems_name_variables_but_never_values(self):
        self.assertEqual(da.config_problems({}), ["DASHBOARD_USERNAME is missing", "DASHBOARD_PASSWORD_HASH is missing"])
        self.assertEqual(da.config_problems({}, require=False), [])  # nothing configured == local open mode
        only_user = da.config_problems({"DASHBOARD_USERNAME": "akhil"}, require=False)
        self.assertEqual(only_user, ["DASHBOARD_PASSWORD_HASH is missing"])  # half-configured is never "open"
        secret = "hunter2-my-plain-password"
        (problem,) = da.config_problems({"DASHBOARD_USERNAME": "a", "DASHBOARD_PASSWORD_HASH": secret})
        self.assertIn("not a valid password hash", problem)
        self.assertNotIn(secret, problem)

    def test_weak_iteration_counts_are_rejected_in_production(self):
        weak = da.hash_password(AUTH_PASSWORD, iterations=1000)
        with mock.patch.object(da, "MIN_ITERATIONS", 100_000):
            (problem,) = da.config_problems({"DASHBOARD_USERNAME": "a", "DASHBOARD_PASSWORD_HASH": weak})
        self.assertIn("too few iterations", problem)

    def test_require_config_raises_a_safe_message(self):
        with self.assertRaises(da.ConfigError) as cm:
            da.require_config({"DASHBOARD_PASSWORD_HASH": da.hash_password(AUTH_PASSWORD, iterations=1000)})
        self.assertEqual(str(cm.exception), "Dashboard authentication is not configured: DASHBOARD_USERNAME is missing.")
        self.assertNotIn("pbkdf2", str(cm.exception))

    def test_legacy_token_is_ignored(self):
        self.assertFalse(da.configured({"DASHBOARD_TOKEN": "t" * 40}))

    def test_session_binding_follows_the_password_hash(self):
        a, b = auth_env(), auth_env()
        self.assertNotEqual(da.fingerprint(a), da.fingerprint(b))  # different salt -> different hash -> different binding
        self.assertNotIn(a["DASHBOARD_PASSWORD_HASH"], da.fingerprint(a))

    def test_session_lifetime_setting_is_bounded(self):
        self.assertEqual(da.session_lifetime_seconds({}), da.DEFAULT_SESSION_HOURS * 3600)
        self.assertEqual(da.session_lifetime_seconds({"DASHBOARD_SESSION_HOURS": "junk"}), da.DEFAULT_SESSION_HOURS * 3600)
        self.assertEqual(da.session_lifetime_seconds({"DASHBOARD_SESSION_HOURS": "0"}), 900)
        self.assertEqual(da.session_lifetime_seconds({"DASHBOARD_SESSION_HOURS": "99999"}), 24 * 30 * 3600)


class Cookies(unittest.TestCase):
    SID = "A" * 43

    def test_cookie_attributes_over_https(self):
        c = da.build_cookie(self.SID, True, 3600)
        self.assertTrue(c.startswith("__Host-jobagent_session=" + self.SID))
        for attr in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/", "Max-Age=3600"):
            self.assertIn(attr, c)
        self.assertNotIn("Domain", c)  # required for the __Host- prefix

    def test_cookie_attributes_over_plain_http_for_local_development(self):
        c = da.build_cookie(self.SID, False, 60)
        self.assertTrue(c.startswith("jobagent_session="))
        self.assertIn("HttpOnly", c)
        self.assertNotIn("Secure", c)

    def test_clear_cookie_expires_it(self):
        self.assertIn("Max-Age=0", da.clear_cookie(True))
        self.assertIn("HttpOnly", da.clear_cookie(False))

    def test_reading_the_session_id(self):
        self.assertEqual(da.read_session_id(f"theme=dark; __Host-jobagent_session={self.SID}"), self.SID)
        self.assertEqual(da.read_session_id(f"jobagent_session={self.SID}"), self.SID)
        for bad in (None, "", "jobagent_session=short", "jobagent_session=" + "<" * 30, "garbage;;;", "other=" + self.SID):
            self.assertIsNone(da.read_session_id(bad), repr(bad))

    def test_secure_detection_and_client_address(self):
        self.assertFalse(da.request_is_secure({}, {}))
        self.assertTrue(da.request_is_secure({}, {"DASHBOARD_COOKIE_SECURE": "1"}))
        self.assertTrue(da.request_is_secure({"X-Forwarded-Proto": "https"}, {"DASHBOARD_TRUST_PROXY": "1"}))
        self.assertFalse(da.request_is_secure({"X-Forwarded-Proto": "https"}, {}))  # untrusted proxy header is ignored
        self.assertEqual(da.client_ip({"X-Forwarded-For": "6.6.6.6"}, "10.0.0.1", {}), "10.0.0.1")
        # behind the trusted proxy the RIGHTMOST entry is the one the proxy added; the left is client-controlled
        self.assertEqual(da.client_ip({"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}, "10.0.0.1", {"DASHBOARD_TRUST_PROXY": "1"}), "203.0.113.9")
        self.assertEqual(da.client_ip({}, "10.0.0.1", {"DASHBOARD_TRUST_PROXY": "1"}), "10.0.0.1")


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jobagent-sessions-"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.now = [1000.0]
        self.path = self.tmp / "sessions.json"
        self.store = da.SessionStore(lambda: self.path, clock=lambda: self.now[0])

    def test_create_validate_destroy(self):
        sid = self.store.create("akhil", "fp1", 3600)
        self.assertRegex(sid, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(self.store.validate(sid, "akhil", "fp1")["username"], "akhil")
        self.assertIsNotNone(self.store.destroy(sid))
        self.assertIsNone(self.store.validate(sid, "akhil", "fp1"))
        self.assertIsNone(self.store.destroy(sid))

    def test_sessions_expire(self):
        sid = self.store.create("akhil", "fp1", 3600)
        self.now[0] += 3599
        self.assertIsNotNone(self.store.validate(sid, "akhil", "fp1"))
        self.now[0] += 2
        self.assertIsNone(self.store.validate(sid, "akhil", "fp1"))

    def test_a_password_change_or_other_account_invalidates_the_session(self):
        sid = self.store.create("akhil", "fp1", 3600)
        self.assertIsNone(self.store.validate(sid, "akhil", "fp2"))
        sid = self.store.create("akhil", "fp1", 3600)
        self.assertIsNone(self.store.validate(sid, "someone-else", "fp1"))

    def test_unknown_and_malformed_ids_never_validate(self):
        for bad in (None, "", "short", "A" * 43, "<script>" * 6):
            self.assertIsNone(self.store.validate(bad, "akhil", "fp1"))

    def test_only_a_digest_of_the_session_id_is_stored(self):
        sid = self.store.create("akhil", "fp1", 3600)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn(sid, text)
        self.assertIn(da._sid_hash(sid), text)

    def test_sessions_survive_a_restart_and_logout_survives_too(self):
        sid = self.store.create("akhil", "fp1", 3600)
        revived = da.SessionStore(lambda: self.path, clock=lambda: self.now[0])  # a new container reading the Volume
        self.assertIsNotNone(revived.validate(sid, "akhil", "fp1"))
        revived.destroy(sid)
        self.assertIsNone(da.SessionStore(lambda: self.path, clock=lambda: self.now[0]).validate(sid, "akhil", "fp1"))

    def test_session_count_is_capped_oldest_first(self):
        ids = []
        for i in range(da.MAX_SESSIONS + 5):
            self.now[0] += 1
            ids.append(self.store.create("akhil", "fp", 99999))
        self.assertEqual(self.store.count(), da.MAX_SESSIONS)
        self.assertIsNone(self.store.validate(ids[0], "akhil", "fp"))
        self.assertIsNotNone(self.store.validate(ids[-1], "akhil", "fp"))

    def test_an_unwritable_mirror_never_breaks_sign_in(self):
        blocker = self.tmp / "file"
        blocker.write_text("x")
        store = da.SessionStore(lambda: blocker / "sub" / "s.json")
        sid = store.create("akhil", "fp", 60)
        self.assertIsNotNone(store.validate(sid, "akhil", "fp"))  # still works from memory

    def test_a_corrupt_mirror_file_is_ignored(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(da.SessionStore(lambda: self.path).count(), 0)


class Limiter(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.limiter = da.LoginLimiter(clock=lambda: self.now[0])

    def fail(self, n, client="1.1.1.1", user="akhil"):
        for _ in range(n):
            self.limiter.failure(client, user)

    def test_blocks_after_the_limit_and_recovers_after_the_cooldown(self):
        self.fail(da.MAX_FAILURES - 1)
        self.assertFalse(self.limiter.blocked("1.1.1.1", "akhil"))
        self.fail(1)
        self.assertTrue(self.limiter.blocked("1.1.1.1", "akhil"))
        self.now[0] += da.LOCKOUT_SECONDS + 1
        self.assertFalse(self.limiter.blocked("1.1.1.1", "akhil"))

    def test_success_clears_the_counter_for_that_pair(self):
        self.fail(da.MAX_FAILURES - 1)
        self.limiter.success("1.1.1.1", "akhil")
        self.fail(da.MAX_FAILURES - 1)
        self.assertFalse(self.limiter.blocked("1.1.1.1", "akhil"))

    def test_one_client_cannot_lock_out_another(self):
        self.fail(da.MAX_FAILURES, client="6.6.6.6")
        self.assertTrue(self.limiter.blocked("6.6.6.6", "akhil"))
        self.assertFalse(self.limiter.blocked("203.0.113.5", "akhil"))  # the owner, from elsewhere

    def test_username_spraying_from_one_client_hits_the_client_wide_limit(self):
        for i in range(da.MAX_CLIENT_FAILURES):
            self.limiter.failure("6.6.6.6", f"user{i}")
        self.assertTrue(self.limiter.blocked("6.6.6.6", "brand-new-name"))

    def test_old_failures_age_out_of_the_window(self):
        self.fail(da.MAX_FAILURES - 1)
        self.now[0] += da.WINDOW_SECONDS + 1
        self.fail(1)
        self.assertFalse(self.limiter.blocked("1.1.1.1", "akhil"))


class Utilities(unittest.TestCase):
    def prompts(self, user, *passwords):
        answers = iter(passwords)
        return (lambda _p: user), (lambda _p: next(answers))

    def test_collect_credentials_hashes_a_confirmed_password(self):
        ask_user, ask_pw = self.prompts("akhil", AUTH_PASSWORD, AUTH_PASSWORD)
        user, phash = da.collect_credentials(ask_user, ask_pw)
        self.assertEqual(user, "akhil")
        self.assertTrue(da.verify_password(AUTH_PASSWORD, phash))
        self.assertNotIn(AUTH_PASSWORD, phash)

    def test_collect_credentials_rejects_mismatch_weak_and_bad_usernames(self):
        for user, pws, message in (("akhil", (AUTH_PASSWORD, "different-password-9"), "do not match"),
                                   ("akhil", ("short", "short"), "at least"),
                                   ("", (AUTH_PASSWORD, AUTH_PASSWORD), "username"),
                                   ("a:b", (AUTH_PASSWORD, AUTH_PASSWORD), "username")):
            ask_user, ask_pw = self.prompts(user, *pws)
            with self.assertRaisesRegex(ValueError, message):
                da.collect_credentials(ask_user, ask_pw)

    def run_main(self, module, answers, argv=()):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("builtins.input", return_value=answers[0]), \
                mock.patch("getpass.getpass", side_effect=list(answers[1:])), \
                redirect_stdout(out), redirect_stderr(err):
            code = module.main(list(argv)) if module is reset_script else module.main()
        return code, out.getvalue(), err.getvalue()

    def test_create_script_prints_username_and_hash_but_never_the_password(self):
        code, out, err = self.run_main(create_script, ["akhil", AUTH_PASSWORD, AUTH_PASSWORD])
        self.assertEqual(code, 0)
        self.assertIn("DASHBOARD_USERNAME=akhil", out)
        (phash,) = re.findall(r"^DASHBOARD_PASSWORD_HASH=(\S+)$", out, re.M)
        self.assertTrue(da.verify_password(AUTH_PASSWORD, phash))
        self.assertNotIn(AUTH_PASSWORD, out + err)

    def test_create_script_fails_cleanly_on_mismatch(self):
        code, out, err = self.run_main(create_script, ["akhil", AUTH_PASSWORD, "another-password-2"])
        self.assertEqual(code, 1)
        self.assertNotIn("DASHBOARD_PASSWORD_HASH", out)
        self.assertNotIn(AUTH_PASSWORD, out + err)

    def test_reset_script_prints_the_update_command_and_redeploy_steps(self):
        code, out, err = self.run_main(reset_script, ["akhil", AUTH_PASSWORD, AUTH_PASSWORD], ["--host", "ws--x.modal.run"])
        self.assertEqual(code, 0)
        self.assertIn("modal secret create job-apply-agent-dashboard-secrets", out)
        self.assertIn("DASHBOARD_USERNAME=akhil", out)
        self.assertIn("DASHBOARD_ALLOWED_HOSTS=ws--x.modal.run", out)
        self.assertIn("--force", out)
        self.assertIn("modal deploy modal_app.py", out)
        self.assertNotIn(AUTH_PASSWORD, out + err)
        self.assertNotIn("job-apply-agent-secrets ", out)  # the pipeline's secret is never touched

    def test_apply_uses_a_temp_json_file_that_is_removed_and_never_contains_the_password(self):
        seen = {}

        def fake_run(cmd, env=None):
            path = Path(cmd[cmd.index("--from-json") + 1])
            seen["cmd"], seen["json"], seen["existed"] = cmd, json.loads(path.read_text(encoding="utf-8")), path.exists()
            seen["path"] = path
            return mock.Mock(returncode=0)

        values = reset_script.secret_values("akhil", da.hash_password(AUTH_PASSWORD, iterations=1000), "ws--x.modal.run")
        with mock.patch("shutil.which", return_value="/usr/bin/modal"):
            reset_script.apply_to_modal(values, runner=fake_run)
        self.assertTrue(seen["existed"])
        self.assertFalse(seen["path"].exists())  # deleted straight away
        self.assertEqual(set(seen["json"]), {"DASHBOARD_USERNAME", "DASHBOARD_PASSWORD_HASH", "DASHBOARD_ALLOWED_HOSTS"})
        self.assertIn("--force", seen["cmd"])
        self.assertNotIn(AUTH_PASSWORD, json.dumps(seen["json"]) + " ".join(seen["cmd"]))

    def test_apply_fails_clearly_when_modal_is_missing_or_errors(self):
        values = reset_script.secret_values("a", "h", "x")
        with mock.patch("shutil.which", return_value=None), self.assertRaisesRegex(RuntimeError, "modal"):
            reset_script.apply_to_modal(values)
        with mock.patch("shutil.which", return_value="modal"), self.assertRaisesRegex(RuntimeError, "failed"):
            reset_script.apply_to_modal(values, runner=lambda *a, **k: mock.Mock(returncode=3))


class AuthCase(ServerCase):
    """The real server with sign-in configured."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, auth_env())
        patcher.start()
        self.addCleanup(patcher.stop)
        srv.SESSIONS.clear()
        srv.LIMITER.reset()
        self.addCleanup(srv.SESSIONS.clear)

    def login(self, user=AUTH_USER, password=AUTH_PASSWORD, headers=None):
        return self.call("POST", "/api/auth/login", {"username": user, "password": password}, headers=headers)

    def cookie_of(self, headers):
        return headers["Set-Cookie"].split(";")[0]


class LoginApi(AuthCase):
    def test_unauthenticated_status(self):
        self.assertEqual(self.call("GET", "/api/auth"), (200, mock.ANY, {"authenticated": False}))

    def test_valid_credentials_start_a_session(self):
        s, h, body = self.login()
        self.assertEqual((s, body), (200, {"authenticated": True, "username": AUTH_USER}))
        cookie = self.cookie_of(h)
        self.assertEqual(self.call("GET", "/api/auth", headers={"Cookie": cookie})[2], {"authenticated": True, "username": AUTH_USER})
        self.assertEqual(self.call("GET", "/api/me", headers={"Cookie": cookie})[0], 200)

    def test_session_cookie_attributes(self):
        _, h, _ = self.login()
        c = h["Set-Cookie"]
        for attr in ("HttpOnly", "SameSite=Strict", "Path=/", f"Max-Age={da.DEFAULT_SESSION_HOURS * 3600}"):
            self.assertIn(attr, c)
        self.assertTrue(c.startswith("jobagent_session="))
        self.assertNotIn("Secure", c)  # plain-http local development
        session_id = c.split(";")[0].split("=", 1)[1]
        for secret in (AUTH_PASSWORD, os.environ["DASHBOARD_PASSWORD_HASH"], AUTH_USER):
            self.assertNotIn(secret, c)  # the cookie is an opaque random id, nothing else
        self.assertRegex(session_id, r"^[A-Za-z0-9_-]{43}$")

    def test_cookie_is_secure_behind_https(self):
        with mock.patch.dict(os.environ, {"DASHBOARD_COOKIE_SECURE": "1"}):
            _, h, _ = self.login()
            c = h["Set-Cookie"]
            self.assertTrue(c.startswith("__Host-jobagent_session="))
            self.assertIn("Secure", c)
            self.assertEqual(self.call("GET", "/api/me", headers={"Cookie": self.cookie_of(h)})[0], 200)
        with mock.patch.dict(os.environ, {"DASHBOARD_TRUST_PROXY": "1"}):
            _, h, _ = self.login(headers={"X-Forwarded-Proto": "https"})
            self.assertIn("Secure", h["Set-Cookie"])

    def test_invalid_username_and_invalid_password_are_indistinguishable(self):
        a = self.login(user="nobody", password=AUTH_PASSWORD)
        b = self.login(user=AUTH_USER, password=WRONG)
        c = self.login(user="nobody", password=WRONG)
        for s, h, body in (a, b, c):
            self.assertEqual(s, 401)
            self.assertEqual(body, {"error": {"code": "invalid_credentials", "message": "Invalid username or password."}})
            self.assertNotIn("Set-Cookie", h)
        self.assertEqual(a[2], b[2])

    def test_bad_request_shapes(self):
        for body in ({}, {"username": AUTH_USER}, {"password": AUTH_PASSWORD}, {"username": "", "password": ""},
                     {"username": 5, "password": 5}, {"username": AUTH_USER, "password": "x" * 5000}):
            s, _, r = self.call("POST", "/api/auth/login", body)
            self.assertEqual(s, 400, body)
            self.assertEqual(r["error"]["code"], "invalid_input")

    def test_responses_never_contain_the_password_or_the_hash(self):
        blobs = []
        for s, h, body in (self.login(), self.login(password=WRONG), self.call("GET", "/api/auth")):
            blobs.append(json.dumps(body) + json.dumps(h))
        cookie = self.cookie_of(self.login()[1])
        for path in ("/api/me", "/api/settings", "/api/auth"):
            blobs.append(json.dumps(self.call("GET", path, headers={"Cookie": cookie})[2]))
        for blob in blobs:
            self.assertNotIn(AUTH_PASSWORD, blob)
            self.assertNotIn(os.environ["DASHBOARD_PASSWORD_HASH"], blob)
            self.assertNotIn("pbkdf2", blob)

    def test_logout_ends_the_session_and_clears_the_cookie(self):
        cookie = self.cookie_of(self.login()[1])
        self.assertEqual(self.call("GET", "/api/me", headers={"Cookie": cookie})[0], 200)
        s, h, body = self.call("POST", "/api/auth/logout", {}, headers={"Cookie": cookie})
        self.assertEqual((s, body), (200, {"authenticated": False}))
        self.assertIn("Max-Age=0", h["Set-Cookie"])
        self.assertEqual(self.call("GET", "/api/me", headers={"Cookie": cookie})[0], 401)  # the old cookie is dead server-side
        self.assertEqual(self.call("GET", "/api/auth", headers={"Cookie": cookie})[2], {"authenticated": False})

    def test_logout_without_a_session_is_harmless(self):
        s, h, body = self.call("POST", "/api/auth/logout", {})
        self.assertEqual((s, body), (200, {"authenticated": False}))
        self.assertIn("Max-Age=0", h["Set-Cookie"])

    def test_logging_out_one_session_leaves_another_alone(self):
        a, b = self.cookie_of(self.login()[1]), self.cookie_of(self.login()[1])
        self.call("POST", "/api/auth/logout", {}, headers={"Cookie": a})
        self.assertEqual(self.call("GET", "/api/me", headers={"Cookie": a})[0], 401)
        self.assertEqual(self.call("GET", "/api/me", headers={"Cookie": b})[0], 200)

    def test_sessions_expire(self):
        cookie = self.cookie_of(self.login()[1])
        with mock.patch.object(srv.SESSIONS, "_clock", lambda: time.time() + da.DEFAULT_SESSION_HOURS * 3600 + 5):
            self.assertEqual(self.call("GET", "/api/me", headers={"Cookie": cookie})[0], 401)

    def test_changing_the_password_signs_everyone_out(self):
        cookie = self.cookie_of(self.login()[1])
        with mock.patch.dict(os.environ, auth_env(password="a-brand-new-password-1")):
            self.assertEqual(self.call("GET", "/api/me", headers={"Cookie": cookie})[0], 401)
            self.assertEqual(self.login(password=AUTH_PASSWORD)[0], 401)  # the old password no longer works
            self.assertEqual(self.login(password="a-brand-new-password-1")[0], 200)

    def test_forged_and_malformed_cookies_are_rejected(self):
        for cookie in ("jobagent_session=" + "A" * 43, "jobagent_session=short", "jobagent_session=", "x=y",
                       "__Host-jobagent_session=" + "B" * 43, "jobagent_session=%s" % ("../" * 15)):
            self.assertEqual(self.call("GET", "/api/me", headers={"Cookie": cookie})[0], 401, cookie)

    def test_the_legacy_bearer_token_grants_nothing(self):
        with mock.patch.dict(os.environ, {"DASHBOARD_TOKEN": "legacy-token-value-123"}):
            self.assertEqual(self.call("GET", "/api/me", headers={"Authorization": "Bearer legacy-token-value-123"})[0], 401)
            self.assertNotIn("legacy-token-value-123", json.dumps(self.call("GET", "/api/settings")[2]))

    def test_login_when_auth_is_not_configured_is_not_found(self):
        with mock.patch.dict(os.environ, {"DASHBOARD_USERNAME": "", "DASHBOARD_PASSWORD_HASH": ""}):
            self.assertEqual(self.call("POST", "/api/auth/login", {"username": "a", "password": "b"})[0], 404)


class RouteProtection(AuthCase):
    PUBLIC = {"/api/auth", "/api/auth/login", "/api/auth/logout"}

    def concrete(self, pattern):
        path = pattern.pattern.strip("^$")
        return re.sub(r"\(\?P<\w+>\[0-9a-f\]\{(\d+)\}\)", lambda m: "0" * int(m.group(1)), path)

    def test_every_route_but_the_auth_ones_is_default_deny(self):
        checked = []
        for method, pattern, _fn in srv.ROUTES:
            path = self.concrete(pattern)
            self.assertNotRegex(path, r"[()\[\]{}^$?]", f"could not build a concrete path for {pattern.pattern}")
            if path in self.PUBLIC:
                continue
            status, _, body = self.call(method, path, None if method == "GET" else {})
            self.assertEqual(status, 401, f"{method} {path} is reachable without signing in")
            self.assertEqual(body["error"]["code"], "unauthorized")
            checked.append((method, path))
        self.assertGreater(len(checked), 25)
        self.assertIn(("GET", "/api/logs"), checked)
        for name in ("/api/tracker", "/api/settings", "/api/preferences", "/api/tailor", "/api/resumes", "/api/jobs", "/api/me"):
            self.assertTrue(any(p == name for _, p in checked), name)

    def test_unknown_api_paths_are_denied_before_they_are_routed(self):
        for path in ("/api/anything", "/api/logs/x", "/api/../settings"):
            self.assertEqual(self.call("GET", path)[0], 401, path)

    def test_the_auth_endpoints_are_the_only_public_api(self):
        self.assertEqual(srv.PUBLIC_API, frozenset(self.PUBLIC))
        self.assertEqual(self.call("GET", "/api/auth")[0], 200)
        self.assertEqual(self.call("POST", "/api/auth/logout", {})[0], 200)

    def test_signed_in_users_reach_the_api(self):
        cookie = self.cookie_of(self.login()[1])
        for path in ("/api/me", "/api/settings", "/api/preferences", "/api/tracker", "/api/dashboard/status", "/api/logs", "/api/jobs"):
            with mock.patch.object(srv.tracker, "snapshot", return_value={"rows": [], "available": True, "error": None, "stale": False, "configured": True}):
                s, _, _ = self.call("GET", path, headers={"Cookie": cookie})
            self.assertEqual(s, 200, path)

    def test_the_login_screen_assets_stay_public_but_carry_no_data(self):
        for path in ("/", "/js/app.js", "/js/components/loginForm.js", "/css/app.css"):
            self.assertEqual(self.call("GET", path, raw=True)[0], 200, path)

    def test_sensitive_files_are_never_served_statically(self):
        for path in ("/.auth_sessions.json", "/../output/.auth_sessions.json", "/output/.auth_sessions.json", "/.env"):
            self.assertEqual(self.call("GET", path, raw=True)[0], 404, path)


class RateLimitApi(AuthCase):
    def test_repeated_failures_lock_the_client_out_even_for_the_right_password(self):
        for _ in range(da.MAX_FAILURES):
            self.assertEqual(self.login(password=WRONG)[0], 401)
        s, h, body = self.login(password=AUTH_PASSWORD)
        self.assertEqual(s, 429)
        self.assertEqual(body["error"]["code"], "rate_limited")
        self.assertNotIn("Retry-After", h)
        self.assertNotRegex(json.dumps(body), r"\d")  # no counts, timers or thresholds revealed
        self.assertNotIn("Set-Cookie", h)

    def test_a_successful_login_resets_the_counter(self):
        for _ in range(da.MAX_FAILURES - 1):
            self.login(password=WRONG)
        self.assertEqual(self.login()[0], 200)
        for _ in range(da.MAX_FAILURES - 1):
            self.assertEqual(self.login(password=WRONG)[0], 401)
        self.assertEqual(self.login()[0], 200)

    def test_the_lockout_ends(self):
        for _ in range(da.MAX_FAILURES):
            self.login(password=WRONG)
        self.assertEqual(self.login()[0], 429)
        real = time.monotonic
        with mock.patch.object(srv.LIMITER, "_clock", lambda: real() + da.LOCKOUT_SECONDS + 5):
            self.assertEqual(self.login()[0], 200)

    def test_normal_use_is_not_throttled(self):
        for _ in range(10):
            self.assertEqual(self.login()[0], 200)


class CsrfAndHost(AuthCase):
    def test_unlisted_host_is_refused_everywhere_even_with_a_valid_session(self):
        cookie = self.cookie_of(self.login()[1])
        for path in ("/", "/api/auth", "/api/me"):
            self.assertEqual(self.call("GET", path, headers={"Host": "evil.example.com", "Cookie": cookie}, raw=True)[0], 403, path)
        self.assertEqual(self.call("POST", "/api/auth/login", {"username": AUTH_USER, "password": AUTH_PASSWORD},
                                   headers={"Host": "evil.example.com"})[0], 403)

    def test_cross_origin_writes_are_refused_even_with_a_valid_session(self):
        cookie = self.cookie_of(self.login()[1])
        for extra in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            for method, path, body in (("PUT", "/api/preferences", {"keywords": "x"}), ("POST", "/api/actions/find-jobs", {"mode": "find"}),
                                       ("POST", "/api/auth/logout", {})):
                s, _, r = self.call(method, path, body, headers={"Cookie": cookie, **extra})
                self.assertEqual((s, r["error"]["code"]), (403, "bad_origin"), (method, path, extra))
        self.assertEqual(self.call("GET", "/api/me", headers={"Cookie": cookie})[0], 200)  # ...and the session is unharmed

    def test_cross_origin_login_and_form_posts_are_refused(self):
        s, _, r = self.call("POST", "/api/auth/login", {"username": AUTH_USER, "password": AUTH_PASSWORD}, headers={"Origin": "https://evil.example"})
        self.assertEqual((s, r["error"]["code"]), (403, "bad_origin"))
        s, _, r = self.call("POST", "/api/auth/login", {"username": AUTH_USER, "password": AUTH_PASSWORD}, headers={"Content-Type": "text/plain"})
        self.assertEqual(s, 415)  # a cross-site <form> can only send simple content types

    def test_same_origin_requests_pass(self):
        host = f"127.0.0.1:{self.port}"
        cookie = self.cookie_of(self.call("POST", "/api/auth/login", {"username": AUTH_USER, "password": AUTH_PASSWORD},
                                          headers={"Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin"})[1])
        self.assertEqual(self.call("GET", "/api/me", headers={"Cookie": cookie})[0], 200)

    def test_no_cors_headers_are_ever_sent(self):
        for method, path, body in (("GET", "/api/auth", None), ("POST", "/api/auth/login", {"username": "a", "password": "b"})):
            _, h, _ = self.call(method, path, body, headers={"Origin": f"http://127.0.0.1:{self.port}"})
            self.assertFalse([k for k in h if k.lower().startswith("access-control")])


class AuthDisabledLocalMode(ServerCase):
    def test_no_credentials_means_open_on_loopback_only(self):
        self.assertEqual(self.call("GET", "/api/auth")[2], {"authenticated": True, "auth_required": False})
        s, _, me = self.call("GET", "/api/me")
        self.assertEqual((s, me["auth_required"]), (200, False))

    def test_half_configured_credentials_refuse_to_start_instead_of_running_open(self):
        for env in ({"DASHBOARD_USERNAME": "akhil", "DASHBOARD_PASSWORD_HASH": ""},
                    {"DASHBOARD_USERNAME": "", "DASHBOARD_PASSWORD_HASH": auth_env()["DASHBOARD_PASSWORD_HASH"]},
                    {"DASHBOARD_USERNAME": "akhil", "DASHBOARD_PASSWORD_HASH": "plain-text-password"}):
            with mock.patch.dict(os.environ, env), self.assertRaises(SystemExit) as cm:
                srv.make_server("127.0.0.1", 0)
            self.assertNotIn("plain-text-password", str(cm.exception))

    def test_the_legacy_token_alone_cannot_make_a_public_bind_acceptable(self):
        with mock.patch.dict(os.environ, {"DASHBOARD_TOKEN": "t" * 40, "DASHBOARD_USERNAME": "", "DASHBOARD_PASSWORD_HASH": ""}), \
                self.assertRaises(SystemExit):
            srv.make_server("0.0.0.0", 0)

    def test_a_public_bind_starts_once_credentials_exist(self):
        saved = srv.Handler.allowed_hosts
        with mock.patch.dict(os.environ, auth_env()):
            server = srv.make_server("0.0.0.0", 0)
        self.addCleanup(setattr, srv.Handler, "allowed_hosts", saved)
        self.addCleanup(server.server_close)


class AuthLogging(AuthCase):
    def test_events_are_logged_without_credentials(self):
        wrong = "wrong-guess-for-logging-1"
        with self.assertLogs("auth", level="INFO") as cm:
            self.login(password=wrong)
            cookie = self.cookie_of(self.login()[1])
            self.call("POST", "/api/auth/logout", {}, headers={"Cookie": cookie})
            for _ in range(da.MAX_FAILURES):
                self.login(password=wrong)
            self.login()  # rate limited
        events = [r.getMessage() for r in cm.records]
        for event in ("login_failed", "login_success", "logout", "rate_limited"):
            self.assertIn(event, events)
        levels = {r.getMessage(): r.levelno for r in cm.records}
        self.assertEqual((levels["login_success"], levels["login_failed"], levels["rate_limited"]), (logging.INFO, logging.WARNING, logging.WARNING))
        raw = json.dumps([{k: str(v) for k, v in r.__dict__.items() if k != "args"} for r in cm.records])
        for secret in (AUTH_PASSWORD, wrong, os.environ["DASHBOARD_PASSWORD_HASH"], cookie.split("=", 1)[1], "pbkdf2"):
            self.assertNotIn(secret, raw)
        failed = [r for r in cm.records if r.getMessage() == "login_failed"][0]
        self.assertFalse(hasattr(failed, "username"))  # what was typed into the username box is not recorded
        self.assertEqual([r.username for r in cm.records if r.getMessage() == "login_success"][0], AUTH_USER)

    def test_the_login_request_is_logged_without_its_body(self):
        with self.assertLogs("dashboard", level="DEBUG") as cm:
            self.login(password="body-password-should-not-appear")
            time.sleep(0.2)
        text = json.dumps([{k: str(v) for k, v in r.__dict__.items() if k != "args"} for r in cm.records])
        self.assertNotIn("body-password-should-not-appear", text)
        self.assertIn("/api/auth/login", text)

    def test_redaction_masks_hashes_and_session_cookies_if_they_ever_reach_a_log(self):
        import logging_config as lc
        sid = "S" * 43
        out = lc.redact(f"hash pbkdf2-sha256:1000:c2FsdHNhbHRzYWx0:{'a' * 43} cookie __Host-jobagent_session={sid} session={sid}")
        self.assertNotIn(sid, out)
        self.assertNotIn("pbkdf2-sha256:1000", out)


class CronIndependence(unittest.TestCase):
    SRC = (ROOT / "modal_app.py").read_text(encoding="utf-8")

    def test_the_cron_never_touches_dashboard_auth_or_its_secret(self):
        start = self.SRC.index("\ndef run_pipeline(")
        body = self.SRC[start:self.SRC.index("\n@app.function(", start)]
        block = self.SRC[self.SRC.rindex("@app.function(", 0, start):start]
        for text in (body, block):
            for name in ("dashboard_auth", "DASHBOARD_", "dashboard-secrets", "SESSIONS", "login"):
                self.assertNotIn(name, text)
        self.assertIn('schedule=modal.Cron("0 7 * * 1-5", timezone="Asia/Kolkata")', block)

    def test_scripts_the_cron_runs_do_not_import_the_auth_module(self):
        for name in ("scrape_jobs", "score_jobs", "tailor_job", "company_research", "write_sheet", "run_pipeline"):
            self.assertNotIn("dashboard_auth", (ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8"), name)

    def test_the_dashboard_secret_has_no_legacy_token_requirement(self):
        self.assertNotRegex(self.SRC, r'env\.get\("DASHBOARD_TOKEN"\)')
        self.assertIn("DASHBOARD_PASSWORD_HASH", self.SRC)


class FrontendHasNoCredentialsOrTokens(unittest.TestCase):
    WEB = ROOT / "web"

    def sources(self):
        return [p for pattern in ("*.js", "*.html", "*.css") for p in self.WEB.rglob(pattern) if "tests" not in p.relative_to(self.WEB).parts]

    def test_no_token_or_credential_storage_in_the_frontend(self):
        for p in self.sources():
            text = p.read_text(encoding="utf-8")
            for banned in ("DASHBOARD_TOKEN", "DASHBOARD_PASSWORD", "sessionStorage", "Authorization", "Bearer", "pbkdf2"):
                self.assertNotIn(banned, text, f"{p.relative_to(self.WEB)} mentions {banned}")
            self.assertNotRegex(text, r"localStorage\.\w+\(\s*['\"][^'\"]*(token|password|session|credential)", p.name)


if __name__ == "__main__":
    unittest.main()
