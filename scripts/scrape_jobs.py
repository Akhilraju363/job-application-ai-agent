"""Scrape Full-Stack Software Engineer (Java/Spring Boot/Angular) job postings from LinkedIn via Apify.

Actor: curious_coder/linkedin-jobs-scraper
Docs: https://apify.com/curious_coder/linkedin-jobs-scraper
"""
import os
import sys
import json
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

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
        })
    return jobs


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import artifacts

    force = "--force" in sys.argv or os.environ.get("FORCE_SCRAPE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    # A failed Modal run may have already scraped today -- reuse that instead of
    # spending another paid Apify call on the local recovery run.
    artifacts.pull("raw_jobs.json")
    cache_age = time.time() - CACHE_PATH.stat().st_mtime if CACHE_PATH.exists() else None

    if not force and cache_age is not None and cache_age < CACHE_MAX_AGE_SECONDS:
        cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        print(
            f"Using cached {CACHE_PATH} ({len(cached)} jobs, "
            f"{cache_age / 60:.0f}m old) -- pass --force or set FORCE_SCRAPE=true to re-scrape"
        )
        artifacts.push("raw_jobs.json")
        sys.exit(0)

    limit = effective_job_limit()
    print(f"{'Local' if LOCAL_MODE else 'Cloud'} mode -- job limit: {limit}")
    jobs = scrape_jobs(limit=limit)
    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
    artifacts.push("raw_jobs.json")
    print(f"Scraped {len(jobs)} jobs -> {CACHE_PATH}")
