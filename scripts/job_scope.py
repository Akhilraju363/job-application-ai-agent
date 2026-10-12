"""Explicit job scope for one run: when given, only these jobs may enter scoring or tailoring.

A caller (the Naukri pipeline in Auto_job_apply) exports some jobs and passes the same list here,
so the Agent's cumulative stores (cached LinkedIn jobs, older Naukri jobs) are not newly scored or
tailored in that run. Without a scope file the stages behave exactly as before.

    python scripts/score_jobs.py  --job-ids-file exports/agent_job_scope.json
    python scripts/tailor_job.py  --job-ids-file exports/agent_job_scope.json
    (or JOB_IDS_FILE=<path>; the command-line flag wins)

Scope file (JSON):  {"run_id": "<id>", "jobs": [{"id": "<caller's job id>", "link": "<job URL>"}]}
Jobs are matched by job_links.canonical_link -- the key every stage already uses. An empty "jobs"
list is valid and means "process nothing", never "process everything". A missing or malformed file
is an error (ScopeError): a scoped run never silently becomes an unscoped one.

Each scoped stage records what it did in output/scoped_run_report.json (one report per run_id,
read by the caller). Historical scores and resumes are never removed or rewritten here.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_links import canonical_link  # noqa: E402

SCOPE_ARG = "--job-ids-file"
SCOPE_ENV = "JOB_IDS_FILE"
REPORT_NAME = "scoped_run_report.json"


class ScopeError(ValueError):
    """The scope file is missing or malformed. Stages must stop, not fall back to an unscoped run."""


class JobScope:
    def __init__(self, path, run_id, ids):
        self.path = Path(path)
        self.run_id = run_id
        self.ids = ids  # canonical link -> the caller's id (insertion order = the caller's order)

    @property
    def links(self):
        return set(self.ids)

    def id_for(self, link):
        return self.ids.get(canonical_link(link), canonical_link(link))

    def ids_for(self, links):
        """Caller ids for these links, in the scope's own order."""
        wanted = {canonical_link(link) for link in links}
        return [job_id for link, job_id in self.ids.items() if link in wanted]


def scope_path(argv=None, environ=None):
    """The scope file path from --job-ids-file PATH / --job-ids-file=PATH, else JOB_IDS_FILE; None if unscoped."""
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    for index, arg in enumerate(argv):
        if arg == SCOPE_ARG:
            if index + 1 >= len(argv) or not argv[index + 1].strip():
                raise ScopeError(f"{SCOPE_ARG} needs a file path")
            return argv[index + 1]
        if arg.startswith(SCOPE_ARG + "="):
            value = arg.split("=", 1)[1]
            if not value.strip():
                raise ScopeError(f"{SCOPE_ARG} needs a file path")
            return value
    value = (environ.get(SCOPE_ENV) or "").strip()
    return value or None


def load_scope(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ScopeError(f"job scope file not found: {path}") from None
    except (OSError, ValueError) as e:
        raise ScopeError(f"job scope file is not readable JSON: {path}: {e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ScopeError(f'job scope file must be an object with a "jobs" list: {path}')
    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        # The report is keyed by run_id: without one, a caller could read another run's results.
        raise ScopeError(f'job scope file needs a non-empty "run_id": {path}')
    ids = {}
    for index, entry in enumerate(data["jobs"]):
        link = entry.get("link") if isinstance(entry, dict) else None
        if not isinstance(link, str) or not link.startswith(("http://", "https://")):
            raise ScopeError(f"job scope entry {index} has no valid link: {path}")
        key = canonical_link(link.strip())
        job_id = entry.get("id")
        ids.setdefault(key, str(job_id).strip() if job_id not in (None, "") else key)
    return JobScope(path, run_id.strip(), ids)


def from_cli_or_env(argv=None, environ=None):
    """The JobScope for this run, or None when no scope was requested (the unscoped behaviour)."""
    path = scope_path(argv, environ)
    return load_scope(path) if path is not None else None


def select_for_scoring(raw_jobs, done_links, scope):
    """(jobs to score now, report). Only scope members not yet scored; scope members absent from
    raw_jobs are reported as missing, never replaced by other jobs."""
    present = {}
    for job in raw_jobs:
        key = canonical_link(job.get("link"))
        if key in scope.links:
            present.setdefault(key, job)  # one entry per job, as the scrape stage dedupes
    to_score = [job for key, job in present.items() if key not in done_links]
    already = scope.links & set(done_links)  # scored earlier, whether or not raw_jobs.json still holds it
    report = {
        "in_scope": list(scope.ids.values()),
        "missing": scope.ids_for(scope.links - set(present) - already),
        "already_scored": scope.ids_for(already),
        "to_score": scope.ids_for(canonical_link(j["link"]) for j in to_score),
    }
    return to_score, report


def finish_scoring(report, to_score, scored, scope):
    """Add what actually got a score this run (failed jobs stay unscored and are listed)."""
    scored_links = {canonical_link(j.get("link")) for j in scored}
    attempted = {canonical_link(j["link"]) for j in to_score}
    report = dict(report)
    report["newly_scored"] = scope.ids_for(attempted & scored_links)
    report["failed"] = scope.ids_for(attempted - scored_links)
    return report


def select_for_tailoring(scored_jobs, scope):
    """(qualified jobs in scope, report). Scope members with no score are 'not_scored'; scored but
    below the cutoff are 'not_qualified'. Nothing outside the scope is ever returned."""
    in_scope = {}
    for job in scored_jobs:
        key = canonical_link(job.get("link"))
        if key in scope.links:
            in_scope.setdefault(key, job)
    qualified = [job for job in in_scope.values() if job.get("qualified")]
    report = {
        "in_scope": list(scope.ids.values()),
        "not_scored": scope.ids_for(scope.links - set(in_scope)),
        "not_qualified": scope.ids_for(k for k, j in in_scope.items() if not j.get("qualified")),
        "qualified": scope.ids_for(canonical_link(j["link"]) for j in qualified),
    }
    return qualified, report


def write_report(output_dir, stage, section, scope):
    """Record one stage's section in output/scoped_run_report.json for this run_id (a new run_id
    starts a fresh report; the file is a per-run report, not history)."""
    path = Path(output_dir) / REPORT_NAME
    try:
        report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        report = {}
    if report.get("run_id") != scope.run_id or report.get("scope_file") != str(scope.path):
        report = {"run_id": scope.run_id, "scope_file": str(scope.path), "ids": list(scope.ids.values())}
    report[stage] = section
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
