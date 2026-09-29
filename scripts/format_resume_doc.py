"""Populate a Google Doc with a tailored resume, converting markdown structure into
real Google Docs formatting instead of literal markdown syntax, in the master resume's
layout: centered name/headline/contact, uppercase section headings with a rule under them,
bold employer and job-title lines, bold skill labels, real bullets. ATS-friendly: plain
single-column text, no #, ##, ### or leading "- " characters left in the visible text.

Usage: format_resume_doc.py <markdown_path> <document_id>
"""
import json
import os
import re
import shutil
import subprocess
import sys


def _resolve_gws():
    # On Windows, gws is an npm-installed .cmd shim -- CreateProcess can't launch
    # that directly (it's not a PE executable). shell=True would work but re-parses
    # every argument through cmd.exe, which mangles/misroutes JSON payloads containing
    # quotes or special characters. Instead, invoke the shim's underlying node script
    # directly (a real executable, so argv passes through untouched).
    if os.name != "nt":
        return ["gws"]
    gws_cmd = shutil.which("gws.cmd")
    if gws_cmd:
        run_js = os.path.join(os.path.dirname(gws_cmd), "node_modules", "@googleworkspace", "cli", "run.js")
        if os.path.exists(run_js):
            return ["node", run_js]
    return ["gws"]


GWS_CMD = _resolve_gws()


