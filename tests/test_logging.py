"""Application logging: configuration, redaction, request/task ids, the /api/logs viewer, and
that the instrumented services log what they should without leaking secrets, JDs or resumes.

Stdlib only:  python -m unittest discover -s tests -v
Nothing here touches Modal, Google, or an LLM provider.
"""
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest import mock

from fixtures import AUTH_PASSWORD, AUTH_USER, BASE, JD_TEXT, ANALYSIS, TempOutput, auth_env, reorder_bullets  # noqa: F401 -- also disables .env loading
import activity
import dashboard_tasks as tasks
import log_reader
import logging_config as lc
from test_dashboard_server import ServerCase
from test_tailoring_service import FAKE_KAFKA, FAKE_ROLE, run as run_tailor

SECRET_ENV = {"MY_SERVICE_API_KEY": "sk-live-ABCDEFGH12345678", "SOME_SERVICE_TOKEN": "svc-TOKEN-value-987654"}


def reset_logging():
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_job_agent_handler", False)]:
        root.removeHandler(handler)
        try:
            handler.close()
        except ValueError:  # a test deliberately closed its stream
            pass
    root.setLevel(logging.WARNING)
    logging.raiseExceptions = True
    sys.excepthook = sys.__excepthook__
    lc._context.set({})


class LogCase(unittest.TestCase):
    """Fresh temp log dir, clean env, and guaranteed handler cleanup (Windows can't delete open files)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jobagent-logtest-"))
        self.logs = self.tmp / "logs"
        env = {k: v for k, v in os.environ.items() if not k.startswith("LOG_")}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(reset_logging)  # runs before rmtree (LIFO)
        self.stream = io.StringIO()

    def configure(self, **kw):
        kw.setdefault("log_dir", self.logs)
        kw.setdefault("stream", self.stream)
        return lc.configure_logging("test", **kw)

    def entries(self, name="application.log"):
        for h in logging.getLogger().handlers:
            h.flush()
        path = self.logs / name
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []

    def everything_written(self):
        for h in logging.getLogger().handlers:
            h.flush()
        files = "".join(p.read_text(encoding="utf-8") for p in self.logs.glob("*.log*")) if self.logs.exists() else ""
        return files + self.stream.getvalue()


class Configuration(LogCase):
    def test_configure_initializes_console_and_file_handlers(self):
        directory = self.configure()
        self.assertEqual(directory, self.logs)
        tagged = [h for h in logging.getLogger().handlers if getattr(h, "_job_agent_handler", False)]
        self.assertTrue(any(type(h) is logging.StreamHandler for h in tagged))
        self.assertTrue(any(isinstance(h, RotatingFileHandler) for h in tagged))
        self.assertEqual(logging.getLogger().level, logging.INFO)
        self.assertTrue(any(e["message"] == "Logging initialized" for e in self.entries()))

    def test_log_directory_is_created_including_parents(self):
        deep = self.tmp / "fresh" / "volume" / "logs"
        self.assertFalse(deep.exists())
        self.configure(log_dir=deep)
        get = lc.get_logger("dashboard")
        get.info("hello")
        self.assertTrue((deep / "application.log").is_file())

    def test_default_directory_is_output_logs(self):
        with TempOutput() as out:
            self.assertEqual(lc.default_log_dir(), out / "logs")
        with mock.patch.dict(os.environ, {"LOG_DIR": str(self.tmp / "custom")}):
            self.assertEqual(lc.default_log_dir(), self.tmp / "custom")

    def test_console_logging_is_readable_and_carries_context(self):
        self.configure()
        with lc.bind(request_id="req123", task_id="task9"):
            lc.get_logger("dashboard").info("Dashboard request", extra={"method": "GET", "status": 200})
        line = [l for l in self.stream.getvalue().splitlines() if "Dashboard request" in l][0]
        self.assertRegex(line, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO \[dashboard\] ")
        for part in ('request_id="req123"', 'task_id="task9"', "method=", "status=200", 'message="Dashboard request"'):
            self.assertIn(part, line)

    def test_file_logging_writes_json_lines_with_the_required_fields(self):
        self.configure()
        with lc.bind(resume_id="abc123abc123", job_id="j1", company="Acme"):
            lc.get_logger("tailoring").info("Tailoring started")
        (entry,) = [e for e in self.entries() if e["message"] == "Tailoring started"]
        self.assertEqual((entry["level"], entry["component"]), ("INFO", "tailoring"))
        self.assertEqual((entry["resume_id"], entry["job_id"], entry["company"]), ("abc123abc123", "j1", "Acme"))
        self.assertRegex(entry["timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")

    def test_errors_go_to_errors_log_with_a_traceback_and_info_does_not(self):
        self.configure()
        log = lc.get_logger("drive")
        log.info("routine")
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("Drive upload failed", extra={"resume_id": "r1"})
        errors = self.entries("errors.log")
        self.assertEqual([e["message"] for e in errors], ["Drive upload failed"])
        self.assertIn("ValueError: boom", errors[0]["exception"])
        self.assertIn("Traceback", errors[0]["exception"])
        self.assertIn("ValueError: boom", self.stream.getvalue())  # and on the console (Modal logs)

    def test_rotating_handler_defaults_and_env_overrides(self):
        self.configure()
        rot = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
        self.assertTrue(rot)
        self.assertTrue(all((h.maxBytes, h.backupCount) == (10 * 1024 * 1024, 5) for h in rot))
        with mock.patch.dict(os.environ, {"LOG_MAX_BYTES": "2048", "LOG_BACKUP_COUNT": "2"}):
            self.configure()
        rot = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
        self.assertTrue(all((h.maxBytes, h.backupCount) == (2048, 2) for h in rot))
        with mock.patch.dict(os.environ, {"LOG_MAX_BYTES": "garbage", "LOG_BACKUP_COUNT": "-3"}):
            self.configure()
        rot = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
        self.assertTrue(all((h.maxBytes, h.backupCount) == (10 * 1024 * 1024, 1) for h in rot))

    def test_logs_actually_rotate_and_stay_bounded(self):
        with mock.patch.dict(os.environ, {"LOG_MAX_BYTES": "1024", "LOG_BACKUP_COUNT": "2"}):
            self.configure(to_console=False)
        log = lc.get_logger("dashboard")
        for i in range(200):
            log.info("padding line to force rotation %s", i, extra={"n": i})
        for h in logging.getLogger().handlers:
            h.flush()
        names = sorted(p.name for p in self.logs.glob("application.log*"))
        self.assertEqual(names, ["application.log", "application.log.1", "application.log.2"])  # never a .3
        self.assertTrue(all(p.stat().st_size < 4096 for p in self.logs.glob("application.log*")))

    def test_component_files_are_filtered_by_area(self):
        self.configure()
        lc.get_logger("drive").info("drive-only")
        lc.get_logger("tracker").info("tracker-only")
        lc.get_logger("dashboard.tasks").info("dashboard-area")
        self.assertEqual([e["message"] for e in self.entries("drive.log")], ["drive-only"])
        self.assertEqual([e["message"] for e in self.entries("tracker.log")], ["tracker-only"])
        self.assertIn("dashboard-area", [e["message"] for e in self.entries("dashboard.log")])
        self.assertTrue({"drive-only", "tracker-only", "dashboard-area"} <= {e["message"] for e in self.entries()})

    def test_reconfiguring_replaces_handlers_instead_of_duplicating_lines(self):
        self.configure()
        self.configure()
        lc.get_logger("dashboard").info("once")
        self.assertEqual(sum(e["message"] == "once" for e in self.entries()), 1)
        self.assertEqual(self.stream.getvalue().count('message="once"'), 1)

    def test_log_level_env_and_invalid_value(self):
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "WARNING"}):
            self.configure()
        lc.get_logger("dashboard").info("hidden")
        lc.get_logger("dashboard").warning("shown")
        self.assertNotIn("hidden", self.everything_written())
        self.assertIn("shown", self.everything_written())
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "loud"}):
            self.configure()
        self.assertEqual(logging.getLogger().level, logging.INFO)
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "debug"}):
            self.configure()
        self.assertEqual(logging.getLogger().level, logging.DEBUG)

    def test_file_logging_can_be_turned_off_and_console_stays(self):
        with mock.patch.dict(os.environ, {"LOG_TO_FILE": "0"}):
            self.assertIsNone(self.configure())
        lc.get_logger("dashboard").info("console only")
        self.assertFalse(self.logs.exists())
        self.assertIn("console only", self.stream.getvalue())


class LoggingFailuresAreHarmless(LogCase):
    def test_unwritable_log_directory_falls_back_to_console_without_raising(self):
        blocker = self.tmp / "not-a-dir"
        blocker.write_text("x")  # a *file* where the log directory should be
        self.assertIsNone(self.configure(log_dir=blocker / "logs"))
        lc.get_logger("tailoring").info("still logging")
        self.assertIn("still logging", self.stream.getvalue())
        self.assertIn("File logging unavailable", self.stream.getvalue())

    def test_a_failing_file_handler_never_raises_into_the_application(self):
        self.configure()
        for h in logging.getLogger().handlers:
            if isinstance(h, RotatingFileHandler) and h.stream is not None:
                h.stream.close()  # writes to a closed stream now fail inside emit()
        lc.get_logger("dashboard").error("must not raise")  # would raise if handleError propagated
        self.assertIn("must not raise", self.stream.getvalue())

    def test_dashboard_still_starts_when_file_logging_is_impossible(self):
        import dashboard_server as srv
        blocker = self.tmp / "file"
        blocker.write_text("x")
        self.assertIsNone(self.configure(log_dir=blocker / "logs"))
        saved_hosts = srv.Handler.allowed_hosts  # make_server sets this class-wide; don't disturb other tests
        server = srv.make_server("127.0.0.1", 0)
        self.addCleanup(setattr, srv.Handler, "allowed_hosts", saved_hosts)
        self.addCleanup(server.server_close)
        self.assertGreater(server.server_address[1], 0)

    def test_bad_format_arguments_do_not_lose_or_break_the_record(self):
        self.configure()
        lc.get_logger("dashboard").info("value %s and %s", "only-one")  # wrong arg count
        self.assertIn("value %s and %s", self.everything_written())

    def test_uncaught_exceptions_are_logged_with_a_traceback(self):
        self.configure()
        try:
            raise RuntimeError("crash in a script")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
        (entry,) = [e for e in self.entries("errors.log") if e["message"] == "Uncaught exception"]
        self.assertEqual(entry["level"], "CRITICAL")
        self.assertIn("RuntimeError: crash in a script", entry["exception"])


class Redaction(LogCase):
    def test_secrets_are_masked_in_messages_fields_and_tracebacks(self):
        with mock.patch.dict(os.environ, SECRET_ENV):
            self.configure()
            log = lc.get_logger("llm")
            log.info("calling with key %s", SECRET_ENV["MY_SERVICE_API_KEY"])
            log.info("headers Authorization: Bearer abcdefghijklmnop", extra={"reason": "api_key=hunter2hunter2"})
            log.info("token=" + SECRET_ENV["SOME_SERVICE_TOKEN"])
            try:
                raise RuntimeError("failed for https://x.example/?token=" + SECRET_ENV["SOME_SERVICE_TOKEN"])
            except RuntimeError:
                log.exception("request failed")
            out = self.everything_written()
        for leaked in (SECRET_ENV["MY_SERVICE_API_KEY"], SECRET_ENV["SOME_SERVICE_TOKEN"], "abcdefghijklmnop", "hunter2hunter2"):
            self.assertNotIn(leaked, out)
        self.assertIn("***", out)

    def test_provider_key_shapes_are_masked_even_when_not_in_the_environment(self):
        self.configure()
        lc.get_logger("llm").warning("provider said gsk_abcdefghij1234567890 and AIzaSyA-abcdefghijklmnopqrstuvwxyz")
        out = self.everything_written()
        self.assertNotIn("gsk_abcdefghij1234567890", out)
        self.assertNotIn("AIzaSyA-abcdefghijklmnopqrstuvwxyz", out)

    def test_oversized_values_are_truncated(self):
        self.configure()
        lc.get_logger("dashboard").info("x" * 10000, extra={"blob": "y" * 10000})
        (entry,) = [e for e in self.entries() if e["message"].startswith("xxx")]
        self.assertLessEqual(len(entry["message"]), lc.MAX_MESSAGE_CHARS)
        self.assertLessEqual(len(entry["blob"]), lc.MAX_FIELD_CHARS)


class ContextAndIds(LogCase):
    def test_bind_is_scoped_and_nests(self):
        self.configure()
        log = lc.get_logger("dashboard")
        with lc.bind(request_id="r1"):
            with lc.bind(task_id="t1"):
                log.info("inner")
            log.info("outer")
        log.info("after")
        by = {e["message"]: e for e in self.entries()}
        self.assertEqual((by["inner"]["request_id"], by["inner"]["task_id"]), ("r1", "t1"))
        self.assertEqual(by["outer"]["request_id"], "r1")
        self.assertNotIn("task_id", by["outer"])
        self.assertNotIn("request_id", by["after"])

    def test_bind_ignores_unknown_keys_and_blank_values(self):
        with lc.bind(request_id="", task_id=None, password="x"):
            self.assertEqual(lc.current_context(), {})

    def test_safe_request_id_accepts_boring_ids_only(self):
        for good in ("abc123", "7f81a2c1", "req-1.2_3", "a" * 64):
            self.assertEqual(lc.safe_request_id(good), good)
        for bad in ("", None, "a" * 65, "has space", "semi;colon", "<script>", "new\nline", "a/b", "quote\""):
            self.assertIsNone(lc.safe_request_id(bad), repr(bad))
        self.assertRegex(lc.new_request_id(), r"^[0-9a-f]{12}$")
        self.assertNotEqual(lc.new_request_id(), lc.new_request_id())

    def test_ids_reach_a_child_process_through_the_environment(self):
        with lc.bind(request_id="req1"):
            env = lc.child_env(task_id="task1")
        self.assertEqual((env["LOG_CTX_REQUEST_ID"], env["LOG_CTX_TASK_ID"]), ("req1", "task1"))
        with mock.patch.dict(os.environ, {"LOG_CTX_TASK_ID": "task1"}):
            self.configure()
            lc.get_logger("scoring").info("in the child")
        self.assertEqual([e["task_id"] for e in self.entries() if e["message"] == "in the child"], ["task1"])


class LogReader(LogCase):
    def write(self, name, entries):
        self.logs.mkdir(parents=True, exist_ok=True)
        (self.logs / name).write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    def entry(self, i, level="INFO", component="tailoring", **kw):
        return {"timestamp": f"2026-09-21T10:{i // 60:02d}:{i % 60:02d}+00:00", "level": level, "component": component,
                "message": f"msg {i}", **kw}

    def q(self, **params):
        return log_reader.query(self.logs, log_reader.parse_filters(params))

    def test_newest_first_pagination_and_has_more(self):
        self.write("application.log", [self.entry(i) for i in range(30)])
        first = self.q(limit="10")
        self.assertEqual([e["message"] for e in first["entries"]][:2], ["msg 29", "msg 28"])
        self.assertEqual((len(first["entries"]), first["has_more"], first["offset"]), (10, True, 0))
        last = self.q(limit="10", offset="20")
        self.assertEqual([e["message"] for e in last["entries"]][-1], "msg 0")
        self.assertFalse(last["has_more"])

    def test_filters_level_component_date_and_ids(self):
        self.write("application.log", [
            self.entry(1, "INFO", "drive", resume_id="r1"), self.entry(2, "ERROR", "drive", resume_id="r1", task_id="t9"),
            self.entry(3, "CRITICAL", "dashboard"), self.entry(4, "WARNING", "dashboard.tasks", request_id="q1"),
            {**self.entry(5), "timestamp": "2026-09-20T09:00:00+00:00"}])
        msgs = lambda **p: sorted(e["message"] for e in self.q(**p)["entries"])  # noqa: E731
        self.assertEqual(msgs(level="ERROR"), ["msg 2", "msg 3"])  # ERROR also covers CRITICAL
        self.assertEqual(msgs(level="warning"), ["msg 4"])
        self.assertEqual(msgs(component="drive"), ["msg 1", "msg 2"])
        self.assertEqual(msgs(component="dashboard"), ["msg 3", "msg 4"])  # includes dashboard.tasks
        self.assertEqual(msgs(date="2026-09-20"), ["msg 5"])
        self.assertEqual(msgs(resume_id="r1", level="ERROR"), ["msg 2"])
        self.assertEqual(msgs(task_id="t9"), ["msg 2"])
        self.assertEqual(msgs(request_id="q1"), ["msg 4"])

    def test_error_level_reads_errors_log_and_rotated_files_are_included(self):
        self.write("errors.log", [self.entry(1, "ERROR")])
        self.write("application.log", [self.entry(9)])
        self.write("application.log.1", [self.entry(2)])
        self.assertEqual([e["message"] for e in self.q(level="ERROR")["entries"]], ["msg 1"])
        self.assertEqual([e["message"] for e in self.q()["entries"]], ["msg 9", "msg 2"])

    def test_malformed_lines_and_missing_files_are_skipped(self):
        self.logs.mkdir(parents=True)
        (self.logs / "application.log").write_text('not json\n{"level": "INFO", "message": "ok", "component": "x"}\n[1,2]\n\n{broken', encoding="utf-8")
        self.assertEqual([e["message"] for e in self.q()["entries"]], ["ok"])
        self.assertEqual(log_reader.query(self.tmp / "nowhere", log_reader.parse_filters({}))["entries"], [])

    def test_reads_are_bounded(self):
        self.write("application.log", [{**self.entry(i % 3600), "message": "x" * 200} for i in range(3000)])
        with mock.patch.object(log_reader, "MAX_SCAN_BYTES", 64 * 1024):
            page = self.q(limit="500", component="nothing_matches")
        self.assertEqual(page["entries"], [])
        self.assertTrue(page["truncated"])
        self.assertEqual(self.q(limit="99999")["limit"], log_reader.MAX_LIMIT)

    def test_entries_are_redacted_on_the_way_out(self):
        self.write("application.log", [{**self.entry(1), "message": "Authorization: Bearer zzzzzzzzzzzz leaked",
                                        "exception": "token=abc12345678"}])
        (e,) = self.q()["entries"]
        self.assertNotIn("zzzzzzzzzzzz", json.dumps(e))
        self.assertNotIn("abc12345678", json.dumps(e))

    def test_bad_filters_are_rejected(self):
        for params in ({"level": "LOUD"}, {"component": "../etc"}, {"component": "a/b"}, {"date": "yesterday"},
                       {"date": "2026-9-1"}, {"resume_id": "../../etc/passwd"}, {"task_id": "a b"}, {"request_id": "x" * 65},
                       {"job_id": "a;b"}, {"limit": "abc"}, {"offset": "1.5"}):
            with self.assertRaises(log_reader.LogQueryError, msg=str(params)):
                log_reader.parse_filters(params)

    def test_no_user_supplied_path_can_change_which_file_is_read(self):
        self.write("application.log", [self.entry(1)])
        (self.tmp / "secret.txt").write_text('{"level": "INFO", "message": "from outside", "component": "x"}\n')
        page = self.q(**{"file": "../secret.txt", "path": str(self.tmp / "secret.txt")})  # unknown params are ignored
        self.assertEqual([e["message"] for e in page["entries"]], ["msg 1"])


class LogsApi(ServerCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jobagent-apilog-"))
        self.logs = self.tmp / "logs"
        patcher = mock.patch.dict(os.environ, {"LOG_DIR": str(self.logs), "DASHBOARD_USERNAME": "", "DASHBOARD_PASSWORD_HASH": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(reset_logging)
        self.stream = io.StringIO()
        lc.configure_logging("test", log_dir=self.logs, stream=self.stream)

    def flush(self):
        for h in logging.getLogger().handlers:
            h.flush()

    def seed(self):
        log = lc.get_logger("tailoring")
        for i in range(12):
            log.info("entry %s", i, extra={"resume_id": "aaaaaaaaaaaa"})
        lc.get_logger("drive").error("Drive upload failed", extra={"resume_id": "bbbbbbbbbbbb"})
        lc.get_logger("tracker").warning("Tracker slow")
        self.flush()

    def test_requires_authentication(self):
        with mock.patch.dict(os.environ, auth_env()):
            self.assertEqual(self.call("GET", "/api/logs")[0], 401)
            self.assertEqual(self.call("GET", "/api/logs", headers={"Authorization": "Bearer nope"})[0], 401)
            self.assertEqual(self.call("GET", "/api/logs", headers={"Cookie": "jobagent_session=" + "x" * 43})[0], 401)
            cookie = self.sign_in()
            s, _, body = self.call("GET", "/api/logs", headers={"Cookie": cookie})
            self.assertEqual(s, 200)
            self.assertIn("entries", body)
            self.call("POST", "/api/auth/logout", {}, headers={"Cookie": cookie})
            self.assertEqual(self.call("GET", "/api/logs", headers={"Cookie": cookie})[0], 401)  # protected again after logout

    def test_returns_structured_entries_newest_first(self):
        self.seed()
        s, _, body = self.call("GET", "/api/logs?limit=5")
        self.assertEqual(s, 200)
        self.assertEqual(len(body["entries"]), 5)
        top = body["entries"][0]
        for field in ("timestamp", "level", "component", "message"):
            self.assertIn(field, top)
        self.assertIn("drive", body["components"])

    def test_level_filter_and_id_filter(self):
        self.seed()
        _, _, errors = self.call("GET", "/api/logs?level=ERROR")
        self.assertEqual([e["message"] for e in errors["entries"]], ["Drive upload failed"])
        _, _, warn = self.call("GET", "/api/logs?level=WARNING")
        self.assertEqual([e["message"] for e in warn["entries"]], ["Tracker slow"])
        _, _, by_resume = self.call("GET", "/api/logs?resume_id=aaaaaaaaaaaa&limit=100")
        self.assertEqual(len(by_resume["entries"]), 12)
        self.assertTrue(all(e["resume_id"] == "aaaaaaaaaaaa" for e in by_resume["entries"]))

    def test_pagination(self):
        self.seed()
        _, _, p1 = self.call("GET", "/api/logs?resume_id=aaaaaaaaaaaa&limit=5&offset=0")
        _, _, p2 = self.call("GET", "/api/logs?resume_id=aaaaaaaaaaaa&limit=5&offset=5")
        _, _, p3 = self.call("GET", "/api/logs?resume_id=aaaaaaaaaaaa&limit=5&offset=10")
        self.assertEqual([len(p["entries"]) for p in (p1, p2, p3)], [5, 5, 2])
        self.assertEqual([p["has_more"] for p in (p1, p2, p3)], [True, True, False])
        msgs = [e["message"] for p in (p1, p2, p3) for e in p["entries"]]
        self.assertEqual(len(set(msgs)), 12)
        self.assertEqual(msgs[0], "entry 11")

    def test_limit_is_capped_and_default_is_bounded(self):
        self.seed()
        self.assertEqual(self.call("GET", "/api/logs?limit=100000")[2]["limit"], log_reader.MAX_LIMIT)
        self.assertEqual(self.call("GET", "/api/logs")[2]["limit"], log_reader.DEFAULT_LIMIT)

    def test_path_traversal_and_bad_values_are_rejected_with_400(self):
        self.seed()
        (self.tmp / "outside.log").write_text('{"level":"INFO","message":"outside","component":"x"}\n')
        for q in ("resume_id=../../etc/passwd", "component=../x", "component=..%2F..%2Fetc", "level=../x", "date=../..",
                  "task_id=%2e%2e%2f", "request_id=a%00b"):
            s, _, body = self.call("GET", "/api/logs?" + q)
            self.assertEqual(s, 400, q)
            self.assertEqual(body["error"]["code"], "bad_log_query")
        s, _, body = self.call("GET", "/api/logs?file=../outside.log&path=/etc/passwd")
        self.assertEqual(s, 200)
        self.assertNotIn("outside", json.dumps(body))  # unknown params never select a file

    def test_other_log_endpoints_do_not_exist_for_arbitrary_files(self):
        for path in ("/api/logs/../settings", "/api/logs/application.log", "/output/logs/application.log", "/logs/application.log"):
            s, _, body = self.call("GET", path, raw=True)
            self.assertNotIn(b'"level"', body if isinstance(body, bytes) else json.dumps(body).encode(), path)

    def test_secrets_in_the_files_are_not_returned(self):
        self.logs.mkdir(parents=True, exist_ok=True)
        with (self.logs / "application.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": "2026-09-21T10:00:00+00:00", "level": "ERROR", "component": "llm",
                                "message": "failed: Authorization: Bearer abcdefghijklmnopqrstuv",
                                "exception": "requests error url=https://x/?api_key=verysecretvalue1"}) + "\n")
        _, _, body = self.call("GET", "/api/logs")
        blob = json.dumps(body)
        self.assertNotIn("abcdefghijklmnopqrstuv", blob)
        self.assertNotIn("verysecretvalue1", blob)

    def test_unreadable_log_dir_gives_a_safe_error_not_a_stack_trace(self):
        with mock.patch.object(log_reader, "query", side_effect=OSError("disk exploded at /app/output/logs")):
            s, _, body = self.call("GET", "/api/logs")
        self.assertEqual(s, 500)
        self.assertEqual(body["error"]["message"], "Unable to load application logs.")
        self.assertNotIn("/app/output", json.dumps(body))

    def test_missing_log_directory_is_an_empty_page(self):
        with mock.patch.dict(os.environ, {"LOG_DIR": str(self.tmp / "never-created")}):
            s, _, body = self.call("GET", "/api/logs")
        self.assertEqual((s, body["entries"]), (200, []))


class Records(logging.Handler):
    """Captures raw LogRecords (before any formatting/redaction) so tests can prove a value was
    never handed to the logger at all, rather than merely masked on the way out."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def raw_text(self):
        return json.dumps([{**{k: str(v) for k, v in r.__dict__.items() if k != "args"}, "msg": r.getMessage()}
                           for r in self.records])


