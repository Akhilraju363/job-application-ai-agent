# Tailor Resume (`/tailor-resume`)

## Purpose
Turn a job description into a verified, truthful resume built only from the master resume.

## Workflow stepper (top of the page)
`Job description → Analyze → Match → Tailor → Validate → Preview → Export`
The stepper reflects real state only: before submit, “Job description” is current; while the task runs, the server's
task stages mark Analyze/Match/Tailor/Validate; when a record is shown, Preview is current; after a PDF/DOCX export
succeeds, Export is complete. It never runs ahead of the server.

## Layout
- Row 1: JD form (title, company, URL, source, description) · analysis panel (idle empty state → live stage list → JD analysis).
- Row 2: Generated Resume (ResumePreview: verification badge, notices, paper preview, actions).
- Row 3: Recent resumes (history list; click to load).

## What is shown
- JD analysis: overall match ring; skills / experience / keyword coverage; matched skills; missing (unsupported) requirements.
- Fabrication validation: “Verified: no fabrication detected” or “Blocked: failed verification” with the reasons; the reorder-only fallback notice.
- ATS validation: score, checks, keyword coverage.
- Export: Download PDF / DOCX (blocked until verified), Markdown, Regenerate, Edit, Save to Tracker; Drive link or Drive failure notice.

## Entry points (unchanged)
Manual JD → `POST /api/tailor`; scraped job (`?job=key`) → `POST /api/jobs/:key/tailor`; saved resume (`?resume=id`) →
`GET /api/resumes/:id`. Progress → `GET /api/tasks/:id`.

## Do not show
Prompts, internal file paths or raw provider errors; the task error message is already user-facing.
