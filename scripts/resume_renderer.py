"""Render verified resume Markdown locally without creating a Google Drive document."""
from pathlib import Path
from xml.sax.saxutils import escape

from format_resume_doc import parse


def _pdf(markdown_path, out_path):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    common = {"fontName": "Helvetica", "fontSize": 9, "leading": 9.36, "spaceAfter": 0}
    body = ParagraphStyle("ResumeBody", parent=styles["Normal"], **common)
    centered = ParagraphStyle("ResumeCentered", parent=body, alignment=TA_CENTER)
    name_style = ParagraphStyle("ResumeName", parent=centered, fontName="Helvetica-Bold", fontSize=20, leading=21)
    headline_style = ParagraphStyle("ResumeHeadline", parent=centered, fontSize=12, leading=13)
    contact_style = ParagraphStyle("ResumeContact", parent=centered, fontSize=9, leading=10)
    section_style = ParagraphStyle("ResumeSection", parent=body, fontName="Helvetica-Bold",
                                   fontSize=13, leading=14, spaceBefore=6, spaceAfter=2)
    title_style = ParagraphStyle("ResumeTitle", parent=body, fontName="Helvetica-Bold", spaceBefore=3)
    position_style = ParagraphStyle("ResumePosition", parent=body, fontName="Helvetica-Bold")
    bullet_style = ParagraphStyle("ResumeBullet", parent=body, leftIndent=14, firstLineIndent=-10)
    story = []
    for kind, text in parse(Path(markdown_path).read_text(encoding="utf-8")):
        content = escape(text)
        if kind == "NAME":
            story.append(Paragraph(content, name_style))
        elif kind == "HEADLINE":
            story.append(Paragraph(content, headline_style))
        elif kind == "CONTACT":
            story.append(Paragraph(content, contact_style))
        elif kind == "SECTION":
            story.extend((Spacer(1, 3), Paragraph(content, section_style),
                          HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#bfbfbf"),
                                     spaceBefore=0, spaceAfter=3)))
        elif kind == "JOB_TITLE":
            story.append(Paragraph(content, title_style))
        elif kind == "POSITION":
            story.append(Paragraph(content, position_style))
        elif kind == "BULLET":
            story.append(Paragraph(f"&#8226;&nbsp;{content}", bullet_style))
        elif kind == "SKILL":
            label, separator, remainder = text.partition(":")
            story.append(Paragraph(f"<b>{escape(label)}:</b>{escape(remainder)}", body))
        elif content:
            story.append(Paragraph(content, body))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(out), pagesize=A4, leftMargin=40, rightMargin=40,
                      topMargin=26, bottomMargin=26, title="Tailored Resume").build(story)


def _docx(markdown_path, out_path):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document = Document()
    section = document.sections[0]
    section.page_width = Inches(8.27)
    section.page_height = Inches(11.69)
    section.left_margin = section.right_margin = Inches(40 / 72)
    section.top_margin = section.bottom_margin = Inches(26 / 72)
    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(9)
    normal.paragraph_format.line_spacing = 1.04
    normal.paragraph_format.space_after = Pt(0)

    for kind, text in parse(Path(markdown_path).read_text(encoding="utf-8")):
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(0)
        if kind == "NAME":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(text)
            run.bold = True
            run.font.size = Pt(20)
        elif kind == "HEADLINE":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(text)
            run.font.size = Pt(12)
        elif kind == "CONTACT":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif kind == "SECTION":
            paragraph.paragraph_format.space_before = Pt(6)
            paragraph.paragraph_format.space_after = Pt(3)
            run = paragraph.add_run(text.upper())
            run.bold = True
            run.font.size = Pt(13)
            borders = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:color"), "BFBFBF")
            borders.append(bottom)
            paragraph._p.get_or_add_pPr().append(borders)
        elif kind in ("JOB_TITLE", "POSITION"):
            paragraph.paragraph_format.space_before = Pt(3) if kind == "JOB_TITLE" else Pt(0)
            paragraph.add_run(text).bold = True
        elif kind == "BULLET":
            paragraph.paragraph_format.left_indent = Inches(14 / 72)
            paragraph.paragraph_format.first_line_indent = Inches(-10 / 72)
            paragraph.add_run("\u2022  " + text)
        elif kind == "SKILL":
            label, separator, remainder = text.partition(":")
            paragraph.add_run(label + separator).bold = bool(separator)
            paragraph.add_run(remainder)
        else:
            paragraph.add_run(text)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(out))


def render(markdown_path, out_path, mime_type):
    if mime_type == "application/pdf":
        _pdf(markdown_path, out_path)
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        _docx(markdown_path, out_path)
    else:
        raise ValueError(f"Unsupported resume export type: {mime_type}")
