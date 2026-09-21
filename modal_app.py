"""Modal deployment for the job-apply-agent pipeline (PRD 3.7).

Runs the full pipeline unattended every morning: scrape -> score -> tailor
(OpenRouter, not claude -p -- see CLAUDE.md for why) -> company research ->
write to the Job Application Tracker Sheet. Resumes land in a Google Drive
folder (Modal has no access to the local Desktop), linked from the Sheet.

Deploy:  modal deploy modal_app.py
Test:    modal run modal_app.py
"""
import sys
from pathlib import Path

import modal

# Where the image below copies scripts/. Locally scripts/ sits next to this file, but in the
# Modal container this file is auto-mounted alone at /root/, so <this file>/scripts does not
# exist there -- only the image copy does. Put both on sys.path or `import modal_app` fails
# at container startup (ModuleNotFoundError: job_links) and run_pipeline crash-loops.
SCRIPTS_REMOTE_PATH = "/app/scripts"
for _scripts_dir in (Path(__file__).resolve().parent / "scripts", Path(SCRIPTS_REMOTE_PATH)):
    if _scripts_dir.is_dir():
        sys.path.insert(0, str(_scripts_dir))
from job_links import canonical_link  # noqa: E402
import logging_config as lc  # noqa: E402

log = lc.get_logger("pipeline")
dashboard_log = lc.get_logger("dashboard")

app = modal.App("job-apply-agent")


def reconcile_qualified(scored, tailored):
    """Return (unmet_links, reason_by_link): qualified jobs with no saved resume.

    Pure function so the no-silent-failure guard is unit-testable. Empty unmet == the
    run genuinely delivered every qualifying job. Compared by *canonical* link
    (tracking-param-stripped, see scripts/job_links.py), not the raw one -- a posting
    re-scraped on a different day gets a new trackingId/position but is still the same
    job, and tailor_job.py correctly skips creating a second Drive folder for it, so
    that must not read here as "no saved resume". A job saved on an earlier run still
    counts as met either way.
    """
    qualified_links = {canonical_link(j["link"]) for j in scored if j.get("qualified")}
    saved_links = {canonical_link(j["link"]) for j in tailored if j.get("status") == "saved"}
    unmet = qualified_links - saved_links
    reason_by_link = {
        canonical_link(j["link"]): j.get("reason", j.get("status", "no tailored_jobs entry"))
        for j in tailored if canonical_link(j.get("link")) in unmet
    }
    return unmet, reason_by_link

GWS_VERSION = "v0.22.5"
GWS_URL = (
    f"https://github.com/googleworkspace/cli/releases/download/{GWS_VERSION}/"
    "google-workspace-cli-x86_64-unknown-linux-musl.tar.gz"
)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl")
    .pip_install("requests", "python-dotenv")
    .run_commands(
        f"curl -fsSL {GWS_URL} -o /tmp/gws.tar.gz",
        "tar -xzf /tmp/gws.tar.gz -C /usr/local/bin",
        "chmod +x /usr/local/bin/gws",
        "rm /tmp/gws.tar.gz",
    )
    .add_local_dir("scripts", remote_path=SCRIPTS_REMOTE_PATH)
    .add_local_dir("resume", remote_path="/app/resume")
    .add_local_dir("web", remote_path="/app/web")
)

# Dashboard state (generated resumes, activity log, preferences, pipeline artifacts a dashboard
# action produces). Mounted at /app/output because the pipeline scripts hardcode ROOT/"output".
# Deliberately NOT attached to the cron: that run stays stateless and resume-safe via the Drive
# mirror, so a stale volume can never change what the daily run scrapes or skips.
dashboard_volume = modal.Volume.from_name("job-apply-agent-dashboard", create_if_missing=True)
DASHBOARD_PORT = 8765


def materialize_gws_credentials():
    """gws credentials are Keychain-encrypted locally; a container has no Keychain, so write the
    plain credentials file gws also supports and keep its token cache on disk."""
    import json
    import os

    os.environ["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] = "file"
    config_dir = Path.home() / ".config" / "gws"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "credentials.json").write_text(json.dumps({
        "client_id": os.environ["client_id"],
        "client_secret": os.environ["client_secret"],
        "refresh_token": os.environ["refresh_token"],
        "type": os.environ["type"],
    }))


