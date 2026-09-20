"""Local dashboard: static SPA (web/) + JSON API over the existing pipeline artifacts.

    python scripts/dashboard_server.py            # http://127.0.0.1:8765
    DASHBOARD_TOKEN=... python scripts/dashboard_server.py --host 0.0.0.0

Stdlib only (no new dependencies). The Modal cron is unchanged -- this is the interactive
surface: it reads the same output/*.json artifacts and the same Google Sheet the pipeline
writes, and calls the same tailoring / scoring code.

Security model
  * binds to loopback; a non-loopback --host is refused unless DASHBOARD_TOKEN is set
  * every request's Host header must be one we serve (blocks DNS-rebinding)
  * every state-changing request must be application/json with a same-origin Origin
    (blocks cross-site form posts; these endpoints spend LLM quota / paid Apify calls)
  * optional bearer token (DASHBOARD_TOKEN) on all /api/* routes except /api/auth
  * secrets are never serialised: settings/sources expose booleans and labels only
"""
import argparse
import hmac
import json
import mimetypes
import os
import re
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

import paths  # noqa: E402

load_dotenv(paths.ROOT / ".env")
# A local 7B model can take minutes to write a resume; llm.py's default 120s per-attempt cap
# is sized for cloud providers. Only affects this process (and pipeline runs it launches),
# only in local mode, and only if the user hasn't set their own.
if os.environ.get("llm_base_url", "").strip() or os.environ.get("LOCAL_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
    os.environ.setdefault("llm_request_deadline", "600")

import activity  # noqa: E402
import dashboard_data as dd  # noqa: E402
import dashboard_tasks as tasks  # noqa: E402
import jd_analysis  # noqa: E402
import no_fabrication as nf  # noqa: E402
import resume_store  # noqa: E402
import scrape_jobs  # noqa: E402
import tailoring_service  # noqa: E402
from tracker_service import TrackerError, tracker  # noqa: E402

MAX_BODY = 1_048_576
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EXPORT_FORMATS = {"pdf": "application/pdf", "docx": DOCX_MIME}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class ApiError(Exception):
    def __init__(self, status, message, code="error"):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), fn))
        return fn
    return deco


class Request:
    def __init__(self, handler, params, query):
        self.handler, self.params, self.query = handler, params, query
        self._body = None

    def q(self, name, default=None):
        return (self.query.get(name) or [default])[0]

    @property
    def body(self):
        if self._body is None:
            n = int(self.handler.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ApiError(413, "Request body too large", "too_large")
            raw = self.handler.rfile.read(n) if n else b""
            self.handler.body_read = True
            try:
                self._body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, UnicodeDecodeError):
                raise ApiError(400, "Request body must be valid JSON", "bad_json") from None
            if not isinstance(self._body, dict):
                raise ApiError(400, "Request body must be a JSON object", "bad_json")
        return self._body


# ---------------------------------------------------------------------------
# shared loaders
# ---------------------------------------------------------------------------

def _jobs(tr):
    raw, scored, tailored = dd.load_artifacts()
    return dd.build_jobs(raw, scored, tailored, tr["rows"]), tailored


def _find_job(key, tr=None):
    tr = tr or tracker.snapshot()
    job = next((j for j in _jobs(tr)[0] if j["key"] == key), None)
    if not job:
        raise ApiError(404, "Job not found", "not_found")
    return job


def _period(req):
    try:
        return dd.parse_range(req.q("range", "7d"), req.q("from"), req.q("to"))
    except ValueError as e:
        raise ApiError(400, str(e), "bad_range") from None


def _get_resume(rid):
    try:
        return resume_store.get(rid)
    except KeyError:
        raise ApiError(404, "Resume not found", "not_found") from None


def _int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _resume_public(meta):
    """Resume record for the UI: full version list incl. markdown."""
    return meta


# ---------------------------------------------------------------------------
# meta / auth
# ---------------------------------------------------------------------------

@route("GET", "/api/auth")
def auth_info(req):
    return {"required": bool(os.environ.get("DASHBOARD_TOKEN"))}


@route("GET", "/api/me")
def me(req):
    return {**dd.profile(), "auth_required": bool(os.environ.get("DASHBOARD_TOKEN"))}


