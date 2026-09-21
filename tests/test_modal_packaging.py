"""Regression test for the 2026-09-21 Modal crash loop:

    /root/modal_app.py, line 17: from job_links import canonical_link
    ModuleNotFoundError: No module named 'job_links'

modal_app.py added <its own dir>/scripts to sys.path. That works from a checkout, but in the
Modal container modal_app.py is auto-mounted alone at /root/, so /root/scripts does not exist;
the image copies scripts/ to /app/scripts instead. `import modal_app` therefore failed at
container startup and run_pipeline crash-looped, while every local test (which has scripts/
next to modal_app.py) stayed green.

These tests reproduce the container layout -- modal_app.py in a directory with no scripts/
sibling, the only copy of scripts/ at the image's remote path -- so that gap can't reopen.
The `modal` package is stubbed, so this runs without it installed.

Stdlib only (no pytest): python -m unittest discover -s tests -v
"""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
MODAL_APP = ROOT / "modal_app.py"

# Fresh interpreter, so nothing the test runner put on sys.path can mask a missing module.
IMPORT_SNIPPET = """
import sys
from unittest import mock
sys.modules["modal"] = mock.MagicMock()   # stub: the real package isn't needed to test imports
import modal_app
from job_links import canonical_link
assert modal_app.canonical_link is canonical_link
print("IMPORT_OK", canonical_link("https://example.com/job?id=123&trackingId=x"))
"""


class ModalContainerImport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # "/root" in the container: modal_app.py by itself, no scripts/ next to it.
        self.container_root = self.tmp / "root"
        self.container_root.mkdir()
        self.image_scripts = self.tmp / "app_scripts"  # stands in for /app/scripts

    def _simulate_container(self, scripts_copied):
        src = MODAL_APP.read_text(encoding="utf-8")
        remote = 'SCRIPTS_REMOTE_PATH = "/app/scripts"'
        self.assertIn(remote, src, "modal_app.py must keep SCRIPTS_REMOTE_PATH")
        (self.container_root / "modal_app.py").write_text(
            src.replace(remote, f"SCRIPTS_REMOTE_PATH = {str(self.image_scripts)!r}"),
            encoding="utf-8",
        )
        if scripts_copied:
            shutil.copytree(SCRIPTS, self.image_scripts, ignore=shutil.ignore_patterns("__pycache__"))
        return subprocess.run(
            [sys.executable, "-c", IMPORT_SNIPPET],
            cwd=self.container_root, capture_output=True, text=True, timeout=60,
            env={"PATH": "", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
        )

    def test_modal_app_imports_when_only_the_image_copy_of_scripts_exists(self):
        r = self._simulate_container(scripts_copied=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("IMPORT_OK https://example.com/job", r.stdout)

    def test_harness_detects_the_original_failure(self):
        # Negative control: with no scripts/ anywhere reachable, the container-style import
        # must fail with the exact production error -- proving the test above isn't vacuous.
        r = self._simulate_container(scripts_copied=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("No module named 'job_links'", r.stderr)


class ModalImagePackaging(unittest.TestCase):
    def test_job_links_is_in_the_directory_the_image_copies(self):
        self.assertTrue((SCRIPTS / "job_links.py").is_file())

    def test_sys_path_and_image_copy_share_one_remote_path(self):
        src = MODAL_APP.read_text(encoding="utf-8")
        self.assertRegex(src, r'add_local_dir\(\s*"scripts",\s*remote_path=SCRIPTS_REMOTE_PATH\s*\)')
        self.assertRegex(src, r"Path\(SCRIPTS_REMOTE_PATH\)")

    def test_canonical_link_is_imported_not_reimplemented(self):
        src = MODAL_APP.read_text(encoding="utf-8")
        self.assertRegex(src, re.compile(r"^from job_links import canonical_link", re.M))
        self.assertNotRegex(src, r"def canonical_link")


if __name__ == "__main__":
    unittest.main()
