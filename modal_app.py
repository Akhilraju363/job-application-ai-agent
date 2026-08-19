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
    schedule=modal.Cron("0 7 * * *", timezone="Asia/Kolkata"),
    # 25 jobs scored sequentially through a free-tier OpenRouter model (plus retries
    # on rate limits/timeouts) can run well past 15 minutes -- observed 20+ min locally.
    timeout=3600,
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

    try:
        # gws credentials are Keychain-encrypted locally; this container has no
        # Keychain, so materialize the plain credentials file gws also supports.
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

        def run(script):
            print(f"--- {script} ---")
            subprocess.run(["python3", f"scripts/{script}"], check=True, cwd=workdir)

        run("scrape_jobs.py")
        run("score_jobs.py")
        run("tailor_job.py")
        run("company_research.py")
        run("write_sheet.py")

        print("JOB-APPLY-AGENT — daily run complete")
    except Exception as e:
        print("JOB-APPLY-AGENT — PIPELINE FAILED")
        traceback.print_exc()
        send_telegram_alert(f"JOB-APPLY-AGENT — WHAT BROKE\n\n{e}")
        raise


@app.local_entrypoint()
def main():
    run_pipeline.remote()
