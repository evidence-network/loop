"""A validator that anchors with $ must refuse a trailing newline: re.match accepts "x\\n" for ^x$ (loop#329).

Run: python3 -m unittest discover -s tests -v
"""
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
        self.assertEqual(check_commitment.count_errors("12\n", ("p",)), [])
        self.assertIsNone(check_commitment.card_checkpoint_key({"checkpoint_key": "AAAA\n"}))

    def test_check_nodes(self):
        self.assertEqual(check_nodes.check_operator("octocat"), [])
        self.assertTrue(check_nodes.check_operator("octocat\n"))
        entry = {"node_id": HEX + "\n", "key_id": HEX + "\n"}
        errs = check_nodes.check_entry(Path(HEX + ".json"), entry)
        self.assertTrue(any("node_id is not 64" in e for e in errs), errs)
        self.assertTrue(any("key_id is not 64" in e for e in errs), errs)
        self.assertIsNone(check_nodes.DATE.fullmatch("2026-11-01\n"))

    def test_check_reports(self):
        self.assertIsNone(check_reports.HEX64.fullmatch(HEX + "\n"))
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
        self.assertIsNone(check_versions_lock.NAME.fullmatch("a\n"))
        self.assertIsNone(check_versions_lock.REPO.fullmatch("o/r\n"))

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
        self.assertIsNone(reporter_key.HEX64.fullmatch(HEX + "\n"))

    def test_layout_period_patterns(self):
        for period in (report_layout.Daily, report_layout.Rehearsal, report_layout.OnDemand):
            self.assertIsNone(period.pattern.fullmatch("2026-11-01\n"))
        self.assertIsNone(report_layout.ATTEMPT_NAME.fullmatch("20261101T000000Z\n"))
        self.assertIsNone(report_layout.ATTEMPT_LINE.fullmatch("at=2026-11-01T00:00:00Z by=ask\n"))
        self.assertIsNotNone(report_layout.ATTEMPT_NAME.fullmatch("20261101T000000Z"))

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
                        (stats_model.NODE_FILE if hasattr(stats_model, "NODE_FILE") else stats_model.DAY_FILE, "2026-11-01.json")):
            self.assertIsNotNone(pat.fullmatch(ok))
            self.assertIsNone(pat.fullmatch(ok + "\n"))

    def test_finding_issue_ref(self):
        self.assertTrue(finding.outside("x", "o/r#1"))
        with self.assertRaises(ValueError):
            finding.outside("x", "o/r#1\n")
        self.assertIsNone(finding.STEP_DIR.fullmatch("01-seal\n"))


if __name__ == "__main__":
    unittest.main()
