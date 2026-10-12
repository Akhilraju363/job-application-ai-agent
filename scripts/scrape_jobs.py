"""Scrape Full-Stack Software Engineer (Java/Spring Boot/Angular) job postings from LinkedIn via Apify.

Actor: curious_coder/linkedin-jobs-scraper
Docs: https://apify.com/curious_coder/linkedin-jobs-scraper

Second source: Naukri jobs handed off by the separate Auto_job_apply project as a JSON file
(NAUKRI_JOBS_PATH) already in the raw-job format below. This script never scrapes Naukri; it
only reads that file and merges it into output/raw_jobs.json (deduped by canonical link).
JOB_SOURCES selects the sources (default "linkedin,naukri"; "naukri" alone skips the Apify call).
"""
import os
import shutil
import sys
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logging_config as lc  # noqa: E402
import paths  # noqa: E402
from activity import log_event  # noqa: E402
from job_links import canonical_link  # noqa: E402

log = lc.get_logger("scraper")

ACTOR_ID = "curious_coder~linkedin-jobs-scraper"
RUN_URL = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"

CACHE_PATH = ROOT / "output" / "raw_jobs.json"
CACHE_MAX_AGE_SECONDS = 6 * 60 * 60  # 6h -- each real scrape is a paid Apify call

# Effective per-run job count is mode-dependent (see effective_job_limit()), not the
# scrape_jobs() default below (which only applies to direct/test calls that bypass
# __main__).
#
# Cloud (Modal cron, LOCAL_MODE unset/false): JOB_LIMIT, default 10. score_jobs.py +
# tailor_job.py + company_research.py all share OpenRouter's free-tier ~50-req/day cap
# when Groq is throttled and OpenRouter is tried as fallback. Worst case (10 jobs, every
# job maxes retries and qualifies) is 3*10 + 2*10 = 50 requests, right at the cap;
# typical case is ~16. At limit=25 this was regularly exceeding 50 and causing
# silent/partial-failure runs -- see commit history on scripts/score_jobs.py.
#
# Local (LOCAL_MODE=true, Ollama only): LOCAL_JOB_LIMIT, default 50. No OpenRouter
# request cap applies here at all -- Ollama runs on your own machine with no daily
# quota. The real ceiling for local high-volume runs is local compute time, not a
# free-tier request count.
LOCAL_MODE = os.environ.get("LOCAL_MODE", "").strip().lower() in ("1", "true", "yes", "on")
JOB_LIMIT = int(os.environ.get("JOB_LIMIT", "10") or 10)
LOCAL_JOB_LIMIT = int(os.environ.get("LOCAL_JOB_LIMIT", "50") or 50)


def effective_job_limit():
    return LOCAL_JOB_LIMIT if LOCAL_MODE else JOB_LIMIT


DEFAULT_KEYWORDS = "Full Stack Java Spring Boot Angular AWS Developer"
DEFAULT_LOCATION = "India"
DEFAULT_DATE_POSTED = "past24Hours"
DATE_POSTED_OPTIONS = ("past24Hours", "pastWeek", "pastMonth")


