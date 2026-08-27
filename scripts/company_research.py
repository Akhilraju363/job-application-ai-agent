"""Company research (PRD 3.5). Per successfully-tailored job, one OpenRouter call
returning 3-5 talking points -- context for Akhil, never written into the resume
itself. Adds a "company_notes" field to each saved entry in output/tailored_jobs.json.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "nvidia/nemotron-3.5-lightning:free"

RESEARCH_PROMPT = """Give 3-5 short talking points about __COMPANY__ useful for someone
interviewing for a __TITLE__ role there -- what they do/their product, engineering culture
or tech stack if known, anything relevant to bring up in an interview. Only include things
you're confident about; skip recent news or specifics you're not sure of rather than
guessing. Plain text, one point per line, no headers, no commentary before or after."""


MAX_RETRIES = 2
REQUEST_DEADLINE = 240  # see scripts/score_jobs.py for why -- same reasoning model and
                        # same hidden-"thinking"-tokens behavior applies here too.


def _post(payload, api_key):
    return requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=REQUEST_DEADLINE,
    )


def research_company(company, title, api_key):
    prompt = RESEARCH_PROMPT.replace("__COMPANY__", company).replace("__TITLE__", title)
    # see scripts/score_jobs.py for why -- caps hidden reasoning tokens to cut timeout rate.
    payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "reasoning": {"effort": "low"}}

    for attempt in range(1, MAX_RETRIES + 1):
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            resp = ex.submit(_post, payload, api_key).result(timeout=REQUEST_DEADLINE)
        except FutureTimeoutError:
            ex.shutdown(wait=False)
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt
            print(f"  retry {attempt}/{MAX_RETRIES} for {company!r} after hard timeout ({REQUEST_DEADLINE}s, waiting {wait}s)")
            time.sleep(wait)
            continue
        ex.shutdown(wait=False)

        try:
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt
            print(f"  retry {attempt}/{MAX_RETRIES} for {company!r} after {type(e).__name__}: {e} (waiting {wait}s)")
            time.sleep(wait)


if __name__ == "__main__":
    api_key = os.environ["open_router_apikey"]
    tailored_path = ROOT / "output" / "tailored_jobs.json"
    jobs = json.loads(tailored_path.read_text(encoding="utf-8"))

    researched = 0
    for job in jobs:
        if job.get("status") != "saved" or job.get("company_notes"):
            continue
        try:
            job["company_notes"] = research_company(job["company"], job["title"], api_key)
            researched += 1
            print(f"researched {job['company']}")
        except Exception as e:
            job["company_notes"] = ""
            print(f"ERROR researching {job['company']}: {e}")

    tailored_path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
    print(f"{researched} companies researched")
