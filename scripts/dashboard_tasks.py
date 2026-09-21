"""Background tasks for the dashboard, with *real* stage reporting.

A task is a thread running one unit of work. The work reports the stage it is actually in
(tailoring_service calls on_stage as each real step begins; pipeline runs report which
script is running) and the UI polls /api/tasks/<id> -- so progress shown to the user is the
backend's actual state, never a timer.
"""
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque

import activity
import jd_analysis
import logging_config as lc
import paths

logger = lc.get_logger("dashboard.tasks")

MAX_TASKS = 60
_tasks = {}
_order = deque()
_lock = threading.Lock()

# One LLM-heavy job at a time: free-tier providers are rate limited and a local Ollama
# model serialises requests anyway, so parallel tailoring only makes everything slower.
LLM_SLOT = threading.Semaphore(1)
PIPELINE_LOCK = threading.Lock()


class Busy(RuntimeError):
    pass


class Task:
    def __init__(self, kind, stages):
        self.id = uuid.uuid4().hex[:16]
        self.kind = kind
        self.status = "queued"
        self.stages = [{"key": k, "label": label, "status": "pending"} for k, label in stages]
        self.result = None
        self.error = None
        self.logs = deque(maxlen=60)
        self.created = time.time()
        self.finished = None
        self.request_id = lc.current_context().get("request_id")

    def set_stage(self, key):
        seen = False
        for s in self.stages:
            if s["key"] == key:
                s["status"], seen = "active", True
            elif not seen and s["status"] != "done":
                s["status"] = "done"
            elif seen and s["status"] in ("active", "done"):
                s["status"] = "pending"  # a retry re-enters an earlier stage
        self.status = "running"
        logger.info("Task stage changed", extra={"task_id": self.id, "kind": self.kind, "stage": key})

    def finish(self, result=None):
        for s in self.stages:
            s["status"] = "done"
        self.result, self.status, self.finished = result, "done", time.time()

    def fail(self, message, code="failed"):
        for s in self.stages:
            if s["status"] == "active":
                s["status"] = "error"
        self.error, self.status, self.finished = {"code": code, "message": message}, "error", time.time()

    def to_json(self):
        return {"id": self.id, "kind": self.kind, "status": self.status, "stages": self.stages,
                "result": self.result, "error": self.error, "logs": list(self.logs)}


def get(task_id):
    with _lock:
        return _tasks.get(task_id)


def _register(task):
    with _lock:
        _tasks[task.id] = task
        _order.append(task.id)
        while len(_order) > MAX_TASKS:
            _tasks.pop(_order.popleft(), None)


def friendly_error(exc):
    """User-facing message; never the raw exception text of an unknown failure."""
    from llm import AllProvidersFailed

    if isinstance(exc, paths.MasterResumeError):
        return str(exc), "no_master_resume"
    if isinstance(exc, jd_analysis.InputError):
        return str(exc), "input"
    if isinstance(exc, AllProvidersFailed):
        return (f"Every configured LLM provider failed. {exc}", "llm_unavailable")
    if isinstance(exc, RuntimeError) and "no LLM provider configured" in str(exc):
        return (str(exc), "llm_not_configured")
    return (f"{type(exc).__name__}: {str(exc).strip().splitlines()[0][:200]}" if str(exc).strip()
            else type(exc).__name__), "failed"


def start(kind, stages, work, *, exclusive=None, slot=None):
    """Run work(task) on a thread. `exclusive` (a Lock) makes a second start raise Busy."""
    task = Task(kind, stages)
    if exclusive is not None and not exclusive.acquire(blocking=False):
        logger.warning("Task rejected: another run is in progress", extra={"kind": kind})
        raise Busy(f"A {kind} run is already in progress")
    _register(task)
    parent_context = lc.current_context()  # the request that started it (request_id, ...)
    logger.info("Task started", extra={"task_id": task.id, "kind": kind, "stage_count": len(stages)})

    def runner():
        # a new thread has no log context of its own: carry the request's ids and add the task's
        with lc.bind(**parent_context, task_id=task.id, operation=kind):
            try:
                if slot is not None:
                    slot.acquire()
                try:
                    work(task)
                finally:
                    if slot is not None:
                        slot.release()
                if task.status != "error":
                    task.finish(task.result)
                    resume_id = (task.result or {}).get("resume_id") if isinstance(task.result, dict) else None
                    logger.info("Task completed", extra={
                        "task_id": task.id, "kind": kind, "duration_ms": int((task.finished - task.created) * 1000),
                        **({"resume_id": resume_id} if resume_id else {})})
            except Exception as e:  # noqa: BLE001 -- reported through the task, not raised
                message, code = friendly_error(e)
                logger.exception("Task failed", extra={"task_id": task.id, "kind": kind, "error_code": code})
                task.fail(message, code)
            finally:
                if exclusive is not None:
                    exclusive.release()

    threading.Thread(target=runner, daemon=True, name=f"task-{task.id}").start()
    return task


# ---------------------------------------------------------------------------
# pipeline runs (existing scripts, unchanged)
# ---------------------------------------------------------------------------

PIPELINES = {
    "find": [("scrape_jobs.py", "Scraping LinkedIn jobs"), ("score_jobs.py", "Scoring jobs against your resume")],
    "full": [("scrape_jobs.py", "Scraping LinkedIn jobs"), ("score_jobs.py", "Scoring jobs against your resume"),
             ("tailor_job.py", "Tailoring qualified resumes"), ("company_research.py", "Researching companies"),
             ("write_sheet.py", "Logging to the tracker")],
}
STEP_TIMEOUT = {"scrape_jobs.py": 600, "score_jobs.py": 6000, "tailor_job.py": 7200,
                "company_research.py": 5400, "write_sheet.py": 300}
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)


def redact(line):
    """Mask any secret env value that leaks into a subprocess's output."""
    for name, value in os.environ.items():
        if value and len(value) >= 8 and _SECRET_NAME.search(name):
            line = line.replace(value, "***")
    return line


def run_pipeline(mode, on_done=None):
    steps = PIPELINES[mode]

    def work(task):
        for script, _label in steps:
            task.set_stage(script)
            task.logs.append(f"--- {script} ---")
            logger.info("Pipeline step started", extra={"task_id": task.id, "script": script})
            # the child logs to the same rotating files; child_env hands it this task's ids
            proc = subprocess.Popen([sys.executable, "-u", str(paths.ROOT / "scripts" / script)],
                                    cwd=paths.ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace",
                                    env=lc.child_env(task_id=task.id))
            timer = threading.Timer(STEP_TIMEOUT[script], proc.kill)
            timer.start()
            try:
                for line in proc.stdout:
                    if line.strip():
                        task.logs.append(redact(line.rstrip())[:300])
                code = proc.wait()
            finally:
                timer.cancel()
            if code != 0:
                logger.error("Pipeline step failed", extra={"task_id": task.id, "script": script, "exit_code": code})
                raise RuntimeError(f"{script} exited with code {code} -- see the log below")
            logger.info("Pipeline step finished", extra={"task_id": task.id, "script": script})
        task.result = {"mode": mode}
        activity.log_event("pipeline_run", "Job search run finished: " + ", ".join(l for _, l in steps),
                           mode=mode)
        if on_done:
            on_done()

    return start("pipeline", [(s, l) for s, l in steps], work, exclusive=PIPELINE_LOCK)
