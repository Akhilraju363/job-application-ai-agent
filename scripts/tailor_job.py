"""Tailor the resume for each qualifying job and deliver it as a PDF in Google Drive.

Automated-path equivalent of .claude/skills/tailor-resume/SKILL.md — same hard rule
(reorder/reword only, never fabricate) and same validation gate, but driven by a
single OpenRouter call per job instead of live Claude Code reasoning, so it can run
unattended (Modal cron) without an Anthropic API key.

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
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import sys
sys.path.insert(0, str(ROOT / "scripts"))
from validate_resume import validate
from llm import call_llm, validate_local_setup  # noqa: E402 -- LLM endpoint/model/retry config, .env-driven
from job_links import canonical_link  # noqa: E402
from activity import log_event  # noqa: E402

TAILOR_PROMPT = """You are tailoring a candidate's resume to a specific job posting.

Hard rule: never invent experience, employers, tools, dates, or metrics. Only reorder and
reword content that already exists in the base resume below. If the job wants something the
resume doesn't have, leave it out.

Preserve the section headers exactly: ## Summary, ## Skills, ## Experience, ## Education,
## Certifications.
- Summary: reword to mirror the job's language/keywords.
- Skills: reorder so items matching the job's matched requirements appear first.
- Experience: reorder/re-emphasize existing bullets toward what the job asks for. Do not alter
  dates, employers, titles, or the substance of any bullet -- only reorder bullets and lightly
  reword phrasing, never metrics. Never add or remove bullets, and never move a technology from
  one employer's section to another (e.g. a language used at one job must not appear under a
  different job). Keep every "###" role heading and its date line exactly as written.
- Skills: only items already in the base resume's Skills section may appear.
- Education and Certifications: carry over unchanged.

Respond with ONLY the tailored resume in markdown, no commentary, no code fences.

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


def create_job_folder(company, slug, parent_folder_id):
    folder = gws("drive", "files", "create", "--params", json.dumps({"fields": "id,webViewLink"}),
                 "--json", json.dumps({
                     "name": f"{company}-{slug}",
                     "mimeType": "application/vnd.google-apps.folder",
                     "parents": [parent_folder_id],
                 }))
    return folder["id"], folder["webViewLink"]


def export_doc_file(markdown_path, out_path, mime_type="application/pdf"):
    """Markdown -> formatted Google Doc -> exported file at out_path (PDF by default; pass the
    DOCX mime type for Word). The intermediate Doc is always deleted. Shared by the Drive
    pipeline below and the dashboard's Download PDF/DOCX."""
    doc = gws("docs", "documents", "create", "--json",
              json.dumps({"title": "Akhil Dalali Resume"}))
    doc_id = doc["documentId"]
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
    export_dir = tmp_pdf_path.parent.resolve()

    uploaded = gws("drive", "files", "create", "--params", json.dumps({"fields": "id,webViewLink"}),
                    "--json", json.dumps({
                        "name": "Akhil Dalali Resume.pdf",
                        "parents": [folder_id],
                    }),
                    "--upload", tmp_pdf_path.name,
                    cwd=export_dir)

    return uploaded["webViewLink"]


if __name__ == "__main__":
    import artifacts

    validate_local_setup()  # no-op unless LOCAL_MODE=true; fails loudly, never falls back to cloud
    parent_folder_id = os.environ["google_drive_folder_id"]
    resume_text = (ROOT / "resume" / "base_resume.md").read_text(encoding="utf-8")

    # Recovery: reuse the scores and any resumes an earlier run already finished.
    artifacts.pull("scored_jobs.json")
    artifacts.pull("tailored_jobs.json")

    jobs = json.loads((ROOT / "output" / "scored_jobs.json").read_text(encoding="utf-8"))
    qualified = [j for j in jobs if j.get("qualified")]

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
            print(f"SKIP (already tailored) {folder_name}")
            continue

        try:
            text = tailor_text(job, resume_text)
            ok, reason = validate(text)
            if not ok:
                by_link[key] = {**job, "status": "flagged_validation_failed", "reason": reason}
                print(f"FLAGGED {folder_name}: {reason}")
            else:
                md_path = tailored_dir / f"{folder_name}.md"
                md_path.write_text(text, encoding="utf-8")

                folder_id, folder_link = create_job_folder(job["company"], slug, parent_folder_id)
                tmp_pdf_path = tmp_dir / folder_name / "Akhil Dalali Resume.pdf"
                resume_link = build_and_upload_resume(md_path, folder_id, tmp_pdf_path)

                by_link[key] = {
                    "title": job["title"], "company": job["company"], "link": link,
                    "score": job["score"], "drive_folder_link": folder_link,
                    "resume_link": resume_link, "status": "saved",
                    "tailored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                print(f"SAVED {folder_name}")
                log_event("resume_tailored", f"Resume tailored for {job['company']} - {job['title']}",
                          company=job["company"], title=job["title"], link=link, source="pipeline")
        except Exception as e:
            by_link[key] = {**job, "status": "flagged_error", "reason": str(e)}
            print(f"ERROR {folder_name}: {e}")

        out_path.write_text(json.dumps(list(by_link.values()), indent=2), encoding="utf-8")
        artifacts.push("tailored_jobs.json")

    out_path.write_text(json.dumps(list(by_link.values()), indent=2), encoding="utf-8")
    artifacts.push("tailored_jobs.json")

    results = list(by_link.values())
    saved = sum(1 for r in results if r["status"] == "saved")
    flagged = len(results) - saved
    print(f"{len(qualified)} qualified, {saved} saved, {flagged} flagged")