def gws(*args):
    # encoding="utf-8" is required: gws (a Node CLI) always writes UTF-8 stdout, but
    # text=True alone decodes using the OS locale encoding -- cp1252 on this Windows
    # setup, which crashes on any non-ASCII output (accented names, em dashes, (R), etc).
    result = subprocess.run([*GWS_CMD, *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"gws {' '.join(args)} failed: {result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


DATE_RE = re.compile(r"\b(19|20)\d{2}\b")

# The master ATS resume's section names; the markdown keeps the short keys the validator and
# the no-fabrication checker read (## Summary / ## Skills / ## Experience).
SECTION_TITLES = {"SUMMARY": "PROFESSIONAL SUMMARY", "SKILLS": "TECHNICAL SKILLS",
                  "EXPERIENCE": "PROFESSIONAL EXPERIENCE"}

# Master-resume layout (Akhil_Dalali_Master_ATS_Full_Stack_Resume_Section_Lines.pdf): A4, one page.
FONT = "Arial"
BODY_PT = 9
MARGIN_PT = 40  # ~0.55in left/right
MARGIN_TB_PT = 26  # ~0.36in top/bottom
A4 = {"width": {"magnitude": 595.28, "unit": "PT"}, "height": {"magnitude": 841.89, "unit": "PT"}}
LINE_SPACING = 104  # percent
BULLET_INDENT_PT = 14
SIZES = {"NAME": 20, "HEADLINE": 12, "CONTACT": 9, "SECTION": 13}
RULE = {"color": {"color": {"rgbColor": {"red": 0.75, "green": 0.75, "blue": 0.75}}},
        "width": {"magnitude": 0.75, "unit": "PT"}, "padding": {"magnitude": 2, "unit": "PT"},
        "dashStyle": "SOLID"}


def pt(n):
    return {"magnitude": n, "unit": "PT"}


def classify(line, prev_type, section=None):
    if line.startswith("# "):
        return "NAME", line[2:].strip()
    if line.startswith("## "):
        name = line[3:].strip().upper()
        return "SECTION", SECTION_TITLES.get(name, name)
    if line.startswith("### "):
        return "JOB_TITLE", line[4:].strip()
    if section is None and prev_type is not None:  # header block, under the name
        text = line.strip()
        if prev_type == "NAME" and "@" not in text and "linkedin.com" not in text.lower():
            return "HEADLINE", text
        return "CONTACT", text
    if line.startswith("- "):
        if section == "SKILLS" and ":" in line:
            return "SKILL", line[2:].strip()
        return "BULLET", line[2:].strip()
    if section == "SKILLS" and ":" in line:
        return "SKILL", line.strip()
    if prev_type in ("JOB_TITLE", "POSITION") and DATE_RE.search(line):
        return "DATE", line.strip()
    if prev_type == "JOB_TITLE":
        return "POSITION", line.strip()
    return "PLAIN", line.strip()


def parse(markdown_text):
    """Returns list of (type, text) blocks, blank lines dropped."""
    blocks = []
    prev_type = None
    section = None
    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("## "):
            section = line[3:].strip().upper()
        block_type, text = classify(line, prev_type, section)
        blocks.append((block_type, text))
        prev_type = block_type
    return blocks


def build_requests(blocks):
    """Assembles full plain text + style/bullet requests using tracked char offsets."""
    text_parts = []
    style_spans = []  # (start, end, text_style)
    para_spans = []  # (start, end, paragraph_style)
    bullet_ranges = []  # (start, end) contiguous bullet runs
    offset = 0
    current_bullet_start = None

    def append(s):
        nonlocal offset
        start = offset
        text_parts.append(s)
        offset += len(s)
        return start, offset

    for block_type, text in blocks:
        if block_type != "BULLET" and current_bullet_start is not None:
            bullet_ranges.append((current_bullet_start, offset))
            current_bullet_start = None

        start, end = append(text + "\n")
        if block_type in ("NAME", "HEADLINE", "CONTACT"):
            style = {"fontSize": pt(SIZES[block_type])}
            if block_type == "NAME":
                style["bold"] = True
            style_spans.append((start, end - 1, style))
            para_spans.append((start, end, {"alignment": "CENTER"}))
        elif block_type == "SECTION":
            style_spans.append((start, end - 1, {"bold": True, "fontSize": pt(SIZES["SECTION"])}))
            para_spans.append((start, end, {"borderBottom": RULE, "spaceAbove": pt(6),
                                            "spaceBelow": pt(3)}))
        elif block_type == "JOB_TITLE":
            style_spans.append((start, end - 1, {"bold": True}))
            para_spans.append((start, end, {"spaceAbove": pt(3)}))
        elif block_type == "POSITION":
            style_spans.append((start, end - 1, {"bold": True}))
        elif block_type == "SKILL":
            label = text.find(":")
            if label > 0:
                style_spans.append((start, start + label + 1, {"bold": True}))
        elif block_type == "BULLET":
            if current_bullet_start is None:
                current_bullet_start = start

    if current_bullet_start is not None:
        bullet_ranges.append((current_bullet_start, offset))

    # A new Doc's body already ends in a newline; ours would leave an empty last paragraph,
    # which can spill onto a blank second page.
    full_text = "".join(text_parts)[:-1]

    requests = [
        {"insertText": {"location": {"index": 1}, "text": full_text}},
        {"updateDocumentStyle": {
            "documentStyle": {"pageSize": A4, "marginTop": pt(MARGIN_TB_PT), "marginBottom": pt(MARGIN_TB_PT),
                              "marginLeft": pt(MARGIN_PT), "marginRight": pt(MARGIN_PT)},
            "fields": "pageSize,marginTop,marginBottom,marginLeft,marginRight"}},
        # Base style for the whole body; the per-block spans below override it.
        {"updateTextStyle": {
            "range": {"startIndex": 1, "endIndex": len(full_text) + 1},
            "textStyle": {"weightedFontFamily": {"fontFamily": FONT}, "fontSize": pt(BODY_PT),
                          "bold": False, "italic": False},
            "fields": "weightedFontFamily,fontSize,bold,italic"}},
        {"updateParagraphStyle": {
            "range": {"startIndex": 1, "endIndex": len(full_text) + 1},
            "paragraphStyle": {"lineSpacing": LINE_SPACING, "spaceAbove": pt(0), "spaceBelow": pt(0)},
            "fields": "lineSpacing,spaceAbove,spaceBelow"}},
    ]

    for start, end, style in style_spans:
        requests.append({
            "updateTextStyle": {
                "range": {"startIndex": start + 1, "endIndex": end + 1},
                "textStyle": style,
                "fields": ",".join(style.keys()),
            }
        })

    for start, end, style in para_spans:
        requests.append({
            "updateParagraphStyle": {
                "range": {"startIndex": start + 1, "endIndex": end + 1},
                "paragraphStyle": style,
                "fields": ",".join(style.keys()),
            }
        })

    for start, end in bullet_ranges:
        requests.append({
            "createParagraphBullets": {
                "range": {"startIndex": start + 1, "endIndex": end + 1},
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
            }
        })
        # Shallow hanging indent like the master (Docs' default bullet indent is a full 36pt).
        requests.append({
            "updateParagraphStyle": {
                "range": {"startIndex": start + 1, "endIndex": end + 1},
                "paragraphStyle": {"indentStart": pt(BULLET_INDENT_PT), "indentFirstLine": pt(4)},
                "fields": "indentStart,indentFirstLine",
            }
        })

    return requests


if __name__ == "__main__":
    markdown_path, document_id = sys.argv[1], sys.argv[2]
    markdown_text = open(markdown_path, encoding="utf-8").read()
    blocks = parse(markdown_text)
    requests = build_requests(blocks)
    gws("docs", "documents", "batchUpdate", "--params",
        json.dumps({"documentId": document_id}),
        "--json", json.dumps({"requests": requests}))
    print(f"Formatted {document_id} from {markdown_path}")
