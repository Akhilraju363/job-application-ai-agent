# Job Application AI Agent

**Fastest way to use this:** give this whole repo to any Claude Code agent (or Cursor, Codex,
whatever you use) and say "set this up for me." The pipeline order, every environment variable,
and the exact run/deploy commands are all below — nothing else to figure out.

## What This Is

An AI agent that finds Full-Stack Software Engineer (Java/Spring Boot/Angular) job postings, scores each one against your resume, tailors
your resume to every job that's actually a strong fit, researches the company, and logs it all to
a Google Sheet — hosted on Modal to run on its own every morning.

Built as the capstone project for the final session of the Claude Code Masterclass.

## Architecture

```
Scrape (Apify)
    -> Score + Filter (LLM, 8+/10 cutoff)
        -> Tailor Resume (Skill + validation Hook)
            -> Company Research (LLM)
                -> Log to Google Sheet (gws CLI)
```

The three LLM steps (score, tailor, research) all go through one shared client,
[`scripts/llm.py`](scripts/llm.py). In the cloud it runs a **failover chain over independent
free LLM tiers** — Groq → OpenRouter `:free` → Gemini — so one provider rate-limiting or
delisting a model doesn't stop the run. **It is free-only by design: no paid models, no
OpenRouter credit, no paid fallback.** Locally you can point it at Ollama instead with a few
`.env` vars — see [LLM providers](#llm-providers-free-only) below.

Every job gets scored against the base resume. Only jobs scoring 8 or higher continue past that
step — the rest are logged as rejected but never tailored, never touch the Sheet.

## Setup

**Prerequisites:**
- Python 3.12+
- The [`gws` CLI](https://github.com/googleworkspace/cli) — see [`GWS_SETUP.md`](GWS_SETUP.md) for
  the full walkthrough (Google Cloud project, OAuth, first login)
- An [Apify](https://apify.com) account (free tier)
- A [Groq](https://console.groq.com) API key (free, no credit card) — the primary LLM provider.
  Optionally also [OpenRouter](https://openrouter.ai) and/or [Google AI Studio](https://aistudio.google.com/apikey)
  keys for the failover chain (all free tiers)
- A [Modal](https://modal.com) account, only if you want to deploy the daily cron

**Install:**

```bash
git clone https://github.com/akhildalali/job-application-ai-agent.git
cd job-application-ai-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Environment:**

```bash
cp .env.example .env
# fill in apify_api_key and GROQ_API_KEY (add OPENROUTER_API_KEY / GEMINI_API_KEY too for
# a deeper failover chain — see .env.example for where to get each)
```

`google_drive_folder_id` — create a folder in Drive and paste its id (from the URL). It holds
the per-job resume folders and the `pipeline-artifacts/` sync folder.

`google_sheet_id` — set this to the ID of the single master "Job Application Tracker"
spreadsheet. This is recommended for both local and Modal runs so every execution
uses the same spreadsheet deterministically.

`RESUME_CONTACT_LINE` — your email and location, e.g. `name@example.com | Bengaluru, India`.
`resume/base_resume.md` is public, so it has no contact details. `scripts/contact.py` adds this
line to every exported PDF/DOCX/Markdown download, after the no-fabrication check, joined onto the
LinkedIn line. Put it in `job-apply-agent-secrets` too so the cron and hosted dashboard get it.

For Modal, `google_sheet_id` must be provided through the
`job-apply-agent-secrets` secret because the Modal container does not have a
persistent `.env`.

If `google_sheet_id` is not configured, `write_sheet.py` falls back to searching
Drive for an existing "Job Application Tracker" and creates one only if none
exists.

Lookup priority is:

configured `google_sheet_id`
→ existing tracker found in Drive
→ create tracker only if none exists.

### LLM providers (free only)

**No paid models, no OpenRouter credit, no paid fallback — anywhere.** In the cloud,
[`scripts/llm.py`](scripts/llm.py) tries independent free tiers in order (`llm_provider_order`,
default `groq,openrouter,gemini`), building the chain from whichever keys are set:

| Provider | Default model | Free tier | Notes |
|---|---|---|---|
| **Groq** (primary) | `openai/gpt-oss-120b` | no card · ~30 RPM · ~1K RPD | not used for training; commercial use OK |
| **OpenRouter** | `google/gemma-4-26b-a4b-it:free` | no card · ~50 req/day | `:free` only; burst-throttled |
| **Gemini** | `gemini-2.5-flash` | no card · ~10-15 RPM | ⚠️ Google may train on free-tier data — optional, omit `GEMINI_API_KEY` to skip |

On a `429` it backs off (honoring `Retry-After`) up to `llm_max_rate_limit_retries` times
(default 5, separate from the 5xx/timeout budget) then moves to the next provider; on a `404`
(model delisted) it skips that provider immediately. When every provider fails, the run fails
loudly and Telegram fires. Tune with `llm_request_delay_seconds`, `llm_max_retries`, `llm_max_rate_limit_retries`,
`llm_<provider>_model` (see [`.env.example`](.env.example)).

**Local recovery with [Ollama](https://ollama.com)** — set `llm_base_url` and the whole
pipeline switches to your local server, disabling the cloud chain entirely:

```bash
llm_base_url=http://localhost:11434/v1
llm_model=qwen2.5:7b        # or any model you've `ollama pull`ed
llm_api_key=ollama
```

Modal never uses this — `modal_app.py` refuses to run if `llm_base_url` is set. Small local
models score tougher and are weaker at company research; use them to exercise the pipeline,
not for real output.

For scraping and processing more jobs per day than the cloud path's free-tier LLM quota allows
(not just Modal-run recovery), see [Local High-Volume Mode](#local-high-volume-mode) below —
`LOCAL_MODE=true` + `scripts/run_pipeline.py`.

## Running It

Manually, step by step, in order:

```bash
python3 scripts/scrape_jobs.py
python3 scripts/score_jobs.py
python3 scripts/tailor_job.py
python3 scripts/company_research.py
python3 scripts/write_sheet.py
```

Or as a single test run through Modal (without deploying it):

```bash
modal run modal_app.py
```

Each stage writes a JSON artifact to `output/` (`raw_jobs.json` → `scored_jobs.json` →
`tailored_jobs.json`) and the pipeline is **resume-safe**: re-running a stage reuses the work
already in those artifacts instead of redoing it. See
[Recovering a failed daily run](#recovering-a-failed-daily-run).

## Dashboard

An interactive UI over the same artifacts, Google Sheet and tailoring code the pipeline uses.
It does not replace the Modal cron.

```
python scripts/dashboard_server.py --open      # http://127.0.0.1:8765
```

Pages: Dashboard, Find Jobs, **Tailor Resume** (`/tailor-resume`), Applications, Job Tracker,
Job Alerts (search preferences), Settings. No new dependencies and no build step (stdlib server +
plain ES modules).

**JD -> tailored resume.** Paste any job description (or pick a scraped job and click Tailor). Both
entry points call the same `scripts/tailoring_service.py`: extract requirements (LLM, via the
existing provider chain) -> match against `resume/base_resume.md` in code -> tailor with the
pipeline's own prompt -> verify -> store. Verification (`scripts/no_fabrication.py`) rejects any
invented technology, employer, title, date, number or leadership claim, and any technology moved
between employers. If the model's rewrite fails twice, a reorder-only version of the master resume
is produced instead (clearly labelled). PDF/DOCX come from the same Google Doc export the pipeline
uses (needs `gws` auth); Markdown is always available. Results live in `output/generated_resumes/`.

**Entry point:** `tailoring_service.tailor_resume(job_description, job_title, company, job_url, source, job_id, user_id)`
returns `{status, job, jd_analysis, match_analysis, resume, ats_validation, verification}`; also served at
`GET /api/resumes/<id>/result`. A pasted JD and a scraped job (`job_id` set) differ only in that argument.

**ATS score** = weighted, reproducible average of: required-skill coverage 30, keyword coverage 25, title
relevance 10, structure 15, formatting 10, integrity (no unsupported claims) 10. It also lists missing and
over-repeated keywords. It is a transparent heuristic, not a guarantee about any employer's ATS.

**Duplicates:** the same job + JD + master-resume version reuses the existing verified resume (no LLM call);
Regenerate always creates a new version (v2, v3...). The Sheet gets two extra columns, Resume ID and Match %.

**Notes**
- Application status lives in the Google Sheet (the tracker); the dashboard reads/writes it via
  `write_sheet.py`. If Sheets is unreachable, tracker-backed cards show an error + Retry.
- Human-initiated tailoring/saving works on any score. The automated pipeline's 8+ cutoff is
  unchanged; the UI warns when you override it.
- On a local model, resume generation can take minutes. In local mode the dashboard defaults
  `llm_request_deadline` to 600s (override in `.env`).
- Security: loopback only unless sign-in is configured, Host/Origin checks, JSON-only writes, optional
  username/password sign-in (see [Dashboard authentication](#dashboard-authentication)),
  secrets never serialised.
- Tests: `python -m unittest discover -s tests` and `node --test "web/tests/*.test.mjs"`.

## Deploying (daily automatic run)

```bash
modal secret create job-apply-agent-secrets \
  apify_api_key=... GROQ_API_KEY=... \
  OPENROUTER_API_KEY=... GEMINI_API_KEY=... \
  google_drive_folder_id=... google_sheet_id=... \
  RESUME_CONTACT_LINE="name@example.com | Bengaluru, India" \
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...

modal secret create gws-credentials \
  client_id=... client_secret=... refresh_token=... type=...

modal deploy modal_app.py
```

`GROQ_API_KEY` is the minimum LLM requirement; `OPENROUTER_API_KEY` and `GEMINI_API_KEY` are
optional extra links in the failover chain. Runs daily at 7am Asia/Kolkata (IST). See
[`GWS_SETUP.md`](GWS_SETUP.md) for the `gws-credentials` values and the Telegram section for the
bot token.

**`google_sheet_id` must be in the secret** (not just `.env`). The Modal container has no
persistent `.env`, so without it in the secret `write_sheet.py` falls back to a Drive lookup by
name on every run. Get the id once from your master "Job Application Tracker" sheet's URL
(`docs.google.com/spreadsheets/d/<ID>/edit`) and set it. **Never put `llm_base_url` in the
secret** — Modal must use the cloud provider chain, and `modal_app.py` refuses to start if it
finds `llm_base_url` set.

## Deploying the dashboard on Modal (UI + API, one URL)

`modal_app.py` also defines a `dashboard` web function that runs the same
`scripts/dashboard_server.py` you use locally. That one process serves the `web/` UI **and** the
`/api/...` routes, so the browser talks to a single HTTPS origin — no GitHub Pages, no second
service, no CORS. The frontend only uses relative `/api/...` URLs.

```
Browser --HTTPS--> https://<workspace>--job-apply-agent-dashboard.modal.run
                     -> dashboard_server.py (0.0.0.0:8765)
                          +- /            web/ static UI
                          +- /api/...     dashboard API (needs a signed-in session)
```

**Secret.** The dashboard reads its own secret, `job-apply-agent-dashboard-secrets` (kept separate so you
never re-list the pipeline's API keys). The function refuses to start unless all three keys are present and
valid, so it can never run without sign-in or with Host checking off:

- `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD_HASH` — the sign-in account; see
  [Dashboard authentication](#dashboard-authentication) for how to create them.
- `DASHBOARD_ALLOWED_HOSTS` — the deployed hostname **without** `https://`:
  `akhildalali07--job-apply-agent-dashboard.modal.run` (Modal builds it as
  `<workspace>--job-apply-agent-dashboard.modal.run`).

```bash
modal deploy modal_app.py   # also (re)deploys the daily cron, code unchanged
```

- **Persistence:** the `job-apply-agent-dashboard` Volume is mounted at `/app/output` (generated
  resumes, activity log, preferences), committed every 30s. The cron does **not** mount it and stays
  stateless. Exported PDF/DOCX files live in Google Drive; the tracker links the Drive URL.
- **One container:** `max_containers=1` with scale-to-zero (`scaledown_window=900`). Background
  tailoring/export tasks are held in memory, so a container restart or redeploy while a task is
  running loses that task — just re-run it (results already saved to Drive/Sheet are unaffected).
- **Google access:** the same `gws-credentials` secret and `materialize_gws_credentials()` helper
  as the cron.
- Local use is unchanged: `python scripts/dashboard_server.py` on `127.0.0.1:8765`.

## Dashboard authentication

**Dashboard:** https://akhildalali07--job-apply-agent-dashboard.modal.run

Open it, enter your username and password, and you're in — no token to paste. The morning cron does not
depend on this login (it never touches the dashboard or its secret).

```
Browser -> Login page -> POST /api/auth/login {username, password}
        -> rate-limit check -> PBKDF2 check against the stored hash
        -> random session id in an HttpOnly cookie -> dashboard (/api/* needs that session)
```

- **Credentials** live in the Modal secret `job-apply-agent-dashboard-secrets`: `DASHBOARD_USERNAME`,
  `DASHBOARD_PASSWORD_HASH` (a salted PBKDF2-SHA256 hash, 600k rounds — never the password) and
  `DASHBOARD_ALLOWED_HOSTS`. Nothing is committed; nothing is stored in the browser except the session cookie.
- **Sessions** are server-side (the cookie is an opaque random id; only its SHA-256 is stored), last 12 hours
  (`DASHBOARD_SESSION_HOURS` to change), survive a scale-to-zero restart (mirrored to the Volume) and are
  signed out by Logout or by changing the password. The cookie is `HttpOnly`, `Secure` (`__Host-` prefixed on
  HTTPS), `SameSite=Strict`. Writes also require a same-origin `Origin` / `Sec-Fetch-Site` (CSRF), and the
  Host-header allow-list still applies.
- **Brute force:** 5 failed attempts per client/username (15 per client) lock that client out for 15 minutes
  (HTTP 429). This is in-memory, **single-container** protection — not distributed rate limiting.
- **Local development** with no credentials configured runs open on `127.0.0.1` only (the server refuses to bind
  anywhere else without them). To try the login screen locally, export `DASHBOARD_USERNAME` and
  `DASHBOARD_PASSWORD_HASH` (from the command below) before `python scripts/dashboard_server.py`.

### Initial password setup

```bash
python scripts/create_dashboard_password_hash.py      # prompts (hidden, confirmed); prints username + hash
```

Put the two printed values into the secret together with the host (this replaces the secret's contents, so
include all three keys), then deploy:

```bash
modal secret create job-apply-agent-dashboard-secrets   DASHBOARD_USERNAME=<username> DASHBOARD_PASSWORD_HASH=<hash>   DASHBOARD_ALLOWED_HOSTS=akhildalali07--job-apply-agent-dashboard.modal.run --force
modal deploy modal_app.py
```

### Password reset

Forgot it, or want a new one? No source edits needed:

```bash
python scripts/reset_dashboard_password.py --host akhildalali07--job-apply-agent-dashboard.modal.run
```

1. Run it and choose a username and new password (hidden, confirmed). It prints the exact
   `modal secret create ... --force` command with the new hash. (`--apply` runs that for you through the
   `modal` CLI you are already logged into; no Modal credential is read or stored by this repo.)
2. Update the `job-apply-agent-dashboard-secrets` secret with that command.
3. Redeploy: `modal deploy modal_app.py` — every old session is signed out.
4. Sign in with the new password.

The old `DASHBOARD_TOKEN` is no longer used; remove it from the secret when convenient.

## Logging

Diagnostics for whoever runs this (developer/operator), separate from the **Recent Activity** feed
(`output/activity_log.jsonl`), which stays the user-facing history. Everything goes through one
module, [`scripts/logging_config.py`](scripts/logging_config.py) (standard `logging`, no new
dependency); modules just call `get_logger("tailoring")` etc.

```
code -> Python logging -+-> stderr  -> Modal runtime logs   (always; survives a crash)
                        +-> rotating files -> output/logs/  -> Modal Volume on the dashboard
```

- **Application logs** — `output/logs/` (`/app/output/logs/` on Modal): `application.log` (everything,
  JSON lines), `errors.log` (ERROR+, with tracebacks) and one file per area (`dashboard`, `pipeline`,
  `tailoring`, `drive`, `tracker`). The directory is created automatically; if it can't be written the
  app keeps running with console logging only.
- **Modal logs** — the same records, human-readable, in Modal's runtime logs for both the dashboard and
  the daily cron (`modal app logs job-apply-agent`). The cron logs to the console only: it has no Volume.
- **Dashboard → Logs page** — authenticated viewer over `GET /api/logs` (level, component, date, and
  request/task/resume/job id filters, pagination, click a row for the traceback). It can only read the
  log directory, validates every filter, caps results at 500 per page and never loads a whole file.
  Level `ERROR` also shows `CRITICAL`.
- **Ids** — every dashboard response carries `X-Request-ID` (a safe client-supplied one is kept); the
  same id, plus the background `task_id`, `resume_id` and `job_id` when known, appears on the related
  log lines, so one request can be followed from the HTTP call to the LLM call to the Drive upload.
- **Rotation** — `RotatingFileHandler`, 10 MB x 5 backups per file by default
  (`LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`); only log files rotate — resumes and the activity log are never
  touched. Other settings: `LOG_LEVEL` (default `INFO`), `LOG_DIR`, `LOG_TO_FILE=0`.
- **Security** — never logged: passwords, password hashes, session cookies, API keys, Google credentials, `Authorization`/cookie
  headers, request bodies, query strings, prompts, model output, full JDs or full resumes. Only metadata
  (ids, counts, durations, provider/model, status codes). A redaction pass masks secret-shaped values as a
  safety net, and `/api/logs` redacts again on the way out.
- **Persistence** — dashboard logs live on the existing `job-apply-agent-dashboard` Volume (no new
  Volume), committed every 30s. A container restart does not necessarily preserve an in-progress
  background tailoring task, but the log files on the Volume remain.

## Recovering a failed daily run

Normal day: the Modal cron runs the free provider chain (Groq → OpenRouter → Gemini), finishes,
and updates the master Sheet. Nothing to do.

If every free provider is rate-limited or down when the cron fires, Modal sends a Telegram alert
naming the stage that failed and stops. You then finish that day's work **locally with Ollama** —
it picks up where Modal left off:

```bash
ollama serve                        # in another terminal, if not already running
ollama pull qwen2.5:7b              # once

# .env — switch to local Ollama (see "LLM providers" above)
llm_base_url=http://localhost:11434/v1
llm_model=qwen2.5:7b
llm_api_key=ollama

# recover TODAY's run: just re-run the pipeline in order
python3 scripts/scrape_jobs.py       # reuses Modal's scrape, no new Apify call
python3 scripts/score_jobs.py        # scores only the jobs Modal didn't get to
python3 scripts/tailor_job.py        # tailors only the qualifying jobs still missing a resume
python3 scripts/company_research.py  # researches only companies still missing notes
python3 scripts/write_sheet.py       # appends only jobs not already in the Sheet
```

To recover an **earlier** day's failed run, set `PIPELINE_DATE=YYYY-MM-DD` in `.env` first.

How it works: every stage mirrors its artifact to a `pipeline-artifacts` subfolder of your
`google_drive_folder_id` Drive folder, date-stamped (`scored_jobs-2026-09-06.json`). A local run
pulls the newest copy before starting and pushes its progress back, so Modal and your laptop
share the same state. Jobs are identified by their URL throughout, so nothing is scored, tailored,
researched, or logged twice — every recovery run converges on the same Sheet. Set `artifact_sync=0`
to turn the Drive mirror off (pure-local development without Google auth still works).

### Local startup

Run from the repository root (Windows PowerShell):

```powershell
.\start_local.ps1
```

This starts, as two separate processes sharing your console:
- the local pipeline with `LOCAL_MODE=true` (`scripts/run_pipeline.py`)
- the dashboard server (`scripts/dashboard_server.py`, http://127.0.0.1:8765/)

It uses your active virtual environment, else the repo's `.venv`, else `python` on PATH
(`-Python <path>` overrides; nothing is installed or created). The pipeline is a one-shot run:
when it finishes or fails the script says so and the dashboard keeps running. Options:
`-NoPipeline` (dashboard only), `-Port <n>`, `-DryRun` (print the resolved commands, start nothing).
If port 8765 is taken (often an older dashboard still running), it stops with the PID to close.

**To stop:** press `Ctrl+C` in that window. Both processes, and anything they started, are
stopped; other Python processes are left alone. If the dashboard exits on its own, the script
reports it, stops the pipeline and exits.

### Windows double-click startup

Double-click `start_local.bat` in the repository root (from Explorer).

Alternatively, from PowerShell:

```powershell
.\start_local.ps1
```

`start_local.bat` is only a launcher for `start_local.ps1`: it runs that script (next to itself,
whatever the current directory) with `-ExecutionPolicy Bypass` and passes any flags through
(`start_local.bat -NoPipeline`). All startup logic stays in `start_local.ps1`. The console window
stays open with the dashboard and pipeline output; stop with `Ctrl+C` (answer `Y` if Windows asks
"Terminate batch job"). When opened by double-click, the window waits for a key after the script
ends so any final error stays readable.

## Local High-Volume Mode

Two ways to run this pipeline:

| | Cloud (Modal cron) | Local high-volume (`LOCAL_MODE=true`) |
|---|---|---|
| Trigger | Modal scheduled cron, unattended | You, manually, on your own machine |
| Jobs/run | 10 (`JOB_LIMIT`) | 50 (`LOCAL_JOB_LIMIT`) by default |
| LLM provider | Groq → OpenRouter `:free` → Gemini | Ollama only, no cloud calls, no fallback |
| Why the different job count | OpenRouter's free-tier ~50-req/day cap (score+tailor+research share it — see [`scripts/llm.py`](scripts/llm.py) COST NOTES) | No such cap — Ollama has no daily quota; the real ceiling is your machine's compute time |
| Drive / Sheet | Same `pipeline-artifacts` mirror, same master "Job Application Tracker" | Same — identical dedup by job URL, no separate sheet or folder tree |

Local mode is for scraping and processing more jobs per day than the cloud path's free-tier
LLM quota allows, entirely on your own hardware — not just Modal-run recovery (though it works
for that too; see [Recovering a failed daily run](#recovering-a-failed-daily-run) below).

**Setup:**

```bash
ollama serve                    # in another terminal, if not already running
ollama pull qwen2.5:7b          # once
```

**Linux/macOS:**

```bash
LOCAL_MODE=true LOCAL_JOB_LIMIT=50 python3 scripts/run_pipeline.py
```

**Windows PowerShell:**

```powershell
$env:LOCAL_MODE = "true"
$env:LOCAL_JOB_LIMIT = "50"
python scripts/run_pipeline.py
```

Or set `LOCAL_MODE=true` (and optionally `LOCAL_JOB_LIMIT`, `LLM_MODEL`) in `.env` instead of the
shell, then just run `python3 scripts/run_pipeline.py` / `python scripts/run_pipeline.py`.

`scripts/run_pipeline.py` is a thin wrapper — it runs the exact same five scripts, in the same
order, as `modal_app.py`'s cloud path, and imports the same `reconcile_qualified` no-silent-failure
guard rather than reimplementing it, so the two paths can't drift. Provider selection and job
limit are the only things that differ, both resolved from `LOCAL_MODE` inside
[`scripts/llm.py`](scripts/llm.py) and [`scripts/scrape_jobs.py`](scripts/scrape_jobs.py).

At startup, `LOCAL_MODE=true` verifies Ollama is actually reachable and the configured model is
pulled — and **fails loudly with an actionable error instead of silently falling back to a cloud
provider** if not:

```
Ollama endpoint http://localhost:11434/v1 is not reachable (ConnectionError: ...).
Start it with:
    ollama serve
```

or

```
Ollama model 'qwen2.5:7b' is not available. Run:
    ollama pull qwen2.5:7b
```

It's still resume-safe at any scale — if a 50-job local run stops partway (e.g. after 27/50
scored), rerunning `scripts/run_pipeline.py` picks up exactly where it left off (same
`output/*.json` artifacts, same Drive mirror, same job-URL dedup as the cloud path — see
[Recovering a failed daily run](#recovering-a-failed-daily-run)) instead of redoing completed work
or creating duplicate Sheet rows / Drive folders.

To force a fresh Apify scrape instead of reusing today's cached `raw_jobs.json` (the default,
6-hour cache still applies — see `scrape_jobs.py`), set `FORCE_SCRAPE=true` or pass `--force`.

Small local models score tougher and are weaker at company research than the cloud chain; that's
expected, not a bug.

## Telegram Failure Alerts

Every scheduled run is wrapped in try/except. On failure it sends a Telegram message naming the
pipeline stage that broke (e.g. `Stage: company_research.py`) and the exception — no secrets — so
a broken cron never fails silently and you know which step to recover. To turn this on:

1. Message [@BotFather](https://t.me/BotFather) on Telegram, run `/newbot`, copy the token it
   gives you into `TELEGRAM_BOT_TOKEN`.
2. Message your new bot once, then hit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser to find your `chat_id`, put that
   in `TELEGRAM_CHAT_ID`.

Without these two set, the pipeline still runs and still fails loudly in the Modal logs — it just
won't message you.

## Honest Status

- Scrape, score/filter, tailor, company research, and Sheet logging all run correctly end to end.
- The resume-upload step had a real macOS-specific bug (a `/tmp` symlink resolution mismatch
  against the `gws` CLI's sandbox check) — fixed in `scripts/tailor_job.py`.
- Telegram alerting is wired into `modal_app.py` but needs your own bot token (above) to actually
  fire.
- Early Modal runs created a new "Job Application Tracker" sheet every day because the container's
  `.env` write doesn't persist — fixed in `scripts/write_sheet.py` (configured `google_sheet_id`
  first, then a Drive lookup by name, then create) and by putting `google_sheet_id` in the Modal
  secret.
- `tailor_job.py` used to re-tailor every qualifying job (and create duplicate Drive folders) on
  every run — now it reuses resumes already marked `saved` and only tailors what's missing, keyed
  by job URL. Combined with `scripts/artifacts.py` (the Drive artifact mirror), a day where every
  free provider is throttled can be finished locally with Ollama without redoing work or
  duplicating Sheet rows.

## After the Sheet Is Ready — Applying

This repo stops at "qualifying jobs, tailored resumes, one spreadsheet row each." Nothing here
auto-submits an application — a human always makes the final click. Two ways to take it from there:

**Recommended — [Simplify Jobs](https://simplify.jobs/copilot)** (free Chrome extension). Fill out
its one-time profile, then it autofills the rest of any job application form for you. Open each
row's job link, drop in your tailored resume, click through. Roughly 30 applications in 30 minutes.

**Alternative — [browser-use](https://github.com/browser-use/browser-use).** An open-source
framework for building your own browser-controlling AI agent. You could wire it up to fully
auto-apply from the Sheet, but it's less reliable than filling forms yourself with autofill.

If you want to build real auto-apply on top of this repo, the tools to look at are
[Playwright](https://github.com/microsoft/playwright) (browser automation) and
[Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp) (lets a coding agent
drive a real Chrome browser). Neither is implemented here — this is just where you'd start.

## Claude Code Building Blocks Used

- **`CLAUDE.md`** — always-on project memory: hard rules, pipeline order, folder layout.
- **A Skill** (`.claude/skills/tailor-resume/`) — the interactive resume-tailoring procedure.
- **A Hook** (`.claude/hooks/resume_hook.py`) — blocks any tailored resume from being saved if it's
  missing a required section or still has placeholder text in it.
- **Company research runs as a plain script**, not a Claude Code subagent — a real subagent needs a
  live interactive session, which Modal's headless daily cron can't provide.

## Limits

- Full-Stack Java/Spring Boot/Angular only, for now — one niche, on purpose.
- The 8+/10 fit cutoff is non-negotiable; nothing below it reaches tailoring or the Sheet.
- The tailoring step only reorders and rewords what's already on the base resume — it never
  invents experience, employers, tools, or metrics.
- A human always clicks the final "apply" — nothing here submits an application on its own.

## License

MIT — see [`LICENSE`](LICENSE).
