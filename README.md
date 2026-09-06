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
[`scripts/llm.py`](scripts/llm.py), which talks to any OpenAI-compatible
`/chat/completions` endpoint. It defaults to OpenRouter's free tier (what the Modal cron
uses); point it at a local Ollama server with a few `.env` vars to test without spending
quota — see [LLM endpoint](#llm-endpoint-local-testing-with-ollama) below.

Every job gets scored against the base resume. Only jobs scoring 8 or higher continue past that
step — the rest are logged as rejected but never tailored, never touch the Sheet.

## Setup

**Prerequisites:**
- Python 3.12+
- The [`gws` CLI](https://github.com/googleworkspace/cli) — see [`GWS_SETUP.md`](GWS_SETUP.md) for
  the full walkthrough (Google Cloud project, OAuth, first login)
- An [Apify](https://apify.com) account (free tier)
- An [OpenRouter](https://openrouter.ai) account (free-tier models used by default)
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
# fill in apify_api_key, open_router_apikey (see .env.example for where to get each)
```

`google_drive_folder_id` — create a folder in Drive and paste its id (from the URL). It holds
the per-job resume folders and the `pipeline-artifacts/` sync folder.

`google_sheet_id` — set this to the ID of the single master "Job Application Tracker"
spreadsheet. This is recommended for both local and Modal runs so every execution
uses the same spreadsheet deterministically.

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

### LLM endpoint (local testing with Ollama)

By default score/tailor/research hit OpenRouter using `open_router_apikey`. To run them
against a local [Ollama](https://ollama.com) server instead — no key, no daily quota, no rate
limits — add these to `.env`:

```bash
llm_base_url=http://localhost:11434/v1
llm_model=qwen2.5:7b        # or any model you've `ollama pull`ed
llm_api_key=ollama
llm_reasoning_effort=       # blank — non-reasoning models don't understand it
```

All `llm_*` vars are optional; blank means "use the OpenRouter defaults". Ollama can't be
reached from the Modal cron container, so the deployed path always uses OpenRouter — this
switch is for local runs only. Small local models score noticeably tougher and are weaker at
company research; use them to exercise the pipeline, not for real output.

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

## Deploying (daily automatic run)

```bash
modal secret create job-apply-agent-secrets \
  apify_api_key=... open_router_apikey=... \
  google_drive_folder_id=... google_sheet_id=... \
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...

modal secret create gws-credentials \
  client_id=... client_secret=... refresh_token=... type=...

modal deploy modal_app.py
```

Runs daily at 7am Asia/Kolkata (IST). See [`GWS_SETUP.md`](GWS_SETUP.md) for how to get the
`gws-credentials` values, and the Telegram section below for the bot token.

**`google_sheet_id` must be in the secret** (not just `.env`). The Modal container has no
persistent `.env`, so without it in the secret `write_sheet.py` falls back to a Drive lookup by
name on every run. Get the id once from your master "Job Application Tracker" sheet's URL
(`docs.google.com/spreadsheets/d/<ID>/edit`) and set it. `open_router_apikey` stays the
production LLM provider — Modal always uses OpenRouter and never touches Ollama.

## Recovering a failed daily run

Normal day: the Modal cron runs on OpenRouter, finishes, and updates the master Sheet. Nothing
to do.

If OpenRouter (or a model) is down when the cron fires, Modal sends a Telegram alert naming the
stage that failed and stops. You then finish that day's work **locally with Ollama** — it picks
up where Modal left off:

```bash
ollama serve                        # in another terminal, if not already running
ollama pull qwen2.5:7b              # once

# .env — switch the LLM endpoint to local Ollama (see "LLM endpoint" above)
llm_base_url=http://localhost:11434/v1
llm_model=qwen2.5:7b
llm_api_key=ollama
llm_reasoning_effort=

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
  by job URL. Combined with `scripts/artifacts.py` (the Drive artifact mirror), a failed OpenRouter
  day can be finished locally with Ollama without redoing work or duplicating Sheet rows.

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
