"""Deployment contract for the Modal-hosted dashboard (UI + API from ONE web function).

`modal` is stubbed, so this runs without the package and without network/Modal access. The
checks are on modal_app.py's source plus its pure startup validation -- they pin the
architecture (one function, one origin, /app/output volume, cron untouched) so a refactor can't
silently regress it. A real `modal deploy` is still the only proof the container starts.

Stdlib only: python -m unittest discover -s tests -v
"""
import http.client
import importlib
import json
import os
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import fixtures  # noqa: F401 -- disables .env loading before any script is imported

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "modal_app.py").read_text(encoding="utf-8")
DASHBOARD_BODY = SRC[SRC.index("\ndef dashboard("):SRC.index("@app.local_entrypoint")]


def load_modal_app():
    with mock.patch.dict(sys.modules, {"modal": mock.MagicMock()}):
        sys.modules.pop("modal_app", None)
        mod = importlib.import_module("modal_app")
    sys.modules.pop("modal_app", None)
    return mod


def decorator_block(func_name):
    """The @app.function(...) decorator text immediately above `def <func_name>`."""
    end = SRC.index(f"\ndef {func_name}(")
    return SRC[SRC.rindex("@app.function(", 0, end):end]


class DashboardFunction(unittest.TestCase):
    def test_dashboard_function_is_a_web_server_on_8765(self):
        self.assertIn("DASHBOARD_PORT = 8765", SRC)
        self.assertRegex(SRC, r"@modal\.web_server\(DASHBOARD_PORT")
        self.assertRegex(SRC, r"\ndef dashboard\(")

    def test_serves_through_the_existing_stdlib_server_only(self):
        self.assertIn("import dashboard_server", SRC)
        self.assertIn('dashboard_server.make_server("0.0.0.0", DASHBOARD_PORT)', SRC)
        for framework in ("fastapi", "flask", "asgi_app", "wsgi_app", "CORSMiddleware", "Access-Control-Allow"):
            self.assertNotIn(framework, SRC, f"{framework}: UI and API must stay on the one stdlib server")

    def test_image_ships_scripts_web_and_resume_at_explicit_remote_paths(self):
        self.assertIn('SCRIPTS_REMOTE_PATH = "/app/scripts"', SRC)
        self.assertRegex(SRC, r'add_local_dir\(\s*"scripts",\s*remote_path=SCRIPTS_REMOTE_PATH\s*\)')
        self.assertRegex(SRC, r'add_local_dir\(\s*"web",\s*remote_path="/app/web"\s*\)')
        self.assertRegex(SRC, r'add_local_dir\(\s*"resume",\s*remote_path="/app/resume"\s*\)')
        self.assertIn("/usr/local/bin/gws", SRC)  # the Drive/Sheets CLI is installed in the shared image
        self.assertTrue((ROOT / "web" / "index.html").is_file())

    def test_image_layout_matches_what_the_server_resolves_at_runtime(self):
        # paths.ROOT = <scripts dir>/.. -> /app in the image, so web and resume must sit under it.
        import paths
        self.assertEqual(paths.WEB_DIR, paths.ROOT / "web")
        self.assertEqual(paths.BASE_RESUME, paths.ROOT / "resume" / "base_resume.md")

    def test_dashboard_uses_the_dedicated_volume_at_app_output(self):
        self.assertRegex(SRC, r'modal\.Volume\.from_name\("job-apply-agent-dashboard"')
        self.assertIn('volumes={"/app/output": dashboard_volume}', decorator_block("dashboard"))

    def test_single_container_scale_to_zero(self):
        block = decorator_block("dashboard")
        self.assertRegex(block, r"max_containers=1\b")
        self.assertNotRegex(block, r"min_containers=[1-9]")

    def test_dashboard_reads_the_shared_secret_and_shared_gws_helper(self):
        block = decorator_block("dashboard")
        self.assertIn('modal.Secret.from_name("job-apply-agent-secrets")', block)
        self.assertIn('modal.Secret.from_name("gws-credentials")', block)
        self.assertIn("materialize_gws_credentials()", DASHBOARD_BODY)
        self.assertEqual(len(re.findall(r"credentials\.json", SRC)), 1, "credential setup must exist once")

    def test_no_secret_values_are_committed(self):
        self.assertNotRegex(SRC, r"DASHBOARD_TOKEN\s*=\s*[\"'][^\"']+[\"']")


class CronUnchanged(unittest.TestCase):
    def test_cron_schedule_secrets_and_timeout_are_as_before(self):
        block = decorator_block("run_pipeline")
        self.assertIn('schedule=modal.Cron("0 7 * * 1-5", timezone="Asia/Kolkata")', block)
        self.assertIn("timeout=21600", block)
        self.assertIn('modal.Secret.from_name("job-apply-agent-secrets")', block)
        self.assertIn('modal.Secret.from_name("gws-credentials")', block)

    def test_cron_stays_stateless_no_dashboard_volume(self):
        block = decorator_block("run_pipeline")
        self.assertNotIn("volumes", block)
        self.assertNotIn("dashboard_volume", block)
        self.assertNotIn("max_containers", block)

    def test_cron_pipeline_steps_and_limits_are_untouched(self):
        for step in ('run("scrape_jobs.py", timeout=600', 'run("score_jobs.py", timeout=6000)',
                     'run("tailor_job.py", timeout=7200)', 'run("company_research.py", timeout=5400)',
                     'run("write_sheet.py", timeout=300)', "reconcile_qualified(scored, tailored)",
                     "JOB-APPLY-AGENT — WHAT BROKE"):
            self.assertIn(step, SRC)

    def test_cron_uses_the_shared_credential_helper(self):
        cron_body = SRC[SRC.index("\ndef run_pipeline("):SRC.index("\n@app.function(", SRC.index("\ndef run_pipeline("))]
        self.assertIn("materialize_gws_credentials()", cron_body)
        self.assertNotIn("credentials.json", cron_body)


