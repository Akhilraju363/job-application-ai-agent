# Changelog

Newest first. One line per change. Entries up to 2026-09-28 come from `git log`; 2026-09-29 entries are uncommitted.

- 2026-10-07 (uncommitted) feat: verified local PDF artifacts (`scripts/resume_artifacts.py`, `resume_store.mark_artifact`); pipeline publishes `v<n>.pdf` and uploads that file to Drive; saved records carry `resume_id`/`resume_version`
- 2026-10-07 (uncommitted) feat: Naukri source from the Auto_job_apply handoff file (`NAUKRI_JOBS_PATH`, `JOB_SOURCES=naukri` skips Apify); `source` kept through tailoring + Sheet; dashboard lists Naukri
- 2026-09-29 (uncommitted) docs: short `CLAUDE.md`; add `docs/ARCHITECTURE|CONFIG|RUNBOOK|MEMORY|TASKS|CHANGELOG.md` and `docs/prompts/`
- 2026-09-29 (uncommitted) ui: design tokens, grouped sidebar, account menu, page headers, sort/search, Tailor stepper; `docs/ui/` specs
- 2026-09-29 (uncommitted) feat: Master Resume editor page + `GET/PUT /api/master-resume` (`scripts/master_resume.py`)
- 2026-09-29 (uncommitted) fix: scheduled pipeline tailors via `tailoring_service` (no unverified resumes reach Drive)
- 2026-09-29 (uncommitted) feat: no-fabrication rejects header/section/title/date/location changes; `master_resume_current` flag
- 2026-09-29 (uncommitted) feat: master resume from the ATS PDF; exports match its A4 layout
- 2026-09-28 fix: fixed resume header for every job; dedupe LinkedIn in contact line
- 2026-09-26 feat: add Windows double-click startup launcher
- 2026-09-25 feat: add role-based resumes and local startup
- 2026-09-24 fix: remove personal contact info from public resume
- 2026-09-24 fix: give LLM 429s a separate retry budget
- 2026-09-23 fix: remove pipeline status hints from job status dropdown
- 2026-09-22 feat: add editable job status dropdown and table scroll
- 2026-09-21 feat: username/password sign-in for the dashboard (replaces the pasted token)
- 2026-09-21 feat: production logging (console + rotating files, request/task ids, /api/logs + Logs page)
- 2026-09-21 feat: host dashboard UI + API on one Modal web function (own secret)
- 2026-09-21 feat: upload tailored resumes to Google Drive
- 2026-09-21 fix: allow manual JDs without company names; package job links for Modal runtime
- 2026-09-20 feat: add JD tailored resume dashboard workflow; add Jenkins and Maven to master resume
- 2026-09-15 feat: add local Ollama high-volume mode; `--force` passthrough for manual re-scrapes; correct resume content
- 2026-09-08 feat: resilient LLM pipeline with free providers; Groq `openai/gpt-oss-120b`; harden `.gitignore`
- 2026-09-07 fix: LLM fallback and qualified-job failure alerts
- 2026-09-06 feat: shared LLM client, resume-safe recovery, Drive artifact sync
- 2026-08-28 feat: minimax fallback when nemotron fails
- 2026-08-27 chore: cap reasoning effort to low on OpenRouter calls
- 2026-08-25 fix: more headroom per attempt, fewer retries for score/tailor/research
- 2026-08-21 change: daily scrape limit 25 → 10; cron Mon–Fri only; revert concurrent scoring, guard silent total failure
- 2026-08-20 fix: score_jobs crash on null OpenRouter content
- 2026-08-19 change: broaden keywords to full stack; past-24-hours window; India; free research model; scrape cache; IST schedule
- 2026-08-18 init: job application agent for Akhil Dalali / Full-Stack Java
