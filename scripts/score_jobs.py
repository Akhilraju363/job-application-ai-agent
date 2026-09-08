"""Score scraped jobs 1-10 against the base resume and apply the hard 8+ cutoff.

Reads output/raw_jobs.json, writes output/scored_jobs.json (qualified + rejected,
so reject counts stay auditable).

LLM endpoint/model/retry config lives in scripts/llm.py (.env-driven -- point it at a
local Ollama server to test without spending OpenRouter free-tier quota).
"""
import sys
import json
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm import call_llm, PROVIDER_SUMMARY  # noqa: E402

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


def score_job(job, resume_text):
    prompt = (
        RUBRIC_PROMPT
        .replace("__RESUME__", resume_text)
        .replace("__TITLE__", str(job.get("title")))
        .replace("__COMPANY__", str(job.get("company")))
        .replace("__DESCRIPTION__", str(job.get("description")))
    )
    result = json.loads(call_llm(prompt, job.get("title"), json_mode=True))

    return {
        **job,
        "score": result["score"],
        "reasoning": result["reasoning"],
        "matched_must_haves": result.get("matched_must_haves", []),
        "missing_must_haves": result.get("missing_must_haves", []),
        "qualified": result["score"] >= QUALIFY_CUTOFF,
    }


def score_jobs(jobs, resume_text, out_path=None, scored=None, on_progress=None):
    scored = list(scored) if scored else []
    for job in jobs:
        try:
            scored.append(score_job(job, resume_text))
        except Exception as e:
            print(f"FAILED, skipping {job.get('title')!r} (providers: {PROVIDER_SUMMARY}): "
                  f"{type(e).__name__}: {e}")
            continue
        if out_path is not None:
            out_path.write_text(json.dumps(scored, indent=2), encoding="utf-8")
            if on_progress is not None:
                on_progress()
    return scored


if __name__ == "__main__":
    import artifacts

    resume_text = (ROOT / "resume" / "base_resume.md").read_text(encoding="utf-8")

    # Recovery: reuse whatever a failed earlier run already scraped/scored so a
    # local Ollama run only fills the gap instead of re-scoring from scratch.
    artifacts.pull("raw_jobs.json")
    artifacts.pull("scored_jobs.json")

    jobs = json.loads((ROOT / "output" / "raw_jobs.json").read_text(encoding="utf-8"))
    out_path = ROOT / "output" / "scored_jobs.json"

    already_scored = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else []
    done_links = {j["link"] for j in already_scored}
    remaining = [j for j in jobs if j["link"] not in done_links]
    if already_scored:
        print(f"Resuming: {len(already_scored)}/{len(jobs)} already scored, {len(remaining)} left")

    push_scored = lambda: artifacts.push("scored_jobs.json")  # noqa: E731
    scored = (score_jobs(remaining, resume_text, out_path=out_path, scored=already_scored,
                         on_progress=push_scored)
              if remaining else already_scored)
    out_path.write_text(json.dumps(scored, indent=2), encoding="utf-8")
    artifacts.push("scored_jobs.json")

    if remaining and len(scored) == len(already_scored):
        # Every job in this run failed (e.g. OpenRouter free-tier daily quota exhausted).
        # exit 0 here would look like a normal "0 qualified" day to modal_app.py's
        # subprocess.run(check=True) -- raise so the pipeline's failure path (and Telegram
        # alert) actually fires instead of silently producing an empty scored_jobs.json.
        raise RuntimeError(f"scored 0/{len(remaining)} jobs this run -- every job failed, see retry logs above")

    qualified_count = sum(1 for j in scored if j["qualified"])
    print(f"{len(scored)} scraped, {qualified_count} qualified -> {out_path}")