# ---------------------------------------------------------------------------
# dashboard read models
# ---------------------------------------------------------------------------

@route("GET", "/api/dashboard/summary")
def dash_summary(req):
    p, tr = _period(req), tracker.snapshot()
    return dd.summary(_jobs(tr)[0], resume_store.list_all(), tr, p)


@route("GET", "/api/dashboard/overview")
def dash_overview(req):
    p, tr = _period(req), tracker.snapshot()
    return dd.overview(_jobs(tr)[0], resume_store.list_all(), tr, p)


@route("GET", "/api/dashboard/status")
def dash_status(req):
    return dd.status_counts(tracker.snapshot(force=req.q("refresh") == "1"))


@route("GET", "/api/dashboard/jobs")
def dash_jobs(req):
    jobs, _ = _jobs(tracker.snapshot())
    limit = _int(req.q("limit"), 6, 1, 25)
    return {"jobs": [dd.public_job(j) for j in jobs[:limit]], "total": len(jobs)}


@route("GET", "/api/dashboard/activity")
def dash_activity(req):
    tr = tracker.snapshot()
    _, tailored = _jobs(tr)
    return {"events": dd.activity_feed(activity.read_events(), tailored, tr["rows"],
                                       _int(req.q("limit"), 10, 1, 50))}


@route("GET", "/api/dashboard/sources")
def dash_sources(req):
    return dd.sources()


# ---------------------------------------------------------------------------
# jobs (Find Jobs)
# ---------------------------------------------------------------------------

@route("GET", "/api/jobs")
def list_jobs(req):
    jobs, _ = _jobs(tracker.snapshot())
    f, q = req.q("filter", "all"), (req.q("q") or "").strip().lower()
    if f == "qualified":
        jobs = [j for j in jobs if j["qualified"]]
    elif f == "below":
        jobs = [j for j in jobs if j["qualified"] is False]
    elif f == "tailored":
        jobs = [j for j in jobs if j["tailored"]]
    elif f != "all":
        raise ApiError(400, "filter must be all, qualified, below or tailored", "bad_filter")
    if q:
        jobs = [j for j in jobs if q in f"{j['title']} {j['company']} {j['location'] or ''}".lower()]
    total = len(jobs)
    off, lim = _int(req.q("offset"), 0, 0, 10**6), _int(req.q("limit"), 50, 1, 200)
    return {"jobs": [dd.public_job(j) for j in jobs[off:off + lim]], "total": total}


@route("GET", "/api/jobs/(?P<key>[0-9a-f]{12})")
def job_detail(req):
    return dd.public_job(_find_job(req.params["key"]), with_description=True)


@route("POST", "/api/jobs/(?P<key>[0-9a-f]{12})/save")
def job_save(req):
    job = _find_job(req.params["key"])
    try:
        return tracker.save_job(title=job["title"], company=job["company"], link=job["link"],
                                score=job["score"] if job["score"] is not None else "",
                                resume_path=job.get("resume_link") or "", source=job["source"],
                                company_notes=job.get("company_notes") or "")
    except TrackerError as e:
        raise ApiError(502, f"Tracker unavailable: {e}", "tracker_unavailable") from None


# ---------------------------------------------------------------------------
# tailoring -- ONE service, two entry points
# ---------------------------------------------------------------------------

def _require_master():
    """Fail fast (409, before any LLM spend) when there is no usable master resume."""
    try:
        paths.read_base_resume()
    except paths.MasterResumeError as e:
        raise ApiError(409, str(e), "no_master_resume") from None


def _start_tailor(fields, *, job_id=None, resume_id=None, pipeline_score=None):
    """The only place a tailoring task is started. Manual JD and scraped-job requests
    both end up here, and from here in tailoring_service.tailor_resume()."""
    _require_master()

    def work(task):
        result = tailoring_service.tailor_resume(
            fields["description"], fields["title"], fields["company"], fields["url"], fields["source"],
            job_id, None, pipeline_score=pipeline_score, resume_id=resume_id, reuse=not resume_id,
            on_stage=task.set_stage)
        task.result = {"resume_id": result["resume"]["id"], "reused": result["reused"], "result": result}

    try:
        return tasks.start("tailor", tailoring_service.STAGES, work, slot=tasks.LLM_SLOT)
    except tasks.Busy as e:
        raise ApiError(409, str(e), "busy") from None


