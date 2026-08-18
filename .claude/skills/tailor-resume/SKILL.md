---
name: tailor-resume
description: Tailor the base resume to each qualifying Full-Stack Java/Spring Boot/Angular job in output/scored_jobs.json and deliver it as a PDF in a per-job folder on the Desktop, via the gws (Google Workspace) CLI. Use when asked to tailor resumes, run the tailor-resume step, or generate resumes for qualifying jobs.
---

# Tailor Resume

Turns each qualifying job in `output/scored_jobs.json` into a tailored PDF resume saved on the
Desktop, using Google Docs (via the `gws` CLI) as the intermediate build step.

## Hard rule

**Never invent experience, employers, tools, dates, or metrics.** Only reorder and reword content
that already exists in `resume/base_resume.md`. If a job wants something the resume doesn't have,
leave it out — do not fabricate it to look like a better fit.

## Prerequisite

`gws` must be authenticated. Check with `gws auth status` — if `auth_method` is `none`, stop and
tell the user to run `gws auth setup` themselves (interactive browser login, cannot be automated).

## Procedure

Read `output/scored_jobs.json` and filter to entries where `qualified == true`. For each one, run
all of the following steps. If any step for a job fails, record it as `flagged` (see step 9) and
move on to the next job rather than stopping the whole run.

1. Read `resume/base_resume.md`.

2. Write a tailored version of the resume, preserving the same section headers exactly
   (`## Summary`, `## Skills`, `## Experience`, `## Education`, `## Certifications`):
   - **Summary**: reword to mirror the job's language/keywords.
   - **Skills**: reorder so items matching the job's `matched_must_haves` appear first.
   - **Experience**: reorder/re-emphasize existing bullets toward what the job asks for. Do not
     alter dates, employers, titles, or the substance of any bullet — only reorder bullets and
     lightly reword phrasing, never metrics.
   - Education and Certifications: carry over unchanged.

3. Compute `slug` = the job title, lowercased, non-alphanumeric characters replaced with `-`,
   collapsed/trimmed (e.g. "Full Stack Java Developer" → `full-stack-java-developer`). Compute
   `folder_name = f"{company}-{slug}"`.

4. Write the tailored text to `output/tailored/{folder_name}.md` using the Write tool. A
   `PreToolUse` hook (`.claude/hooks/resume_hook.py`) checks this write automatically — it
   blocks (and prints a reason) if required sections are missing or placeholder text remains. If
   the write is blocked, skip to step 9 for this job with `status: flagged_validation_failed` and
   the hook's reason — do not attempt the remaining steps.

5. Create `~/Desktop/Job Applications/{folder_name}/` with `mkdir -p` — all per-job folders nest
   under one `Job Applications` parent so the Desktop root doesn't fill up with one folder per job.

6. Create a Google Doc:
   ```
   gws docs documents create --json '{"title": "Akhil Dalali Resume - {Company}"}'
   ```
   Capture the returned `documentId`.

7. Populate the doc with real formatting via `scripts/format_resume_doc.py` — **do not** use
   `gws docs +write` with the raw markdown text; it inserts `#`/`##`/`###`/`-` as literal visible
   characters instead of converting them to headings/bullets, which reads as broken and
   unprofessional (confirmed by visual inspection, not just a style preference). The script parses
   the markdown structure from step 2 and applies real bold section headers, bold job titles,
   italic date lines, and native bullet lists — no markdown syntax left in the visible text:
   ```
   python3 scripts/format_resume_doc.py output/tailored/{folder_name}.md "$DOCUMENT_ID"
   ```

8. Export the doc straight to the Desktop folder as a PDF, then delete the intermediate doc
   (note: `files` is a sub-resource of `drive`, not a top-level service — `gws drive files ...`).
   `--output` for `gws drive files export` is sandboxed to the current directory, so `cd` into the
   Desktop folder first and export with a relative filename — an absolute path is rejected:
   ```bash
   cd "$HOME/Desktop/Job Applications/{folder_name}"
   gws drive files export --params '{"fileId": "'"$DOCUMENT_ID"'", "mimeType": "application/pdf"}' \
     --output "Akhil Dalali Resume.pdf"
   gws drive files delete --params '{"fileId": "'"$DOCUMENT_ID"'"}'
   cd -
   ```

9. Append a record for this job to `output/tailored_jobs.json` (create the file with an empty
   array first if it doesn't exist yet):
   ```json
   {
     "title": "...", "company": "...", "link": "...", "score": ...,
     "desktop_folder": "~/Desktop/Job Applications/{folder_name}",
     "desktop_file": "~/Desktop/Job Applications/{folder_name}/Akhil Dalali Resume.pdf",
     "status": "saved"
   }
   ```
   Use `"status": "flagged_validation_failed"` and add a `"reason"` field for jobs blocked at
   step 4. Omit `desktop_folder`/`desktop_file` for flagged jobs (nothing was saved).

10. After processing every qualifying job, print a one-line summary:
    `f"{qualified_count} qualified, {saved_count} saved, {flagged_count} flagged"`.
