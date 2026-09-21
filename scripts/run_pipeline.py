"""Local high-volume orchestrator (LOCAL_MODE=true) -- runs the same five pipeline
scripts Modal runs, in the same order, against a local Ollama instance instead of the
cloud provider chain. Not used by Modal; see modal_app.py for the cloud cron entrypoint,
which this script deliberately does not touch or duplicate.

Usage (Linux/macOS):
    LOCAL_MODE=true python3 scripts/run_pipeline.py

Usage (Windows PowerShell):
    $env:LOCAL_MODE="true"
    python scripts/run_pipeline.py

Same resume-safe artifacts (output/raw_jobs.json -> scored_jobs.json ->
tailored_jobs.json), same Drive artifact mirror (scripts/artifacts.py), same master
"Job Application Tracker" Google Sheet, and the exact same reconciliation guard as the
cloud path -- imported from modal_app.reconcile_qualified rather than reimplemented, so
the two paths can't drift. This script only differs in provider and job-limit
configuration, both already resolved by scripts/llm.py and scripts/scrape_jobs.py from
LOCAL_MODE / LOCAL_JOB_LIMIT.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from modal_app import reconcile_qualified  # noqa: E402 -- reuse the exact cloud guard, no duplication
import llm  # noqa: E402
import logging_config as lc  # noqa: E402

log = lc.get_logger("pipeline")


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


def run(script, args=None):
    cmd = [sys.executable, str(ROOT / "scripts" / script), *(args or [])]
    log.info("Pipeline step started", extra={"script": script})
    subprocess.run(cmd, check=True, cwd=ROOT)
    log.info("Pipeline step finished", extra={"script": script})


def main():
    lc.configure_logging("pipeline")
    if not llm.LOCAL_MODE:
        raise RuntimeError(
            "run_pipeline.py is the LOCAL high-volume entrypoint -- set LOCAL_MODE=true "
            "before running it (see the usage docstring at the top of this file). For "
            "the cloud path, use `modal run modal_app.py` or the deployed cron instead."
        )

    stage = {"name": "startup"}
    started = time.monotonic()
    log.info("Pipeline started", extra={"mode": "local"})
    try:
        stage["name"] = "ollama validation"
        llm.validate_local_setup()
        log.info("Job limit configured", extra={"job_limit": os.environ.get("LOCAL_JOB_LIMIT", "50")})

        force_scrape = os.environ.get("FORCE_SCRAPE", "").strip().lower() in ("1", "true", "yes", "on")

        stage["name"] = "scrape_jobs.py"
        run("scrape_jobs.py", args=["--force"] if force_scrape else None)
        stage["name"] = "score_jobs.py"
        run("score_jobs.py")
        stage["name"] = "tailor_job.py"
        run("tailor_job.py")
        stage["name"] = "company_research.py"
        run("company_research.py")
        stage["name"] = "write_sheet.py"
        run("write_sheet.py")

        # Same no-silent-failure guard as modal_app.run_pipeline() -- a job that cleared
        # the 8+ cutoff but never produced a saved resume must fail the run, not just
        # get flagged and forgotten.
        stage["name"] = "post-run reconciliation"
        output_dir = ROOT / "output"
        scored = json.loads((output_dir / "scored_jobs.json").read_text(encoding="utf-8"))
        tailored_path = output_dir / "tailored_jobs.json"
        tailored = json.loads(tailored_path.read_text(encoding="utf-8")) if tailored_path.exists() else []
        qualified_total = sum(1 for j in scored if j.get("qualified"))
        unmet, reasons = reconcile_qualified(scored, tailored)
        if unmet:
            detail = "\n".join(f"  - {link}: {reasons.get(link, 'never reached tailoring')}" for link in unmet)
            raise RuntimeError(
                f"{len(unmet)}/{qualified_total} qualified job(s) produced no saved resume:\n{detail}"
            )

        log.info("JOB-APPLY-AGENT (local) -- run complete", extra={
            "qualified_count": qualified_total, "duration_seconds": round(time.monotonic() - started, 1)})
    except Exception as e:
        failed_stage = stage["name"]
        log.exception(f"JOB-APPLY-AGENT (local) -- PIPELINE FAILED at {failed_stage}",
                      extra={"stage": failed_stage, "duration_seconds": round(time.monotonic() - started, 1)})
        # Stage name + exception type/message only -- never secrets.
        send_telegram_alert(
            "JOB-APPLY-AGENT — WHAT BROKE\n\n"
            f"Mode: LOCAL / Ollama\n"
            f"Stage: {failed_stage}\n"
            f"Reason: {type(e).__name__}: {e}"
        )
        raise


if __name__ == "__main__":
    main()
