"""An open-month preview (steps 10 and 11 --preview-open-month, evidence-network/loop#242) never reaches a public
surface or main: nothing that builds the site, the stats pages, the reports or the baseline reads the preview
directory, and a builder handed one anyway refuses it, the way the ecosystem gate does."""
import contextlib
import io
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("steps", "scripts", "status", "site"):
    sys.path.insert(0, str(ROOT / sub))
import build  # noqa: E402
import finding  # noqa: E402
import status_page  # noqa: E402
from tests.test_site import DATA, make_findings, run_build  # noqa: E402

# What builds a public surface, the reports or the baseline.
BUILDERS = [*sorted((ROOT / "site").glob("*.py")), *sorted((ROOT / "status").glob("*.py")),
            *(ROOT / "scripts" / n for n in ("daily_report.py", "check_reports.py", "scrub_reports.py",
                                              "ecosystem_gate.py", "history_cards.py", "report_layout.py"))]


def preview_run(parent, name="2026-10-31-live"):
    """A run directory as step 10 writes it under --preview-open-month, with a live population."""
    d = parent / name / "10-judge"
    d.mkdir(parents=True)
    (d / "p.txt").write_text("proof\n")
    finding.write(d, result="PASS", population="live", asserts="a fact holds", pinned=[], proof=["p.txt"],
                  detail=finding.PREVIEW + ". run r, the open month", started=finding.now())
    return d.parent


class PreviewIsolationTest(unittest.TestCase):
    def test_preview_is_gitignored_and_nothing_under_it_is_tracked(self):
        self.assertIn("/preview/", (ROOT / ".gitignore").read_text().splitlines())
        try:
            tracked = subprocess.run(["git", "ls-files", "--", "preview"], cwd=ROOT, capture_output=True, text=True,
                                     check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        self.assertEqual(tracked, [])

    def test_no_builder_lists_the_repository_root_or_names_the_preview_directory(self):
        for path in BUILDERS:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bROOT\s*(?:/\s*\"[^\"]*\")?\s*\.(?:iterdir|glob|rglob)\(|os\.walk\(\s*ROOT", text),
                              f"{path.name} lists a directory under the repository root")
            self.assertNotIn("preview/", text.replace("PREVIEW/", ""), f"{path.name} names a preview directory")

    def test_the_site_is_built_from_the_findings_it_is_given_and_not_from_a_preview_beside_them(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            make_findings(d / "findings")
            preview_run(d / "preview")
            code, err = run_build(DATA, d / "findings", d / "_site")
            self.assertEqual(code, 0, err)
            for page in (d / "_site").rglob("*"):
                if page.is_file():
                    text = page.read_bytes().decode("utf-8", "replace")
                    self.assertNotIn(finding.PREVIEW, text, page.name)
                    self.assertNotIn("2026-10-31-live", text, page.name)

    def test_the_status_page_and_the_site_refuse_a_findings_directory_holding_a_preview(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            preview_run(d / "findings")
            with self.assertRaises(status_page.StatusError) as e:
                status_page.build(d / "findings", ROOT / "versions.lock", d / "status")
            self.assertIn("an open-month preview", str(e.exception))
            self.assertFalse((d / "status").exists())
            code, err = run_build(DATA, d / "findings", d / "_site")
            self.assertEqual(code, 1, err)
            self.assertFalse((d / "_site").exists())


if __name__ == "__main__":
    unittest.main()
