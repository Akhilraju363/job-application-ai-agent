"""Tailor the resume for each qualifying job and deliver it as a PDF in Google Drive.

Automated-path equivalent of .claude/skills/tailor-resume/SKILL.md — same hard rule
(reorder/reword only, never fabricate) and same validation gate, but driven by the free
LLM chain instead of live Claude Code reasoning, so it can run unattended (Modal cron)
without an Anthropic API key. Each job goes through tailoring_service.tailor() -- the same
path as the dashboard: JD analysis, tailoring, no-fabrication check, retry, reorder-only
fallback -- so only verified resumes are exported (verified_resume below).

Reads output/scored_jobs.json (qualified == true), writes output/tailored_jobs.json.
Requires: google_drive_folder_id (the "Job Applications" parent Drive folder, created
once during provisioning) plus an LLM endpoint -- open_router_apikey, or the llm_*
overrides in scripts/llm.py -- in .env.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import sys
sys.path.insert(0, str(ROOT / "scripts"))
from validate_resume import validate
from contact import contact_line, with_contact  # noqa: E402
from resume_role import export_filename, fixed_header  # noqa: E402
import paths  # noqa: E402
import employment_history  # noqa: E402
from llm import call_llm, validate_local_setup  # noqa: E402 -- LLM endpoint/model/retry config, .env-driven
from job_links import canonical_link  # noqa: E402
import logging_config as lc  # noqa: E402

log = lc.get_logger("tailoring")

TAILOR_PROMPT = """You are tailoring a candidate's resume to a specific job posting.

The BASE RESUME below is the authoritative source of truth about the candidate. Never invent
experience, employers, tools, dates, locations, achievements or metrics -- not even to match a
keyword. Only reorder and reword content that already exists in the base resume, and only where
the base resume's facts already support the new wording. If the job wants something the resume
doesn't have, leave it out.

EMPLOYMENT HISTORY IS IMMUTABLE. Copy these protected lines character for character, in this
order, each employer exactly once. Do not combine, split, move or rewrite them: never add a
location to a job-title line, never move a location off its date line, never change a date,
title, employer name, punctuation or capitalization, never reorder, drop or repeat an employer.
PROTECTED EMPLOYMENT LINES:
__EMPLOYMENT__

Preserve the section headers exactly: ## Summary, ## Skills, ## Experience, ## Education,
## Certifications.
- Summary: reword to mirror the job's language/keywords, using only facts in the base resume.
- Skills: reorder so items matching the job's matched requirements appear first.
- Experience: under each protected employer block, reorder/re-emphasize that employer's existing
  bullets toward what the job asks for -- only reorder bullets and lightly reword phrasing, never
  metrics or substance. Never add or remove bullets, and never move a bullet or a technology from one
  employer's section to another (e.g. a language used at one job must not appear under a
  different job).
- Keep the header (name, headline, LinkedIn line) exactly as written.
- Keep exactly the base resume's sections in the same order. Never add a section (no Projects,
  Achievements, Technical Highlights) and never remove or rename one.
- Skills: only items already in the base resume's Skills section may appear.
- Education and Certifications: carry over unchanged.

Respond with ONLY the tailored resume in markdown, in the base resume's format -- no
explanations, no notes before or after it, no code fences.

BASE RESUME:
__RESUME__

