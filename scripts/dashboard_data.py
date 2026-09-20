"""Read models for the dashboard: KPIs, overview chart, status donut, latest jobs,
activity, sources. Pure functions over already-loaded data (jobs, resumes, tracker rows)
so they are unit-testable; only `load_*` touch the filesystem.

Data provenance -- everything here is derived from the existing artifacts:
  output/raw_jobs.json -> scored_jobs.json -> tailored_jobs.json   (pipeline stages)
  output/generated_resumes/                                         (dashboard-tailored resumes)
  the Job Application Tracker sheet                                 (application status)
  output/activity_log.jsonl                                         (events from all of the above)

Dates: jobs are dated by `found_at` (set by the scraper), falling back to `posted_date`;
applications/interviews by the tracker's "Status Updated" date, falling back to the row's
logged date -- a status changed by hand in the Sheet has no change date, so it is dated by
when the row was logged.
"""
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import activity
import paths
import resume_store
from job_links import canonical_link

STATUSES = ["Not Applied", "Applied", "Interviewing", "Offer", "Rejected"]
APPLIED_STATUSES = {"Applied", "Interviewing", "Offer"}
INTERVIEW_STATUSES = {"Interviewing", "Offer"}
QUALIFY_CUTOFF = 8
RANGES = {"7d": 7, "30d": 30, "90d": 90}
MAX_CUSTOM_DAYS = 366


def job_key(link):
    return hashlib.sha1((canonical_link(link) or "").encode("utf-8")).hexdigest()[:12]


def to_date(value):
    if not value:
        return None
    s = str(value).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _load(name):
    try:
        data = json.loads((paths.OUTPUT_DIR / name).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def load_artifacts():
    return _load("raw_jobs.json"), _load("scored_jobs.json"), _load("tailored_jobs.json")


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

def build_jobs(raw, scored, tailored, tracker_rows):
    merged = {}
    for j in raw:
        if j.get("link"):
            merged[job_key(j["link"])] = dict(j)
    for j in scored:
        if j.get("link"):
            # a None in the scored copy must not blank a value the raw job already had
            merged.setdefault(job_key(j["link"]), {}).update({k: v for k, v in j.items() if v is not None})
    saved = {job_key(t["link"]): t for t in tailored if t.get("status") == "saved" and t.get("link")}
    tracked = {job_key(r["link"]): r for r in tracker_rows if r.get("link")}

    jobs = []
    for key, j in merged.items():
        score = j.get("score")
        score = int(score) if isinstance(score, (int, float)) or str(score).isdigit() else None
        qualified = None if score is None else score >= QUALIFY_CUTOFF
        t, row = saved.get(key), tracked.get(key)
        if row:
            state = row["status"]
        elif t:
            state = "Tailored"
        elif qualified:
            state = "Qualified"
        elif qualified is False:
            state = "Below cutoff"
        else:
            state = "Unscored"
        jobs.append({
            "key": key, "title": j.get("title"), "company": j.get("company"), "link": j.get("link"),
            "location": j.get("location"), "posted_date": j.get("posted_date"),
            "found_at": j.get("found_at"), "source": j.get("source") or "LinkedIn",
            "score": score, "match_pct": None if score is None else score * 10, "qualified": qualified,
            "matched": j.get("matched_must_haves") or [], "missing": j.get("missing_must_haves") or [],
            "reasoning": j.get("reasoning"), "description": j.get("description"),
            "tailored": bool(t), "resume_link": (t or {}).get("resume_link"),
            "drive_folder_link": (t or {}).get("drive_folder_link"),
            "tailored_at": (t or {}).get("tailored_at"), "company_notes": (t or {}).get("company_notes"),
            "tracker_status": row["status"] if row else None, "state": state,
        })
    jobs.sort(key=lambda j: (str(to_date(j["found_at"]) or to_date(j["posted_date"]) or ""),
                             j["score"] or 0), reverse=True)
    return jobs


def public_job(job, with_description=False):
    out = {k: v for k, v in job.items() if k != "description"}
    if with_description:
        out["description"] = job.get("description")
    return out


# ---------------------------------------------------------------------------
# periods + counts
# ---------------------------------------------------------------------------

def parse_range(rng, start=None, end=None, today=None):
    today = today or date.today()
    if rng in RANGES:
        n = RANGES[rng]
        s, e = today - timedelta(days=n - 1), today
    elif rng == "custom":
        s, e = to_date(start), to_date(end)
        if not s or not e:
            raise ValueError("custom range needs valid from/to dates (YYYY-MM-DD)")
        if s > e:
            raise ValueError("range start must not be after its end")
        if (e - s).days + 1 > MAX_CUSTOM_DAYS:
            raise ValueError(f"custom range is limited to {MAX_CUSTOM_DAYS} days")
    else:
        raise ValueError("range must be 7d, 30d, 90d or custom")
    length = (e - s).days + 1
    prev_end = s - timedelta(days=1)
    return {"start": s, "end": e, "prev_start": prev_end - timedelta(days=length - 1),
            "prev_end": prev_end, "days": length}


def resume_is_ok(meta):
    v = meta["versions"][-1] if meta.get("versions") else None
    return bool(v and v["validation"].get("ok"))


def collect_events(jobs, resumes, tracker_rows):
    """kind -> [date, ...] (one entry per thing that happened)."""
    ev = {"found": [], "qualified": [], "tailored": [], "applied": [], "interviews": []}
    tracked = {job_key(r["link"]): r for r in tracker_rows if r.get("link")}
    for j in jobs:
        d = to_date(j["found_at"]) or to_date(j["posted_date"])
        if d:
            ev["found"].append(d)
            if j["qualified"]:
                ev["qualified"].append(d)
        if j["tailored"]:
            t = to_date(j["tailored_at"]) or to_date((tracked.get(j["key"]) or {}).get("timestamp"))
            if t:
                ev["tailored"].append(t)
    for m in resumes:
        if resume_is_ok(m):
            d = to_date(m["created_at"])
            if d:
                ev["tailored"].append(d)
    for r in tracker_rows:
        d = to_date(r.get("status_updated")) or to_date(r.get("timestamp"))
        if not d:
            continue
        if r["status"] in APPLIED_STATUSES:
            ev["applied"].append(d)
        if r["status"] in INTERVIEW_STATUSES:
            ev["interviews"].append(d)
    return ev


def _count(dates, start, end):
    return sum(1 for d in dates if start <= d <= end)


def _metric(dates, p):
    cur, prev = _count(dates, p["start"], p["end"]), _count(dates, p["prev_start"], p["prev_end"])
    if prev == 0:
        change, trend = None, ("new" if cur else "flat")
    else:
        change = round(100 * (cur - prev) / prev)
        trend = "up" if cur > prev else "down" if cur < prev else "flat"
    return {"current": cur, "previous": prev, "change_pct": change, "trend": trend}


def summary(jobs, resumes, tracker, p):
    ev = collect_events(jobs, resumes, tracker["rows"])
    metrics = {"jobs_found": _metric(ev["found"], p), "resumes_tailored": _metric(ev["tailored"], p)}
    for name, kind in (("applications_sent", "applied"), ("interviews", "interviews")):
        metrics[name] = _metric(ev[kind], p) if tracker["available"] else None
    return {"period": _period_json(p), "metrics": metrics,
            "tracker": {"available": tracker["available"], "error": tracker["error"],
                        "stale": tracker["stale"], "configured": tracker["configured"]}}


def overview(jobs, resumes, tracker, p):
    ev = collect_events(jobs, resumes, tracker["rows"])
    items = [("found", "Jobs Found", True), ("qualified", "Qualified (8+)", True),
             ("tailored", "Resumes Tailored", True), ("applied", "Applications", tracker["available"]),
             ("interviews", "Interviews", tracker["available"])]
    return {"period": _period_json(p),
            "series": [{"key": k, "label": label, "value": _count(ev[k], p["start"], p["end"])
                        if ok else None} for k, label, ok in items],
            "tracker": {"available": tracker["available"], "error": tracker["error"]}}


def status_counts(tracker):
    counts = {s: 0 for s in STATUSES}
    for r in tracker["rows"]:
        counts[r["status"] if r["status"] in counts else "Not Applied"] += 1
    return {"statuses": [{"status": s, "count": counts[s]} for s in STATUSES],
            "total": sum(counts.values()), "tracker": {"available": tracker["available"],
                                                       "error": tracker["error"], "stale": tracker["stale"],
                                                       "configured": tracker["configured"]}}


def _period_json(p):
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items()}


