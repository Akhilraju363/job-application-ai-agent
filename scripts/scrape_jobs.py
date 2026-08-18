"""Scrape Full-Stack Software Engineer (Java/Spring Boot/Angular) job postings from LinkedIn via Apify.

Actor: curious_coder/linkedin-jobs-scraper
Docs: https://apify.com/curious_coder/linkedin-jobs-scraper
"""
import os
import sys
import json
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ACTOR_ID = "curious_coder~linkedin-jobs-scraper"
RUN_URL = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"


def scrape_jobs(keywords="Full Stack Java Developer", location="United States", date_posted="pastWeek", limit=25):
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
    jobs = scrape_jobs()
    out_path = ROOT / "output" / "raw_jobs.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
    print(f"Scraped {len(jobs)} jobs -> {out_path}")
