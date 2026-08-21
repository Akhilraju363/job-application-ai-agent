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


def scrape_jobs(keywords="Full Stack Java Spring Boot Angular AWS Developer", location="India", date_posted="past24Hours", limit=10):
    # limit=10, not 25 -- score_jobs.py + tailor_job.py + company_research.py all share
    # OpenRouter's free-tier 50-req/day cap (no paid credits added). Worst case (10 jobs,
    # every job maxes retries and qualifies) is 3*10 + 2*10 = 50 requests, right at the
    # cap; typical case is ~16. At limit=25 this was regularly exceeding 50 and causing
    # silent/partial-failure runs -- see commit history on scripts/score_jobs.py.
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
    force = "--force" in sys.argv
    cache_age = time.time() - CACHE_PATH.stat().st_mtime if CACHE_PATH.exists() else None

    if not force and cache_age is not None and cache_age < CACHE_MAX_AGE_SECONDS:
        cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        print(
            f"Using cached {CACHE_PATH} ({len(cached)} jobs, "
            f"{cache_age / 60:.0f}m old) -- pass --force to re-scrape from Apify"
        )
        sys.exit(0)

    jobs = scrape_jobs()
    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
    print(f"Scraped {len(jobs)} jobs -> {CACHE_PATH}")