# ---------------------------------------------------------------------------
# activity, sources, profile
# ---------------------------------------------------------------------------

def activity_feed(events, tailored, tracker_rows, limit=12):
    """Logged events, plus back-filled 'resume tailored' entries for pipeline resumes made
    before logging existed (dated by tailored_at, else the tracker's logged date)."""
    feed = list(events)
    logged = {e.get("link") for e in events if e.get("kind") == "resume_tailored"}
    tracked = {job_key(r["link"]): r for r in tracker_rows if r.get("link")}
    for t in tailored:
        if t.get("status") != "saved" or t.get("link") in logged:
            continue
        d = to_date(t.get("tailored_at")) or to_date((tracked.get(job_key(t["link"])) or {}).get("timestamp"))
        if d:
            feed.append({"kind": "resume_tailored", "ts": f"{d.isoformat()}T00:00:00+00:00",
                         "message": f"Resume tailored for {t['company']} - {t['title']}",
                         "link": t["link"], "date_only": True})
    feed.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return feed[:limit]


def _env(*names):
    return any(os.environ.get(n, "").strip() for n in names)


def sources():
    """Job sources + the services the pipeline depends on. Booleans and non-secret labels
    only -- never a key, token, id or credential."""
    import llm  # local import: provider chain is built from env at import time

    job_sources = [{"id": "linkedin", "name": "LinkedIn", "via": "Apify",
                    "active": _env("apify_api_key"),
                    "detail": "Full-Stack Java/Spring Boot/Angular postings via the Apify LinkedIn actor"}]
    services = [
        {"id": "groq", "name": "Groq", "active": _env("GROQ_API_KEY") and not llm.IS_LOCAL},
        {"id": "openrouter", "name": "OpenRouter", "active": _env("OPENROUTER_API_KEY", "open_router_apikey")
         and not llm.IS_LOCAL},
        {"id": "gemini", "name": "Gemini", "active": _env("GEMINI_API_KEY", "GOOGLE_API_KEY")
         and not llm.IS_LOCAL},
        {"id": "ollama", "name": "Local (Ollama)", "active": llm.IS_LOCAL},
        {"id": "sheets", "name": "Google Sheets", "active": _env("google_sheet_id")},
        {"id": "drive", "name": "Google Drive", "active": _env("google_drive_folder_id")},
        {"id": "telegram", "name": "Telegram alerts", "active": _env("TELEGRAM_BOT_TOKEN")
         and _env("TELEGRAM_CHAT_ID")},
    ]
    return {"job_sources": job_sources, "services": services,
            "llm": {"mode": "local" if llm.IS_LOCAL else "cloud", "chain": llm.PROVIDER_SUMMARY}}


def profile():
    try:
        md = paths.read_base_resume()
    except OSError:
        return {"name": "", "email": ""}
    name = next((l[2:].strip() for l in md.splitlines() if l.startswith("# ")), "")
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", md)
    return {"name": name, "email": m.group(0) if m else ""}
