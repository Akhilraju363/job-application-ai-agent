# Tasks

No TODO/FIXME comments exist in the code (checked 2026-09-29); items below come from the owner's notes and recent work.

## In progress
- [ ] Review and commit the uncommitted 2026-09-29 work on branch `fix/fixed-resume-header-contact-dedupe`
      (master resume from PDF + export layout, stricter no-fabrication, verified pipeline tailoring, Master Resume editor,
      UI redesign + `docs/ui/`, project docs). Not committed or pushed yet.

## Next
- [ ] Naukri on Modal: needs the handoff file mirrored through Drive (`artifacts.py`); not implemented -- local only.
- [ ] (title, company) dedupe across sources: the same posting on LinkedIn and Naukri has two links and is scored twice.
- [ ] Test `agentx/all-jobs-scraper` (platforms Naukri, foundit; country India) with `limit=2`; record its input params and
      output field names in `CONFIG.md`.
- [ ] Split `scripts/scrape_jobs.py`: `scrape_jobs()` orchestrates `scrape_linkedin()` + `scrape_naukri_foundit()`, then
      `dedupe_jobs()` on normalised (title, company); map the new actor's fields onto the raw job schema
      (`ARCHITECTURE.md`), set `source` per platform. Keep the per-mode job limit and the 6h cache.
- [ ] Decide the schedule: owner notes say "daily 7am IST", code runs Mon–Fri (`0 7 * * 1-5`). TODO: confirm.
- [ ] Watch Apify Free-plan credit once a second actor runs (more results per run → more credit and more LLM calls).
- [ ] UI Phase 2 (`docs/ui/UI_IMPLEMENTATION.md`): server-side pagination on Find Jobs; pipeline-status card once the
      backend exposes the last run's outcome; Logs filter chips.
- [ ] Show `master_resume_current: false` in the dashboard (API already returns it) for resumes built from an older master.
- [ ] Consider loading prompts from `docs/prompts/*.md` instead of Python constants (suggestion only).
- [ ] Optional: reorder `RESUME_CONTACT_LINE` to `email | phone | linkedin | location` (in `.env` and the Modal secret) —
      owner chose to keep the current order for now.

## Done
- [x] 2026-10-07 (uncommitted) Naukri source via the Auto_job_apply JSON handoff (`NAUKRI_JOBS_PATH`,
      `JOB_SOURCES`); merge also on the 6h cache path; `source` carried through tailoring and the Sheet.
- [x] 2026-09-29 Project docs: `CLAUDE.md` (short), `docs/ARCHITECTURE|CONFIG|RUNBOOK|MEMORY|TASKS|CHANGELOG.md`, `docs/prompts/`.
- [x] 2026-09-29 UI modernisation phase 1 + `docs/ui/` specs.
- [x] 2026-09-29 Master Resume editor (GET/PUT `/api/master-resume`, section-by-section, preview, versioning).
- [x] 2026-09-29 Scheduled pipeline tailors through the verified `tailoring_service` path.
- [x] 2026-09-29 Master resume transcribed from the ATS PDF; exports use the PDF layout.
- [x] 2026-09-28 Fixed resume header per job; LinkedIn deduped in the contact line.
- [x] 2026-09-21 Hosted dashboard on Modal with sign-in, production logging, Drive resume uploads.
- [x] 2026-09-15 Local Ollama high-volume mode.
- [x] 2026-09-08 Free-only LLM failover chain.