def _validated_fields(title, company, url, description, source):
    """Reject bad input with a 400 immediately (the service re-validates with the same function)."""
    try:
        jd_analysis.normalize_job(title, company, url, description)
        return {"title": title, "company": company, "url": url, "description": description,
                "source": jd_analysis._one_line(source, "Job source")}
    except jd_analysis.InputError as e:
        raise ApiError(400, str(e), "invalid_input") from None


@route("POST", "/api/tailor")  # entry point A: manual JD
def tailor_manual(req):
    b = req.body
    fields = _validated_fields(b.get("title"), b.get("company"), b.get("url"), b.get("description"), b.get("source"))
    return {"task_id": _start_tailor(fields).id}


@route("POST", "/api/jobs/(?P<key>[0-9a-f]{12})/tailor")  # entry point B: scraped job
def tailor_scraped(req):
    j = _find_job(req.params["key"])
    if not j.get("description"):
        raise ApiError(422, "This job has no description to tailor against", "no_description")
    try:
        jd_analysis.normalize_job(j["title"], j["company"], "", j["description"])
    except jd_analysis.InputError as e:
        raise ApiError(422, str(e), "invalid_input") from None
    fields = {"title": j["title"], "company": j["company"], "url": j["link"], "description": j["description"],
              "source": j["source"]}
    # the pipeline's own 1-10 fit score is carried along so the tracker keeps a consistent Fit Score
    return {"task_id": _start_tailor(fields, job_id=j["key"], pipeline_score=j["score"]).id}


@route("GET", "/api/tasks/(?P<id>[0-9a-f]{16})")
def task_status(req):
    t = tasks.get(req.params["id"])
    if not t:
        raise ApiError(404, "Task not found (it may have expired)", "not_found")
    return t.to_json()


@route("GET", "/api/resumes")
def resumes_list(req):
    return {"resumes": [{"id": m["id"], "title": m["job"]["title"], "company": m["job"]["company"],
                         "source": m["source"], "created_at": m["created_at"],
                         "overall": m["match"]["overall"], "versions": len(m["versions"]),
                         "ok": dd.resume_is_ok(m)} for m in resume_store.list_all()[:25]]}


@route("GET", "/api/resumes/(?P<id>[0-9a-f]{12})")
def resume_get(req):
    return _resume_public(_get_resume(req.params["id"]))


@route("PUT", "/api/resumes/(?P<id>[0-9a-f]{12})")
def resume_edit(req):
    _get_resume(req.params["id"])
    md = req.body.get("markdown")
    if not isinstance(md, str) or not md.strip():
        raise ApiError(400, "markdown is required", "invalid_input")
    try:
        return tailoring_service.revalidate_edit(req.params["id"], md)
    except jd_analysis.InputError as e:
        raise ApiError(400, str(e), "invalid_input") from None


@route("POST", "/api/resumes/(?P<id>[0-9a-f]{12})/regenerate")
def resume_regenerate(req):
    meta = _get_resume(req.params["id"])
    j = meta["job"]
    fields = {"title": j["title"], "company": j["company"], "description": j["description"],
              "url": "" if str(j["link"]).startswith("manual:") else j["link"], "source": j.get("source") or ""}
    return {"task_id": _start_tailor(fields, job_id=j.get("job_key"), resume_id=meta["id"],
                                     pipeline_score=j.get("pipeline_score")).id}


@route("GET", "/api/resumes/(?P<id>[0-9a-f]{12})/result")
def resume_result(req):
    """The structured tailoring result (job / jd_analysis / match_analysis / resume / ats_validation)."""
    meta = _get_resume(req.params["id"])
    return tailoring_service.to_result(meta, _version(meta, req)["n"])


def _version(meta, req):
    n = _int(req.q("version") or req.body.get("version"), meta["versions"][-1]["n"], 1, 10**4)
    v = next((v for v in meta["versions"] if v["n"] == n), None)
    if not v:
        raise ApiError(404, "Version not found", "not_found")
    return v


