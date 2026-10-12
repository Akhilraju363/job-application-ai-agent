# Tasks

No TODO/FIXME comments exist in the code (checked 2026-09-29); items below come from the owner's notes and recent work.

## In progress
- [ ] Review and commit the uncommitted 2026-09-29 work on branch `fix/fixed-resume-header-contact-dedupe`
      (master resume from PDF + export layout, stricter no-fabrication, verified pipeline tailoring, Master Resume editor,
      UI redesign + `docs/ui/`, project docs). Not committed or pushed yet.

## Next
- [ ] First live scoped run (Auto_job_apply -> `--job-ids-file`): so far verified only in an isolated copy with real
      exported ids and no LLM/Drive (2026-10-12, 12/12 checks). On the first real run, confirm
      `output/scoped_run_report.json` lists exactly the exported ids and that no other job was newly scored/tailored.
- [x] 2026-10-12 (uncommitted) `test_tracker_outage_degrades_sections_instead_of_failing` fixed: it pins "today" to
      2026-09-20 through `parse_range(today=…)` (fixture jobs found 2026-09-18/19 fell outside the default 7d window
      ending the real `date.today()`); assertions unchanged, no production change. Full suite now passes.
- [x] 2026-10-12 (uncommitted) `test_summary_overview_status_use_real_data` pinned the same way (its `range=30d`
      window from the real today would have dropped the 2026-09-18 fixture job from 2026-10-18). Both tests now share
      `pinned_today()` in `tests/test_dashboard_server.py`; assertions unchanged.
- [ ] Local bullet tailoring is still shallow -- a model limit, not the checker: across 7 SourcingXPress probes on
      2026-10-09 (original prompt re-run + 5 prompt variants + final harness run) qwen2.5:7b returned 0-2 changed
      bullets of 18, and every change was a deletion or punctuation. The final run fell back to reorder-only. The 5
      rewrites of the 11:53 run were run-to-run variance. Options to evaluate (owner's call): a larger local model,
      or one call per employer (more latency). Summary stays the master's; Skills are re-ordered only. "maintaining"
      -> "managing" stays blocked by design. Same on the new master (2026-10-09 live runs): a Java/Spring Boot JD
      got 0 changed bullets -> reorder-only (`conservative`, verified; Skills still re-ordered for the JD); an
      Angular/Python JD got 2 accepted rewrites + 1 rejected (`generated`, verified).
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
      backend exposes the last run's outcome.
- [ ] Show `master_resume_current: false` in the dashboard (API already returns it) for resumes built from an older master.
- [ ] Consider loading prompts from `docs/prompts/*.md` instead of Python constants (suggestion only).
- [ ] Optional: reorder `RESUME_CONTACT_LINE` to `email | phone | linkedin | location` (in `.env` and the Modal secret) —
      owner chose to keep the current order for now.

## Done
- [x] 2026-10-12 (uncommitted) Removed the Job Tracker and Logs pages (old URLs redirect; backend APIs and logging kept).
- [x] 2026-10-12 (uncommitted) Applications pagination (client-side; 10/20/50/100, default 10, remembered per browser).
      Browser-checked at 1600/820/390 in both themes against the real tracker (88 rows).
- [x] 2026-10-12 (uncommitted) Applications / Job Tracker: “Open Google Sheet” action (`tracker.sheet_url` on
      `GET /api/tracker`) + per-page “Sort by” (newest first by default, remembered in `localStorage`). Browser-checked at
      1600/820/390 in both themes; the real link opened a new tab (headless browser landed on Google sign-in).
- [x] 2026-10-09 (uncommitted) Domain-neutral master resume (`resume/base_resume.md`) + matching one-page A4 DOCX/PDF
      (`Akhil_Dalali_Master_ATS_Resume.*`, kept outside the repo); tests re-pointed, no pipeline code changed.
- [x] 2026-10-09 (uncommitted) Bullet prompt v2 (restructure, not synonym swaps; examples; instruction repeated after
      the bullets) + two stricter `check_rewrite` rules (goal -> achieved result; deletion-only); punctuation-only
      output counts as unchanged. Probe script: `output/test_runs/bullet_prompt_probe.py` (bullet call only, local).
- [x] 2026-10-09 (uncommitted) Evidence-grounded bullet tailoring for local models (`scripts/bullet_tailoring.py`,
      `tests/test_bullet_tailoring.py`); SourcingXPress isolated run passed with genuine rewrites.
- [x] 2026-10-09 (uncommitted) Immutable employment history: deterministic restore + per-field employment validation
      + protected lines in the tailor prompt; `tests/test_employment_history.py`. Isolated harness:
      `output/test_runs/isolated_tailor_test.py <company>` (local only, gitignored).
- [x] 2026-10-07 (uncommitted) Local verified PDF artifact per tailored job (`scripts/resume_artifacts.py`);
      the pipeline publishes it and uploads that file to Drive; saved records carry `resume_id`/`resume_version`.
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
