"""Tests for versions.lock: its check, and the digest each day's report names.

Run: python3 -m unittest discover -s tests -v
"""
import base64
import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_reports  # noqa: E402
import check_versions_lock  # noqa: E402
import daily_report  # noqa: E402
import reporter_key  # noqa: E402

SHA = "b5ed6bd8ca825e223a65adc9736fb7b422cc9e3e"
REPORTER = {"node_id": "e" * 64, "key_id": "f" * 64}


def spki_pem(key_hex):
    der = reporter_key.ED25519_SPKI + bytes.fromhex(key_hex)
    return "-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PUBLIC KEY-----\n"


def lock(*pins):
    return "# a comment\n\n" + "".join(f"{p}\n" for p in pins)


class CheckTest(unittest.TestCase):
    def errs(self, text):
        return check_versions_lock.check(text)[0]

    def test_the_committed_lock_checks(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(check_versions_lock.main(["check_versions_lock.py", str(ROOT / "versions.lock")]), 0)
        self.assertIn("pins ok", out.getvalue())

    def test_full_shas_and_released_versions_are_pins(self):
        for ref in (SHA, "0.1.5", "v0.1.5", "v0.1.0-rc23", "crates/evidencebook-v0.0.7", "capsulectl/v0.1.0-rc23"):
            with self.subTest(ref=ref):
                self.assertEqual(self.errs(lock(f"x o/r {ref}")), [])

    def test_branch_names_are_refused(self):
        for ref in ("main", "master", "HEAD", "fix/plugin-restart-mesh-snapshot", "release-0.1", "v1", "0.1", "latest"):
            with self.subTest(ref=ref):
                errs = self.errs(lock(f"x o/r {ref}"))
                self.assertEqual(len(errs), 1)
                self.assertIn("not a full commit SHA or a released version", errs[0])

    def test_short_shas_are_refused(self):
        for ref in (SHA[:7], SHA[:8], SHA[:12], SHA[:39], SHA.upper()[:12]):
            with self.subTest(ref=ref):
                errs = self.errs(lock(f"x o/r {ref}"))
                self.assertEqual(len(errs), 1)
                self.assertIn("short", errs[0])

    def test_an_uppercase_or_overlong_sha_is_refused(self):
        self.assertTrue(self.errs(lock(f"x o/r {SHA.upper()}")))
        self.assertTrue(self.errs(lock(f"x o/r {SHA}0")))

    def test_malformed_lines_and_repeated_names_are_refused(self):
        self.assertIn("want `<name> <repository> <ref>`", self.errs(lock(f"x {SHA}"))[0])
        self.assertIn("want `<name> <repository> <ref>`", self.errs(lock(f"x o/r {SHA} # note"))[0])
        self.assertIn("pinned twice", self.errs(lock(f"x o/r {SHA}", "x o/r 0.1.5"))[0])
        self.assertIn("not <owner>/<repo>", self.errs(lock(f"x repo {SHA}"))[0])
        self.assertIn("lowercase", self.errs(lock(f"X o/r {SHA}"))[0])
        self.assertEqual(self.errs(lock()), ["no pins"])

    def test_cli_refuses_a_bad_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "versions.lock"
            path.write_text(lock("mesh-llm Mesh-LLM/mesh-llm main"))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(check_versions_lock.main(["check_versions_lock.py", str(path)]), 1)
            self.assertIn("line 3: mesh-llm: main", err.getvalue())

    def test_bumping_a_pin_is_a_one_line_change(self):
        text = (ROOT / "versions.lock").read_text()
        bumped = re.sub(r"(?m)^capsules Mesh-LLM/capsules-plugin \S+$",
                        "capsules Mesh-LLM/capsules-plugin v9.9.9", text)
        changed = [(a, b) for a, b in zip(text.splitlines(), bumped.splitlines()) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertEqual(self.errs(bumped), [])


class ReporterNode:
    """Just enough of the reporter node's console for a run with no node listed."""

    def __init__(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith(daily_report.LEDGER_ROUTE):
                    body = json.dumps({"records": [], "node_pub_key_pem": spki_pem(REPORTER["key_id"])}).encode()
                else:
                    body = json.dumps({"announced_key_id": REPORTER["key_id"]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ReportNamesTheLockTest(unittest.TestCase):
    def test_the_days_run_names_the_versions_lock_digest(self):
        node = ReporterNode()
        self.addCleanup(node.close)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "nodes").mkdir()
            (tmp / "reporter.json").write_text(json.dumps(REPORTER))
            out = tmp / "daily"
            argv = ["daily_report.py", "run", "--date", "2026-10-27", "--console", node.url,
                    "--nodes", str(tmp / "nodes"), "--out", str(out), "--reporter", str(tmp / "reporter.json")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(daily_report.main(argv), 0)
            run = json.loads((out / "2026-10-27" / "RUN.json").read_text())
        want = hashlib.sha256((ROOT / "versions.lock").read_bytes()).hexdigest()
        self.assertEqual(run["versions_lock_sha256"], want)

    def test_check_reports_requires_the_digest(self):
        node = ReporterNode()
        self.addCleanup(node.close)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "nodes").mkdir()
            (tmp / "reporter.json").write_text(json.dumps(REPORTER))
            out = tmp / "daily"
            argv = ["daily_report.py", "run", "--date", "2026-10-27", "--console", node.url,
                    "--nodes", str(tmp / "nodes"), "--out", str(out), "--reporter", str(tmp / "reporter.json")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(daily_report.main(argv), 0)
            self.assertEqual(check_reports.check(out)[0], [])
            path = out / "2026-10-27" / "RUN.json"
            run = json.loads(path.read_text())
            for bad in (None, "", "abc", run["versions_lock_sha256"].upper()):
                if bad is None:
                    run.pop("versions_lock_sha256")
                else:
                    run["versions_lock_sha256"] = bad
                path.write_text(json.dumps(run))
                errs = check_reports.check(out)[0]
                self.assertEqual(len(errs), 1, (bad, errs))
                self.assertIn("versions_lock_sha256 must be the 64-hex sha256", errs[0])

    def test_the_digest_is_of_the_lock_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "versions.lock"
            path.write_text(lock(f"x o/r {SHA}"))
            before = daily_report.versions_lock_sha256(path)
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
            path.write_text(lock("x o/r 0.1.5"))
            self.assertNotEqual(daily_report.versions_lock_sha256(path), before)


if __name__ == "__main__":
    unittest.main()
