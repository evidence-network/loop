"""scripts/test_impact.py: which unit test modules a change can affect (evidence-network/loop#489)."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import test_impact  # noqa: E402


class ThisRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.repo = test_impact.Repo(test_impact.git("ls-files").split())

    def test_every_test_module_depends_on_code_outside_tests(self):
        # A module that depends on nothing outside tests/ would never be selected by a change to what it tests.
        unmapped = [m for m in test_impact.modules(self.repo)
                    if not [d for d in self.repo.deps(m) if not d.startswith("tests/")]]
        self.assertEqual(unmapped, [])

    def test_a_step_change_selects_that_steps_tests(self):
        picked, _ = test_impact.select(self.repo, ["steps/05-deal"])
        self.assertIsInstance(picked, list)
        self.assertIn("test_step05_deal", picked)


class SelectionTest(unittest.TestCase):
    """A small repository of its own, with test_impact pointed at it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.root)])
        files = {
            "scripts/mod_a.py": ('import subprocess\nfrom pathlib import Path\nHERE = Path(__file__).parent\nRUN = ["steps/s1"]\n'
                                 'NOTE = "see scripts/mod_c.py for why"\nT = HERE / "page.html"\n'),
            "scripts/page.html": "<p>\n",
            "scripts/mod_b.py": "X = 1\n",
            "scripts/mod_c.py": "Y = 2\n",
            "steps/s1": "#!/usr/bin/env python3\nimport mod_b\n",
            "steps/s2": "#!/bin/sh\n# scripts/mod_c.py is not run here\necho \"run scripts/mod_c.py yourself\"\nexit 0\n",
            "tests/test_a.py": "import mod_a\n",
            "tests/test_b.py": 'from pathlib import Path\nROOT = Path(".")\nD = ROOT / "data" / "day1"\nS = ROOT / "steps" / "s2"\n',
            "data/day1/f.json": "{}\n",
            "data/day2/f.json": "{}\n",
            "docs/x.md": "steps/s1\n",
            "versions.lock": "x 1\n",
        }
        for rel, body in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
            if rel.startswith("steps/"):
                p.chmod(0o755)
        for c in (["init", "-q"], ["add", "."]):
            subprocess.run(["git", "-C", str(self.root), *c], check=True, capture_output=True)
        old = test_impact.ROOT
        test_impact.ROOT = self.root
        self.addCleanup(setattr, test_impact, "ROOT", old)
        self.repo = test_impact.Repo(test_impact.git("ls-files").split())

    def pick(self, *paths):
        return test_impact.select(self.repo, list(paths))[0]

    def test_an_import_and_what_the_imported_code_runs_select_the_module(self):
        self.assertEqual(self.pick("scripts/mod_a.py"), ["test_a"])
        self.assertEqual(self.pick("steps/s1"), ["test_a"])  # mod_a names steps/s1
        self.assertEqual(self.pick("scripts/mod_b.py"), ["test_a"])  # steps/s1 imports mod_b

    def test_a_path_in_a_message_or_a_comment_is_not_a_dependency(self):
        self.assertEqual(self.pick("scripts/mod_c.py"), "ALL")  # named only in messages and comments: no module
        self.assertNotIn("scripts/mod_c.py", self.repo.deps("tests/test_b.py"))

    def test_a_pathlib_chain_names_a_file_or_a_directory(self):
        self.assertEqual(self.pick("steps/s2"), ["test_b"])
        self.assertEqual(self.pick("data/day1/f.json"), ["test_b"])
        self.assertEqual(self.pick("data/day2/f.json"), [])  # data no module names

    def test_a_document_no_module_names_selects_nothing(self):
        self.assertEqual(self.pick("docs/x.md"), [])

    def test_a_code_file_no_module_depends_on_selects_everything(self):
        self.assertEqual(self.pick("scripts/new.py"), "ALL")

    def test_the_pins_and_the_selection_itself_select_everything(self):
        self.assertEqual(self.pick("versions.lock"), "ALL")
        self.assertEqual(self.pick("scripts/test_impact.py"), "ALL")
        self.assertEqual(self.pick("scripts/unittest_parallel.py"), "ALL")

    def test_the_test_module_itself_selects_itself(self):
        self.assertEqual(self.pick("tests/test_a.py"), ["test_a"])

    def test_a_file_named_beside_the_code_that_reads_it_is_a_dependency(self):
        self.assertEqual(self.pick("scripts/page.html"), ["test_a"])  # mod_a reads HERE / "page.html"

    def test_a_deleted_file_keeps_its_dependents_and_an_unknown_deleted_program_runs_everything(self):
        files = set(test_impact.git("ls-files").split()) | {"steps/old"}
        for rel in ("steps/s1",):
            (self.root / rel).unlink()
            subprocess.run(["git", "-C", str(self.root), "rm", "-q", "--cached", rel], check=True)
        repo = test_impact.Repo(files)  # the base's files, as main() builds it
        self.assertEqual(test_impact.select(repo, ["steps/s1"])[0], ["test_a"])
        self.assertEqual(test_impact.select(repo, ["steps/old"])[0], "ALL")


class CommandTest(unittest.TestCase):
    def test_loop_tests_all_runs_everything(self):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "test_impact.py")], capture_output=True,
                           text=True, env={**os.environ, "LOOP_TESTS": "all"}, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "ALL")

    def test_a_base_that_does_not_exist_runs_everything(self):
        env = {k: v for k, v in os.environ.items() if k != "LOOP_TESTS"}
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "test_impact.py"), "--base", "no/such-ref"],
                           capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "ALL")


if __name__ == "__main__":
    unittest.main()
