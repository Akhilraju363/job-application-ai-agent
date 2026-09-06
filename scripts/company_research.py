"""Company research (PRD 3.5). Per successfully-tailored job, one LLM call returning
3-5 talking points -- context for Akhil, never written into the resume itself. Adds a
"company_notes" field to each saved entry in output/tailored_jobs.json.

LLM endpoint/model/retry config lives in scripts/llm.py (.env-driven).
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm import call_llm  # noqa: E402

RESEARCH_PROMPT = """Give 3-5 short talking points about __COMPANY__ useful for someone
interviewing for a __TITLE__ role there -- what they do/their product, engineering culture
or tech stack if known, anything relevant to bring up in an interview. Only include things
you're confident about; skip recent news or specifics you're not sure of rather than
guessing. Plain text, one point per line, no headers, no commentary before or after."""


def research_company(company, title):
    prompt = RESEARCH_PROMPT.replace("__COMPANY__", company).replace("__TITLE__", title)
    return call_llm(prompt, company)


if __name__ == "__main__":
    import artifacts

    # Recovery: reuse the tailored resumes + any company notes an earlier run wrote.
    artifacts.pull("tailored_jobs.json")

    tailored_path = ROOT / "output" / "tailored_jobs.json"
    jobs = json.loads(tailored_path.read_text(encoding="utf-8"))

    researched = 0
    for job in jobs:
        # A non-empty company_notes means a prior run already did this one -- skip it.
        if job.get("status") != "saved" or job.get("company_notes"):
            continue
        try:
            job["company_notes"] = research_company(job["company"], job["title"])
            researched += 1
            print(f"researched {job['company']}")
        except Exception as e:
            job["company_notes"] = ""
            print(f"ERROR researching {job['company']}: {e}")
        tailored_path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
        artifacts.push("tailored_jobs.json")

    tailored_path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
    artifacts.push("tailored_jobs.json")
    print(f"{researched} companies researched")
