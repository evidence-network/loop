"""Tests for what the repository needs before it is public: the DCO check, the direct-push
guard, the personal-data check, and that the branch-protection script requires the checks the
workflows define.

Run: python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_personal_data  # noqa: E402
import dco  # noqa: E402
import direct_push  # noqa: E402


class GitRepo:
    """A throwaway repository; `commit` returns the new commit's sha."""

    def __init__(self, base):
        self.path = Path(base)
        self.git("init", "-q", "-b", "main")

    def git(self, *args, **env):
        e = dict(os.environ, GIT_AUTHOR_NAME="A Tester", GIT_AUTHOR_EMAIL="a@example.invalid",
                 GIT_COMMITTER_NAME="A Tester", GIT_COMMITTER_EMAIL="a@example.invalid", **env)
        return subprocess.run(["git", *args], cwd=self.path, check=True, capture_output=True,
                              text=True, env=e).stdout.strip()

    def commit(self, message, files=None, **env):
        for rel, text in (files or {}).items():
            p = self.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, **env)
        return self.git("rev-parse", "HEAD")


@contextlib.contextmanager
def cwd(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def quiet(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = fn(*args)
    return code, out.getvalue()


class DcoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = GitRepo(tmp.name)
        self.base = self.repo.commit("base")

    def check(self, head):
        with cwd(self.repo.path):
            return quiet(dco.main, [self.base, head])

    def test_signed_off_commits_pass(self):
        self.repo.commit("one\n\nSigned-off-by: A Tester <a@example.invalid>")
        head = self.repo.commit("two\n\nSigned-off-by: A Tester <A@Example.Invalid>")
        code, out = self.check(head)
        self.assertEqual(code, 0, out)
        self.assertIn("2 commit(s), all signed off", out)

    def test_unsigned_commit_fails(self):
        self.repo.commit("one\n\nSigned-off-by: A Tester <a@example.invalid>")
        head = self.repo.commit("two, no sign-off")
        code, out = self.check(head)
        self.assertEqual(code, 1)
        self.assertIn(head[:12], out)
        self.assertIn("no Signed-off-by line", out)

    def test_sign_off_by_someone_else_fails(self):
        head = self.repo.commit("one\n\nSigned-off-by: B Other <b@example.invalid>")
        code, out = self.check(head)
        self.assertEqual(code, 1)
        self.assertIn("author's email", out)

    def test_sign_off_mentioned_in_the_body_is_not_a_trailer(self):
        head = self.repo.commit("one: add a Signed-off-by: A Tester <a@example.invalid> note")
        self.assertEqual(self.check(head)[0], 1)

    def test_merge_commit_is_skipped(self):
        signed = "\n\nSigned-off-by: A Tester <a@example.invalid>"
        self.repo.git("checkout", "-q", "-b", "side")
        self.repo.commit("side" + signed, {"s.txt": "s"})
        self.repo.git("checkout", "-q", "main")
        self.repo.commit("main" + signed, {"m.txt": "m"})
        self.repo.git("merge", "-q", "--no-ff", "--no-edit", "side")
        code, out = self.check(self.repo.git("rev-parse", "HEAD"))
        self.assertEqual(code, 0, out)

    def test_bad_range(self):
        self.assertEqual(self.check("0" * 40)[0], 2)


class DirectPushTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = GitRepo(tmp.name)
        self.before = self.repo.commit("base", {"README.md": "x"})

    def check(self, after, via_pr="false", before=None):
        with cwd(self.repo.path):
            return quiet(direct_push.main, [before or self.before, after, via_pr])

    def test_reporter_push_of_a_day_passes(self):
        after = self.repo.commit("report", {"stats/data/daily/2026-11-01.json": "{}",
                                            "stats/data/daily/2026-10-31.json": "{}"})
        self.assertEqual(self.check(after)[0], 0)

    def test_direct_push_outside_stats_data_fails(self):
        self.repo.commit("stats", {"stats/data/daily/2026-11-01.json": "{}"})
        after = self.repo.commit("sneak", {"nodes/x.json": "{}"})
        code, out = self.check(after)
        self.assertEqual(code, 1)
        self.assertIn("nodes/x.json", out)
        self.assertNotIn("RUN.json", out)

    def test_reports_readme_is_outside(self):
        after = self.repo.commit("edit", {"reports/README.md": "y"})
        self.assertEqual(self.check(after)[0], 1)

    def test_deleting_outside_fails(self):
        self.repo.git("rm", "-q", "README.md")
        after = self.repo.commit("delete")
        self.assertEqual(self.check(after)[0], 1)

    def test_merged_pull_request_passes_whatever_it_touches(self):
        after = self.repo.commit("pr", {"nodes/x.json": "{}"})
        self.assertEqual(self.check(after, via_pr="true")[0], 0)

    def test_new_branch_push_checks_the_whole_tree(self):
        after = self.repo.git("rev-parse", "HEAD")
        self.assertEqual(self.check(after, before=direct_push.ZERO)[0], 1)


class PersonalDataTest(unittest.TestCase):
    def test_finds(self):
        cases = {
            "contact: someone@gmail.com": "email address",
            '"operator": "first.last+tag@mail.co.uk"': "email address",
            "call +1 415 555 0134": "phone number",
            "call +442079460958": "phone number",
            "(415) 555-0134": "phone number",
            "415-555-0134": "phone number",
            "host 8.8.8.8:3131": "public IP address",
            "peer [2606:4700::1111]": "public IP address",
        }
        for line, kind in cases.items():
            with self.subTest(line=line):
                self.assertIn(kind, check_personal_data.find(line))

    def test_does_not_find(self):
        lines = [
            '"key_id": "' + "4155550134" * 6 + '1234"',
            '"sig": "ab+14155550134cd/ef+12025550100Q=="',
            "git commit -s (Signed-off-by: A <a@example.invalid>)",
            "user@example.com and x@node.test and y@nodes.example",
            "console http://127.0.0.1:3131 and 10.0.0.7 and 192.168.1.20 and ::1",
            "documentation addresses 192.0.2.1 and 2001:db8::1",
            '"date": "2026-10-27T06:00:00Z", "version": "0.78.1", "time": "06:00:00"',
            '"checkpoints": [1, 2, 3], "size": 1234567890',
            "mesh-llm main b5ed6bd8 + PR #2620, capsules 0.1.5",
            "fe80::1 link-local",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(check_personal_data.find(line), [])

    def test_scans_a_tree_and_reports_path_and_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "reports" / "daily" / "2026-11-01" / ("a" * 64)
            day.mkdir(parents=True)
            (day / "history_card.response.json").write_text('{\n  "note": "a@corp.io"\n}\n')
            (day / "NODE").write_text("discloses: checkpoints\n")
            outside = Path(tmp) / "outside.txt"
            outside.write_text("b@corp.io\n")
            (day / "link.txt").symlink_to(outside)
            code, out = quiet(check_personal_data.main, [str(Path(tmp) / "reports")])
        self.assertEqual(code, 1)
        self.assertIn("history_card.response.json:2: email address", out)
        self.assertNotIn("link.txt", out)

    def test_missing_path(self):
        self.assertEqual(quiet(check_personal_data.main, ["/nonexistent/nodes"])[0], 2)

    def test_this_repository_is_clean(self):
        with cwd(ROOT):
            code, out = quiet(check_personal_data.main, [])
        self.assertEqual(code, 0, out)


class BranchProtectionTest(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "apply_branch_protection.sh"

    def required_checks(self):
        m = re.search(r"^REQUIRED_CHECKS=\(([^)]*)\)", self.SCRIPT.read_text(), re.M)
        return set(m.group(1).split())

    def pull_request_jobs(self):
        """Job ids of the workflows that run on pull requests."""
        jobs = set()
        for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
            text = wf.read_text()
            if not re.search(r"^  pull_request(_target)?:", text, re.M):
                continue
            body = text.split("\njobs:\n", 1)[1]
            jobs |= set(re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.M))
        return jobs

    # Jobs that run on PR but are not yet required; the maintainers remove each when its gate is decided.
    NOT_YET_REQUIRED = {"loop", "commitments"}

    def test_required_checks_are_the_pull_request_jobs(self):
        self.assertEqual(self.required_checks(), self.pull_request_jobs() - self.NOT_YET_REQUIRED)

    def test_job_names_are_unique(self):
        names = []
        for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
            body = wf.read_text().split("\njobs:\n", 1)[1]
            names += re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.M)
        self.assertEqual(len(names), len(set(names)), names)

    def test_dry_run_prints_both_rulesets(self):
        out = subprocess.run(["bash", str(self.SCRIPT), "--dry-run"], capture_output=True,
                             text=True, check=True).stdout
        history, end = json.JSONDecoder().raw_decode(out)
        review = json.loads(out[end:])
        self.assertEqual(history["bypass_actors"], [])
        self.assertEqual({r["type"] for r in history["rules"]}, {"deletion", "non_fast_forward"})
        self.assertEqual(review["bypass_actors"],
                         [{"actor_id": None, "actor_type": "DeployKey", "bypass_mode": "always"}])
        rules = {r["type"]: r.get("parameters") for r in review["rules"]}
        self.assertTrue(rules["pull_request"]["require_code_owner_review"])
        checks = rules["required_status_checks"]["required_status_checks"]
        self.assertEqual({c["context"] for c in checks}, self.required_checks())
        self.assertEqual({c["integration_id"] for c in checks}, {15368})


class RepositoryFilesTest(unittest.TestCase):
    def test_license_is_apache_2(self):
        text = (ROOT / "LICENSE").read_text()
        self.assertTrue(text.lstrip().startswith("Apache License\n                           Version 2.0, January 2004"))
        self.assertIn("END OF TERMS AND CONDITIONS", text)

    def test_codeowners_names_only_the_marked_placeholder_team(self):
        lines = (ROOT / ".github" / "CODEOWNERS").read_text().splitlines()
        rules = [ln for ln in lines if ln.strip() and not ln.startswith("#")]
        self.assertEqual([ln.split()[1:] for ln in rules], [["@evidence-network/loop-maintainers"]])
        self.assertIn("PLACEHOLDER", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