def validate_dashboard_config(env):
    """Fail-fast startup checks for the dashboard (pure, so they are unit-testable).

    The dashboard exposes the real resume, tracker and Drive links, so it must never come up
    unauthenticated or with Host-header validation off. Raises RuntimeError naming the fix.
    """
    if not (env.get("DASHBOARD_TOKEN") or "").strip():
        raise RuntimeError("DASHBOARD_TOKEN is not set in the Modal secret -- refusing to serve the "
                           "dashboard unauthenticated. Add a long random token to job-apply-agent-dashboard-secrets.")
    if not (env.get("DASHBOARD_ALLOWED_HOSTS") or "").strip():
        raise RuntimeError("DASHBOARD_ALLOWED_HOSTS is not set in the Modal secrets -- add the deployed "
                           "*.modal.run hostname without https:// (the Host-header check would reject every request).")
    if (env.get("llm_base_url") or "").strip():
        raise RuntimeError("llm_base_url is set in the Modal secret -- Modal must use the "
                           "cloud provider chain, not a local endpoint. Remove it from the secret.")


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("job-apply-agent-secrets"),
        modal.Secret.from_name("gws-credentials"),
    ],
    # Mon-Fri only -- skips 2 paid Apify scrapes/week. NOTE: scrape_jobs.py's window is
    # a fixed past24Hours, so postings from Fri evening through Sunday are never caught
    # (accepted tradeoff, not a bug -- see CLAUDE.md/commit history if revisiting this).
    schedule=modal.Cron("0 7 * * 1-5", timezone="Asia/Kolkata"),
    # Generous outer cap -- the per-step timeouts below (which sum to well under this)
    # are what's meant to actually fire first. A platform-level timeout kill bypasses
    # the try/except/Telegram-alert logic entirely, which violates the "no silent
    # failures" rule -- so each step gets its own subprocess timeout that raises
    # TimeoutExpired *inside* run_pipeline, where it's caught and alerted like any
    # other failure, well before this outer timeout could ever be hit.
    timeout=21600,
)
def run_pipeline(force: bool = False):
    import json
    import os
    import subprocess
    import time
    from pathlib import Path

    import requests

    # stdout/stderr -> Modal runtime logs. Console only: this container has no Volume, so files
    # would vanish with it. Exported so the pipeline subprocesses below inherit the same choice.
    os.environ["LOG_TO_FILE"] = "0"
    lc.configure_logging("cron", to_file=False)
    started = time.monotonic()

    def send_telegram_alert(message):
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            log.warning("Telegram alert skipped: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set")
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message},
                timeout=15,
            )
        except Exception as alert_error:
            log.warning("Telegram alert failed to send", extra={"reason": f"{type(alert_error).__name__}: {alert_error}"})

    stage = {"name": "startup"}
    log.info("Pipeline started", extra={"mode": "cron", "force": force})

    try:
        materialize_gws_credentials()

        workdir = Path("/app")
        (workdir / "output").mkdir(exist_ok=True)

        # Config sanity check (fail fast, before spending an Apify scrape). Modal must run
        # the cloud provider chain, never local Ollama -- localhost inside this container
        # is not the user's machine. And at least one free provider key must be present.
        stage["name"] = "config check"
        if os.environ.get("llm_base_url", "").strip():
            raise RuntimeError("llm_base_url is set in the Modal secret -- Modal must use the "
                               "cloud provider chain, not a local endpoint. Remove it from the secret.")
        provider_keys = [k for k in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "open_router_apikey", "GEMINI_API_KEY")
                         if os.environ.get(k, "").strip()]
        if not provider_keys:
            raise RuntimeError("no free LLM provider key in the Modal secret -- set at least "
                               "GROQ_API_KEY (see scripts/llm.py).")
        log.info("LLM providers available", extra={"providers": ",".join(provider_keys)})

        def run(script, timeout, args=None):
            stage["name"] = script
            log.info("Pipeline step started", extra={"script": script})
            step_started = time.monotonic()
            cmd = ["python3", f"scripts/{script}", *(args or [])]
            subprocess.run(cmd, check=True, cwd=workdir, timeout=timeout)
            log.info("Pipeline step finished", extra={"script": script,
                                                       "duration_seconds": round(time.monotonic() - step_started, 1)})

        # Per-step timeouts sized generously for the free-provider chain: each job may walk
        # groq -> openrouter -> gemini, each with retries/backoff (see scripts/llm.py), plus
        # llm_request_delay_seconds spacing between calls. Worst case is minutes/job; these
        # caps are the outer bound before the step is killed and alerted.
        run("scrape_jobs.py", timeout=600, args=["--force"] if force else None)
        run("score_jobs.py", timeout=6000)
        run("tailor_job.py", timeout=7200)
        run("company_research.py", timeout=5400)
        run("write_sheet.py", timeout=300)

        # Post-run reconciliation. No single step raising doesn't mean the run
        # succeeded: on 2026-09-07 every step exited 0 but the one qualifying job
        # timed out during tailoring, got flagged (not raised), and the run reported
        # "complete" while delivering nothing -- a silent failure the "no silent
        # failures" rule is meant to catch. So: if a job cleared the 8+ cutoff but
        # never produced a saved resume, alert (and raise) instead of reporting success.
        stage["name"] = "post-run reconciliation"
        scored = json.loads((workdir / "output" / "scored_jobs.json").read_text())
        tailored_path = workdir / "output" / "tailored_jobs.json"
        tailored = json.loads(tailored_path.read_text()) if tailored_path.exists() else []
        qualified_total = sum(1 for j in scored if j.get("qualified"))
        unmet, reasons = reconcile_qualified(scored, tailored)
        if unmet:
            detail = "\n".join(f"  - {link}: {reasons.get(link, 'never reached tailoring')}" for link in unmet)
            raise RuntimeError(
                f"{len(unmet)}/{qualified_total} qualified job(s) produced no saved resume:\n{detail}"
            )

        log.info("JOB-APPLY-AGENT — daily run complete", extra={
            "scored_count": len(scored), "qualified_count": qualified_total,
            "duration_seconds": round(time.monotonic() - started, 1)})
        log.info("Pipeline completed", extra={"duration_seconds": round(time.monotonic() - started, 1)})
    except Exception as e:
        failed_stage = stage["name"]
        log.exception(f"JOB-APPLY-AGENT — PIPELINE FAILED at {failed_stage}", extra={
            "stage": failed_stage, "duration_seconds": round(time.monotonic() - started, 1)})
        # Stage name + exception type/message only -- never secrets. If every free LLM
        # provider was throttled/down, recover locally with Ollama (see README).
        send_telegram_alert(
            "JOB-APPLY-AGENT — WHAT BROKE\n\n"
            f"Stage: {failed_stage}\n"
            f"Reason: {type(e).__name__}: {e}"
        )
        raise


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("job-apply-agent-secrets"),
        modal.Secret.from_name("gws-credentials"),
        # Dashboard-only keys (DASHBOARD_TOKEN, DASHBOARD_ALLOWED_HOSTS). Kept out of the shared
        # secret so adding them never means re-listing (and risking) the pipeline's API keys.
        modal.Secret.from_name("job-apply-agent-dashboard-secrets"),
    ],
    volumes={"/app/output": dashboard_volume},
    # One container only: dashboard_tasks keeps task state in memory and the stdlib server
    # serialises LLM work behind a single slot, so a second container would split both.
    max_containers=1,
    # Scale to zero when idle, but stay up long enough that a tailoring/export task the UI is
    # polling is never cut off. Requests in flight keep the container alive regardless.
    scaledown_window=900,
    timeout=21600,
)
@modal.concurrent(max_inputs=32)
@modal.web_server(DASHBOARD_PORT, startup_timeout=60)
def dashboard():
    """Serves web/ + the JSON API (scripts/dashboard_server.py) at the function's modal.run URL.

    Required in job-apply-agent-dashboard-secrets:
      DASHBOARD_TOKEN          bearer token the UI prompts for -- the server refuses to start
                               without it, since this exposes the real resume and tracker.
      DASHBOARD_ALLOWED_HOSTS  the deployed hostname (e.g. <workspace>--job-apply-agent-dashboard.modal.run);
                               known only after the first deploy, then add it and redeploy.
    """
    import os
    import threading
    import time

    # Console (-> Modal runtime logs) + rotating files under /app/output/logs on the dashboard Volume.
    # Configure first so a rejected configuration below is itself logged, not just raised.
    (Path("/app") / "output").mkdir(exist_ok=True)
    log_dir = lc.configure_logging("dashboard")
    dashboard_log.info("Dashboard starting", extra={"log_dir": str(log_dir) if log_dir else "console-only"})
    try:
        validate_dashboard_config(os.environ)
    except RuntimeError as e:
        dashboard_log.critical("Dashboard configuration invalid", extra={"reason": str(e)})
        raise
    dashboard_log.info("Dashboard configuration validated")

    materialize_gws_credentials()
    dashboard_log.info("Output directory initialized", extra={"path": "/app/output"})

    import dashboard_server  # noqa: E402 -- needs SCRIPTS_REMOTE_PATH on sys.path (set at top)

    server = dashboard_server.make_server("0.0.0.0", DASHBOARD_PORT)  # SystemExit if DASHBOARD_TOKEN unset
    threading.Thread(target=server.serve_forever, daemon=True).start()
    dashboard_log.info("Dashboard server starting", extra={"host": "0.0.0.0", "port": DASHBOARD_PORT})

    def commit_volume_periodically():
        while True:
            time.sleep(30)
            try:
                dashboard_volume.commit()
            except Exception as e:  # noqa: BLE001 -- best-effort; Drive stays the source of truth for resumes
                dashboard_log.warning("Volume commit failed", extra={"reason": f"{type(e).__name__}: {e}"})

    threading.Thread(target=commit_volume_periodically, daemon=True).start()


@app.local_entrypoint()
def main(force: bool = False):
    run_pipeline.remote(force=force)
