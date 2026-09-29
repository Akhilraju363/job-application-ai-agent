# Runbook

## Run one stage locally
From the repo root with `.env` filled in (see `CONFIG.md`); each stage reads/writes `output/*.json` and skips work
already done (resume-safe):

```bash
python3 scripts/scrape_jobs.py            # uses the 6h cache; --force (or FORCE_SCRAPE=true) spends a new Apify run
python3 scripts/score_jobs.py             # scores only links not yet in scored_jobs.json
python3 scripts/tailor_job.py             # tailors + verifies + uploads qualified jobs not yet "saved"
python3 scripts/company_research.py       # researches saved jobs without company_notes
python3 scripts/write_sheet.py            # appends rows not already in the Sheet (dedupe by link)
python3 scripts/run_pipeline.py           # all five in order (LOCAL_MODE=true / Ollama)
```

Whole cloud pipeline once, without deploying: `modal run modal_app.py` (add `--force` to bypass the scrape cache).
Dashboard: `python scripts/dashboard_server.py` (Windows: `start_local.ps1` / `start_local.bat`).
New scrape source or changed field mapping: test with `limit=2` first (e.g. `scrape_jobs(limit=2)`) and inspect the dicts.

## Read logs
- **Modal (cron + hosted dashboard):** `modal app logs job-apply-agent`, or the Modal web UI → app → function logs.
  The cron logs to the console only (no Volume). Key lines: `Pipeline step started/finished`, `N scraped, M qualified`,
  `JOB-APPLY-AGENT — daily run complete`, `JOB-APPLY-AGENT — PIPELINE FAILED at <stage>`.
- **Local / dashboard:** `output/logs/` (JSON lines; `errors.log` has tracebacks), or the dashboard **Logs** page.
- **Activity feed:** `output/activity_log.jsonl` (dashboard Recent Activity).

## Telegram alerts
Only one alert exists: `JOB-APPLY-AGENT — WHAT BROKE`, sent by `modal_app.run_pipeline` on any exception, followed by
`Stage: <stage>` and `Reason: <ExceptionType>: <message>` (no secrets). Then the run re-raises.

| `Stage:` | Typical meaning | Action |
|---|---|---|
| `startup` | `gws-credentials` secret missing a key (`client_id`/`client_secret`/`refresh_token`/`type`) | fix the secret (`GWS_SETUP.md`) |
| `config check` | `llm_base_url` set in the Modal secret, or no LLM provider key | remove `llm_base_url`; add `GROQ_API_KEY` |
| `scrape_jobs.py` | Apify error (bad token, credit exhausted, timeout 600 s) — `CalledProcessError` | see "Apify credit exhausted" |
| `score_jobs.py` | `scored 0/N jobs this run` = every provider failed for every job (quota/outage); or timeout | recover locally with Ollama |
| `tailor_job.py` | a step crash or timeout (per-job failures are flagged, not raised) | re-run locally |
| `company_research.py` | crash / timeout (per-company failures store `""` and retry next run) | re-run locally |
| `write_sheet.py` | `gws` auth or Sheets error | check `gws` credentials / `google_sheet_id` |
| `post-run reconciliation` | `N/M qualified job(s) produced no saved resume` — steps exited 0 but a qualified job has no Drive resume | re-run `tailor_job.py` (+ research + sheet) locally |

`TimeoutExpired` in the reason = that step's subprocess timeout (see `CONFIG.md`). No alert at all with a failed run in
the logs = `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` unset (a warning is logged).

## Re-run a failed day
1. **Today:** run the stages above in order locally. `artifacts.py` pulls Modal's `raw/scored/tailored` JSON from Drive
   (`pipeline-artifacts/<date>`), so nothing is re-scraped, re-scored, re-tailored or logged twice. If every free provider
   is throttled, switch `.env` to Ollama first (`llm_base_url=http://localhost:11434/v1`, `llm_model=qwen2.5:7b`,
   `llm_api_key=ollama`; `ollama serve`, `ollama pull qwen2.5:7b`) — and remove those lines afterwards.
2. **An earlier day:** set `PIPELINE_DATE=YYYY-MM-DD` in `.env`, run the stages, then unset it.
3. **Or in the cloud:** `modal run modal_app.py` (same resume-safe behaviour, cloud providers).

## Apify credit exhausted
Symptom: the alert says `Stage: scrape_jobs.py` with an HTTP error from `api.apify.com` (TODO: confirm the exact status
code Apify returns when Free-plan credit runs out). Scoring/tailoring of already-scraped jobs is unaffected.
- Nothing is lost: the next successful scrape continues normally; weekday runs will keep alerting until credit returns.
- Options: wait for the monthly credit reset, or top up / change plan in the Apify console (owner decision).
- Meanwhile: tailor manually from pasted JDs in the dashboard (Tailor Resume page — no Apify needed); keep scrape volume
  low (`JOB_LIMIT` default 10, `past24Hours` window; the cache avoids repeat runs within 6 h).
- TODO: confirm whether to pause the Modal schedule during a long outage (e.g. `modal app stop` + redeploy later).