class ModalLogging(unittest.TestCase):
    def test_cron_uses_central_logging_console_only_and_no_volume(self):
        start = SRC.index("\ndef run_pipeline(")
        cron_body = SRC[start:SRC.index("\n@app.function(", start)]
        self.assertIn('lc.configure_logging("cron", to_file=False)', cron_body)
        self.assertIn('os.environ["LOG_TO_FILE"] = "0"', cron_body)  # children inherit console-only too
        self.assertIn('log.info("Pipeline started"', cron_body)
        self.assertIn('log.info("Pipeline completed"', cron_body)
        self.assertIn("log.exception(", cron_body)  # failures carry a traceback into Modal's logs
        self.assertNotIn("print(", cron_body)
        self.assertNotIn("traceback.print_exc", cron_body)
        self.assertNotIn("volumes", decorator_block("run_pipeline"))  # logging must not attach the dashboard Volume

    def test_dashboard_configures_file_logging_before_validating_and_logs_startup(self):
        self.assertLess(DASHBOARD_BODY.index('lc.configure_logging("dashboard")'), DASHBOARD_BODY.index("validate_dashboard_config("))
        for message in ("Dashboard starting", "Dashboard configuration validated", "Output directory initialized",
                        "Dashboard server starting", "Dashboard configuration invalid"):
            self.assertIn(message, DASHBOARD_BODY)
        self.assertNotIn("print(", DASHBOARD_BODY)
        self.assertNotIn("DASHBOARD_TOKEN\"]", DASHBOARD_BODY)  # the token is never passed to a logger

    def test_logs_live_on_the_existing_dashboard_volume_and_no_second_volume_exists(self):
        self.assertEqual(SRC.count("modal.Volume.from_name("), 1)
        self.assertIn('volumes={"/app/output": dashboard_volume}', decorator_block("dashboard"))


class StartupValidation(unittest.TestCase):
    GOOD = {"DASHBOARD_TOKEN": "t" * 32, "DASHBOARD_ALLOWED_HOSTS": "ws--job-apply-agent-dashboard.modal.run"}

    @classmethod
    def setUpClass(cls):
        cls.check = staticmethod(load_modal_app().validate_dashboard_config)

    def test_valid_config_passes(self):
        self.check(self.GOOD)

    def test_missing_or_blank_token_fails_startup(self):
        for env in ({k: v for k, v in self.GOOD.items() if k != "DASHBOARD_TOKEN"}, {**self.GOOD, "DASHBOARD_TOKEN": "  "}):
            with self.assertRaisesRegex(RuntimeError, "DASHBOARD_TOKEN"):
                self.check(env)

    def test_missing_or_blank_allowed_hosts_fails_startup(self):
        for env in ({"DASHBOARD_TOKEN": "x"}, {**self.GOOD, "DASHBOARD_ALLOWED_HOSTS": ""}):
            with self.assertRaisesRegex(RuntimeError, "DASHBOARD_ALLOWED_HOSTS"):
                self.check(env)

    def test_local_llm_endpoint_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "llm_base_url"):
            self.check({**self.GOOD, "llm_base_url": "http://localhost:11434/v1"})

    def test_dashboard_validates_before_starting_the_server(self):
        self.assertLess(DASHBOARD_BODY.index("validate_dashboard_config(os.environ)"), DASHBOARD_BODY.index("make_server("))


class ModalBindAndHost(unittest.TestCase):
    """The real server, bound the way the Modal function binds it (0.0.0.0 + token + allowed host)."""

    HOST = "ws--job-apply-agent-dashboard.modal.run"

    def setUp(self):
        import dashboard_server as srv
        self.srv = srv
        self._saved_hosts = srv.Handler.allowed_hosts
        env = mock.patch.dict(os.environ, {"DASHBOARD_TOKEN": "s3cret", "DASHBOARD_ALLOWED_HOSTS": self.HOST})
        env.start()
        self.addCleanup(env.stop)
        self.server = srv.make_server("0.0.0.0", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self._teardown)

    def _teardown(self):
        self.server.shutdown()
        self.server.server_close()
        self.srv.Handler.allowed_hosts = self._saved_hosts

    def get(self, path, host, token=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        conn.request("GET", path, headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, r.getheader("Content-Type", ""), body

    def test_binds_publicly_and_serves_ui_and_api_from_one_origin(self):
        status, ctype, body = self.get("/", self.HOST)
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"/js/app.js", body)
        status, _, body = self.get("/api/auth", self.HOST)
        self.assertEqual((status, json.loads(body)), (200, {"required": True}))
        for asset in ("/js/app.js", "/css/app.css"):
            self.assertEqual(self.get(asset, self.HOST)[0], 200, asset)

    def test_api_needs_the_token_and_rejects_a_wrong_one(self):
        self.assertEqual(self.get("/api/settings", self.HOST)[0], 401)
        self.assertEqual(self.get("/api/settings", self.HOST, token="wrong")[0], 401)

    def test_unlisted_hosts_are_refused(self):
        self.assertEqual(self.get("/", "evil.example.com")[0], 403)
        self.assertEqual(self.get("/api/auth", "evil.example.com")[0], 403)


if __name__ == "__main__":
    unittest.main()