JOB TITLE: __TITLE__
COMPANY: __COMPANY__
MATCHED REQUIREMENTS: __MATCHED__
MISSING REQUIREMENTS: __MISSING__
JOB DESCRIPTION:
__DESCRIPTION__
"""


def _resolve_gws():
    # See format_resume_doc.py's _resolve_gws() for why this bypasses the .cmd shim on Windows.
    if os.name != "nt":
        return ["gws"]
    gws_cmd = shutil.which("gws.cmd")
    if gws_cmd:
        run_js = os.path.join(os.path.dirname(gws_cmd), "node_modules", "@googleworkspace", "cli", "run.js")
        if os.path.exists(run_js):
            return ["node", run_js]
    return ["gws"]


GWS_CMD = _resolve_gws()


def gws(*args, cwd=None):
    # See format_resume_doc.py's gws() for why encoding="utf-8" is required on Windows.
    result = subprocess.run([*GWS_CMD, *args], capture_output=True, text=True, encoding="utf-8", cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"gws {' '.join(args)} failed: {result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def slugify(title):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")


def tailor_text(job, resume_text, feedback=""):
    prompt = (
        TAILOR_PROMPT
        .replace("__EMPLOYMENT__", employment_history.protected_lines(resume_text))
        .replace("__RESUME__", resume_text)
        .replace("__TITLE__", str(job.get("title")))
        .replace("__COMPANY__", str(job.get("company") or "(not specified)"))
        .replace("__MATCHED__", ", ".join(job.get("matched_must_haves", [])))
        .replace("__MISSING__", ", ".join(job.get("missing_must_haves", [])))
        .replace("__DESCRIPTION__", str(job.get("description")))
    )
    if feedback:
        prompt += ("\nYOUR PREVIOUS ATTEMPT WAS REJECTED for these reasons -- fix every one, keeping "
                   "to the hard rule:\n" + feedback + "\n")
    return call_llm(prompt, job.get("title"))


def verified_resume(job, base_md, want_record=False):
    """A scraped job's resume through the same path as the dashboard: tailoring_service.tailor()
    (JD analysis -> tailor a fresh copy of the master -> no-fabrication check -> one retry ->
    reorder-only fallback). Returns (markdown, None), or (None, reason) when even the fallback
    fails verification -- an unverified rewrite never reaches Drive. want_record=True appends the
    resume record id and version number (None, None on failure) for publishing the local PDF."""
    import jd_analysis
    import tailoring_service  # lazy: it imports this module lazily too

    norm = jd_analysis.normalize_job(job.get("title"), job.get("company"), job.get("link"), job.get("description"))
    norm["source"] = job.get("source") or "LinkedIn"  # scraped jobs carry their source (LinkedIn, Naukri)
    if job.get("score") is not None:
        norm["pipeline_score"] = job["score"]
    rec = tailoring_service.tailor(norm, source="scraped", base_md=base_md, job_key=canonical_link(job.get("link")))
    version = rec["versions"][-1]
    record = (rec.get("id"), version.get("n")) if want_record else ()
    if not version["validation"]["ok"]:
        reason = "failed no-fabrication verification: " + "; ".join(version["validation"]["problems"])[:500]
        return (None, reason, None, None) if want_record else (None, reason)
    ok, reason = validate(version["markdown"])
    if not ok:
        return (None, reason, None, None) if want_record else (None, reason)
    return (version["markdown"], None, *record)


def create_job_folder(company, slug, parent_folder_id):
    folder = gws("drive", "files", "create", "--params", json.dumps({"fields": "id,webViewLink"}),
                 "--json", json.dumps({
                     "name": f"{company}-{slug}",
                     "mimeType": "application/vnd.google-apps.folder",
                     "parents": [parent_folder_id],
                 }))
    return folder["id"], folder["webViewLink"]


def render_markdown(markdown):
    """The resume exactly as every export shows it: the fixed header (the master resume's name and
    headline, whatever the job -- also for versions saved before it was fixed) plus the private
    contact line. The stored markdown is never rewritten."""
    return with_contact(fixed_header(markdown, paths.read_base_resume()))


def export_doc_file(markdown_path, out_path, mime_type="application/pdf"):
    """Markdown -> formatted Google Doc -> exported file at out_path (PDF by default; pass the
    DOCX mime type for Word). The intermediate Doc is always deleted. Shared by the Drive
    pipeline below and the dashboard's Download PDF/DOCX. The fixed header and the private contact
    line (render_markdown) are applied here, so they reach every export but never the stored markdown."""
    if not contact_line():
        log.warning("Exporting a resume without a contact line: RESUME_CONTACT_LINE is not set",
                    extra={"file": Path(out_path).name})
    doc = gws("docs", "documents", "create", "--json",
              json.dumps({"title": "Akhil Dalali Resume"}))
    doc_id = doc["documentId"]
    with tempfile.TemporaryDirectory() as tmp:
        render_path = Path(tmp) / "resume.md"
        render_path.write_text(render_markdown(Path(markdown_path).read_text(encoding="utf-8")), encoding="utf-8")
        _export_doc(render_path, doc_id, out_path, mime_type)


def _export_doc(markdown_path, doc_id, out_path, mime_type):
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "format_resume_doc.py"), str(markdown_path), doc_id],
            check=True,
        )

        export_dir = out_path.parent
        export_dir.mkdir(parents=True, exist_ok=True)
        # macOS /tmp is a symlink to /private/tmp -- gws resolves the upload path to its
        # real location but compares it against the unresolved cwd, so the sandbox check
        # fails unless we resolve symlinks here too before either subprocess call.
        export_dir = export_dir.resolve()
        result = subprocess.run(
            [*GWS_CMD, "drive", "files", "export", "--params",
             json.dumps({"fileId": doc_id, "mimeType": mime_type}),
             "--output", out_path.name],
            cwd=export_dir, capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode != 0:
            raise RuntimeError(f"export failed: {result.stderr}")
    finally:
        try:
            gws("drive", "files", "delete", "--params", json.dumps({"fileId": doc_id}))
        except Exception:  # noqa: BLE001 -- a leaked scratch Doc must not mask the real error
            pass


def build_and_upload_resume(markdown_path, folder_id, tmp_pdf_path):
    export_doc_file(markdown_path, tmp_pdf_path)
    return upload_pdf(tmp_pdf_path, folder_id)


def upload_pdf(tmp_pdf_path, folder_id):
    """Upload an existing PDF (named as the recruiter should see it) into the job's Drive folder."""
    export_dir = tmp_pdf_path.parent.resolve()

    uploaded = gws("drive", "files", "create", "--params", json.dumps({"fields": "id,webViewLink"}),
                    "--json", json.dumps({
                        "name": tmp_pdf_path.name,
                        "parents": [folder_id],
                    }),
                    "--upload", tmp_pdf_path.name,
                    cwd=export_dir)

    return uploaded["webViewLink"]


