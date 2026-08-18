# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project State

Built. The full pipeline (scrape, score, tailor, validate, research, log) is implemented in
`scripts/`, gated by `.claude/hooks/resume_hook.py` and `.claude/skills/tailor-resume/SKILL.md`,
and deployable via `modal_app.py`. `PRD.md` is still the source of truth for scope and hard rules —
read it before changing pipeline behavior. This is a Claude Code Masterclass capstone demo project;
treat the PRD's hard rules as binding, not suggestions to redesign.

## What This Builds

A job-application agent: scrapes Full-Stack Software Engineer (Java/Spring Boot/Angular) postings,
scores each against the base resume, tailors a resume for every job scoring 8+/10, logs qualifying
jobs to a Google Sheet, saves tailored resumes to per-job folders on Desktop, and runs unattended
once daily on Modal.

## Pipeline (build in this order — each step's output feeds the next)

1. **Scrape** — Apify actor (free tier) for LinkedIn Full-Stack Software Engineer / Java Developer
   postings, remote/hybrid, full-time. Output: `{title, company, link, description, posted_date}`.
2. **Score + Filter** — score each job 1-10 against `resume/base_resume.md`, extracting
   `matched_must_haves`/`missing_must_haves` as part of the same call (no separate parse step).
   **Hard cutoff: only 8+ continues.** Log reject count alongside qualified count (e.g. "10
   scraped, 3 qualified") — this is the on-camera proof the filter works.
3. **Tailor Resume** (Skill: `.claude/skills/tailor-resume/SKILL.md`) — reorder/reword the base
   resume to mirror the job's language and keywords. **Never invent experience, employers, tools,
   or metrics.** Reorder and reword only.
4. **Validate** (Hook) — before a tailored resume is saved or logged, check required sections exist
   (Summary, Skills, Experience) and no placeholder text remains. On failure, do not write the row —
   flag it instead.
5. **Company Research** (`scripts/company_research.py`) — per qualifying job, one OpenRouter call
   returns 3-5 talking points about the company/role. This is a plain script, not a Claude Code
   subagent — a real subagent needs a live interactive session, which the Modal headless cron can't
   provide. Used for human context only, never written verbatim into the resume.
6. **Outputs**:
   - Google Sheet row per qualifying job: title, company, job link, fit score, resume path, status,
     timestamp.
   - Desktop folder (interactive path) or Drive folder (Modal path) per qualifying job:
     `Job Applications/{company}-{job-title-slug}/` on Desktop (all per-job folders nest under one
     `Job Applications` parent), containing the tailored resume. The Sheet row references this
     exact folder/file.
7. **Headless + Hosting** — the automated path (`scripts/tailor_job.py`, `scripts/company_research.py`)
   uses OpenRouter instead of live Claude reasoning, so it can run unattended without an Anthropic
   API key. Deployed as a Modal scheduled function (`@app.function(schedule=modal.Cron(...))`),
   once daily. Secrets (Apify key, OpenRouter key, Google creds, Telegram bot token) via Modal
   secrets — never committed.

## Hard Rules

- **8+/10 cutoff is non-negotiable** — nothing below it reaches tailoring, Sheet, or Desktop/Drive.
- **No fabrication** in tailored resumes — reorder/reword existing content only.
- **No auto-submitting applications** — the agent prepares; a human clicks apply.
- **Real candidate data** — `resume/base_resume.md` is Akhil Dalali's actual resume; never fabricate
  additions to it, only reorder/reword existing content per job.
- **Single niche** — Full-Stack Java/Spring Boot/Angular Engineer only.
- Wrap the scheduled Modal run in try/except; on any failure, send a Telegram alert named
  `JOB-APPLY-AGENT — WHAT BROKE`, then re-raise. No silent failures.

## Folder Structure (as built)

```
job-apply-agent/
  CLAUDE.md
  PRD.md
  README.md
  GWS_SETUP.md
  .env.example
  .claude/skills/tailor-resume/SKILL.md
  .claude/hooks/resume_hook.py
  resume/base_resume.md
  scripts/
    scrape_jobs.py         # Apify
    score_jobs.py           # scores + extracts matched/missing requirements
    tailor_job.py            # automated tailoring path (OpenRouter, for Modal)
    company_research.py
    write_sheet.py          # Google Sheets
    format_resume_doc.py     # markdown -> real Google Docs formatting
    validate_resume.py       # shared validation logic
  output/                    # gitignored — raw/scored/tailored job data
  modal_app.py                # scheduled entrypoint
  .env                        # gitignored — Apify key, OpenRouter key, Google creds, Telegram bot token
```

## Scoring (built)

`scripts/score_jobs.py` scores each job in `output/raw_jobs.json` 1-10 against
`resume/base_resume.md` via one OpenRouter call per job (`nvidia/nemotron-3.5-lightning:free`), using an explicit
rubric (must-have skills weighted heaviest, then years-of-experience/seniority fit, then
nice-to-haves as a tiebreaker — see the `RUBRIC_PROMPT` constant in the script for exact wording).
The script applies the `score >= 8` cutoff in code, not via a model-declared verdict. Output is
`output/scored_jobs.json` — **both qualified and rejected jobs are kept**, with a `qualified` bool,
so reject counts stay auditable per the PRD's "log the reject count too" requirement. Requires
`open_router_apikey` in `.env`.

## Tailoring + Sheet tracking (built)

`.claude/skills/tailor-resume/SKILL.md` tailors the resume per qualifying job (reorder/reword
only, no fabrication), gated by `.claude/hooks/resume_hook.py` (a `PreToolUse` hook that blocks
the write if required sections are missing or placeholder text remains), then builds a Google Doc
via `gws`, formats it with `scripts/format_resume_doc.py` (converts the markdown structure into
real bold headers/bullets/italics — **never use `gws docs +write`** with raw markdown text, it
inserts `#`/`##`/`-` as literal characters instead of formatting), exports to PDF into
`~/Desktop/{company}-{slug}/Akhil Dalali Resume.pdf`, and deletes the intermediate Doc.
`scripts/write_sheet.py` then logs each `status: "saved"` entry from `output/tailored_jobs.json` to
the "Job Application Tracker" Google Sheet (id cached in `.env` as `google_sheet_id`), deduped by
job link, with `Status` starting at `"Not Applied"` for manual tracking.

Two `gws` gotchas worth knowing: `files` is a sub-resource of `drive`, not top-level
(`gws drive files export/delete`, not `gws files ...`), and `--output` for `gws drive files export`
is sandboxed to the current directory — `cd` into the target folder and export with a relative
filename, an absolute path is rejected.

## Verification Per Step

- Scrape: returns N jobs with title, company, link, description.
- Score: every job has a numeric score; jobs below 8 are excluded from all downstream steps.
- Tailor: output resume has no placeholder text and passes the validation hook.
- Sheet: row count matches qualifying-job count; every row has a working job link and resume
  reference.
- Desktop: folder count matches qualifying-job count; each folder has exactly one resume file.
- Headless run: `claude -p` completes with the same output as the interactive run, restricted to
  the allowed tools.
- Modal: scheduled function deploys, manual trigger runs end to end, forced failure produces the
  named Telegram alert.
