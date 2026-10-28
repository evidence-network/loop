"""Tests for the daily reporter, against a fake reporter-node console.

Run: python3 -m unittest discover -s tests -v
"""
import base64
import contextlib
import io
import json
import os
import shutil
import subprocess
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
import check_nodes  # noqa: E402
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
    or "timeout"; announced[node_id] is the key its host announces, or None;
    signing is the key the console's own plugin signs with (its ledger route).
    """

    def __init__(self, behaviour, announced, delay=1.0, signing=REPORTER["key_id"]):
        self.behaviour, self.announced, self.delay, self.signing = behaviour, announced, delay, signing
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
                if url.path == daily_report.LEDGER_ROUTE:
                    pem = spki_pem(fake.signing) if fake.signing else None
                    return self.reply(200, json.dumps({"records": [], "node_pub_key_pem": pem}).encode())
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
        "consent": check_nodes.CONSENT,
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
            node_id(1): {"checkpoints": "verified", "history_card": "verified_history_card"},
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
        self.assertEqual(self.line(d, 1, "history_card"),
                         f"verified halves=2 anchor={'a' * 64} key={key_id(1)} listed_key=match")
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
        self.assertEqual(set(run), {"date", "reporter", "started", "finished", "questions", "asked", "versions_lock_sha256"})

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

    def test_a_run_that_stopped_on_an_earlier_day_is_marked_missed(self):
        self.list_nodes(entry(node_id(1), key_id(1)))
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                   {node_id(1): key_id(1)})
        self.assertEqual(self.run_reporter("2026-10-26"), 0)
        stale = self.out / "2026-10-27" / node_id(1)
        stale.mkdir(parents=True)
        (stale / "NODE").write_text("joined, discloses nothing\n")
        ahead = self.out / "2026-11-02" / node_id(1)  # a run under a clock that ran ahead
        ahead.mkdir(parents=True)
        self.assertEqual(self.run_reporter("2026-10-30"), 0)
        for day in ("2026-10-28", "2026-10-29"):  # the gap before today is still marked
            self.assertIn("the reporter did not run", (self.out / day / "MISSED").read_text())
        for day in ("2026-10-27", "2026-11-02"):
            self.assertEqual([p.name for p in (self.out / day).iterdir()], ["MISSED"])
            self.assertIn("stopped before it finished", (self.out / day / "MISSED").read_text())
        self.assertEqual(check_reports.check(self.out, check_reports.dt.date(2026, 10, 30))[0], [])

    def test_a_run_stopped_before_run_json_is_replaced_the_next_day(self):
        self.list_nodes(entry(node_id(1), key_id(1)))
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                   {node_id(1): key_id(1)})
        self.assertEqual(self.run_reporter("2026-10-26"), 0)
        self.assertEqual(self.run_reporter("2026-10-27"), 0)
        # Stopped after README.md, mid-way through writing RUN.json.
        day = self.out / "2026-10-27"
        (day / "RUN.json").replace(day / "RUN.json.partial")
        self.assertEqual(self.run_reporter("2026-10-28"), 0)
        self.assertEqual([p.name for p in day.iterdir()], ["MISSED"])
        self.assertEqual(check_reports.check(self.out, check_reports.dt.date(2026, 10, 28))[0], [])

    def test_the_console_must_sign_with_the_reporter_key(self):
        self.list_nodes(entry(node_id(1), key_id(1)))
        for signing in ("c" * 64, None):
            self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                       {node_id(1): key_id(1)}, signing=signing)
            self.assertEqual(self.run_reporter("2026-10-28"), 1)
            self.assertEqual(self.console.asked, [])
            self.assertFalse((self.out / "2026-10-28").exists())
            self.console.close()
            self.console = None

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

    def test_the_watchdog_refuses_a_deployed_reporter_that_never_ran(self):
        through = check_reports.dt.date(2026, 10, 28)
        self.assertEqual(check_reports.check(self.out, through, self.reporter)[0],
                         [f"{self.reporter} names a reporter key, but {self.out} holds no report"])
        placeholder = self.dir / "placeholder.json"
        # The placeholder's shape, with the all-zero ids it had before the reporter node existed.
        shape = json.loads((ROOT / "reporter" / "reporter.json").read_text())
        placeholder.write_text(json.dumps(dict(shape, node_id="0" * 64, key_id="0" * 64)))
        self.assertEqual(check_reports.check(self.out, through, placeholder)[0], [])

    def test_check_reports_requires_peer_key_and_the_day_readme(self):
        self.list_nodes(entry(node_id(1), key_id(1)))
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                   {node_id(1): key_id(1)})
        self.assertEqual(self.run_reporter("2026-10-28"), 0)
        (self.out / "2026-10-28" / node_id(1) / "peer-key.json").unlink()
        (self.out / "2026-10-28" / "README.md").unlink()
        self.assertEqual(sorted(check_reports.check(self.out)[0]),
                         [f"2026-10-28/{node_id(1)}: missing peer-key.json", "2026-10-28: missing README.md"])

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


class RunDailyTest(unittest.TestCase):
    """run_daily.sh in a throwaway loop clone that pushes its reports to a scratch loop-data repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.origin, self.clone, data = tmp / "origin.git", tmp / "loop", tmp / "capsules"
        self.data_origin, self.data = tmp / "loop-data.git", tmp / "loop-data"
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "CAPSULES_DATA_DIR": str(data), "LOOP_DATA_DIR": str(self.data)}
        self.env.pop("CAPSULES_PEER_KEYS", None)
        self.git(tmp, "init", "--quiet", "--bare", "-b", "main", str(self.origin))
        self.git(tmp, "init", "--quiet", "-b", "main", str(self.clone))
        (self.clone / "scripts").mkdir()
        for name in ("check_nodes.py", "check_reports.py", "daily_report.py", "report_layout.py", "reporter_key.py",
                     "run_daily.sh", "loop_data.sh"):
            shutil.copy2(ROOT / "scripts" / name, self.clone / "scripts" / name)
        shutil.copy2(ROOT / "versions.lock", self.clone / "versions.lock")
        (self.clone / "reporter").mkdir()
        (self.clone / "reporter" / "reporter.json").write_text(json.dumps(REPORTER))
        (self.clone / "nodes").mkdir()
        (self.clone / "nodes" / f"{node_id(1)}.json").write_text(json.dumps(entry(node_id(1), key_id(1))))
        (self.clone / "README.md").write_text("loop\n")
        self.seed(self.clone, self.origin)
        self.git(tmp, "init", "--quiet", "--bare", "-b", "main", str(self.data_origin))
        self.git(tmp, "init", "--quiet", "-b", "main", str(self.data))
        (self.data / "reports" / "daily").mkdir(parents=True)
        (self.data / "reports" / "daily" / ".gitkeep").write_text("")
        (self.data / "README.md").write_text("loop-data\n")
        self.seed(self.data, self.data_origin)
        (data / "keys").mkdir(parents=True)
        (data / "self-peer-id").write_text(REPORTER["node_id"] + "\n")
        (data / "keys" / "node-key.pub.pem").write_text(spki_pem(REPORTER["key_id"]))
        private_key(data)
        self.console = FakeConsole({node_id(1): {"checkpoints": "verified", "history_card": "verified"}},
                                   {node_id(1): key_id(1)})
        self.env["LOOP_CONSOLE"] = self.console.url
        self.loop_head = self.head(self.origin)

    def tearDown(self):
        # The loop clone is only read: nothing committed there, nothing pushed to loop, whatever the run did.
        self.assertEqual(self.head(self.origin), self.loop_head)
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD").strip(), self.loop_head)
        status = self.git(self.clone, "status", "--porcelain").splitlines()
        self.assertEqual([l for l in status if "__pycache__" not in l], [])
        self.console.close()
        self.tmp.cleanup()

    def seed(self, repo, origin):
        self.git(repo, "config", "user.name", "reporter")
        self.git(repo, "config", "user.email", "reporter@example.invalid")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "--quiet", "-m", "seed")
        self.git(repo, "remote", "add", "origin", str(origin))
        self.git(repo, "push", "--quiet", "-u", "origin", "main")

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def run_daily(self):
        return subprocess.run(["bash", str(self.clone / "scripts" / "run_daily.sh")], cwd=self.clone,
                              env=self.env, capture_output=True, text=True)

    def head(self, origin):
        return self.git(origin, "rev-parse", "main").strip()

    def origin_head(self):
        return self.head(self.data_origin)

    def test_a_report_is_pushed_to_loop_data_main_and_touches_only_reports_daily(self):
        before = self.origin_head()
        done = self.run_daily()
        self.assertEqual(done.returncode, 0, done.stderr)
        changed = self.git(self.data_origin, "diff", "--name-only", before, "main").split()
        self.assertTrue(changed)
        self.assertTrue(all(name.startswith("reports/daily/") for name in changed), changed)
        self.assertTrue(any(name.endswith("checkpoints.response.json") for name in changed), changed)
        self.assertIn("report: daily", self.git(self.data_origin, "log", "-1", "--format=%s", "main"))
        self.assertFalse((self.clone / "reports").exists())  # no raw byte lands in the loop clone

    def test_loop_data_dir_is_required(self):
        before = self.origin_head()
        for value in (None, "", str(self.clone), str(Path(self.tmp.name) / "nowhere")):
            if value is None:
                self.env.pop("LOOP_DATA_DIR", None)
            else:
                self.env["LOOP_DATA_DIR"] = value
            done = self.run_daily()
            self.assertEqual(done.returncode, 1, (value, done.stderr))
            self.assertIn("LOOP_DATA_DIR", done.stderr)
            self.assertEqual(self.console.asked, [])
        self.assertEqual(self.origin_head(), before)

    def test_a_local_commit_outside_reports_daily_is_not_pushed(self):
        before = self.origin_head()
        (self.data / "README.md").write_text("changed\n")
        self.git(self.data, "commit", "--quiet", "-am", "local change")
        done = self.run_daily()
        self.assertEqual(done.returncode, 1)
        self.assertIn("outside reports/daily", done.stderr)
        self.assertEqual(self.origin_head(), before)

    def test_a_file_added_then_deleted_in_local_commits_is_not_pushed(self):
        before = self.origin_head()
        (self.data / "secret.txt").write_text("not for the repo\n")
        self.git(self.data, "add", "secret.txt")
        self.git(self.data, "commit", "--quiet", "-m", "add")
        self.git(self.data, "rm", "--quiet", "secret.txt")
        self.git(self.data, "commit", "--quiet", "-m", "remove")
        done = self.run_daily()
        self.assertEqual(done.returncode, 1)
        self.assertIn("secret.txt", done.stderr)
        self.assertEqual(self.origin_head(), before)

    def test_a_failed_commit_fails_the_run(self):
        before = self.origin_head()
        self.git(self.data, "config", "--unset", "user.email")
        self.git(self.data, "config", "user.useConfigOnly", "true")
        done = self.run_daily()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.origin_head(), before)

    def test_no_git_identity_is_refused_before_asking_anyone(self):
        before = self.origin_head()
        self.git(self.data, "config", "--unset", "user.email")
        self.git(self.data, "config", "--unset", "user.name")
        self.git(self.data, "config", "user.useConfigOnly", "true")
        done = self.run_daily()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("no git identity to commit with", done.stderr)
        self.assertEqual(self.console.asked, [])
        self.assertEqual(self.git(self.data, "status", "--porcelain"), "")
        self.assertEqual(self.origin_head(), before)

    def test_it_runs_only_on_main(self):
        before = self.origin_head()
        self.git(self.data, "checkout", "--quiet", "-b", "other")
        done = self.run_daily()
        self.assertEqual(done.returncode, 1)
        self.assertIn("not on main", done.stderr)
        self.git(self.data, "checkout", "--quiet", "main")
        self.git(self.clone, "checkout", "--quiet", "-b", "other")
        done = self.run_daily()
        self.assertEqual(done.returncode, 1)
        self.assertIn("not on main: the loop clone", done.stderr)
        self.git(self.clone, "checkout", "--quiet", "main")
        self.assertEqual(self.origin_head(), before)


if __name__ == "__main__":
    unittest.main()
