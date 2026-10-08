# Decision log

Newest first. Format: **date | decision | reason | alternatives rejected**. Dates come from commits unless marked
"stated" (from the project owner, not yet in code).

| Date | Decision | Reason | Alternatives rejected |
|---|---|---|---|
| 2026-10-08 | LinkedIn slug `akhil-dalali-320204233` stays canonical (confirms the 2026-09-29 row); the brief switch to the non-hyphenated slug (PR #17) is reverted | owner verified in the browser: the hyphenated URL works, the non-hyphenated one returns 404 | the non-hyphenated slug |
| 2026-10-07 | Verified tailored resumes are published locally as `generated_resumes/<id>/v<n>.pdf` via the existing Google Docs export, recorded with the markdown SHA-1 and job key; this project stays stdlib-only (structural check), the consumer does the text check | Auto_job_apply must upload the exact verified, job-specific file to Naukri without downloading from Drive | a second renderer (Markdown->PDF library), downloading the Drive copy, adding a PDF parser to this project |
| 2026-10-07 | Naukri jobs come from the Auto_job_apply project as a JSON file in the raw-job format (`NAUKRI_JOBS_PATH`); scored by the unchanged pipeline, 8+ cutoff untouched; local only | Auto_job_apply already scrapes Naukri with a headed browser + saved profile; this project stays the single owner of scoring/tailoring/research/tracker | REST API, shared database, direct imports between the repos, Naukri scraping inside this project |
| 2026-09-29 | Master Resume editable only in the local dashboard; hosted (Modal) dashboard is read-only for it | `resume/` is baked into the Modal image — a hosted write would vanish on restart and never reach the cron | Volume-backed master (a second source of truth; needs a deployment change) |
| 2026-09-29 | Scheduled pipeline tailors through `tailoring_service.tailor()` (verify → retry → reorder-only fallback) | the cron previously exported unverified LLM output to Drive | a separate pipeline-only tailoring path |
| 2026-09-29 | `resume/base_resume.md` = transcription of the master ATS PDF; single source of career facts; export layout matches the PDF (A4, ruled sections) | every resume must be traceable to real facts | editing facts per job; storing contact in the repo |
| 2026-09-29 | Contact line stays in `RESUME_CONTACT_LINE` (existing order kept); LinkedIn slug `akhil-dalali-320204233` is canonical | public repo; the PDF's `akhildalali-…` slug is a typo | adding email/phone to the master |
| 2026-09-28 | Fixed resume header for every job; job role only names the exported file | a JD must not rewrite the candidate's headline | role-based headlines |
| 2026-09-24 | Separate retry budget for LLM 429s | rate limits are recoverable; 5xx budget was being burned | one shared retry count |
| 2026-09-21 | Dashboard hosted on Modal behind username/password (PBKDF2) sign-in; own secret | one HTTPS origin; secrets out of the pipeline secret | pasted bearer token (removed) |
| 2026-09-21 | Tailored resumes live in Google Drive; the tracker stores the Drive URL | cross-machine access; local paths broke | local Desktop paths |
| 2026-09-15 | Local Ollama high-volume mode (`LOCAL_MODE`, `LOCAL_JOB_LIMIT` 50) with no cloud fallback | more jobs than free cloud quotas allow | silently falling back to cloud |
| 2026-09-08 | Free-only LLM failover chain Groq → OpenRouter `:free` → Gemini; Groq `openai/gpt-oss-120b` primary | no paid models/credit, ever; one provider failing must not stop the run | paid OpenRouter credit, single provider |
| 2026-09-07 | Post-run reconciliation: a qualified job without a saved resume fails the run (Telegram) | 2026-09-07 run "succeeded" while delivering nothing | trusting per-step exit codes |
| 2026-08-21 | Cron Mon–Fri only (`0 7 * * 1-5` IST) | saves 2 paid Apify scrapes/week; accepted tradeoff: weekend postings missed (24h window) | daily 7am (stated as "daily" by owner — code says weekdays; TODO: confirm intent) |
| 2026-08-21 | Cloud scrape limit 25 → 10 (`JOB_LIMIT`) | fit the free LLM quotas across score/tailor/research | 25 per run |
| 2026-08-19 | India, past 24 hours, full-stack keywords; 6h scrape cache | target market; avoid repeat paid scrapes | United States; past week |
| 2026-08-18 | Hard 8+/10 cutoff applied in code; rejected jobs kept for audit | on-camera proof the filter works; model can't self-qualify | model-declared verdicts |
| stated | Single env key `apify_api_key` for all Apify actors | one account/token | per-actor keys |
| stated | Apify Free plan (~$5/month credit; runs blocked when it runs out) | cost | paid plan (TODO: confirm) |
| stated | Add Naukri + foundit via `agentx/all-jobs-scraper` (country India); `scrape_jobs()` to orchestrate `scrape_linkedin()` + `scrape_naukri_foundit()` then `dedupe_jobs()` on normalised (title, company) | broader coverage of Indian job boards | — (**not implemented yet**; see `TASKS.md`) |