if __name__ == "__main__":
    lc.configure_logging("pipeline")
    import artifacts

    validate_local_setup()  # no-op unless LOCAL_MODE=true; fails loudly, never falls back to cloud
    parent_folder_id = os.environ["google_drive_folder_id"]
    resume_text = paths.read_base_resume()  # the master: every job starts from a fresh copy of it

    # Recovery: reuse the scores and any resumes an earlier run already finished.
    artifacts.pull("scored_jobs.json")
    artifacts.pull("tailored_jobs.json")

    jobs = json.loads((ROOT / "output" / "scored_jobs.json").read_text(encoding="utf-8"))
    qualified = [j for j in jobs if j.get("qualified")]
    log.info("Tailoring started", extra={"operation": "pipeline_tailor", "qualified_count": len(qualified)})

    tailored_dir = ROOT / "output" / "tailored"
    tailored_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path("/tmp/tailor_job")
    out_path = ROOT / "output" / "tailored_jobs.json"

    # A prior run (e.g. the Modal cron before OpenRouter failed) may have already
    # tailored + uploaded some of these jobs. Reuse those records verbatim --
    # re-tailoring would burn LLM calls and create duplicate Drive folders. Only
    # status == "saved" counts as done; flagged/errored jobs are retried. Keyed by
    # *canonical* job link (tracking-param-stripped, see scripts/job_links.py) so a
    # posting re-scraped on a different day with a new trackingId/position is still
    # recognized as the same job and doesn't get a second Drive folder.
    previous = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else []
    by_link = {canonical_link(r["link"]): r for r in previous if r.get("status") == "saved"}

    for job in qualified:
        link = job["link"]
        key = canonical_link(link)
        slug = slugify(job["title"])
        folder_name = f"{job['company']}-{slug}"

        # Checked against the live dict, not a static pre-loop snapshot -- two
        # qualified entries that canonicalize to the same job (e.g. both freshly
        # scraped this run under different tracking params) must not both create a
        # Drive folder; the second sees the first's "saved" write and skips.
        if by_link.get(key, {}).get("status") == "saved":
            log.info("Skipping already-tailored job", extra={"folder": folder_name})
            continue

        try:
            text, reason, resume_id, resume_version = verified_resume(job, resume_text, want_record=True)
            if text is None:
                by_link[key] = {**job, "status": "flagged_validation_failed", "reason": reason}
                log.warning("Tailored resume flagged by validation", extra={"folder": folder_name, "reason": str(reason)[:200]})
            else:
                md_path = tailored_dir / f"{folder_name}.md"
                md_path.write_text(text, encoding="utf-8")

                folder_id, folder_link = create_job_folder(job["company"], slug, parent_folder_id)
                # The verified version becomes the local artifact generated_resumes/<id>/v<n>.pdf
                # (rendered once, checked, recorded); Drive gets that same file under its export name.
                import resume_artifacts
                import resume_store
                resume_artifacts.publish_verified_pdf(resume_id, resume_version)
                tmp_pdf_path = tmp_dir / folder_name / export_filename(text, "pdf", job["title"])
                tmp_pdf_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(resume_store.version_path(resume_id, resume_version, "pdf"), tmp_pdf_path)
                resume_link = upload_pdf(tmp_pdf_path, folder_id)

                by_link[key] = {
                    "title": job["title"], "company": job["company"], "link": link,
                    "score": job["score"], "source": job.get("source") or "LinkedIn",
                    "drive_folder_link": folder_link,
                    "resume_link": resume_link, "resume_id": resume_id, "resume_version": resume_version,
                    "status": "saved",
                    "tailored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                log.info("Tailored resume saved", extra={"folder": folder_name, "company": job["company"]})
                # (the "resume_tailored" activity event is logged by tailoring_service)
        except Exception as e:
            by_link[key] = {**job, "status": "flagged_error", "reason": str(e)}
            log.error("Tailoring failed for job", exc_info=True, extra={"folder": folder_name})

        out_path.write_text(json.dumps(list(by_link.values()), indent=2), encoding="utf-8")
        artifacts.push("tailored_jobs.json")

    out_path.write_text(json.dumps(list(by_link.values()), indent=2), encoding="utf-8")
    artifacts.push("tailored_jobs.json")

    results = list(by_link.values())
    saved = sum(1 for r in results if r["status"] == "saved")
    flagged = len(results) - saved
    log.info(f"{len(qualified)} qualified, {saved} saved, {flagged} flagged",
             extra={"qualified_count": len(qualified), "saved_count": saved, "flagged_count": flagged})