def _require_ok(v):
    if not v["validation"]["ok"]:
        raise ApiError(409, "This version failed fact/ATS verification; edit or regenerate it first",
                       "verification_failed")


@route("POST", "/api/resumes/(?P<id>[0-9a-f]{12})/export")
def resume_export(req):
    meta = _get_resume(req.params["id"])
    fmt = req.body.get("format")
    if fmt not in EXPORT_FORMATS:
        raise ApiError(400, "format must be pdf or docx", "invalid_input")
    v = _version(meta, req)
    _require_ok(v)
    rid, n = meta["id"], v["n"]

    def work(task):
        import tailor_job  # gws + Docs export shared with the Drive pipeline

        task.set_stage("export")
        try:
            tailor_job.export_doc_file(resume_store.version_path(rid, n, "md"),
                                       resume_store.version_path(rid, n, fmt), EXPORT_FORMATS[fmt])
        except Exception as e:  # noqa: BLE001 -- gws missing / not signed in / Docs API error
            detail = str(e).strip().splitlines()[0][:200] if str(e).strip() else type(e).__name__
            raise RuntimeError(f"{'PDF' if fmt == 'pdf' else 'DOCX'} generation failed: {detail}. "
                               "Check that the Google Workspace CLI (gws) is signed in, or download the Markdown.") from e
        resume_store.mark_export(rid, n, fmt)
        task.result = {"resume_id": rid, "version": n, "format": fmt}

    label = "PDF" if fmt == "pdf" else "Word document"
    return {"task_id": tasks.start("export", [("export", f"Building {label} via Google Docs")], work).id}


def _safe_filename(meta, ext):
    base = f"{dd.profile()['name'] or 'Resume'} - {meta['job']['company']} - {meta['job']['title']}"
    return re.sub(r"[^\w .()&-]+", "", base).strip()[:120] + f".{ext}"


@route("GET", "/api/resumes/(?P<id>[0-9a-f]{12})/download")
def resume_download(req):
    meta = _get_resume(req.params["id"])
    fmt = req.q("format", "md")
    if fmt not in ("md", "pdf", "docx"):
        raise ApiError(400, "format must be md, pdf or docx", "invalid_input")
    v = _version(meta, req)
    if fmt != "md":
        _require_ok(v)
    path = resume_store.version_path(meta["id"], v["n"], fmt)
    if not path.exists():
        raise ApiError(404, "That file hasn't been exported yet", "not_exported")
    ctype = {"md": "text/markdown; charset=utf-8", **EXPORT_FORMATS}[fmt]
    return FileResponse(path.read_bytes(), ctype, _safe_filename(meta, fmt))


@route("POST", "/api/resumes/(?P<id>[0-9a-f]{12})/save-to-tracker")
def resume_save(req):
    meta = _get_resume(req.params["id"])
    v = _version(meta, req)
    _require_ok(v)
    rel = resume_store.version_path(meta["id"], v["n"], "pdf" if v["exports"].get("pdf") else "md")
    try:
        rel = rel.relative_to(paths.ROOT).as_posix()
    except ValueError:
        rel = str(rel)
    label = meta["job"].get("source") or ("Manual JD" if meta["source"] == "manual" else "LinkedIn")
    try:
        out = tracker.save_job(title=meta["job"]["title"], company=meta["job"]["company"],
                               link=meta["job"]["link"],
                               score=meta["job"].get("pipeline_score") or meta["match"]["fit_score"],
                               resume_path=rel, source=label, resume_id=f"{meta['id']}-v{v['n']}",
                               match_pct=meta["match"]["overall"])
    except TrackerError as e:
        raise ApiError(502, f"Tracker unavailable: {e}", "tracker_unavailable") from None
    resume_store.update_meta(meta["id"], tracker={"saved_at": resume_store.now_iso(), "result": out["result"],
                                                 "version": v["n"], "resume_path": rel})
    return {"result": out["result"], "resume_path": rel}


# ---------------------------------------------------------------------------
# tracker
# ---------------------------------------------------------------------------

