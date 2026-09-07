"""Modal deployment for the job-apply-agent pipeline (PRD 3.7).

Runs the full pipeline unattended every morning: scrape -> score -> tailor
(OpenRouter, not claude -p -- see CLAUDE.md for why) -> company research ->
write to the Job Application Tracker Sheet. Resumes land in a Google Drive
folder (Modal has no access to the local Desktop), linked from the Sheet.

Deploy:  modal deploy modal_app.py
Test:    modal run modal_app.py
"""
import modal

app = modal.App("job-apply-agent")

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
    .add_local_dir("scripts", remote_path="/app/scripts")
    .add_local_dir("resume", remote_path="/app/resume")
)


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
def run_pipeline():
    import json
    import os
    import subprocess
    import traceback
    from pathlib import Path

    import requests

    def send_telegram_alert(message):
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            print("Telegram alert skipped: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set")
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message},
                timeout=15,
            )
        except Exception as alert_error:
            print(f"Telegram alert failed to send: {alert_error}")

    stage = {"name": "startup"}

    try:
        # gws credentials are Keychain-encrypted locally; this container has no
        # Keychain, so materialize the plain credentials file gws also supports and
        # tell gws to keep its token cache on disk instead of a (missing) keyring.
        os.environ["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] = "file"
        config_dir = Path.home() / ".config" / "gws"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "credentials.json").write_text(json.dumps({
            "client_id": os.environ["client_id"],
            "client_secret": os.environ["client_secret"],
            "refresh_token": os.environ["refresh_token"],
            "type": os.environ["type"],
        }))

        workdir = Path("/app")
        (workdir / "output").mkdir(exist_ok=True)

        def run(script, timeout):
            stage["name"] = script
            print(f"--- {script} ---")
            subprocess.run(["python3", f"scripts/{script}"], check=True, cwd=workdir, timeout=timeout)

        # score/tailor/research timeouts sized for worst case at limit=10 jobs: each now
        # retries at REQUEST_DEADLINE=240s x MAX_RETRIES=2 (see scripts/score_jobs.py),
        # so worst case per job is ~482s -> ~4820s for all 10, plus margin for
        # tailor's extra Drive/Docs API calls per job.
        run("scrape_jobs.py", timeout=600)
        run("score_jobs.py", timeout=5400)
        run("tailor_job.py", timeout=6000)
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
        qualified_links = {j["link"] for j in scored if j.get("qualified")}
        saved_links = {j["link"] for j in tailored if j.get("status") == "saved"}
        unmet = qualified_links - saved_links
        if unmet:
            reasons = {
                j["link"]: j.get("reason", j.get("status", "no tailored_jobs entry"))
                for j in tailored if j["link"] in unmet
            }
            detail = "\n".join(f"  - {link}: {reasons.get(link, 'never reached tailoring')}" for link in unmet)
            raise RuntimeError(
                f"{len(unmet)}/{len(qualified_links)} qualified job(s) produced no saved resume:\n{detail}"
            )

        print("JOB-APPLY-AGENT — daily run complete")
    except Exception as e:
        failed_stage = stage["name"]
        print(f"JOB-APPLY-AGENT — PIPELINE FAILED at {failed_stage}")
        traceback.print_exc()
        # Stage name + exception type/message only -- never secrets. If the failure
        # was an LLM/OpenRouter outage, recover locally with Ollama (see README).
        send_telegram_alert(
            "JOB-APPLY-AGENT — WHAT BROKE\n\n"
            f"Stage: {failed_stage}\n"
            f"Reason: {type(e).__name__}: {e}"
        )
        raise


@app.local_entrypoint()
def main():
    run_pipeline.remote()
