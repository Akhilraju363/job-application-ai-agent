"""Score scraped jobs 1-10 against the base resume and apply the hard 8+ cutoff.

Reads output/raw_jobs.json, writes output/scored_jobs.json (qualified + rejected,
so reject counts stay auditable).
"""
import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "nvidia/nemotron-3.5-lightning:free"
QUALIFY_CUTOFF = 8

RUBRIC_PROMPT = """You are screening a job posting against a candidate's resume for fit.

Score 1-10 using this rubric:
- Must-have skills/tools match (heaviest weight): does the resume cover the JD's explicitly
  listed required skills/tools?
- Years-of-experience fit: score down if the JD wants notably more experience than the resume
  shows, or if the JD's level signals a different seniority than the resume.
- Seniority/role-type match: e.g. IC Full-Stack vs Backend-only vs Frontend-only vs a
  differently-scoped role (mobile, data engineering, etc).
- Nice-to-haves: bonus signal only, never offsets a missing must-have.

Score bands:
9-10 = meets/exceeds nearly all must-haves and experience fits.
7-8 = meets most must-haves, close experience fit.
5-6 = roughly half the must-haves, or a real experience/seniority mismatch.
1-4 = missing most must-haves or a fundamentally different role.

Respond with ONLY valid JSON, no markdown fences, in this exact shape:
{"score": <int 1-10>, "reasoning": "<1-3 sentences>", "matched_must_haves": ["..."], "missing_must_haves": ["..."]}

RESUME:
__RESUME__

JOB TITLE: __TITLE__
COMPANY: __COMPANY__
JOB DESCRIPTION:
__DESCRIPTION__
"""


MAX_RETRIES = 3
REQUEST_DEADLINE = 90  # hard wall-clock cap per attempt -- requests' own `timeout` can be
                       # bypassed by a server that trickles bytes slowly enough to keep
                       # resetting the per-read timeout window without ever finishing.


def _post(payload, api_key):
    return requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=REQUEST_DEADLINE,
    )


def score_job(job, resume_text, api_key):
    prompt = (
        RUBRIC_PROMPT
        .replace("__RESUME__", resume_text)
        .replace("__TITLE__", str(job.get("title")))
        .replace("__COMPANY__", str(job.get("company")))
        .replace("__DESCRIPTION__", str(job.get("description")))
    )
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }

    for attempt in range(1, MAX_RETRIES + 1):
        # Not using ThreadPoolExecutor as a context manager: its __exit__ calls
        # shutdown(wait=True), which would block on the very hung thread we're
        # trying to time out on. shutdown(wait=False) abandons it instead --
        # the orphaned request just gets discarded when it eventually returns.
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            resp = ex.submit(_post, payload, api_key).result(timeout=REQUEST_DEADLINE)
        except FutureTimeoutError as e:
            ex.shutdown(wait=False)
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt
            print(f"  retry {attempt}/{MAX_RETRIES} for {job.get('title')!r} after hard timeout ({REQUEST_DEADLINE}s, waiting {wait}s)")
            time.sleep(wait)
            continue
        ex.shutdown(wait=False)

        try:
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            result = json.loads(content)
            break
        except (requests.exceptions.RequestException, FutureTimeoutError, json.JSONDecodeError, KeyError) as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt
            print(f"  retry {attempt}/{MAX_RETRIES} for {job.get('title')!r} after {type(e).__name__}: {e} (waiting {wait}s)")
            time.sleep(wait)

    return {
        **job,
        "score": result["score"],
        "reasoning": result["reasoning"],
        "matched_must_haves": result.get("matched_must_haves", []),
        "missing_must_haves": result.get("missing_must_haves", []),
        "qualified": result["score"] >= QUALIFY_CUTOFF,
    }


def score_jobs(jobs, resume_text, api_key, out_path=None, scored=None):
    scored = list(scored) if scored else []
    for job in jobs:
        try:
            scored.append(score_job(job, resume_text, api_key))
        except (requests.exceptions.RequestException, FutureTimeoutError, json.JSONDecodeError, KeyError) as e:
            print(f"FAILED after {MAX_RETRIES} retries, skipping {job.get('title')!r}: {e}")
            continue
        if out_path is not None:
            out_path.write_text(json.dumps(scored, indent=2), encoding="utf-8")
    return scored


if __name__ == "__main__":
    api_key = os.environ["open_router_apikey"]
    resume_text = (ROOT / "resume" / "base_resume.md").read_text(encoding="utf-8")
    jobs = json.loads((ROOT / "output" / "raw_jobs.json").read_text(encoding="utf-8"))

    out_path = ROOT / "output" / "scored_jobs.json"

    already_scored = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else []
    done_links = {j["link"] for j in already_scored}
    remaining = [j for j in jobs if j["link"] not in done_links]
    if already_scored:
        print(f"Resuming: {len(already_scored)}/{len(jobs)} already scored, {len(remaining)} left")

    scored = score_jobs(remaining, resume_text, api_key, out_path=out_path, scored=already_scored) if remaining else already_scored
    out_path.write_text(json.dumps(scored, indent=2), encoding="utf-8")

    qualified_count = sum(1 for j in scored if j["qualified"])
    print(f"{len(scored)} scraped, {qualified_count} qualified -> {out_path}")
