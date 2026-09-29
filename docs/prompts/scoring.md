# Scoring prompt

- **Purpose:** score one scraped job 1-10 against the master resume and extract matched/missing must-haves in the same call.
- **Source (hard-coded):** `scripts/score_jobs.py` line 28, constant `RUBRIC_PROMPT`; filled by `score_job()` and sent via
  `llm.call_llm(prompt, title, json_mode=True)`.
- **Inputs (placeholders):** `__RESUME__` = `resume/base_resume.md`; `__TITLE__`, `__COMPANY__`, `__DESCRIPTION__` from the scraped job.
- **Expected output:** JSON only — `{"score": int 1-10, "reasoning": str, "matched_must_haves": [str], "missing_must_haves": [str]}`.
  `llm.py` validates the JSON (tolerates fences/prose) and fails over to the next provider on bad JSON.
- **Cutoff:** applied in code, not by the model — `QUALIFY_CUTOFF = 8` in `scripts/score_jobs.py`; `qualified = score >= 8`.
  Changing the cutoff or this prompt: update this file in the same change (see `CLAUDE.md`).
- **Version:** as of commit `5c03f4c` + working tree, 2026-09-29.
- **Suggestion (not done):** load the prompt from this file instead of the Python constant, so prompt edits don't need a code change.

## Prompt (verbatim)

```text
You are screening a job posting against a candidate's resume for fit.

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
```
