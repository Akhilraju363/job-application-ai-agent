# CLAUDE.md

Automated job-hunting pipeline for Full-Stack Java / Spring Boot / Angular roles in India (candidate: Akhil Dalali).
Scrape (Apify LinkedIn) → LLM-score vs the master resume (keep 8+/10) → tailor + verify a resume per strong match →
company research → log to Google Sheets; runs on Modal (07:00 IST, Mon–Fri) with Telegram alerts, plus a dashboard.

## Session workflow
- **Start:** read `docs/TASKS.md` and `docs/MEMORY.md`.
- **End:** update `docs/TASKS.md`; add to `docs/MEMORY.md` if a decision was made; add a line to `docs/CHANGELOG.md`.
- `PRD.md` is the source of truth for scope. This is a Claude Code Masterclass capstone — treat the hard rules as binding.

## Where things are documented (don't duplicate them here)
| Topic | File |
|---|---|
| Pipeline diagram, stage ownership, job dict schema, subsystem notes (LLM chain, recovery, dashboard, auth, logging) | `docs/ARCHITECTURE.md` |
| Env keys (names only), Modal schedule/secrets, Apify actors + input, Sheet columns | `docs/CONFIG.md` |
| Run a stage, read logs, Telegram alerts, re-run a failed day, Apify credit exhausted | `docs/RUNBOOK.md` |
| LLM prompts (verbatim) | `docs/prompts/scoring.md`, `tailoring.md`, `research.md` |
| Decisions / tasks / changes | `docs/MEMORY.md`, `docs/TASKS.md`, `docs/CHANGELOG.md` |
| Dashboard UI specs (design system, pages, a11y, how to change UI) | `docs/ui/` (start with `UI_IMPLEMENTATION.md`) |
| Setup, local run, deploy | `README.md`, `GWS_SETUP.md` |

## Hard rules
- **Never commit secrets.** No keys, tokens, hashes, passwords, sheet ids or contact details in code, docs or logs.
  `.env` is gitignored; cloud values live in Modal secrets. Document key *names* only.
- **8+/10 cutoff is non-negotiable** — `QUALIFY_CUTOFF` in `scripts/score_jobs.py`, applied in code. Nothing below it
  reaches automated tailoring, the Sheet or Drive. Don't change the cutoff or the scoring prompt without updating
  `docs/prompts/scoring.md` in the same change.
- **No fabrication.** Tailoring reorders/rewords master-resume content only; `scripts/no_fabrication.py` gates every
  export and tracker save; failures retry once, then fall back to reorder-only. Never loosen the checker to pass output.
- **Master resume is the single source of career facts** — `resume/base_resume.md` (transcribes the master ATS PDF).
  Every resume starts from it; header, section order, employer/title/date/location lines, education and certifications
  stay unchanged; the job role only names the exported file. Contact info comes only from `RESUME_CONTACT_LINE` at export.
- **No auto-submitting applications** — the agent prepares; a human applies.
- **Single niche** — Full-Stack Java/Spring Boot/Angular Engineer.
- **Free LLMs only** — Groq → OpenRouter `:free` → Gemini via `scripts/llm.py`; no paid models or credit. Modal never
  sets `llm_base_url`/`LOCAL_MODE`; local mode never falls back to the cloud.
- **No silent failures** — the Modal run is wrapped in try/except; any failure sends `JOB-APPLY-AGENT — WHAT BROKE`
  to Telegram, then re-raises. Post-run reconciliation fails the run if a qualified job has no saved resume.
- **Test new scrape sources with `limit=2`** and check the field mapping before a full run (Apify credit is limited).
- Keep the cloud job limit at `JOB_LIMIT` (default 10); `LOCAL_JOB_LIMIT` (50) is local-only.
- Don't commit, push or deploy without explicit approval.

## Coding conventions
- Python 3.12, stdlib first; runtime deps are only `requests` and `python-dotenv` (`requirements.txt`). Each pipeline
  stage is a standalone script in `scripts/` that reads/writes `output/*.json`.
- Stages are **resume-safe**, keyed by `job_links.canonical_link`: skip work already done; never duplicate Sheet rows or
  Drive folders. Mirror artifacts with `artifacts.pull/push`.
- All LLM calls go through `llm.call_llm` (use `json_mode=True` for JSON). Prompts are module constants with `__PLACEHOLDER__` fills.
- Logging: `logging_config.get_logger("<component>")`, never `print()` for diagnostics. Never log prompts, JDs, resumes,
  request bodies or credentials.
- Google Workspace goes through the `gws` CLI (`drive files …` is under `drive`; export `--output` must be a relative
  path from the target dir). Never `gws docs +write` raw markdown — use `scripts/format_resume_doc.py`.
- Dashboard: `scripts/dashboard_server.py` (stdlib HTTP, default-deny auth) + `web/` (vanilla ES modules, `h()` only,
  no build step). Relative `/api/...` URLs only.
- Tests: `python -m unittest discover -s tests` (tests import `tests/fixtures.py` first; it disables `.env`) and
  `node --test web/tests/*.test.mjs`. Known pre-existing failure:
  `test_tracker_outage_degrades_sections_instead_of_failing`. Add tests with every behaviour change.

## Quick commands
```bash
python3 scripts/scrape_jobs.py && python3 scripts/score_jobs.py && python3 scripts/tailor_job.py \
  && python3 scripts/company_research.py && python3 scripts/write_sheet.py   # pipeline, stage by stage
python3 scripts/run_pipeline.py        # local (LOCAL_MODE=true, Ollama)
python scripts/dashboard_server.py     # dashboard on http://127.0.0.1:8765
modal run modal_app.py                 # one cloud run;  modal deploy modal_app.py  = cron + hosted dashboard
```
