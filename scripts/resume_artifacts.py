"""Publish a verified tailored resume as a local PDF artifact: generated_resumes/<id>/v<n>.pdf.

    verified version (no-fabrication passed) -> existing Google Docs export (tailor_job.export_doc_file)
    -> structural PDF check -> atomic publish -> meta.json versions[n].exports.pdf + artifacts.pdf

Only a version whose validation is ok can be published; v<n>.pdf is always rendered from
v<n>.md (the same render every export uses: fixed header + private contact line). The artifact
record carries the job's canonical link and the SHA-1 of the exact markdown it was rendered
from, so a consumer (e.g. Auto_job_apply's Naukri upload) can reject a stale or wrong-job file.
Stdlib only: the deep text check happens in the consumer, which has a PDF parser.

CLI (local):
    python scripts/resume_artifacts.py --resume-id 810b8ec2012d
    python scripts/resume_artifacts.py --link https://www.naukri.com/job-listings-...
"""
import argparse
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import logging_config as lc  # noqa: E402
import resume_store  # noqa: E402
from job_links import canonical_link  # noqa: E402

log = lc.get_logger("resume")

PDF_MIN_BYTES = 800


class ArtifactError(RuntimeError):
    """The resume cannot be published (not verified, missing markdown, invalid PDF...)."""


def md_sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def check_pdf_structure(path):
    """Problems with a PDF file (empty list == structurally fine). No parser needed."""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return [f"not readable: {e}"]
    problems = []
    if len(data) < PDF_MIN_BYTES:
        problems.append(f"too small ({len(data)} bytes)")
    if not data.startswith(b"%PDF-"):
        problems.append("missing %PDF- header")
    if b"%%EOF" not in data[-2048:]:
        problems.append("truncated (no %%EOF trailer)")
    if not re.search(rb"/Type\s*/Page[^s]", data) and b"/ObjStm" not in data:
        problems.append("no pages")
    return problems


def latest_verified_version(meta):
    for version in reversed(meta.get("versions") or []):
        if (version.get("validation") or {}).get("ok"):
            return version
    return None


def find_record_for_link(link):
    """Newest resume record whose job is this canonical link, or None."""
    key = canonical_link(link)
    for meta in resume_store.list_all():  # newest first
        job = meta.get("job") or {}
        if canonical_link(job.get("job_key") or job.get("link")) == key:
            return meta
    return None


def publish_verified_pdf(rid, n=None, export=None):
    """Make generated_resumes/<rid>/v<n>.pdf exist for a verified version; returns the artifact info.

    n=None -> the latest verified version. Idempotent: an existing artifact rendered from the same
    markdown is reused. Raises ArtifactError (nothing published) on any problem.
    """
    if export is None:
        import tailor_job  # lazy: gws + Docs export, needs the environment
        export = tailor_job.export_doc_file
    meta = resume_store.get(rid, with_markdown=True)
    version = latest_verified_version(meta) if n is None else next(
        (v for v in meta["versions"] if v["n"] == int(n)), None)
    if version is None:
        raise ArtifactError(f"resume {rid} has no {'verified ' if n is None else ''}version {n or ''}".strip())
    if not (version.get("validation") or {}).get("ok"):
        raise ArtifactError(f"resume {rid} v{version['n']} did not pass verification; it is never exported")
    markdown = version.get("markdown") or ""
    if not markdown.strip():
        raise ArtifactError(f"resume {rid} v{version['n']} has no markdown")
    n = version["n"]
    sha = md_sha1(markdown)
    job_key = canonical_link((meta.get("job") or {}).get("job_key") or (meta.get("job") or {}).get("link"))
    out = resume_store.version_path(rid, n, "pdf")
    existing = (version.get("artifacts") or {}).get("pdf") or {}
    if out.exists() and existing.get("md_sha1") == sha and not check_pdf_structure(out):
        log.info("Verified PDF already published", extra={"resume_id": rid, "version": n})
        return existing

    tmp = out.with_name(f".v{n}.pdf.tmp")
    try:
        export(resume_store.version_path(rid, n, "md"), tmp)
        problems = check_pdf_structure(tmp)
        if problems:
            raise ArtifactError("generated PDF failed validation: " + "; ".join(problems))
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()
    info = {
        "file": out.name, "format": "pdf", "version": n, "verified": True, "md_sha1": sha,
        "job_key": job_key, "master_version": meta.get("master_version"), "size": out.stat().st_size,
        "validation": "structure", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    resume_store.mark_export(rid, n, "pdf")
    resume_store.mark_artifact(rid, n, "pdf", info)
    log.info("Verified PDF published", extra={"resume_id": rid, "version": n, "size": info["size"]})
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish a verified tailored resume as a local PDF")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--resume-id")
    group.add_argument("--link", help="job link (canonicalised) of a tailored job")
    parser.add_argument("--version", type=int, default=None)
    args = parser.parse_args(argv)
    lc.configure_logging("pipeline")
    rid = args.resume_id
    if args.link:
        meta = find_record_for_link(args.link)
        if meta is None:
            print("No tailored resume record for that job link.")
            return 1
        rid = meta["id"]
    try:
        info = publish_verified_pdf(rid, args.version)
    except (ArtifactError, KeyError) as e:
        print(f"Not published: {e}")
        return 1
    print(f"Published {resume_store.version_path(rid, info['version'], 'pdf')} (v{info['version']}, verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
