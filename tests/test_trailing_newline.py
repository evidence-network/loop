"""A validator that anchors with $ must refuse a trailing newline: re.match accepts "x\\n" for ^x$ (loop#329).

Run: python3 -m unittest discover -s tests -v
"""
import ast
import importlib.machinery
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for d in ("scripts", "site", "steps", "status"):
    sys.path.insert(0, str(ROOT / d))
import binding_verify  # noqa: E402
import check_binding  # noqa: E402
import check_commitment  # noqa: E402
import check_nodes  # noqa: E402
import check_reports  # noqa: E402
import check_versions_lock  # noqa: E402
import finding  # noqa: E402
import report_layout  # noqa: E402
import reporter_key  # noqa: E402
import scrub_reports  # noqa: E402
import stats_model  # noqa: E402

# Prefix scans: their patterns carry no $, so a trailing newline changes nothing.
ALLOWED = {"_COMMENT_LINE.match"}
HEX = "a" * 64
SHA = "b" * 40


class TrailingNewline(unittest.TestCase):
    def test_check_binding_verdict_fields(self):
        base = {"profile": check_binding.PROFILE, "controller_key_id": HEX, "subjects": ["a", "b"],
                "status": "not_checked", "verifier": None, "checked_at": "2026-11-01T00:00:00Z",
                "cites": "o/r#1"}
        self.assertEqual(check_binding.verdict_errors(base, {}), [])
        for field, bad in (("controller_key_id", HEX + "\n"), ("subjects", ["a", "b\n"]),
                           ("verifier", "binding-verifier@x\n"), ("cites", "o/r#1\n")):
            with self.subTest(field):
                errs = check_binding.verdict_errors({**base, field: bad}, {})
                self.assertTrue(any(field in e for e in errs), errs)
        self.assertFalse(check_binding.pinned("binding-verifier@x\n", {"binding-verifier": "x\n"}))

    def test_check_commitment_digits_and_key(self):
        self.assertEqual(check_commitment.count_errors("12", ("p",)), ["p: a decimal number as a string; a commitment carries no counts"])
        self.assertEqual(check_commitment.count_errors("12\n", ("p",)), ["p: a decimal number as a string; a commitment carries no counts"])
        self.assertIsNone(check_commitment.card_checkpoint_key({"checkpoint_key": "AAAA\n"}))

    def test_check_nodes(self):
        self.assertEqual(check_nodes.check_operator("octocat"), [])
        self.assertTrue(check_nodes.check_operator("octocat\n"))
        entry = {"node_id": HEX + "\n", "key_id": HEX + "\n"}
        errs = check_nodes.check_entry(Path(HEX + ".json"), entry)
        self.assertTrue(any("node_id is not 64" in e for e in errs), errs)
        self.assertTrue(any("key_id is not 64" in e for e in errs), errs)

    def test_check_reports(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t) / "reporter.json"
            r.write_text('{"key_id": "%s\\n"}' % HEX)
            self.assertFalse(check_reports.deployed(r))
            r.write_text('{"key_id": "%s"}' % HEX)
            self.assertTrue(check_reports.deployed(r))

    def test_versions_lock(self):
        self.assertIsNone(check_versions_lock.check_ref(SHA))
        for ref in (SHA + "\n", "v1.2.3\n", "abcd\n"):
            with self.subTest(ref=ref):
                self.assertIsNotNone(check_versions_lock.check_ref(ref))

    def test_binding_verify_digest(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(binding_verify.checkout_list(t, HEX + "\n"), "[]")
        with self.assertRaises(ValueError):
            binding_verify.read_revocations(Path(t) / (HEX + "\n.json"), text="[]")

    def test_reporter_key_identity(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "self-peer-id").write_text("A" * 64 + "\n")  # strip() and lower() leave a valid id
            with mock.patch.object(reporter_key, "key_from_pem", return_value=HEX):
                (d / "keys").mkdir()
                (d / "keys" / "node-key.pub.pem").write_text("x")
                self.assertEqual(reporter_key.read_identity(d)["node_id"], HEX)
            (d / "self-peer-id").write_text("\n")
            with self.assertRaises(ValueError):
                reporter_key.read_identity(d)

    def test_report_layout_names(self):
        for period, ok in ((report_layout.Daily, "2026-11-01"), (report_layout.Rehearsal, "2026-11-01T0000Z"),
                           (report_layout.OnDemand, "2026-11-01T000000Z")):
            with self.subTest(period.set):
                self.assertIsNotNone(period.pattern.fullmatch(ok))
                self.assertIsNone(period.pattern.fullmatch(ok + "\n"))
        self.assertIsNone(report_layout.ATTEMPT_NAME.fullmatch("20261101T000000Z\n"))
        self.assertIsNone(report_layout.ATTEMPT_LINE.fullmatch("at=2026-11-01T00:00:00Z by=ask\n"))
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / ("20261101T000000Z\n")).mkdir()
            self.assertEqual(report_layout.attempts(Path(t)), [])

    def test_scrub_reports(self):
        errs = []
        scrub_reports.match("w", "2026-11-01\n", scrub_reports.DATE, errs)
        scrub_reports.match("w", HEX + "\n", scrub_reports.HEX64, errs)
        scrub_reports.match("w", "2026-11-01T00:00:00Z\n", scrub_reports.ISO, errs)
        self.assertEqual(len(errs), 3)
        errs = []
        scrub_reports.match("w", "2026-11-01", scrub_reports.DATE, errs)
        self.assertEqual(errs, [])
        self.assertIsNone(scrub_reports.day_file(report_layout.Daily).fullmatch("2026-11-01.json\n"))

    def test_stats_model(self):
        self.assertTrue(stats_model._hex_or_null(HEX))
        self.assertFalse(stats_model._hex_or_null(HEX + "\n"))
        for pat, ok in ((stats_model.DATE, "2026-11-01"), (stats_model.STAMP, "2026-11-01T00:00:00Z"),
                        (stats_model.DAY_FILE, "2026-11-01.json"), (stats_model.SLOT_FILE, "2026-11-01T0000Z.json"),
                        (stats_model.RUN_FILE, "2026-11-01T000000Z.json")):
            self.assertIsNotNone(pat.fullmatch(ok))
            self.assertIsNone(pat.fullmatch(ok + "\n"))

    def test_step_validators(self):
        def load(name):
            loader = importlib.machinery.SourceFileLoader("tn_" + name, str(ROOT / "steps" / name))
            mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
            loader.exec_module(mod)
            return mod
        ask, admit = load("07-ask"), load("04-admit")
        self.assertEqual(ask.duration("PT5M").total_seconds(), 300)
        for bad in ("PT5M\n",):
            with self.assertRaises(ValueError):
                ask.duration(bad)
        ask.utc("2026-11-01T00:00:00Z")
        with self.assertRaises(ValueError):
            ask.utc("2026-11-01T00:00:00Z\n")
        self.assertTrue(admit.digest_ok({"alg": "SHA-256", "digest": HEX}))
        self.assertFalse(admit.digest_ok({"alg": "SHA-256", "digest": HEX + "\n"}))

    def test_join_sh_checks_refuse_trailing_newline(self):
        src = (ROOT / "join.sh").read_text()
        self.assertNotIn("re.match(", src)
        for pat in (r"^[A-Za-z0-9][A-Za-z0-9._-]*$", r"^[0-9a-f]{64}$"):
            self.assertIn('re.fullmatch(r"%s"' % pat, src)

    def test_no_dollar_anchored_match_remains(self):
        """The whole class: no tracked Python (incl. extensionless steps/*) calls .match/.search on a $-anchored
        pattern. re.match with a literal is judged by its literal; a compiled pattern's .match is flagged unless
        it is in ALLOWED (detectors and prefix scans)."""
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        bad = []
        for f in files:
            path = ROOT / f
            if f.startswith(("tests/", "site/vendor/")) or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
                tree = ast.parse(text)
            except (UnicodeDecodeError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "match"):
                    continue
                recv = node.func.value
                if isinstance(recv, ast.Name) and recv.id == "re":
                    lit = node.args[0] if node.args else None
                    if isinstance(lit, ast.Constant) and isinstance(lit.value, str) and not lit.value.endswith("$"):
                        continue
                elif ast.unparse(node.func) in ALLOWED:
                    continue
                bad.append(f"{f}:{node.lineno}: {ast.unparse(node)[:80]}")
        for src in ("join.sh",):  # Python embedded in a shell script
            for n, line in enumerate((ROOT / src).read_text().splitlines(), 1):
                if "re.match(" in line:
                    bad.append(f"{src}:{n}: {line.strip()[:80]}")
        self.assertEqual(bad, [])

    def test_finding_issue_ref(self):
        self.assertTrue(finding.outside("x", "o/r#1"))
        with self.assertRaises(ValueError):
            finding.outside("x", "o/r#1\n")


if __name__ == "__main__":
    unittest.main()
