# Master Resume page (`/master-resume`)

## Purpose
View and edit `resume/base_resume.md` — **the single source of truth for career facts**. Every tailored resume
is generated from it; nothing else (a JD, previous resumes, the UI's own JSON) is ever a source.
Header copy (verbatim): “Your master resume is the single source of truth for your career information. All
tailored resumes are generated from this resume.”

## Layout
- Desktop (≥1280px): section nav (sticky, left) · editor column · live preview (sticky, right).
- 1000–1280px: editor · preview. <1000px: one column, preview below the editor.
- The Save / Discard bar is sticky at the bottom of the editor on desktop and static on mobile.
- Page meta row: Version (12-char fingerprint), Last updated, save-status badge, validation badge.

## Sections (one card each, `section[data-section]`)
| Section | Fields | Editing |
|---|---|---|
| Header | Name, Headline; public profile line (read-only) | The headline is fixed for every tailored resume — a JD never changes it; the job role only names the exported file |
| Professional Summary | one multiline editor | Paragraphs separated by a blank line |
| Technical Skills | per group: Group name, Skills (comma-separated) | add / remove / move groups |
| Professional Experience | per employer card: Employer, Job title, Dates, Location, Bullets | add / remove (with confirm) / move employers; every bullet is its own textarea with move / remove; Add bullet |
| Education | per entry: Degree, Institution, Dates | add / remove / move |
| Certifications | one item each | add / remove / move |

Never one giant Markdown textarea.

## Editing behaviour
- Edits live in memory only; there is no autosave. Changed sections get an orange “Edited” badge, a highlighted
  border and a dot in the section nav; the page status becomes “Unsaved changes”.
- Leaving with unsaved edits (sidebar link, Back, reload/close) asks **Save Changes / Discard Changes / Cancel**
  (`router.setLeaveGuard` + `beforeunload`).
- Discard Changes (with confirm) restores the last saved master.

## Save behaviour
1. Client validation (mirrors the server) → inline errors, scroll + focus the first one, no request.
2. Confirm dialog with the impact warning: “Changing the master resume can affect future tailored resumes and may
   cause existing jobs to be re-tailored when the master version changes.”
3. `PUT /api/master-resume {resume, expected_version}` → toast “Master resume saved successfully. Version …”.
4. 422 → field errors from `error.details`; 409 `conflict` → reload; 409 `read_only` → explain (hosted).

## Validation (server is authoritative: `scripts/master_resume.py`)
Required: name, headline, summary, ≥1 skill group (name without `:`, non-empty skills), ≥1 employer (employer,
job title without a year, dates containing a year and no `|`, location without `|`, ≥1 non-empty bullet),
education degree + institution, non-empty certifications. No line may start with `#` or `- `. An email or
phone number anywhere is rejected.

## Preview
Rendered from the structured draft in the export layout (`scripts/format_resume_doc.py`): centred name, headline
and contact row; ruled uppercase headings (PROFESSIONAL SUMMARY, TECHNICAL SKILLS, PROFESSIONAL EXPERIENCE,
EDUCATION, CERTIFICATIONS); bold employer + title; bold skill labels; Arial. Updates on every keystroke; Hide/Show toggle.

## Versioning
Version = the first 12 hex chars of the file's SHA-1. A save that changes the file changes the version; a no-op
save doesn't. Existing tailored resumes are never rewritten — they report `master_resume_current: false`.

## Contact handling
Email and phone are **never** stored in the master. The preview shows `RESUME_CONTACT_LINE` (from the server)
with the note “Contact information is managed separately and is not stored in the public master resume.”, or a
visible warning when it isn't configured. The public profile line (LinkedIn) is kept as stored and never sent back.

## API expectations
- `GET /api/master-resume` → `{resume, version, updated_at, editable, read_only_reason, contact{configured, line}}`.
- `PUT /api/master-resume` body keys exactly `resume` and `expected_version`; never a path. Auth + same-origin enforced by the server.
- Hosted (Modal) deployments are read-only: fields disabled, the reason shown.

Test hooks: `data-section`, `data-field="<section>.<index>.<field>"`, `data-kind` (skill-group, employer, bullet,
education, certification), `data-meta` (version, status, validation), `data-action` (save, discard, toggle-preview),
`data-choice` (save, discard, cancel), `.paper-master`.
