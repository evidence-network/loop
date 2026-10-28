"""Tests for .github/leak_lint.py: each rule shown failing on a planted mutant, then passing.

Each test runs the lint on a throwaway `git init` tree. The term list is a secret in CI, so
these tests supply SYNTHETIC classes and terms through LEAK_LINT_TERMS; none is a real entry.

Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINT = ROOT / ".github" / "leak_lint.py"

#: Synthetic term classes. Real class names and terms live only in the CI secret.
TERMS = {
    "class-alpha": ["SYNTHETIC_QUEUE_WORD", "synthetic-buffer"],
    "class-beta": ["/synthetic/private/path"],
}


def run_lint(root, terms=TERMS, reveal=True, lint=LINT):
    env = {k: v for k, v in os.environ.items() if not k.startswith("LEAK_LINT_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if terms is not None:
        env["LEAK_LINT_TERMS"] = terms if isinstance(terms, str) else json.dumps(terms)
    if reveal:
        env["LEAK_LINT_REVEAL"] = "1"
    return subprocess.run([sys.executable, "-B", str(lint), str(root)],
                          capture_output=True, text=True, env=env)


class Repo:
    def __init__(self, base):
        self.path = Path(base) / "repo"
        self.path.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.path, check=True, capture_output=True)

    def write(self, rel, text):
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "test")


class LeakLintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repo(self.tmp.name)

    def test_planted_term_fails_then_passes(self):
        doc = self.repo.write("reports/README.md", "a line with synthetic-buffer in it\n")
        self.repo.commit()
        red = run_lint(self.repo.path)
        self.assertEqual(red.returncode, 1, red.stdout)
        self.assertIn("class-alpha", red.stdout)
        self.assertIn("reports/README.md:1", red.stdout)

        doc.write_text("a clean line\n")
        self.repo.commit()
        green = run_lint(self.repo.path)
        self.assertEqual(green.returncode, 0, green.stdout)
        self.assertIn("leak-lint: clean.", green.stdout)

    def test_term_in_a_node_entry_and_a_workflow(self):
        self.repo.write("nodes/x.json", '{"host_build": "/synthetic/private/path"}\n')
        self.repo.write(".github/workflows/x.yml", "run: echo SYNTHETIC_QUEUE_WORD\n")
        self.repo.commit()
        result = run_lint(self.repo.path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("nodes/x.json:1:class-beta", result.stdout)
        self.assertIn(".github/workflows/x.yml:1:class-alpha", result.stdout)

    def test_files_without_a_suffix_are_scanned(self):
        self.repo.write("reports/daily/2026-01-01/MISSED", "SYNTHETIC_QUEUE_WORD\n")
        self.repo.commit()
        self.assertEqual(run_lint(self.repo.path).returncode, 1)

    def test_match_is_case_sensitive(self):
        self.repo.write("README.md", "synthetic_queue_word in another casing\n")
        self.repo.commit()
        self.assertEqual(run_lint(self.repo.path).returncode, 0)

    def test_uncommitted_file_is_not_scanned(self):
        self.repo.write("README.md", "clean\n")
        self.repo.commit()
        self.repo.write("scratch.md", "synthetic-buffer\n")
        self.assertEqual(run_lint(self.repo.path).returncode, 0)

    def test_bracketed_id_fails_then_passes(self):
        doc = self.repo.write("NOTES.md", "See [totally-fake-internal-task-id] for context.\n")
        self.repo.commit()
        red = run_lint(self.repo.path)
        self.assertEqual(red.returncode, 1)
        self.assertIn("bracketed-id", red.stdout)
        doc.write_text("See [the notes](notes.md) and [build-system] and [RFC2119].\n")
        self.repo.commit()
        self.assertEqual(run_lint(self.repo.path).returncode, 0)

    def test_missing_or_empty_term_list_fails_closed(self):
        self.repo.write("README.md", "clean\n")
        self.repo.commit()
        for terms in (None, "", "  ", "not json", "[]", "{}", json.dumps({"c": []}),
                      json.dumps({"c": "a-string"}), json.dumps({"c": [""]})):
            with self.subTest(terms=terms):
                result = run_lint(self.repo.path, terms=terms)
                self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
                self.assertNotIn("clean", result.stdout)

    def test_double_encoded_term_list_is_accepted(self):
        self.repo.write("README.md", "synthetic-buffer\n")
        self.repo.commit()
        result = run_lint(self.repo.path, terms=json.dumps(json.dumps(TERMS)))
        self.assertEqual(result.returncode, 1)

    def test_untrusted_run_prints_only_a_constant_verdict(self):
        self.repo.write("a.md", "one synthetic-buffer\n[some-fake-internal-id] too\n")
        self.repo.write("b.md", "/synthetic/private/path\n")
        self.repo.commit()
        first = run_lint(self.repo.path, reveal=False)
        self.assertEqual(first.returncode, 1)
        for secret in ("synthetic-buffer", "/synthetic/private/path", "class-", "a.md", "b.md",
                       "some-fake-internal-id"):
            self.assertNotIn(secret, first.stdout)
        self.repo.write("b.md", "clean now\n")
        self.repo.commit()
        second = run_lint(self.repo.path, reveal=False)
        self.assertEqual((second.returncode, second.stdout), (1, first.stdout))

    def test_untrusted_run_prints_bracket_ids_when_no_term_matched(self):
        self.repo.write("a.md", "See [some-fake-internal-id] here.\n")
        self.repo.commit()
        result = run_lint(self.repo.path, reveal=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("some-fake-internal-id", result.stdout)

    def test_symlink_is_never_followed(self):
        outside = Path(self.tmp.name) / "outside.md"
        outside.write_text("synthetic-buffer and [some-fake-internal-id]\n")
        (self.repo.path / "link.md").symlink_to(outside)
        self.repo.write("README.md", "clean\n")
        self.repo.commit()
        self.assertEqual(run_lint(self.repo.path).returncode, 0)

    def test_allowlist_is_read_from_the_trusted_scanner_only(self):
        text = "Historical record: [an-old-fixed-task-id] was closed.\n"
        self.repo.write("HISTORY.md", text)
        self.repo.write(".github/leak_lint_allowlist.txt", text)
        self.repo.commit()
        # The scanned tree's own allowlist does not count...
        self.assertEqual(run_lint(self.repo.path).returncode, 1)
        # ...the scanner checkout's does.
        trusted = Path(self.tmp.name) / "trusted" / ".github"
        trusted.mkdir(parents=True)
        shutil.copyfile(LINT, trusted / "leak_lint.py")
        (trusted / "leak_lint_allowlist.txt").write_text("# a comment\n" + text)
        result = run_lint(self.repo.path, lint=trusted / "leak_lint.py")
        self.assertEqual(result.returncode, 0, result.stdout)


class ThisRepositoryTest(unittest.TestCase):
    def test_committed_tree_has_no_bracketed_id_or_synthetic_term(self):
        if not (ROOT / ".git").exists():
            self.skipTest("not a git checkout")
        result = run_lint(ROOT)
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
