# Tasks

No TODO/FIXME comments exist in the code (checked 2026-09-29); items below come from the owner's notes and recent work.

## In progress
- [x] 2026-11-11 pre-commit audit: enforce the Sheets write gate for interview appends and all mutating GWS Sheets operations; require explicit spreadsheet IDs without Drive lookup; redact GWS diagnostics; restore the fixed `AKHIL DALALI` master header. Mocked regression coverage added; no live Google or Modal changes. Its GCS filename handling was superseded by the Drive-only restoration below.
- [ ] Run the operator-owned read-only Google health check with the personal OAuth credentials; keep
      `GOOGLE_SHEETS_WRITE_ENABLED=false` until the personal OAuth tracker/Drive reads are confirmed.
- [ ] Review and commit the uncommitted 2026-09-29 work on branch `fix/fixed-resume-header-contact-dedupe`
      (master resume from PDF + export layout, stricter no-fabrication, verified pipeline tailoring, Master Resume editor,
      UI redesign + `docs/ui/`, project docs). Not committed or pushed yet.

## Next
- [x] 2026-10-23 Fixed the blank dashboard after restart: the new Suitability chart had one missing closing parenthesis in its ES-module legend expression. Root HTML/assets, browser console, APIs, startup command, frontend tests, and full backend tests verified.
- [x] 2026-10-21 (merged PR #23) Fixed dashboard tracker append diagnostics and schema validation for `gws 0.22.5`; verified the configured sheet headers/range read-only and added mocked command, failure, payload, and canonical-link duplicate tests. No real append performed.
- [x] First live scoped run (Auto_job_apply -> `--job-ids-file`), 2026-10-12 run `20261012T165349-8dd2834d`: 2 exported
      ids scored (local qwen2.5:7b, 4 LLM calls), 1 tailored + uploaded, nothing out of scope scored/tailored. It showed
      the scrape stage dropping earlier Naukri records from `raw_jobs.json` (`121026001994`, `121026001987`; their scores
      stay in `scored_jobs.json`) -- fixed the same day (`merge_naukri_history`); those two are not re-added to raw.
- [x] 2026-10-12 (uncommitted) `test_tracker_outage_degrades_sections_instead_of_failing` fixed: it pins "today" to
      2026-09-20 through `parse_range(today=…)` (fixture jobs found 2026-09-18/19 fell outside the default 7d window
      ending the real `date.today()`); assertions unchanged, no production change. Full suite now passes.
- [x] 2026-10-12 (uncommitted) `test_summary_overview_status_use_real_data` pinned the same way (its `range=30d`
      window from the real today would have dropped the 2026-09-18 fixture job from 2026-10-18). Both tests now share
      `pinned_today()` in `tests/test_dashboard_server.py`; assertions unchanged.
- [x] 2026-10-13 (uncommitted) Jobs table Posted column shows `MM/DD/YYYY` from `posted_date` (no relative labels,
      no `found_at` fallback); web tests added. Not committed.
- [ ] Local bullet tailoring is still shallow -- a model limit, not the checker: across 7 SourcingXPress probes on
      2026-10-09 (original prompt re-run + 5 prompt variants + final harness run) qwen2.5:7b returned 0-2 changed
      bullets of 18, and every change was a deletion or punctuation. The final run fell back to reorder-only. The 5
      rewrites of the 11:53 run were run-to-run variance. Options to evaluate (owner's call): a larger local model,
      or one call per employer (more latency). Summary stays the master's; Skills are re-ordered only. "maintaining"
      -> "managing" stays blocked by design. Same on the new master (2026-10-09 live runs): a Java/Spring Boot JD
      got 0 changed bullets -> reorder-only (`conservative`, verified; Skills still re-ordered for the JD); an
      Angular/Python JD got 2 accepted rewrites + 1 rejected (`generated`, verified).
- [ ] Naukri on Modal: needs the handoff file mirrored through configured artifact storage (`artifacts.py`); not implemented -- local only.
- [ ] (title, company) dedupe across sources: the same posting on LinkedIn and Naukri has two links and is scored twice.
- [ ] Test `agentx/all-jobs-scraper` (platforms Naukri, foundit; country India) with `limit=2`; record its input params and
      output field names in `CONFIG.md`.
- [ ] Split `scripts/scrape_jobs.py`: `scrape_jobs()` orchestrates `scrape_linkedin()` + `scrape_naukri_foundit()`, then
      `dedupe_jobs()` on normalised (title, company); map the new actor's fields onto the raw job schema
      (`ARCHITECTURE.md`), set `source` per platform. Keep the per-mode job limit and the 6h cache.
- [ ] Decide the schedule: owner notes say "daily 7am IST", code runs Mon–Fri (`0 7 * * 1-5`). TODO: confirm.
- [ ] Watch Apify Free-plan credit once a second actor runs (more results per run → more credit and more LLM calls).
- [x] 2026-10-13 (uncommitted) Find Jobs pagination, client-side, reusing the Applications pager + Rows per page (own
      `jobagent.pageSize.findJobs`); `web/tests/findJobsPagination.test.mjs`. Not committed.
- [ ] UI Phase 2 (`docs/ui/UI_IMPLEMENTATION.md`): server-side Find Jobs pagination (only if >200 jobs per filter becomes
      normal -- the API caps a response at 200); pipeline-status card once the
      backend exposes the last run's outcome.
- [ ] Show `master_resume_current: false` in the dashboard (API already returns it) for resumes built from an older master.
- [ ] Consider loading prompts from `docs/prompts/*.md` instead of Python constants (suggestion only).
- [ ] Optional: reorder `RESUME_CONTACT_LINE` to `email | phone | linkedin | location` (in `.env` and the Modal secret) —
      owner chose to keep the current order for now.

## Done
- [x] 2026-11-12 Fixed Modal artifact backend selection: cloud cron/dashboard default an unset
      `ARTIFACT_STORAGE_BACKEND` to GCS and reject explicit Drive selection; regression tests prove
      GCS resume saving makes no GWS calls. No Modal secrets, schedule, or production services changed.
- [x] 2026-11-12 (supersedes the GCS backend change above) Restored personal Google OAuth for Drive and Sheets;
      removed GCS code/dependencies and Modal GCS enforcement; restored Drive resume uploads, retrieval, and
      authenticated dashboard downloads; made the Sheets write gate apply to OAuth. Added mocked OAuth,
      Drive, backend-selection, download, and write-gate regressions. No secrets, schedule, or production services changed.
- [x] 2026-11-11 Made resume path components Windows-safe and temporary Drive upload copies OS-temp-scoped; RunLoyal colon case, reserved names, length limits, collision resistance, and cleanup are covered. Focused path (9), resume-role (18), artifact/storage/renderer/Drive tests, and frontend (145) pass. Full Python suite passed: 730 tests; an initial transient `ConnectionAbortedError` in dashboard auth did not recur on the full-suite retry or isolated 81-test run.
- [x] 2026-11-11 Diagnosed the local bulk-delete 404: the current HTTP dispatcher registers and reaches
      `POST /api/tracker/bulk-delete`; the generic not-found response therefore indicates a stale/different server
      process without that route. Confirmed the local launcher targets `scripts/dashboard_server.py`; added POST
      unknown-route and bulk-path method-mismatch assertions. No Google or deletion behavior changed.
- [x] 2026-11-11 Added confirmed bulk deletion to Applications using stable tracker IDs and a single descending-row
      Google Sheets batch update; missing/ambiguous IDs fail per-record, tracker/auth failures fail closed, and no
      resumes, artifacts, interviews, source history, or unrelated rows are deleted. Frontend (144) and full Python
      regression (721) tests pass; no live Sheets/Drive writes or deployment performed.
- [x] 2026-11-11 (superseded 2026-11-12) Temporarily implemented service-account Sheets with private GCS storage,
      authenticated dashboard retrieval, and local PDF/DOCX rendering. Replaced by personal Google OAuth and
      Drive-only storage per the owner's no-paid-storage requirement.
- [x] 2026-11-02 Updated shared dashboard password validation to require 8+ characters, an ASCII letter, an ASCII digit,
      and a non-whitespace special character; uppercase remains optional. Creation and reset use the same validator.
- [x] 2026-11-02 Deployed Modal production app `job-apply-agent` as v34 from `origin/main` commit `30e00a3`;
      verified the dashboard returned HTTP 200 and confirmed no pipeline-start event after deployment.
- [x] 2026-11-02 Preserve Interview Tracker search focus by rerendering only results; validate reminder booleans consistently;
      add mocked CRUD API coverage with generic storage errors; re-resolve unique Sheets row identifiers before deletion and document
      the remaining external-edit race; correct storage documentation and consolidate interview toolbar CSS.
- [x] 2026-11-02 Prevented Interview Tracker text from being interpreted as Google Sheets formulas by
      using `RAW` for interview inserts and updates; existing Jobs tracker writes retain `USER_ENTERED`.
- [x] 2026-10-23 Fixed Interview Calendar date alignment: local numeric date helpers now add the first-weekday offset,
      preserve date-only values without UTC shifts, mark today/interview dates, handle leap years and year-boundary navigation,
      and cover deterministic calendar calculations.
- [x] 2026-10-23 Verified and completed Interview Tracker edit/delete coverage: dashboard upcoming/recent rows and the
      Interviews list expose scoped accessible actions; the edit drawer prefills persisted fields including application ID,
      PATCH preserves unchanged values, and delete confirmation names company/role/date while leaving applications untouched.
- [x] 2026-10-23 Fixed blank dashboard after Interview Tracker work: `web/js/pages/interviews.js` had an unmatched
      closing parenthesis in nested DOM construction, so the browser rejected the module graph before `app.js` mounted;
      all ES modules, served assets, APIs, browser rendering, frontend tests, and backend checks were verified.
- [x] 2026-10-23 Added the initial persistent Interview Tracker with atomic JSON storage and authenticated CRUD/filter APIs;
      persistence was later moved to the separate Sheets `Interviews` worksheet, retaining local JSON only for explicit migration.
      Dashboard summaries, list/drawer interactions, status/outcome fields, and stored-but-disabled reminder preferences remain.
- [x] 2026-10-23 (merged PR #24) Added independent tracker Suitability (`Not reviewed`, `Shortlist`, `Not Shortlist`) in Sheet column M,
      with safe header/validation migration, dedicated API updates, independent Applications/Find Jobs controls and filters,
      dashboard counts/chart, documentation, and mocked regression coverage. Existing application Status remains unchanged.
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
