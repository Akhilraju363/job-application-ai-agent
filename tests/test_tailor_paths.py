import re
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock

from fixtures import BASE  # noqa: F401 -- must be first of the project imports: disables .env loading

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import paths  # noqa: E402
import resume_role  # noqa: E402
import tailor_job  # noqa: E402


class SafePathComponentTests(unittest.TestCase):
    def test_company_colon_keeps_a_readable_component(self):
        source = "RunLoyal - Pet Software-sr-full-stack-engineer"
        self.assertEqual(paths.safe_path_component("RunLoyal: Pet Software-sr-full-stack-engineer"), source)

        folder = tailor_job.job_folder_name("RunLoyal: Pet Software", "Sr Full Stack Engineer")
        self.assertTrue(folder.startswith(source + "-"))
        self.assertEqual(folder, tailor_job.job_folder_name("RunLoyal: Pet Software", "Sr Full Stack Engineer"))
        self.assertNotIn(":", folder)

    def test_all_windows_invalid_and_control_characters_are_replaced(self):
        invalid = '<>:"/\\|?*'
        component = paths.safe_path_component(invalid + "\x00\x1f\x7f\x9f")
        self.assertFalse(any(char in component for char in invalid))
        self.assertFalse(any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in component))

    def test_reserved_windows_device_names_are_not_emitted(self):
        reserved = ["CON", "PRN", "AUX", "NUL"]
        reserved += [f"COM{index}" for index in range(1, 10)]
        reserved += [f"LPT{index}" for index in range(1, 10)]
        reserved += ["con.txt", "NUL.pdf"]
        device_name = re.compile(
            r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)
        for value in reserved:
            with self.subTest(value=value):
                self.assertIsNone(device_name.fullmatch(paths.safe_path_component(value)))

    def test_empty_whitespace_and_long_components_are_bounded(self):
        self.assertEqual(paths.safe_path_component(""), "item")
        self.assertEqual(paths.safe_path_component(" \t\r\n "), "item")
        long_name = paths.safe_path_component("Resume😀" * 100)
        self.assertLessEqual(len(long_name.encode("utf-16-le")) // 2, paths.MAX_FILESYSTEM_COMPONENT_UNITS)
        self.assertIn("-", long_name)

    def test_distinct_company_job_pairs_do_not_collide_after_sanitization(self):
        first = tailor_job.job_folder_name("Acme: AI", "Developer")
        second = tailor_job.job_folder_name("Acme - AI", "Developer")
        self.assertNotEqual(first, second)
        self.assertEqual(first.split("-")[:-1], second.split("-")[:-1])

        case_variant = tailor_job.job_folder_name("ACME: AI", "Developer")
        self.assertNotEqual(first.casefold(), case_variant.casefold())

    def test_existing_resume_filename_convention_is_preserved(self):
        markdown = "# Akhil Dalali\nJava Developer\n"
        self.assertEqual(resume_role.export_filename(markdown, "pdf", "Java Developer"),
                         "Akhil_Dalali_Java_Developer.pdf")

    def test_temporary_pdf_path_uses_windows_and_posix_path_flavors(self):
        roots = (
            PureWindowsPath(r"C:\Users\candidate\AppData\Local\Temp"),
            PurePosixPath("/var/tmp/job-agent"),
        )
        for root in roots:
            with self.subTest(root=root):
                result = tailor_job.temporary_pdf_path(
                    root, "RunLoyal: Pet Software-sr-full-stack-engineer", "Akhil: Java.pdf")
                self.assertIsInstance(result, type(root))
                self.assertEqual(result.parents[1], root)
                self.assertEqual(result.parent.name, "RunLoyal - Pet Software-sr-full-stack-engineer")
                self.assertEqual(result.name, "Akhil - Java.pdf")
                self.assertNotIn(":", result.parent.name + result.name)


class TemporaryUploadTests(unittest.TestCase):
    def setUp(self):
        self.source_dir = tempfile.TemporaryDirectory(prefix="resume-artifact-test-")
        self.source_root = Path(self.source_dir.name)
        self.source_pdf = self.source_root / "generated_resumes" / "0123456789ab" / "v1.pdf"
        self.source_pdf.parent.mkdir(parents=True)
        self.source_pdf.write_bytes(b"%PDF-verified")

    def tearDown(self):
        self.source_dir.cleanup()

    def test_upload_temp_files_are_removed_after_success(self):
        captured = {}

        def fake_upload(path, folder_id):
            captured["path"] = path
            captured["temp_root"] = path.parents[1]
            self.assertEqual(folder_id, "mock-folder")
            self.assertEqual(path.read_bytes(), b"%PDF-verified")
            return "mock-url"

        with mock.patch.object(tailor_job, "upload_pdf", side_effect=fake_upload):
            result = tailor_job.upload_verified_pdf(
                self.source_pdf, "mock-folder", "RunLoyal - Pet Software-sr-full-stack-engineer",
                "Akhil_Dalali_Java_Developer.pdf")

        self.assertEqual(result, "mock-url")
        self.assertFalse(captured["temp_root"].exists())
        self.assertTrue(self.source_pdf.is_file())
        self.assertNotIn(self.source_root, captured["path"].parents)

    def test_upload_temp_files_are_removed_after_exception(self):
        captured = {}

        def failing_upload(path, _folder_id):
            captured["path"] = path
            captured["temp_root"] = path.parents[1]
            self.assertTrue(path.is_file())
            raise RuntimeError("mock upload failure")

        with mock.patch.object(tailor_job, "upload_pdf", side_effect=failing_upload):
            with self.assertRaisesRegex(RuntimeError, "mock upload failure"):
                tailor_job.upload_verified_pdf(
                    self.source_pdf, "mock-folder", "RunLoyal - Pet Software-sr-full-stack-engineer",
                    "Akhil_Dalali_Java_Developer.pdf")

        self.assertFalse(captured["temp_root"].exists())
        self.assertTrue(self.source_pdf.is_file())


if __name__ == "__main__":
    unittest.main()