def load_preferences():
    """Optional local search preferences saved from the dashboard's Job Alerts page
    (output/preferences.json). Absent file == the defaults above, so the Modal cron (which
    never has this file) is unaffected. The limit can only lower the mode's job cap."""
    try:
        raw = json.loads((paths.OUTPUT_DIR / "preferences.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    prefs = {
        "keywords": str(raw.get("keywords") or DEFAULT_KEYWORDS).strip() or DEFAULT_KEYWORDS,
        "location": str(raw.get("location") or DEFAULT_LOCATION).strip() or DEFAULT_LOCATION,
        "date_posted": raw.get("date_posted") if raw.get("date_posted") in DATE_POSTED_OPTIONS
        else DEFAULT_DATE_POSTED,
    }
    cap = effective_job_limit()
    try:
        prefs["limit"] = max(1, min(cap, int(raw.get("limit") or cap)))
    except (TypeError, ValueError):
        prefs["limit"] = cap
    return prefs


def scrape_jobs(keywords="Full Stack Java Spring Boot Angular AWS Developer", location="India", date_posted="past24Hours", limit=10):
    api_key = os.environ["apify_api_key"]

    payload = {
        "keywords": keywords,
        "location": location,
        "datePosted": date_posted,
        "limitPerSource": limit,
        "under10Applicants": False,
        "autoConvertToAiSearch": True,
        "scrapeCompany": False,
    }

    resp = requests.post(RUN_URL, params={"token": api_key}, json=payload, timeout=300)
    resp.raise_for_status()
    raw_jobs = resp.json()

    jobs = []
    for job in raw_jobs:
        jobs.append({
            "title": job.get("title"),
            "company": job.get("companyName") or job.get("company"),
            "link": job.get("link") or job.get("jobUrl") or job.get("url"),
            "description": job.get("descriptionText") or job.get("description"),
            "posted_date": job.get("postedDate") or job.get("postedAt") or job.get("publishedAt"),
            "location": job.get("location") or job.get("jobLocation"),
            "source": "LinkedIn",
            "found_at": date.today().isoformat(),
        })
    return jobs


NAUKRI_SOURCE = "Naukri"
KNOWN_SOURCES = ("linkedin", "naukri")
NAUKRI_REQUIRED_FIELDS = ("title", "company", "link", "description")


def job_sources():
    """Enabled sources from JOB_SOURCES (comma separated). Default: both -- the Naukri source is a
    no-op unless NAUKRI_JOBS_PATH points at a file, so the Modal cron is unaffected."""
    raw = os.environ.get("JOB_SOURCES", "").strip().lower()
    sources = tuple(s.strip() for s in raw.split(",") if s.strip()) or KNOWN_SOURCES
    unknown = [s for s in sources if s not in KNOWN_SOURCES]
    if unknown:
        raise ValueError(f"JOB_SOURCES has unknown source(s) {unknown}; use {', '.join(KNOWN_SOURCES)}")
    return sources


def load_naukri_jobs(path=None, limit=None):
    """Raw-job records exported by Auto_job_apply (`python main.py --action export-to-agent`).

    Missing path/file == no Naukri jobs. Records missing a required field are skipped (logged
    without their content). Extra fields (source_job_id, dedup_key, ...) are kept as-is.
    """
    path = path if path is not None else os.environ.get("NAUKRI_JOBS_PATH", "").strip()
    if not path:
        return []
    path = Path(path)
    if not path.exists():
        log.info("No Naukri handoff file; Naukri source skipped", extra={"path": str(path)})
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Naukri handoff file must contain a JSON list: {path}")
    jobs = []
    for index, job in enumerate(data):
        missing = [f for f in NAUKRI_REQUIRED_FIELDS
                   if not isinstance(job, dict) or not str(job.get(f) or "").strip()]
        if missing:
            log.warning("Skipping invalid Naukri record", extra={"index": index, "missing": missing})
            continue
        jobs.append({**job, "source": job.get("source") or NAUKRI_SOURCE,
                     "found_at": job.get("found_at") or date.today().isoformat()})
    limit = effective_job_limit() if limit is None else limit
    if len(jobs) > limit:
        log.info("Naukri jobs capped at the job limit", extra={"loaded": len(jobs), "job_limit": limit})
        jobs = jobs[:limit]
    return jobs


def merge_jobs(base, extra):
    """base + extra jobs; first occurrence wins per canonical link (base jobs are never dropped)."""
    seen = {canonical_link(j.get("link")) for j in base}
    merged = list(base)
    for job in extra:
        key = canonical_link(job.get("link"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(job)
    return merged


def merge_naukri_history(history, incoming):
    """Earlier Naukri records in raw_jobs.json + this run's handoff -> (records, conflicts, stats).

    Identity: canonical link (job_links.canonical_link) and, when present, source_job_id. Every
    earlier record stays unless the handoff legitimately replaces it:
    - new job (no link or id match): appended;
    - same link and same id (or one side has no id): refreshed in place -- the handoff's fields win
      (it is the current export) but the earliest found_at is kept, so re-importing never makes an old
      job look newer, and fields only the old record had are kept;
    - same link but a different id, or same id but a different link, or link and id pointing at two
      different records: a conflict. The handoff record takes its place (the current run exports it
      under that link) and every displaced record is returned in `conflicts` -- never silently dropped.
    Repeated jobs inside the handoff: the first occurrence wins (same as merge_jobs).
    Scoring and tailoring are unaffected: what they process is decided by the run's scope, not by
    what raw_jobs.json holds.
    """
    records = [dict(j) for j in history]
    stats = {"added": 0, "refreshed": 0, "duplicates_in_handoff": 0, "conflicts": 0}
    conflicts = []
    seen_links, seen_ids = set(), set()

    def job_id(job):
        return str(job.get("source_job_id") or "").strip() or None

    for job in incoming:
        link, jid = canonical_link(job.get("link")), job_id(job)
        if link in seen_links or (jid and jid in seen_ids):
            stats["duplicates_in_handoff"] += 1
            continue
        seen_links.add(link)
        if jid:
            seen_ids.add(jid)
        by_link = [i for i, r in enumerate(records) if canonical_link(r.get("link")) == link]
        by_id = [i for i, r in enumerate(records) if jid and job_id(r) == jid]
        matches = sorted(set(by_link) | set(by_id))
        if not matches:
            records.append(job)
            stats["added"] += 1
            continue
        old = records[matches[0]]
        same_job = (len(matches) == 1 and canonical_link(old.get("link")) == link
                    and (job_id(old) is None or jid is None or job_id(old) == jid))
        if same_job:
            found = sorted(d for d in (old.get("found_at"), job.get("found_at")) if d)
            records[matches[0]] = {**old, **job, **({"found_at": found[0]} if found else {})}
            stats["refreshed"] += 1
            continue
        reason = ("same link, different source_job_id" if by_link and not by_id else
                  "same source_job_id, different link" if by_id and not by_link else
                  "link and source_job_id match different records")
        for index in reversed(matches):
            conflicts.append({"reason": reason, "displaced": records.pop(index),
                              "replaced_by": {"link": job.get("link"), "source_job_id": jid}})
        records.append(job)
        stats["conflicts"] += 1
        log.warning("Naukri record conflict: earlier record kept in raw_jobs_conflicts.jsonl",
                    extra={"reason": reason, "source_job_id": jid})
    return records, conflicts, stats


def _keep_conflicts(conflicts):
    """Append displaced records to output/raw_jobs_conflicts.jsonl (append-only, never cleared)."""
    if not conflicts:
        return
    path = CACHE_PATH.with_name("raw_jobs_conflicts.jsonl")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        for conflict in conflicts:
            handle.write(json.dumps({"at": stamp, **conflict}) + "\n")


def _backup_unreadable_cache():
    """Preserve an unreadable cache under a unique name before recovery."""
    stamp = int(time.time())
    keep = CACHE_PATH.with_name(f"raw_jobs.unreadable-{stamp}.json")
    suffix = 1
    while keep.exists():
        keep = CACHE_PATH.with_name(f"raw_jobs.unreadable-{stamp}-{suffix}.json")
        suffix += 1
    shutil.copyfile(CACHE_PATH, keep)
    return keep


def _read_cached_jobs():
    """Read the cache once, preserving malformed or structurally invalid content.

    ``None`` means recovery is required; an empty list is a valid empty cache.
    """
    if not CACHE_PATH.exists():
        return []
    try:
        cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        keep = _backup_unreadable_cache()
        log.error("raw_jobs.json unreadable; copied aside for recovery", extra={"copy": str(keep)})
        return None
    if not isinstance(cached, list):
        keep = _backup_unreadable_cache()
        log.error("raw_jobs.json has an invalid top-level type; copied aside for recovery",
                  extra={"copy": str(keep), "type": type(cached).__name__})
        return None
    return cached


_CACHE_UNSET = object()


def _cached_naukri_history(cached=_CACHE_UNSET):
    """Naukri records already in raw_jobs.json (whatever the LinkedIn cache age)."""
    if cached is _CACHE_UNSET:
        cached = _read_cached_jobs()
    return [j for j in (cached or []) if isinstance(j, dict) and j.get("source") == NAUKRI_SOURCE]


def _write_raw_jobs(jobs, mtime=None):
    """Write output/raw_jobs.json. `mtime` keeps the LinkedIn cache age honest: merging Naukri
    jobs into a cached scrape must not make that scrape look newer than it is."""
    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
    if mtime is not None:
        os.utime(CACHE_PATH, (time.time(), mtime))


def run_scrape(force=False):
    """The scrape stage: LinkedIn (Apify, 6h cache) and/or the Naukri handoff file -> raw_jobs.json."""
    import artifacts

    sources = job_sources()
    # A failed Modal run may have already scraped today -- reuse that instead of
    # spending another paid Apify call on the local recovery run.
    artifacts.pull("raw_jobs.json")
    naukri_jobs = load_naukri_jobs() if "naukri" in sources else []
    cached = _read_cached_jobs()
    cache_recovery_required = cached is None
    # Earlier Naukri records are kept (merged with this run's handoff), not replaced by it.
    naukri_history = _cached_naukri_history(cached)
    cache_age = time.time() - CACHE_PATH.stat().st_mtime if CACHE_PATH.exists() else None
    cache_fresh = (not force and not cache_recovery_required and cache_age is not None
                   and cache_age < CACHE_MAX_AGE_SECONDS)

    def cached_non_naukri():
        return [j for j in (cached or []) if isinstance(j, dict) and j.get("source") != NAUKRI_SOURCE]

    if "linkedin" not in sources:
        # Naukri-only: never call Apify. Keep a still-fresh LinkedIn cache (and its age);
        # otherwise mark the file stale so the next LinkedIn run scrapes as usual.
        base = cached_non_naukri() if cache_fresh else []
        mtime = CACHE_PATH.stat().st_mtime if cache_fresh else 0
        if cache_recovery_required and not naukri_jobs:
            raise ValueError("raw_jobs.json could not be recovered and no Naukri handoff was provided")
        log.info("Naukri-only run: Apify skipped",
                 extra={"cached_count": len(base), "naukri_count": len(naukri_jobs)})
    elif cache_fresh:
        if not naukri_jobs:  # unchanged pre-Naukri behaviour
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            log.info("Using cached scrape (pass --force or set FORCE_SCRAPE=true to re-scrape)",
                     extra={"cached_count": len(cached), "cache_age_minutes": round(cache_age / 60),
                            "path": str(CACHE_PATH)})
            artifacts.push("raw_jobs.json")
            return cached
        base, mtime = cached_non_naukri(), CACHE_PATH.stat().st_mtime
        log.info("Using cached scrape; merging Naukri jobs",
                 extra={"cached_count": len(base), "cache_age_minutes": round(cache_age / 60)})
    else:
        prefs = load_preferences()
        limit = prefs["limit"]
        log.info("Scrape started", extra={"mode": "local" if LOCAL_MODE else "cloud", "job_limit": limit})
        base = scrape_jobs(keywords=prefs["keywords"], location=prefs["location"],
                           date_posted=prefs["date_posted"], limit=limit)
        mtime = None
        log.info("Scrape completed", extra={"scraped_count": len(base), "path": str(CACHE_PATH)})
        log_event("jobs_found", f"Found {len(base)} new jobs from LinkedIn", count=len(base), source="LinkedIn")

    naukri, conflicts, stats = merge_naukri_history(naukri_history, naukri_jobs)
    _keep_conflicts(conflicts)
    jobs = merge_jobs(base, naukri)
    _write_raw_jobs(jobs, mtime)
    artifacts.push("raw_jobs.json")
    if naukri_jobs:
        added = stats["added"]
        log.info("Naukri jobs merged", extra={"naukri_count": len(naukri_jobs), "added": added,
                                              "refreshed": stats["refreshed"], "conflicts": stats["conflicts"],
                                              "kept_history": len(naukri_history), "total": len(jobs)})
        log_event("jobs_found", f"Loaded {added} Naukri jobs from Auto_job_apply", count=added, source=NAUKRI_SOURCE)
    return jobs


if __name__ == "__main__":
    lc.configure_logging("pipeline")
    force = "--force" in sys.argv or os.environ.get("FORCE_SCRAPE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    run_scrape(force=force)
