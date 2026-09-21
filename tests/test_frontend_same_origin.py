"""The UI must only ever call its own origin (relative /api/... paths): the Modal deployment
serves web/ and the API from one URL, with no CORS and no second backend.

Stdlib only: python -m unittest discover -s tests -v
"""
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
SOURCES = [p for pattern in ("*.js", "*.html", "*.css") for p in WEB.rglob(pattern)
           if "tests" not in p.relative_to(WEB).parts]

FORBIDDEN = ("localhost", "127.0.0.1", "0.0.0.0", ":8765", "modal.run", "ngrok", "github.io")
# absolute URLs that are not network calls: the SVG XML namespace, and URL-field hints/validation text
ALLOWED_ABSOLUTE = ("http://www.w3.org/", "http:// or https://")


class FrontendSameOrigin(unittest.TestCase):
    def test_sources_were_found(self):
        self.assertGreater(len(SOURCES), 10)

    def test_no_dev_host_port_or_backend_url_is_hard_coded(self):
        for p in SOURCES:
            text = p.read_text(encoding="utf-8")
            for bad in FORBIDDEN:
                self.assertNotIn(bad, text, f"{p.relative_to(WEB)} hard-codes {bad}")

    def test_no_absolute_url_is_fetched_or_loaded(self):
        for p in SOURCES:
            for line in p.read_text(encoding="utf-8").splitlines():
                for m in re.finditer(r"https?://[^\s'\"`)<>]*", line):
                    if any(m.group(0).startswith(a) for a in ALLOWED_ABSOLUTE) or line.strip().startswith(("//", "if (v.url")):
                        continue
                    if "placeholder" in line or "https?:" in line:  # input hint / URL-validation regex
                        continue
                    self.fail(f"{p.relative_to(WEB)}: unexpected absolute URL {m.group(0)!r} in: {line.strip()}")

    def test_every_fetch_goes_through_a_relative_path(self):
        found = 0
        for p in (WEB / "js").rglob("*.js"):
            for m in re.finditer(r"\bfetch\(\s*([^,)]+)", p.read_text(encoding="utf-8")):
                found += 1
                arg = m.group(1).strip()
                self.assertTrue(arg == "path" or arg.startswith(("'/api/", '"/api/', "`/api/")),
                                f"{p.relative_to(WEB)}: fetch({arg}) is not same-origin")
        self.assertGreater(found, 0)

    def test_api_callers_use_root_relative_api_paths(self):
        found = 0
        for p in (WEB / "js").rglob("*.js"):
            for m in re.finditer(r"api\.(?:get|post|put|patch)\(\s*[`'\"]([^`'\"]+)", p.read_text(encoding="utf-8")):
                found += 1
                self.assertTrue(m.group(1).startswith("/api/"), f"{p.name}: {m.group(1)}")
        self.assertGreater(found, 0)

    def test_page_assets_are_root_relative(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)="([^"]+)"', html)
        self.assertTrue(refs)
        for ref in refs:
            self.assertFalse(ref.startswith(("http://", "https://")), ref)

    def test_token_is_never_in_frontend_source(self):
        for p in SOURCES:
            self.assertNotRegex(p.read_text(encoding="utf-8"), r"DASHBOARD_TOKEN\s*[:=]\s*['\"]\w", p.name)


if __name__ == "__main__":
    unittest.main()
