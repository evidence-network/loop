"""Tests for the daily reporter, against a fake reporter-node console.

Run: python3 -m unittest discover -s tests -v
"""
import base64
import contextlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_reports  # noqa: E402
import daily_report  # noqa: E402
import reporter_key  # noqa: E402

FIXTURES = ROOT / "tests" / "reporter" / "fixtures"
REPORTER = {"node_id": "e" * 64, "key_id": "f" * 64}


def node_id(n):
    return f"{n:x}" * 64


def key_id(n):
    return f"{n + 1:x}" * 64


class FakeConsole:
    """The reporter node's console: peer-key and mesh_evidence_request, per node.

    behaviour[node_id] maps a question stem to a fixture name, "absence" (502),
    or "timeout"; announced[node_id] is the key its host announces, or None.
    """

    def __init__(self, behaviour, announced, delay=1.0):
        self.behaviour, self.announced, self.delay = behaviour, announced, delay
        self.asked = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, body):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                if url.path != daily_report.PEER_KEY_ROUTE:
                    return self.reply(404, b"Not found")
                peer = urllib.parse.parse_qs(url.query)["peer"][0]
                self.reply(200, json.dumps({"announced_key_id": fake.announced.get(peer)}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.asked.append((self.path, body))
                if self.path != daily_report.TOOL_ROUTE:
                    return self.reply(404, b"Not found")
                stem = "history_card" if body["request"].get("derivation") else "checkpoints"
                what = fake.behaviour[body["peer_id"]][stem]
                if what == "timeout":
                    time.sleep(fake.delay)  # the reporter has given up; send nothing
                    return
                if what == "absence":
                    return self.reply(502, (FIXTURES / "absence.txt").read_bytes())
                self.reply(200, (FIXTURES / f"{what}.json").read_bytes())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def entry(nid, kid):
    return {
        "node_id": nid,
        "key_id": kid,
        "operator": "example-operator",
        "mesh": "lab",
        "ask_route": "mesh",
        "disclosure": {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]},
        "host_build": "mesh-llm lab build",
        "plugin_version": "capsules 0.1.5",
    }


def spki_pem(key_hex):
    der = reporter_key.ED25519_SPKI + bytes.fromhex(key_hex)
    return "-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PUBLIC KEY-----\n"


def private_key(data, mode=0o600):
    """Stand-in for the plugin's private key file: only its owner and mode are checked."""
    path = data / "keys" / "node-key.pem"
    path.write_text("not read\n")
    path.chmod(mode)
    return path


class ReporterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.nodes = self.dir / "nodes"
        self.nodes.mkdir()
        self.out = self.dir / "reports" / "daily"
        self.reporter = self.dir / "reporter.json"
        self.reporter.write_text(json.dumps(REPORTER))
        self.console = None

    def tearDown(self):
        if self.console:
            self.console.close()
        self.tmp.cleanup()

    def list_nodes(self, *entries):
        for e in entries:
            (self.nodes / f"{e['node_id']}.json").write_text(json.dumps(e))

    def run_reporter(self, date, console=None, *extra):
        argv = ["daily_report.py", "run", "--date", date, "--console", console or self.console.url,
                "--nodes", str(self.nodes), "--out", str(self.out), "--reporter", str(self.reporter),
                "--timeout", "0.5", *extra]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return daily_report.main(argv)

    def prove(self, date):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = daily_report.main(["daily_report.py", "prove", "--date", date, "--out", str(self.out)])
        return code, out.getvalue()

    def line(self, date, n, stem):
        return (self.out / date / node_id(n) / f"{stem}.verification").read_text().strip()

    def node_line(self, date, n):
        return (self.out / date / node_id(n) / "NODE").read_text().strip()

    def every_outcome(self):
        both = lambda what: {"checkpoints": what, "history_card": what}  # noqa: E731
        behaviour = {
            node_id(1): both("verified"),
            node_id(2): both("refusal"),
            node_id(3): both("no_announced_key"),
            node_id(4): {"checkpoints": "verified", "history_card": "absence"},
            node_id(5): both("timeout"),
            node_id(6): both("not_evidence"),
            node_id(7): both("verified"),
            node_id(8): both("no_checkpoint_yet"),
        }
        announced = {node_id(n): key_id(n) for n in (1, 2, 4, 5, 6, 8)}
        announced[node_id(7)] = "9" * 64  # the host announces a key other than the listed one
        self.list_nodes(*(entry(node_id(n), key_id(n)) for n in range(1, 9)))
        self.console = FakeConsole(behaviour, announced)

    def test_every_outcome_is_saved_with_its_verification_line(self):
        self.every_outcome()
        self.assertEqual(self.run_reporter("2026-10-28"), 0)
        d = "2026-10-28"
        self.assertEqual(self.line(d, 1, "checkpoints"),
                         f"verified checkpoints=3 anchor={'a' * 64} key={key_id(1)} listed_key=match")
        self.assertEqual(self.line(d, 2, "history_card"), f"refusal reason=policy_declined key={key_id(2)} listed_key=match")
        self.assertEqual(self.line(d, 3, "checkpoints"), "no_announced_key listed_key=unknown")
        self.assertTrue(self.line(d, 4, "history_card").startswith('absence why="http 502: could not reach peer'))
        self.assertTrue(self.line(d, 5, "checkpoints").startswith('absence why="no reply from the reporter'))
        self.assertTrue(self.line(d, 6, "checkpoints").startswith('not_evidence why="the envelope'))
        self.assertTrue(self.line(d, 7, "checkpoints").endswith(f"key={'9' * 64} listed_key=differs"))
        for stem in ("checkpoints", "history_card"):
            self.assertEqual(self.line(d, 8, stem),
                             f"no_checkpoint_yet reason=coverage_unsatisfiable key={key_id(8)} listed_key=match")

        # The answer bytes are kept exactly as they came back.
        self.assertEqual((self.out / d / node_id(1) / "checkpoints.response.json").read_bytes(),
                         (FIXTURES / "verified.json").read_bytes())
        self.assertEqual((self.out / d / node_id(2) / "history_card.response.json").read_bytes(),
                         (FIXTURES / "refusal.json").read_bytes())
        self.assertFalse((self.out / d / node_id(4) / "history_card.response.json").exists())
        self.assertIn("could not reach peer", (self.out / d / node_id(4) / "history_card.absence.txt").read_text())

        self.assertEqual(self.node_line(d, 1), "discloses: checkpoints, history_card/1")
        self.assertEqual(self.node_line(d, 4), "discloses: checkpoints")
        for n in (2, 3, 5, 6):
            self.assertEqual(self.node_line(d, n), "joined, discloses nothing")
        self.assertEqual(self.node_line(d, 8), "joined, no checkpoint yet")

        run = json.loads((self.out / d / "RUN.json").read_text())
        self.assertEqual(run["reporter"], REPORTER)
        self.assertEqual(run["questions"], ["checkpoints", "history_card/1"])
        self.assertEqual(run["asked"], sorted(node_id(n) for n in range(1, 9)))
        self.assertEqual(check_reports.check(self.out)[0], [])

    def test_it_asks_the_two_questions_and_nothing_else(self):
        self.every_outcome()
        self.run_reporter("2026-10-28")
        self.assertEqual({path for path, _ in self.console.asked}, {daily_report.TOOL_ROUTE})
        for n in range(1, 9):
            asked = [body for _, body in self.console.asked if body["peer_id"] == node_id(n)]
            self.assertEqual(sorted(json.dumps(b["request"], sort_keys=True) for b in asked),
                             sorted(json.dumps(q, sort_keys=True) for q in daily_report.QUESTIONS.values()))
            for body in asked:
                self.assertEqual(set(body), {"peer_id", "request"})  # never verify: false

    def test_no_score_no_ranking_no_aggregate(self):
        self.every_outcome()
        self.run_reporter("2026-10-28")
        readme = (self.out / "2026-10-28" / "README.md").read_text()
        listed = [l for l in readme.splitlines() if l.startswith("- ")]
        self.assertEqual(listed, sorted(listed))  # by node id, nothing else
        for word in ("score", "rank", "total", "of 8", "%"):
            self.assertNotIn(word, readme.lower())
        run = json.loads((self.out / "2026-10-28" / "RUN.json").read_text())
        self.assertEqual(set(run), {"date", "reporter", "started", "finished", "questions", "asked"})

    def test_day_one_proof(self):
        self.every_outcome()
        self.run_reporter("2026-10-28")
        code, out = self.prove("2026-10-28")
        self.assertEqual(code, 0, out)  # nodes 1 and 7
        self.assertIn(f"{node_id(4)}: no", out)  # history_card/1 absent

    def test_no_checkpoint_yet_is_not_a_refusal_an_absence_or_a_missed_day(self):
        self.list_nodes(entry(node_id(1), key_id(1)))
        self.console = FakeConsole({node_id(1): {"checkpoints": "no_checkpoint_yet", "history_card": "no_checkpoint_yet"}},
                                   {node_id(1): key_id(1)})
        self.assertEqual(self.run_reporter("2026-10-28"), 0)
        day = self.out / "2026-10-28"
        self.assertFalse((day / "MISSED").exists())
        self.assertTrue((day / "RUN.json").exists())
        for stem in ("checkpoints", "history_card"):
            self.assertTrue(self.line("2026-10-28", 1, stem).startswith("no_checkpoint_yet "))
            self.assertEqual((day / node_id(1) / f"{stem}.response.json").read_bytes(),
                             (FIXTURES / "no_checkpoint_yet.json").read_bytes())
            self.assertFalse((day / node_id(1) / f"{stem}.absence.txt").exists())
        self.assertEqual(self.node_line("2026-10-28", 1), "joined, no checkpoint yet")
        self.assertEqual(check_reports.check(self.out, check_reports.dt.date(2026, 10, 28))[0], [])
        self.assertEqual(self.prove("2026-10-28")[0], 1)

    def test_day_one_proof_fails_with_one_verified_node(self):
        self.list_nodes(entry(node_id(1), key_id(1)), entry(node_id(2), key_id(2)))
        self.console = FakeConsole(
            {node_id(1): {"checkpoints": "verified", "history_card": "verified"},
             node_id(2): {"checkpoints": "refusal", "history_card": "verified"}},
            {node_id(1): key_id(1), node_id(2): key_id(2)})
        self.run_reporter("2026-10-28")
        self.assertEqual(self.prove("2026-10-28")[0], 1)

    def test_a_missed_day_is_marked_and_a_silent_gap_is_refused(self):
        self.list_nodes(entry(node_id(1), key_id(1)))
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                   {node_id(1): key_id(1)})
        self.assertEqual(self.run_reporter("2026-10-25"), 0)
        self.assertEqual(self.run_reporter("2026-10-28"), 0)
        for day in ("2026-10-26", "2026-10-27"):
            self.assertEqual([p.name for p in (self.out / day).iterdir()], ["MISSED"])
            self.assertIn("the reporter did not run", (self.out / day / "MISSED").read_text())
        through = check_reports.dt.date(2026, 10, 28)
        self.assertEqual(check_reports.check(self.out, through)[0], [])
        # Today's run not yet in: the watchdog sees the gap.
        self.assertEqual(check_reports.check(self.out, check_reports.dt.date(2026, 10, 29))[0],
                         ["2026-10-29: no report and no MISSED mark"])
        # A gap nobody marked is refused.
        (self.out / "2026-10-27" / "MISSED").unlink()
        (self.out / "2026-10-27").rmdir()
        self.assertEqual(check_reports.check(self.out, through)[0], ["2026-10-27: no report and no MISSED mark"])

    def test_a_reporter_that_cannot_reach_its_node_marks_the_day_missed(self):
        self.list_nodes(entry(node_id(1), key_id(1)))
        self.assertEqual(self.run_reporter("2026-10-28", "http://127.0.0.1:9"), 2)
        day = self.out / "2026-10-28"
        self.assertEqual([p.name for p in day.iterdir()], ["MISSED"])
        self.assertIn("could not reach its own node", (day / "MISSED").read_text())
        self.assertEqual(check_reports.check(self.out)[0], [])
        # A later run the same day replaces the mark.
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                   {node_id(1): key_id(1)})
        self.assertEqual(self.run_reporter("2026-10-28"), 0)
        self.assertFalse((day / "MISSED").exists())
        self.assertEqual(check_reports.check(self.out)[0], [])

    def test_a_day_is_run_once(self):
        self.list_nodes(entry(node_id(1), key_id(1)))
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                   {node_id(1): key_id(1)})
        self.assertEqual(self.run_reporter("2026-10-28"), 0)
        before = (self.out / "2026-10-28" / "RUN.json").read_bytes()
        self.assertEqual(self.run_reporter("2026-10-28"), 1)
        self.assertEqual((self.out / "2026-10-28" / "RUN.json").read_bytes(), before)

    def test_the_reporter_does_not_ask_itself(self):
        self.list_nodes(entry(node_id(1), key_id(1)), entry(REPORTER["node_id"], REPORTER["key_id"]))
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                   {node_id(1): key_id(1)})
        self.assertEqual(self.run_reporter("2026-10-28"), 0)
        self.assertEqual({b["peer_id"] for _, b in self.console.asked}, {node_id(1)})

    def test_a_placeholder_reporter_key_is_refused_before_anything_is_asked(self):
        placeholder = json.loads((ROOT / "reporter" / "reporter.json").read_text())
        self.reporter.write_text(json.dumps(placeholder))
        self.list_nodes(entry(node_id(1), key_id(1)))
        self.console = FakeConsole({}, {})
        self.assertEqual(self.run_reporter("2026-10-28"), 1)
        self.assertEqual(self.console.asked, [])
        self.assertFalse(self.out.exists())

    def test_the_reporter_key_must_be_this_nodes_plugin_key(self):
        data = self.dir / "capsules"
        (data / "keys").mkdir(parents=True)
        (data / "self-peer-id").write_text(REPORTER["node_id"] + "\n")
        (data / "keys" / "node-key.pub.pem").write_text(spki_pem(REPORTER["key_id"]))
        private_key(data)
        self.assertEqual(reporter_key.read_identity(data), REPORTER)
        self.list_nodes(entry(node_id(1), key_id(1)))
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                   {node_id(1): key_id(1)})
        (data / "keys" / "node-key.pub.pem").write_text(spki_pem("c" * 64))
        self.assertEqual(self.run_reporter("2026-10-28", None, "--data-dir", str(data)), 1)
        self.assertEqual(self.console.asked, [])
        (data / "keys" / "node-key.pub.pem").write_text(spki_pem(REPORTER["key_id"]))
        self.assertEqual(self.run_reporter("2026-10-28", None, "--data-dir", str(data)), 0)

    def test_a_private_key_readable_beyond_its_user_is_refused(self):
        data = self.dir / "capsules"
        (data / "keys").mkdir(parents=True)
        (data / "self-peer-id").write_text(REPORTER["node_id"] + "\n")
        (data / "keys" / "node-key.pub.pem").write_text(spki_pem(REPORTER["key_id"]))
        key = private_key(data, 0o640)
        self.list_nodes(entry(node_id(1), key_id(1)))
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                   {node_id(1): key_id(1)})
        self.assertEqual(self.run_reporter("2026-10-28", None, "--data-dir", str(data)), 1)
        self.assertEqual(self.console.asked, [])
        key.unlink()  # no private key at all is refused too
        self.assertEqual(self.run_reporter("2026-10-28", None, "--data-dir", str(data)), 1)
        private_key(data)
        self.assertEqual(self.run_reporter("2026-10-28", None, "--data-dir", str(data)), 0)

    def test_a_run_that_stopped_midway_is_replaced(self):
        self.list_nodes(entry(node_id(1), key_id(1)))
        stale = self.out / "2026-10-28" / node_id(1)
        stale.mkdir(parents=True)
        (stale / "NODE").write_text("joined, discloses nothing\n")
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                   {node_id(1): key_id(1)})
        self.assertEqual(self.run_reporter("2026-10-28"), 0)
        self.assertEqual(self.node_line("2026-10-28", 1), "discloses: checkpoints, history_card/1")
        self.assertEqual(check_reports.check(self.out)[0], [])

    def test_reporter_key_writes_the_public_identity_only(self):
        data = self.dir / "capsules"
        (data / "keys").mkdir(parents=True)
        (data / "self-peer-id").write_text(REPORTER["node_id"])
        (data / "keys" / "node-key.pub.pem").write_text(spki_pem(REPORTER["key_id"]))
        target = self.dir / "reporter-out.json"
        target.write_text((ROOT / "reporter" / "reporter.json").read_text())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(reporter_key.main(["reporter_key.py", str(data), "--write", str(target)]), 0)
        written = json.loads(target.read_text())
        self.assertEqual({k: written[k] for k in ("node_id", "key_id")}, REPORTER)
        self.assertEqual(written["mesh"], "lab")

    def test_check_reports_refuses_anything_beside_the_answers(self):
        self.list_nodes(entry(node_id(1), key_id(1)))
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "absence"}},
                                   {node_id(1): key_id(1)})
        self.run_reporter("2026-10-28")
        node = self.out / "2026-10-28" / node_id(1)
        (node / "score.json").write_text("{}")
        self.assertIn(f"2026-10-28/{node_id(1)}: unexpected file(s) score.json", check_reports.check(self.out)[0])
        (node / "score.json").unlink()
        (node / "history_card.verification").write_text("verified checkpoints=1\n")
        self.assertIn(f"2026-10-28/{node_id(1)}: history_card.verification says verified but the answer is absent",
                      check_reports.check(self.out)[0])
        (self.out / "2026-10-28" / "ranking.md").write_text("")
        self.assertIn("2026-10-28: unexpected entry ranking.md", check_reports.check(self.out)[0])


if __name__ == "__main__":
    unittest.main()