@route("GET", "/api/tracker")
def tracker_list(req):
    tr = tracker.snapshot(force=req.q("refresh") == "1")
    status = req.q("status")
    if status and status not in dd.STATUSES:
        raise ApiError(400, f"status must be one of: {', '.join(dd.STATUSES)}", "bad_status")
    rows = [r for r in tr["rows"] if not status or r["status"] == status]
    return {"rows": rows, "statuses": dd.STATUSES, "total": len(tr["rows"]),
            "tracker": {k: tr[k] for k in ("available", "error", "stale", "configured")}}


@route("PATCH", "/api/tracker/status")
def tracker_status(req):
    link, status = req.body.get("link"), req.body.get("status")
    if not isinstance(link, str) or not link:
        raise ApiError(400, "link is required", "invalid_input")
    try:
        return tracker.set_status(link, status)
    except ValueError as e:
        raise ApiError(400, str(e), "bad_status") from None
    except LookupError as e:
        raise ApiError(404, str(e), "not_found") from None
    except TrackerError as e:
        raise ApiError(502, f"Tracker unavailable: {e}", "tracker_unavailable") from None


# ---------------------------------------------------------------------------
# pipeline actions, preferences, settings
# ---------------------------------------------------------------------------

@route("POST", "/api/actions/find-jobs")
def action_find_jobs(req):
    mode = req.body.get("mode", "find")
    if mode not in tasks.PIPELINES:
        raise ApiError(400, "mode must be find or full", "invalid_input")
    try:
        return {"task_id": tasks.run_pipeline(mode, on_done=tracker.invalidate).id}
    except tasks.Busy as e:
        raise ApiError(409, str(e), "busy") from None


def _prefs_payload():
    return {"preferences": scrape_jobs.load_preferences(),
            "defaults": {"keywords": scrape_jobs.DEFAULT_KEYWORDS, "location": scrape_jobs.DEFAULT_LOCATION,
                         "date_posted": scrape_jobs.DEFAULT_DATE_POSTED, "limit": scrape_jobs.effective_job_limit()},
            "date_posted_options": list(scrape_jobs.DATE_POSTED_OPTIONS),
            "limit_cap": scrape_jobs.effective_job_limit(),
            "note": "Used by Find New Jobs and local runs. The Modal cron uses its own defaults."}


@route("GET", "/api/preferences")
def prefs_get(req):
    return _prefs_payload()


@route("PUT", "/api/preferences")
def prefs_put(req):
    b, cap = req.body, scrape_jobs.effective_job_limit()
    kw, loc = nf.norm_ws(str(b.get("keywords") or "")), nf.norm_ws(str(b.get("location") or ""))
    if not kw or len(kw) > 200 or not loc or len(loc) > 100:
        raise ApiError(400, "keywords (max 200 chars) and location (max 100 chars) are required", "invalid_input")
    if b.get("date_posted") not in scrape_jobs.DATE_POSTED_OPTIONS:
        raise ApiError(400, f"date_posted must be one of {list(scrape_jobs.DATE_POSTED_OPTIONS)}", "invalid_input")
    try:
        limit = int(b.get("limit"))
    except (TypeError, ValueError):
        raise ApiError(400, "limit must be a whole number", "invalid_input") from None
    if not 1 <= limit <= cap:
        raise ApiError(400, f"limit must be between 1 and {cap} for the current mode", "invalid_input")
    paths.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = paths.OUTPUT_DIR / "preferences.json.tmp"
    tmp.write_text(json.dumps({"keywords": kw, "location": loc, "date_posted": b["date_posted"],
                               "limit": limit}, indent=2), encoding="utf-8")
    os.replace(tmp, paths.OUTPUT_DIR / "preferences.json")
    return _prefs_payload()


@route("GET", "/api/settings")
def settings(req):
    md = paths.read_base_resume()
    parsed = nf.parse_resume(md)
    return {**dd.sources(), "profile": dd.profile(),
            "master_resume": {"path": "resume/base_resume.md", "sections": list(parsed["sections"]),
                              "roles": [r["heading"] for r in parsed["roles"]],
                              "skills": len(parsed["skill_items"]), "years": jd_analysis.resume_years(md)},
            "auth": {"token_required": bool(os.environ.get("DASHBOARD_TOKEN"))}}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

class FileResponse:
    def __init__(self, data, content_type, filename):
        self.data, self.content_type, self.filename = data, content_type, filename


