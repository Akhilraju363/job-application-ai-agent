"""Company research (PRD 3.5). Per successfully-tailored job, one OpenRouter call
returning 3-5 talking points -- context for Akhil, never written into the resume
itself. Adds a "company_notes" field to each saved entry in output/tailored_jobs.json.
"""
import json
import os
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


def research_company(company, title, api_key):
    prompt = RESEARCH_PROMPT.replace("__COMPANY__", company).replace("__TITLE__", title)
    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": MODEL, "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


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
