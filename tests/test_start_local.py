"""start_local.ps1 (repository root): path resolution and command construction, via its -DryRun mode.
Windows PowerShell only; skipped elsewhere. Nothing is started."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "start_local.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@unittest.skipUnless(POWERSHELL and os.name == "nt", "needs Windows PowerShell")
class StartLocalDryRun(unittest.TestCase):
    def dry_run(self, *args, env=None):
        with tempfile.TemporaryDirectory() as cwd:   # not the repo: paths must come from the script's location
            r = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                                "-DryRun", *args], cwd=cwd, capture_output=True, text=True, timeout=60,
                               env={**os.environ, **(env or {})})
        self.assertEqual(r.returncode, 0, r.stderr)
        return dict(line.split(":", 1) if ":" in line and not line.startswith("LOCAL_MODE") else (line, "")
                    for line in r.stdout.splitlines() if line.strip())

    def test_resolves_the_repo_root_and_both_commands_from_any_directory(self):
        out = self.dry_run("-Python", sys.executable)
        self.assertEqual(Path(out["RepoRoot"].strip()), ROOT)
        self.assertIn(str(ROOT / "scripts" / "run_pipeline.py"), out["Pipeline"])
        self.assertIn(str(ROOT / "scripts" / "dashboard_server.py"), out["Dashboard"])
        self.assertTrue(out["Dashboard"].rstrip().endswith("--port 8765"))
        self.assertIn("LOCAL_MODE=true", out)
        self.assertIn(sys.executable, out["Python"])

    def test_options(self):
        out = self.dry_run("-Python", sys.executable, "-Port", "8799", "-NoPipeline")
        self.assertNotIn("Pipeline", out)
        self.assertTrue(out["Dashboard"].rstrip().endswith("--port 8799"))

    def test_missing_python_fails_clearly(self):
        with tempfile.TemporaryDirectory() as cwd:
            r = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                                "-DryRun", "-Python", str(Path(cwd) / "no-such-python.exe")],
                               cwd=cwd, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("could not be run", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
