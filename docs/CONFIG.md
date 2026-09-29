# Configuration

Names only — never put values in this repo. Local: `.env` (gitignored). Cloud: Modal secrets (below).
Template: `.env.example`.

## Environment keys

| Key | Where | Purpose | Default / notes |
|---|---|---|---|
| `apify_api_key` | `.env`, `job-apply-agent-secrets` | Apify token for every actor | required for scraping |
| `GROQ_API_KEY` | `.env`, `job-apply-agent-secrets` | LLM provider 1 (primary) | at least one provider key required in cloud |
| `OPENROUTER_API_KEY` | same | LLM provider 2 (`:free` models only) | optional; `open_router_apikey` still read for back-compat |
| `GEMINI_API_KEY` | same | LLM provider 3 (last resort; Google may train on free-tier data) | optional |
| `llm_provider_order` | `.env` | failover order | `groq,openrouter,gemini` |
| `llm_request_delay_seconds` | `.env` | spacing before each call | 5 cloud / 0 local |
| `llm_max_retries` | `.env` | attempts per provider on 5xx/timeout/bad JSON | 2 |
| `llm_max_rate_limit_retries` | `.env` | attempts per provider on 429 | 5 |
| `llm_request_deadline` | `.env` | per-attempt cap (s) | 120 (the dashboard raises it to 600 in local mode) |
| `llm_groq_model` / `llm_openrouter_model` / `llm_gemini_model` | `.env` | model override | `openai/gpt-oss-120b` / `google/gemma-4-26b-a4b-it:free` / `gemini-2.5-flash` |
| `LOCAL_MODE` | `.env` only | switch to local Ollama, no cloud calls | **never set on Modal** |
| `llm_base_url`, `llm_model`, `llm_api_key` | `.env` only | local OpenAI-compatible endpoint (Ollama) | `llm_base_url` defaults to `http://localhost:11434/v1` when `LOCAL_MODE=true`; Modal refuses to run if `llm_base_url` is set |
| `JOB_LIMIT` | `.env` / secret | jobs per cloud run | 10 (sized to free-tier LLM quotas) |
| `LOCAL_JOB_LIMIT` | `.env` | jobs per local run | 50 |
| `FORCE_SCRAPE` | `.env` | bypass the 6h scrape cache (spends an Apify run) | unset |
| `artifact_sync` | `.env` | `0` disables the Drive mirror of stage JSON | on |
| `PIPELINE_DATE` | `.env` | recover an earlier day's artifacts (`YYYY-MM-DD`) | today |
| `google_sheet_id` | `.env`, `job-apply-agent-secrets` | the "Job Application Tracker" sheet | lookup by name → create, if unset |
| `google_drive_folder_id` | `.env`, `job-apply-agent-secrets` | parent Drive folder (per-job folders + `pipeline-artifacts/`) | required for uploads |
| `RESUME_CONTACT_LINE` | `.env`, `job-apply-agent-secrets` | private contact row added at export (never in the repo) | exports warn / refuse without it |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | `.env`, `job-apply-agent-secrets` | failure alerts | optional; the alert is skipped with a warning if unset |
| `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD_HASH`, `DASHBOARD_ALLOWED_HOSTS` | `job-apply-agent-dashboard-secrets` (or `.env`) | dashboard sign-in + Host check | unset locally = open on loopback only |
| `DASHBOARD_COOKIE_SECURE`, `DASHBOARD_TRUST_PROXY`, `DASHBOARD_VERBOSE` | env | Secure cookie / trust `X-Forwarded-*` / verbose request logs | TODO: confirm where these are set for Modal |
| `MASTER_RESUME_READ_ONLY` | env | disable master-resume saves in the dashboard | Modal (`MODAL_TASK_ID` / `MODAL_IS_REMOTE`) is read-only automatically |
| `JOB_AGENT_OUTPUT_DIR` | env | override `output/` | tests / hosting |
| `LOG_LEVEL`, `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`, `LOG_TO_FILE` | env | logging (`scripts/logging_config.py`) | INFO, `output/logs`, 10 MB, 5; the cron forces `LOG_TO_FILE=0` |
| `client_id`, `client_secret`, `refresh_token`, `type` | `gws-credentials` | Google OAuth for `gws` inside Modal (`modal_app.materialize_gws_credentials`) | see `GWS_SETUP.md` |
| `DASHBOARD_TOKEN` | — | legacy (replaced by username/password sign-in) | TODO: confirm it can be removed from any old secret |

## Modal

| Item | Value |
|---|---|
| App | `job-apply-agent` (`modal_app.py`) |
| Cron function | `run_pipeline` — `modal.Cron("0 7 * * 1-5", timezone="Asia/Kolkata")` = **07:00 IST, Monday–Friday** |
| Timeouts | outer 21600 s; per step: scrape 600, score 6000, tailor 7200, research 5400, sheet 300 |
| Cron secrets | `job-apply-agent-secrets`, `gws-credentials` (no Volume; console logs only) |
| Dashboard function | `dashboard` web server; cron secrets + `job-apply-agent-dashboard-secrets`; Volume `job-apply-agent-dashboard` at `/app/output`; `max_containers=1`; `scaledown_window=900` |
| Dashboard URL | `https://akhildalali07--job-apply-agent-dashboard.modal.run` |

## Apify

| Actor | Status | Endpoint / input |
|---|---|---|
| `curious_coder~linkedin-jobs-scraper` | **in use** (`scripts/scrape_jobs.py ACTOR_ID`) | `POST https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items?token=…`, timeout 300 s. Input: `keywords` (default `Full Stack Java Spring Boot Angular AWS Developer`), `location` (`India`), `datePosted` (`past24Hours` \| `pastWeek` \| `pastMonth`), `limitPerSource` (= job limit), `under10Applicants: false`, `autoConvertToAiSearch: true`, `scrapeCompany: false` |
| `agentx/all-jobs-scraper` (platforms Naukri, foundit; country India) | **planned — not in code** | TODO: confirm the actor id and input params; test with `limit=2` before wiring it in |

Dashboard overrides for keywords / location / date window / limit live in `output/preferences.json` (Job Alerts page);
the Modal cron never has that file and uses the defaults. Plan: Apify Free (~$5/month credit; runs are blocked when it
runs out) — per the project owner; TODO: confirm the current plan.

## Google Sheet — "Job Application Tracker", `Sheet1!A:L`

| Col | Header | Written by `write_sheet.build_row` |
|---|---|---|
| A | Job Title | `title` |
| B | Company | `company` |
| C | Job Link | `link` (dedupe key, canonicalised) |
| D | Fit Score | `score` (1-10) |
| E | Resume Path | `resume_link` (Drive URL), or `desktop_file` / `resume_path` for old rows |
| F | Status | starts `"Not Applied"`; then one of Not Applied, Applied, Interviewing, Offer, Rejected (manual / dashboard) |
| G | Timestamp | run date |
| H | Company Notes | `company_notes` |
| I | Source | `"LinkedIn"` (pipeline) or the dashboard's source |
| J | Status Updated | set when the dashboard changes the status |
| K | Resume ID | dashboard resume id |
| L | Match % | dashboard JD match |

Columns I–L were added for the dashboard; `ensure_extra_headers()` adds them to older sheets.