class Handler(BaseHTTPRequestHandler):
    server_version = "JobAgentDashboard"
    sys_version = ""
    allowed_hosts = frozenset()

    def log_message(self, fmt, *args):
        if os.environ.get("DASHBOARD_VERBOSE"):
            super().log_message(fmt, *args)

    # -- helpers
    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status, message, code="error"):
        self._json(status, {"error": {"code": code, "message": message}})

    def _authorized(self):
        token = os.environ.get("DASHBOARD_TOKEN")
        if not token:
            return True
        supplied = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        return hmac.compare_digest(supplied.encode(), token.encode())

    def _check_origin(self, unsafe):
        host = (self.headers.get("Host") or "").lower()
        if host not in self.allowed_hosts:
            raise ApiError(403, "Unrecognised Host header", "bad_host")
        if unsafe:
            origin = self.headers.get("Origin")
            if origin and origin.lower() not in (f"http://{host}", f"https://{host}"):
                raise ApiError(403, "Cross-origin request refused", "bad_origin")
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                raise ApiError(415, "Content-Type must be application/json", "bad_content_type")

    # -- dispatch
    body_read = False

    def _drain(self):
        """Consume a small unread request body before replying to a rejected request; closing
        the socket with unread data makes some stacks (Windows) reset the connection and the
        client never sees the error response."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= MAX_BODY and not self.body_read:
                self.rfile.read(n)
        except (ValueError, OSError):
            pass

    def _dispatch(self, method):
        self.body_read = False
        try:
            self._check_origin(method != "GET")
            url = urlsplit(self.path)
            if url.path.startswith("/api/"):
                return self._api(method, url)
            if method != "GET":
                raise ApiError(405, "Method not allowed", "method_not_allowed")
            return self._static(url.path)
        except ApiError as e:
            self._drain()
            self._error(e.status, e.message, e.code)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._error(500, "Internal error", "internal")

    def _api(self, method, url):
        if url.path != "/api/auth" and not self._authorized():
            raise ApiError(401, "Authentication required", "unauthorized")
        path_matched = False
        for m, pattern, fn in ROUTES:
            match = pattern.match(url.path)
            if not match:
                continue
            path_matched = True
            if m != method:
                continue
            out = fn(Request(self, match.groupdict(), parse_qs(url.query)))
            if isinstance(out, FileResponse):
                return self._send(200, out.data, out.content_type, {
                    "Content-Disposition": f'attachment; filename="{out.filename}"; '
                                           f"filename*=UTF-8''{quote(out.filename)}"})
            return self._json(200, out)
        raise ApiError(405 if path_matched else 404, "Method not allowed" if path_matched else "Not found",
                       "method_not_allowed" if path_matched else "not_found")

    def _static(self, req_path):
        root = paths.WEB_DIR.resolve()
        rel = req_path.lstrip("/")
        target = (root / rel).resolve() if rel else root / "index.html"
        if "." not in Path(rel).name:  # client-side route (/tailor-resume, /applications ...)
            target = root / "index.html"
        try:
            target.relative_to(root)
        except ValueError:
            raise ApiError(404, "Not found", "not_found") from None
        if not target.is_file() or any(part.startswith(".") for part in target.relative_to(root).parts):
            raise ApiError(404, "Not found", "not_found")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in (".js", ".mjs"):
            ctype = "text/javascript"
        if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


def make_server(host="127.0.0.1", port=8765):
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not os.environ.get("DASHBOARD_TOKEN"):
        raise SystemExit("Refusing to bind a non-loopback address without DASHBOARD_TOKEN set.")
    server = ThreadingHTTPServer((host, port), Handler)
    port = server.server_address[1]
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", f"{host}:{port}"}
    hosts |= {h.strip().lower() for h in os.environ.get("DASHBOARD_ALLOWED_HOSTS", "").split(",") if h.strip()}
    Handler.allowed_hosts = frozenset(hosts)
    server.daemon_threads = True
    return server


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    args = ap.parse_args()
    server = make_server(args.host, args.port)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"Job Application AI Agent dashboard: {url}"
          + ("  (token required)" if os.environ.get("DASHBOARD_TOKEN") else ""))
    if args.open:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")


if __name__ == "__main__":
    main()
