import sys
import tempfile
import unittest
from pathlib import Path

from fixtures import BASE

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import resume_renderer


class ResumeRendererTests(unittest.TestCase):
    def test_pdf_and_docx_render_resume_content_without_google_docs(self):
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        with tempfile.TemporaryDirectory() as tmp:
            markdown = Path(tmp) / "resume.md"
            markdown.write_text(BASE, encoding="utf-8")
            pdf, docx = Path(tmp) / "resume.pdf", Path(tmp) / "resume.docx"
            resume_renderer.render(markdown, pdf, "application/pdf")
            resume_renderer.render(markdown, docx, docx_mime)

            content = pdf.read_bytes()
            self.assertTrue(content.startswith(b"%PDF-"))
            self.assertIn(b"%%EOF", content[-2048:])

            from docx import Document
            text = "\n".join(p.text for p in Document(str(docx)).paragraphs)
            self.assertIn("Software Engineer", text)
            self.assertIn("Infinite Computer Solutions", text)

    def test_rejects_unknown_export_type(self):
        with self.assertRaises(ValueError):
            resume_renderer.render("unused.md", "unused.bin", "application/octet-stream")


if __name__ == "__main__":
    unittest.main()
