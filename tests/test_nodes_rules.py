"""Tests for the commitment list rules: consent, contact, leaving.

Run: python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_nodes  # noqa: E402
import check_reports  # noqa: E402
import test_reporter as tr  # noqa: E402  (a module, so its tests are not collected twice)

CASES = ROOT / "tests" / "nodes"
BOTH = {"checkpoints": "verified", "history_card": "verified"}


def errors(directory):
    return check_nodes.check_dir(Path(directory))[0]


class EntryRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.nodes = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, **change):
        e = tr.entry("a" * 64, "b" * 64)
        e.update(change)
        e = {k: v for k, v in e.items() if v is not None}
        (self.nodes / f"{'a' * 64}.json").write_text(json.dumps(e))
        return errors(self.nodes)

    def test_no_entry_opts_in_to_pair_edges_while_the_consent_does_not_name_them(self):
        # loop#77: public pair edges on /watch/ need a consent that names them; the statement does not yet.
        disc = {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]}
        self.assertEqual(self.check(disclosure=disc), [])
        errs = self.check(disclosure=dict(disc, pair_edges="public"))
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("pair_edges", errs[0])
        self.assertIn("pair_edges", self.check(disclosure=dict(disc, pair_edges="yes"))[0])

    def test_the_fixture_cases(self):
        self.assertEqual(errors(CASES / "good"), [])
        name = f"{'a' * 64}.json"
        self.assertEqual(errors(CASES / "no-consent"), [f"{name}: missing consent"])
        self.assertEqual(errors(CASES / "email-contact"),
                         [f"{name}: operator looks like an email address; give a GitHub handle (no personal data)"])
        self.assertEqual(errors(CASES / "phone-contact"),
                         [f"{name}: operator looks like a phone number; give a GitHub handle (no personal data)"])

    def test_consent_is_the_published_statement_word_for_word(self):
        self.assertEqual(self.check(), [])
        for consent in ("yes", "", True, check_nodes.CONSENT.replace("usually daily", "twice daily"),
                        check_nodes.CONSENT + " "):
            self.assertEqual(self.check(consent=consent),
                             [f"{'a' * 64}.json: consent must be the operator statement in nodes/README.md, word for word"],
                             consent)

    def test_the_readme_carries_the_statement(self):
        readme = (ROOT / "nodes" / "README.md").read_text()
        self.assertIn(json.dumps(check_nodes.CONSENT), readme)  # the JSON value, as it goes in an entry

    def test_contact_is_a_github_handle_and_nothing_personal(self):
        for ok in ("octocat", "a", "node-op-2", "A1-b2", "x" * 39):
            self.assertEqual(self.check(operator=ok), [], ok)
        refused = {
            "op@example.org": "email address",
            "mailto:op@example.org": "email address",
            "Op Name <op@example.org>": "email address",
            "+1 555 010 0199": "phone number",
            "555-010-0199": "phone number",
            "(555) 010 0199": "phone number",
            "5550100199": "phone number",
            "@octocat": "without the leading @",
            "https://github.com/octocat": "not a GitHub handle",
            "Op Name": "not a GitHub handle",
            "-octocat": "not a GitHub handle",
            "octo--cat": "not a GitHub handle",
            "x" * 40: "not a GitHub handle",
            "": "non-empty",
        }
        for operator, why in refused.items():
            errs = self.check(operator=operator)
            self.assertEqual(len(errs), 1, (operator, errs))
            self.assertIn(why, errs[0], operator)


class LeaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.nodes = self.dir / "nodes"
        self.nodes.mkdir()
        self.console = None

    def tearDown(self):
        if self.console:
            self.console.close()
        self.tmp.cleanup()

    def mark_left(self, n, day):
        (self.nodes / "left").mkdir(exist_ok=True)
        (self.nodes / "left" / f"{tr.node_id(n)}.json").write_text(json.dumps({"node_id": tr.node_id(n), "left": day}))

    def test_a_removed_node_is_not_asked_from_the_next_run_and_its_past_reports_stay(self):
        out = self.dir / "reports" / "daily"
        reporter = self.dir / "reporter.json"
        reporter.write_text(json.dumps(tr.REPORTER))
        for n in (1, 2):
            e = tr.entry(tr.node_id(n), tr.key_id(n))
            (self.nodes / f"{e['node_id']}.json").write_text(json.dumps(e))
        self.console = tr.FakeConsole({tr.node_id(n): BOTH for n in (1, 2)}, {tr.node_id(n): tr.key_id(n) for n in (1, 2)})

        def run(day):
            argv = ["daily_report.py", "run", "--date", day, "--console", self.console.url, "--nodes", str(self.nodes),
                    "--out", str(out), "--reporter", str(reporter), "--timeout", "0.5"]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return tr.daily_report.main(argv)

        self.assertEqual(run("2026-10-28"), 0)
        before = {p.relative_to(out): p.read_bytes() for p in (out / "2026-10-28").rglob("*") if p.is_file()}

        # Node 2 leaves by pull request: its entry is removed, a leave mark added.
        (self.nodes / f"{tr.node_id(2)}.json").unlink()
        self.mark_left(2, "2026-10-28")
        self.assertEqual(errors(self.nodes), [])
        self.console.asked.clear()
        self.assertEqual(run("2026-10-29"), 0)

        self.assertEqual({b["peer_id"] for _, b in self.console.asked}, {tr.node_id(1)})
        self.assertEqual(json.loads((out / "2026-10-29" / "RUN.json").read_text())["asked"], [tr.node_id(1)])
        self.assertFalse((out / "2026-10-29" / tr.node_id(2)).exists())
        after = {p.relative_to(out): p.read_bytes() for p in (out / "2026-10-28").rglob("*") if p.is_file()}
        self.assertEqual(after, before)  # the past report is kept, byte for byte
        self.assertTrue((out / "2026-10-28" / tr.node_id(2) / "NODE").is_file())
        self.assertEqual(check_reports.check(out, check_reports.dt.date(2026, 10, 29))[0], [])

    def test_leave_marks(self):
        shutil.copy(CASES / "good" / f"{'a' * 64}.json", self.nodes)
        self.mark_left(2, "2026-10-28")
        self.assertEqual(errors(self.nodes), [])
        # A node is listed or left, not both; rejoining removes the mark.
        self.mark_left(10, "2026-10-28")
        self.assertEqual(errors(self.nodes),
                         [f"left/{'a' * 64}.json: {'a' * 64} is listed and marked left; remove the mark to rejoin"])
        (self.nodes / "left" / f"{'a' * 64}.json").unlink()
        self.mark_left(2, "2026-02-30")
        self.assertEqual(errors(self.nodes), [f"left/{tr.node_id(2)}.json: left must be a YYYY-MM-DD date"])
        (self.nodes / "left" / f"{tr.node_id(2)}.json").write_text(
            json.dumps({"node_id": tr.node_id(2), "left": "2026-10-28", "operator": "octocat"}))
        self.assertEqual(errors(self.nodes), [f"left/{tr.node_id(2)}.json: a leave mark has exactly node_id and left"])
        (self.nodes / "left" / f"{tr.node_id(2)}.json").unlink()
        (self.nodes / "left" / f"{tr.node_id(3)}.json").write_text(json.dumps({"node_id": tr.node_id(2), "left": "2026-10-28"}))
        self.assertEqual(errors(self.nodes),
                         [f"left/{tr.node_id(3)}.json: file name must be <node_id>.json, node_id 64 lowercase hex"])


if __name__ == "__main__":
    unittest.main()