class RequestLogging(ServerCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jobagent-reqlog-"))
        patcher = mock.patch.dict(os.environ, {"LOG_DIR": str(self.tmp / "logs"), "LOG_LEVEL": "DEBUG"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(reset_logging)
        self.stream = io.StringIO()
        lc.configure_logging("test", log_dir=self.tmp / "logs", stream=self.stream)
        self.rec = Records()
        logging.getLogger().addHandler(self.rec)
        self.addCleanup(logging.getLogger().removeHandler, self.rec)

    def requests(self, expect=None):
        """The request records so far. The server logs *after* it has sent the response, so a test
        that just got its reply may be a hair ahead of the log line: poll for `expect` (a path)."""
        end = time.time() + 3
        while True:
            found = [r for r in self.rec.records if r.getMessage() == "Dashboard request"]
            if expect is None or any(getattr(r, "path", None) == expect for r in found) or time.time() > end:
                return found
            time.sleep(0.02)

    def test_request_id_is_generated_and_returned(self):
        _, headers, _ = self.call("GET", "/api/auth")
        self.assertRegex(headers["X-Request-ID"], r"^[0-9a-f]{12}$")
        _, h2, _ = self.call("GET", "/api/auth")
        self.assertNotEqual(headers["X-Request-ID"], h2["X-Request-ID"])

    def test_request_id_is_returned_on_static_and_error_responses_too(self):
        self.assertIn("X-Request-ID", self.call("GET", "/", raw=True)[1])
        s, headers, _ = self.call("GET", "/api/nope")
        self.assertEqual(s, 404)
        self.assertIn("X-Request-ID", headers)

    def test_a_safe_client_request_id_is_preserved_and_used_in_the_logs(self):
        _, headers, _ = self.call("GET", "/api/dashboard/status", headers={"X-Request-ID": "client-req-42"})
        self.assertEqual(headers["X-Request-ID"], "client-req-42")
        self.assertIn('request_id="client-req-42"', self.stream.getvalue())

    def test_an_unsafe_client_request_id_is_replaced(self):
        for bad in ("<script>alert(1)</script>", "has spaces in it", "x" * 200, "a;b"):
            _, headers, _ = self.call("GET", "/api/auth", headers={"X-Request-ID": bad})
            self.assertRegex(headers["X-Request-ID"], r"^[0-9a-f]{12}$", bad)
        self.assertNotIn("<script>", self.stream.getvalue())

    def test_api_requests_are_logged_with_method_path_status_duration_and_request_id(self):
        _, headers, _ = self.call("POST", "/api/tailor", {"title": "", "description": "short"})
        (rec,) = [r for r in self.requests("/api/tailor") if r.path == "/api/tailor"]
        self.assertEqual((rec.method, rec.status, rec.error), ("POST", 400, "invalid_input"))
        self.assertIsInstance(rec.duration_ms, int)
        self.assertEqual(rec.levelno, logging.WARNING)  # 4xx
        line = [l for l in self.stream.getvalue().splitlines() if "Dashboard request" in l and "/api/tailor" in l][-1]
        for part in (f'request_id="{headers["X-Request-ID"]}"', "method=", "duration_ms=", "status=400"):
            self.assertIn(part, line)

    def test_successful_api_calls_are_info_and_polling_or_static_is_debug(self):
        self.call("GET", "/api/dashboard/status")
        self.call("GET", "/js/app.js", raw=True)
        self.call("GET", "/api/tasks/" + "0" * 16)  # 404 -> warning
        levels = {(r.path, r.levelno) for r in self.requests("/api/tasks/" + "0" * 16)}
        self.assertIn(("/api/dashboard/status", logging.INFO), levels)
        self.assertIn(("/js/app.js", logging.DEBUG), levels)
        self.assertIn(("/api/tasks/" + "0" * 16, logging.WARNING), levels)

    def test_query_strings_and_bodies_are_not_logged(self):
        import dashboard_server as srv
        with mock.patch.object(srv.tailoring_service, "tailor_resume", side_effect=RuntimeError("stop before any work")):
            self.call("GET", "/api/jobs?q=very-private-search-term")
            s, _, body = self.call("POST", "/api/tailor", {"title": "T", "company": "Distinctive Co Body", "description": JD_TEXT})
            self.assertEqual(s, 200)
            self.wait_task(body["task_id"])
        raw = self.rec.raw_text() + self.stream.getvalue()
        self.assertNotIn("very-private-search-term", raw)
        self.assertNotIn(JD_TEXT[:60], raw)

    def test_credentials_headers_and_cookies_are_never_logged(self):
        wrong = "wrong-guess-abcdefgh"
        with mock.patch.dict(os.environ, auth_env()):
            self.call("POST", "/api/auth/login", {"username": AUTH_USER, "password": wrong})  # 401
            cookie = self.sign_in()
            session_id = cookie.split("=", 1)[1]
            self.call("GET", "/api/me", headers={"Cookie": cookie})
            self.call("GET", "/api/me", headers={"Authorization": "Bearer bearer-guess-abcdef"})  # 401: bearer is not an auth method
            self.call("POST", "/api/auth/logout", {}, headers={"Cookie": cookie})
            phash = os.environ["DASHBOARD_PASSWORD_HASH"]
        raw = self.rec.raw_text()  # what the code handed to the logger -- before redaction could hide anything
        for secret in (AUTH_PASSWORD, wrong, session_id, phash, "bearer-guess-abcdef"):
            self.assertNotIn(secret, raw)
            self.assertNotIn(secret, self.stream.getvalue())
        messages = [(r.name, r.getMessage()) for r in self.rec.records]
        for event in ("login_failed", "login_success", "logout"):
            self.assertIn(("auth", event), messages)
        self.assertIn(("auth", "Authentication failed"), messages)
        end = time.time() + 3
        while len([r for r in self.requests() if r.path == "/api/me"]) < 2 and time.time() < end:
            time.sleep(0.02)
        self.assertEqual(sorted(r.status for r in self.requests() if r.path == "/api/me"), [200, 401])

    def test_unhandled_errors_are_logged_with_a_traceback_and_return_a_safe_500(self):
        import dashboard_data
        with mock.patch.object(dashboard_data, "summary", side_effect=RuntimeError("kaboom-internal")):
            s, headers, body = self.call("GET", "/api/dashboard/summary")
        self.assertEqual(s, 500)
        self.assertEqual(body["error"]["message"], "Internal error")
        self.assertNotIn("kaboom", json.dumps(body))
        errors = [r for r in self.rec.records if r.levelno >= logging.ERROR and r.exc_info]
        self.assertTrue(any("kaboom-internal" in str(r.exc_info[1]) for r in errors))
        self.assertIn("X-Request-ID", headers)
        self.assertTrue(any(r.status == 500 and r.levelno == logging.ERROR for r in self.requests("/api/dashboard/summary")))

    def test_a_client_that_hangs_up_is_not_logged_as_a_server_error(self):
        import http.client
        import dashboard_data
        for exc in (ConnectionAbortedError("aborted"), ConnectionResetError("reset"), BrokenPipeError("pipe")):
            with mock.patch.object(dashboard_data, "summary", side_effect=exc):
                with self.assertRaises((http.client.RemoteDisconnected, ConnectionError)):
                    self.call("GET", "/api/dashboard/summary")
        found = self.requests("/api/dashboard/summary")
        self.assertFalse([r for r in self.rec.records if r.levelno >= logging.ERROR], "disconnects must not reach errors.log")
        self.assertTrue(found and all(r.levelno == logging.DEBUG for r in found if r.path == "/api/dashboard/summary"))

    def test_existing_activity_log_is_untouched_by_logging(self):
        with TempOutput():
            activity.log_event("resume_tailored", "Resume tailored for Acme", company="Acme")
            events = activity.read_events()
        self.assertEqual([(e["kind"], e["message"]) for e in events], [("resume_tailored", "Resume tailored for Acme")])
        self.assertNotIn("Resume tailored for Acme", self.stream.getvalue())  # separate stream: not mirrored


class TaskLogging(LogCase):
    def wait(self, task, timeout=5):
        end = time.time() + timeout
        while task.status not in ("done", "error") and time.time() < end:
            time.sleep(0.02)
        time.sleep(0.05)
        self.assertIn(task.status, ("done", "error"))

    def test_task_lifecycle_is_logged_with_task_and_request_ids(self):
        self.configure()

        def work(task):
            task.set_stage("a")
            task.set_stage("b")
            task.result = {"resume_id": "abcdefabcdef"}

        with lc.bind(request_id="req-7"):
            task = tasks.start("tailor", [("a", "A"), ("b", "B")], work)
        self.wait(task)
        mine = [e for e in self.entries() if e.get("task_id") == task.id]
        messages = [e["message"] for e in mine]
        self.assertEqual(messages, ["Task started", "Task stage changed", "Task stage changed", "Task completed"])
        self.assertTrue(all(e["component"] == "dashboard.tasks" for e in mine))
        self.assertTrue(all(e.get("request_id") == "req-7" for e in mine))  # the request that started it, in a new thread
        self.assertEqual(mine[-1]["resume_id"], "abcdefabcdef")
        self.assertIn("duration_ms", mine[-1])

    def test_failed_task_logs_an_error_with_traceback_and_keeps_its_behaviour(self):
        self.configure()

        def work(task):
            raise ValueError("kaboom in work")

        task = tasks.start("export", [("x", "X")], work)
        self.wait(task)
        self.assertEqual(task.status, "error")
        self.assertIn("ValueError", task.error["message"])
        failed = [e for e in self.entries("errors.log") if e["message"] == "Task failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["task_id"], task.id)
        self.assertIn("kaboom in work", failed[0]["exception"])

    def test_busy_rejection_is_logged_and_still_raises(self):
        self.configure()
        lock = threading.Lock()
        lock.acquire()
        with self.assertRaises(tasks.Busy):
            tasks.start("pipeline", [("x", "X")], lambda t: None, exclusive=lock)
        self.assertIn("Task rejected: another run is in progress", [e["message"] for e in self.entries()])


class TailoringLogging(LogCase):
    def test_happy_path_logs_the_lifecycle_with_ids_and_no_content(self):
        self.configure()
        rec, _, _ = run_tailor([reorder_bullets(BASE)], job_key="abc123abc123")
        msgs = [e["message"] for e in self.entries()]
        for expected in ("Tailoring started", "JD analysis completed", "Resume match completed", "Resume generation completed",
                         "ATS validation completed", "No-fabrication verification passed", "Resume record created",
                         "Resume version stored", "Tailoring completed"):
            self.assertIn(expected, msgs)
        done = [e for e in self.entries() if e["message"] == "Tailoring completed"][0]
        self.assertEqual((done["resume_id"], done["job_id"], done["company"]), (rec["id"], "abc123abc123", "Acme"))
        self.assertEqual(done["verification"], "passed")
        out = self.everything_written()
        self.assertNotIn(JD_TEXT[:80], out)
        for line in ("Developed Python backend services", "Optimized MySQL database performance", "jane@example.com"):
            self.assertNotIn(line, out)  # no resume text

    def test_fact_check_failure_retry_and_fallback_are_logged(self):
        self.configure()
        run_tailor([FAKE_KAFKA, FAKE_ROLE])
        by = [(e["component"], e["level"], e["message"]) for e in self.entries()]
        self.assertIn(("verification", "WARNING", "Resume fact-check failed"), by)
        self.assertIn(("tailoring", "INFO", "Retrying resume generation"), by)
        self.assertIn(("verification", "WARNING", "Resume fact-check failed after retry"), by)
        self.assertIn(("tailoring", "INFO", "Using reorder-only fallback"), by)
        self.assertIn(("verification", "INFO", "Fallback resume verification passed"), by)
        done = [e for e in self.entries() if e["message"] == "Tailoring completed"][0]
        self.assertEqual((done["fallback"], done["kind"]), (True, "conservative"))
        first = [e for e in self.entries() if e["message"] == "Resume fact-check failed"][0]
        self.assertEqual((first["attempt"], first["problem_count"] > 0), (1, True))
        self.assertNotIn(JD_TEXT[:80], self.everything_written())

    def test_fact_check_retry_that_succeeds(self):
        self.configure()
        run_tailor([FAKE_KAFKA, reorder_bullets(BASE)])
        msgs = [e["message"] for e in self.entries()]
        self.assertIn("Resume fact-check failed", msgs)
        self.assertIn("Retrying resume generation", msgs)
        self.assertIn("No-fabrication verification passed", msgs)
        self.assertNotIn("Using reorder-only fallback", msgs)

    def test_failures_are_logged_with_a_traceback_and_still_raised(self):
        self.configure()
        boom = RuntimeError("provider exploded")
        with TempOutput():
            with mock.patch("llm.call_llm", side_effect=boom):
                with self.assertRaises(RuntimeError):
                    import tailoring_service as ts
                    from test_tailoring_service import JOB
                    ts.tailor(JOB, source="manual", base_md=BASE, job_key="jjjjjjjjjjjj")
        (failed,) = [e for e in self.entries("errors.log") if e["message"] == "Tailoring failed"]
        self.assertEqual(failed["job_id"], "jjjjjjjjjjjj")
        self.assertIn("provider exploded", failed["exception"])

    def test_user_input_problems_are_warnings_not_errors(self):
        self.configure()
        import jd_analysis
        import tailoring_service as ts
        from test_tailoring_service import JOB
        with TempOutput():
            with mock.patch.object(jd_analysis, "compute_match", return_value={"overall": None, "skills": {}, "missing_skills": []}), \
                    mock.patch("llm.call_llm", return_value=json.dumps(ANALYSIS)):
                with self.assertRaises(jd_analysis.InputError):
                    ts.tailor(JOB, source="manual", base_md=BASE)
        self.assertEqual(self.entries("errors.log"), [])
        self.assertIn("Tailoring rejected", [e["message"] for e in self.entries()])


class LlmLogging(LogCase):
    def test_llm_calls_log_provider_metadata_but_never_prompt_or_response(self):
        from test_llm import FakeResp, load_llm
        self.configure()
        llm = load_llm(GROQ_API_KEY="gsk_key_should_never_appear_1234", OPENROUTER_API_KEY="or-key-never-appear-5678")
        prompt = "PROMPT-BODY-THAT-MUST-NOT-BE-LOGGED with the whole resume"
        answers = [FakeResp(500), FakeResp(500), FakeResp(200, text="RESPONSE-BODY-MUST-NOT-BE-LOGGED")]
        with mock.patch.object(llm, "_post", side_effect=answers), mock.patch("time.sleep"):
            out = llm.call_llm(prompt, "Java Developer")
        self.assertIn("RESPONSE-BODY", out)
        entries = [e for e in self.entries() if e["component"] == "llm"]
        msgs = [e["message"] for e in entries]
        for expected in ("LLM request started", "LLM request failed", "LLM fallback provider selected", "LLM request completed"):
            self.assertIn(expected, msgs)
        started = [e for e in entries if e["message"] == "LLM request started"][0]
        self.assertEqual((started["provider"], started["label"]), ("groq", "Java Developer"))
        self.assertTrue(all("duration_ms" in e for e in entries if e["message"] == "LLM request completed"))
        out_text = self.everything_written()
        for secret in ("PROMPT-BODY", "RESPONSE-BODY", "gsk_key_should_never_appear_1234", "or-key-never-appear-5678"):
            self.assertNotIn(secret, out_text)

    def test_all_providers_failing_is_logged_as_an_error_and_still_raises(self):
        from test_llm import FakeResp, load_llm
        self.configure()
        llm = load_llm(GROQ_API_KEY="g")
        with mock.patch.object(llm, "_post", return_value=FakeResp(401)), mock.patch("time.sleep"):
            with self.assertRaises(llm.AllProvidersFailed):
                llm.call_llm("p", "job")
        self.assertIn("All LLM providers failed", [e["message"] for e in self.entries("errors.log")])


class DriveAndTrackerLogging(LogCase):
    def test_drive_upload_is_logged_and_idempotent_reuse_is_visible(self):
        import drive_resumes as dr
        from fixtures import FakeDrive
        from test_drive_resumes import make_resume
        self.configure()
        with TempOutput():
            drive = FakeDrive()
            with mock.patch.object(dr, "_gws", side_effect=drive), mock.patch.dict(os.environ, {"google_drive_folder_id": "PARENT"}):
                rid = make_resume()
                dr.upload_resume(rid, 1, "pdf")
                dr.upload_resume(rid, 1, "pdf")
        drive_entries = [e for e in self.entries("drive.log")]
        msgs = [e["message"] for e in drive_entries]
        self.assertEqual(msgs.count("Drive upload started"), 2)
        self.assertEqual(msgs.count("Drive upload completed"), 1)
        self.assertEqual(msgs.count("Drive existing file reused"), 1)
        done = [e for e in drive_entries if e["message"] == "Drive upload completed"][0]
        self.assertEqual((done["resume_id"], done["format"], done["version"]), (rid, "pdf", 1))
        self.assertIn("drive_file_id", done)

    def test_drive_failure_is_logged_with_traceback_and_still_raises(self):
        import drive_resumes as dr
        from test_drive_resumes import make_resume
        self.configure()
        with TempOutput():
            with mock.patch.object(dr, "_gws", side_effect=RuntimeError("network down")), \
                    mock.patch.object(dr, "RETRY_DELAY_SECONDS", 0), mock.patch.dict(os.environ, {"google_drive_folder_id": "PARENT"}):
                rid = make_resume()
                with self.assertRaises(dr.DriveUploadError):
                    dr.upload_resume(rid, 1, "pdf")
        self.assertIn("Drive upload retry", [e["message"] for e in self.entries("drive.log")])
        (failed,) = [e for e in self.entries("errors.log") if e["message"] == "Drive upload failed"]
        self.assertEqual(failed["resume_id"], rid)
        self.assertIn("network down", failed["exception"])

    def test_tracker_create_duplicate_and_failure_are_logged(self):
        import tracker_service as ts
        self.configure()
        tr = ts.Tracker()
        rows = []
        drive_url = "https://drive.google.com/file/d/X/view"
        with mock.patch.object(ts.write_sheet, "ensure_extra_headers"), \
                mock.patch.object(ts.write_sheet, "read_tracker", side_effect=lambda sid: list(rows)), \
                mock.patch.object(ts.write_sheet, "append_rows", side_effect=lambda sid, r: rows.append(
                    {"row": 2, "link": "https://acme.example/jobs/1", "resume_path": drive_url, "title": "T", "company": "Acme"})), \
                mock.patch.dict(os.environ, {"google_sheet_id": "SHEET"}), TempOutput():
            args = dict(title="T", company="Acme", link="https://acme.example/jobs/1", score=9, resume_path=drive_url,
                        resume_id="rid1-v1", match_pct=80)
            self.assertEqual(tr.save_job(**args)["result"], "added")
            self.assertEqual(tr.save_job(**args)["result"], "exists")
            with mock.patch.object(ts.write_sheet, "read_tracker", side_effect=RuntimeError("sheets 500")):
                with self.assertRaises(ts.TrackerError):
                    tr.save_job(**{**args, "link": "https://acme.example/jobs/2"})
        msgs = [e["message"] for e in self.entries("tracker.log")]
        self.assertIn("Tracker row created", msgs)
        self.assertIn("Duplicate tracker row avoided", msgs)
        self.assertIn("Tracker row save failed", [e["message"] for e in self.entries("errors.log")])
        created = [e for e in self.entries("tracker.log") if e["message"] == "Tracker row created"][0]
        self.assertEqual((created["resume_id"], created["company"]), ("rid1-v1", "Acme"))
        self.assertNotIn(drive_url, self.everything_written())  # links/rows are not dumped into logs


if __name__ == "__main__":
    unittest.main()
