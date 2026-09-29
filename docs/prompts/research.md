# Company research prompt

- **Purpose:** 3-5 interview talking points per qualifying company — human context only, never written into the resume.
- **Source (hard-coded):** `scripts/company_research.py` line 23, constant `RESEARCH_PROMPT`; filled by `research_company()`
  and sent via `llm.call_llm(prompt, company)` (plain text, not JSON mode).
- **Inputs:** `__COMPANY__`, `__TITLE__` of a `status: "saved"` entry in `output/tailored_jobs.json`.
- **Expected output:** plain text, one point per line; stored as `company_notes` and logged to the Sheet's "Company Notes" column.
  A failure stores `""` and the job is retried on the next run.
- **Version:** as of commit `5c03f4c` + working tree, 2026-09-29.
- **Suggestion (not done):** load the prompt from this file instead of the Python constant.

## Prompt (verbatim)

```text
Give 3-5 short talking points about __COMPANY__ useful for someone
interviewing for a __TITLE__ role there -- what they do/their product, engineering culture
or tech stack if known, anything relevant to bring up in an interview. Only include things
you're confident about; skip recent news or specifics you're not sure of rather than
guessing. Plain text, one point per line, no headers, no commentary before or after.
```
