"""Offline retry: a node that does not answer is asked again, and every attempt is kept.

A fake clock stands in for time: the reporter's `sleep` moves it, so a run that
asks again over six hours takes milliseconds. Run: python3 -B -m unittest discover -s tests -v
"""
import contextlib
import datetime as dt
import hashlib
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
import report_layout as layout  # noqa: E402
from test_reporter import spki_pem  # noqa: E402

FIXTURES = ROOT / "tests" / "reporter" / "fixtures"
REPORTER = {"node_id": "e" * 64, "key_id": "f" * 64}
SCHEDULE = [3600, 10800, 21600]  # three more attempts over six hours: +1 h, +3 h, +6 h
DAY = "2026-10-28"

# What the reporter's console answers when its plugin got no answer (a 502 with
# the plugin's error text, mesh-llm handle_call over capsules SendError).
NO_REPLY = {
    "unreachable": b"could not reach peer: no route to endpoint",
    "no_answer": b"peer does not answer evidence requests",
    "malformed": b"peer returned malformed response: expected value at line 1 column 1",
}


def node_id(n):
    return f"{n:x}" * 64


def key_id(n):
    return f"{n + 1:x}" * 64


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


def both(what):
    return {"checkpoints": what, "history_card": what}


class Clock:
    """The reporter's clock. sleep() moves it and records how long; `at` hooks fire on the way."""

    def __init__(self, start):
        self.t, self.slept, self.at = start, [], []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += dt.timedelta(seconds=seconds)
        for when, hook in list(self.at):
            if self.t >= when:
                self.at.remove((when, hook))
                hook()


class FakeConsole:
    """The reporter node's console. behaviour[node][stem]: a fixture name, a NO_REPLY key, or "hang"."""

    def __init__(self, behaviour, announced):
        self.behaviour, self.announced = behaviour, announced
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
                    pem = spki_pem(REPORTER["key_id"])
                    return self.reply(200, json.dumps({"records": [], "node_pub_key_pem": pem}).encode())
                peer = urllib.parse.parse_qs(url.query)["peer"][0]
                self.reply(200, json.dumps({"announced_key_id": fake.announced.get(peer)}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stem = "history_card" if body["request"].get("derivation") else "checkpoints"
                fake.asked.append((body["peer_id"], stem))
                what = fake.behaviour[body["peer_id"]][stem]
                if what == "hang":
                    time.sleep(0.6)  # past the reporter's 0.2 s timeout: nothing comes back
                    return
                if what in NO_REPLY:
                    return self.reply(502, NO_REPLY[what])
                self.reply(200, (FIXTURES / f"{what}.json").read_bytes())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def times(self, nid, stem):
        return self.asked.count((nid, stem))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def digests(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


class OfflineRetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.nodes = self.dir / "nodes"
        self.nodes.mkdir()
        self.out = self.dir / "reports" / "daily"
        self.reporter = self.dir / "reporter.json"
        self.reporter.write_text(json.dumps({**REPORTER, "retry_after_s": SCHEDULE}))
        self.clock = Clock(dt.datetime(2026, 10, 28, 6, 0, 0, tzinfo=dt.timezone.utc))
        self.console = None

    def tearDown(self):
        if self.console:
            self.console.close()
        self.tmp.cleanup()

    def setup_nodes(self, behaviour):
        for nid in behaviour:
            (self.nodes / f"{nid}.json").write_text(json.dumps(entry(nid, key_id(int(nid[0], 16)))))
        self.console = FakeConsole(behaviour, {nid: key_id(int(nid[0], 16)) for nid in behaviour})

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = daily_report.main(["daily_report.py", *argv], now=self.clock.now, sleep=self.clock.sleep)
        return code, out.getvalue() + err.getvalue()

    def common(self):
        return ["--console", self.console.url, "--nodes", str(self.nodes), "--out", str(self.out),
                "--reporter", str(self.reporter), "--timeout", "0.2"]

    def run_day(self, date=DAY):
        code, out = self.main("run", "--date", date, *self.common())
        self.assertEqual(code, 0, out)

    def ask(self, *which, date=DAY):
        return self.main("ask", *which, "--date", date, *self.common())

    def node(self, n, date=DAY):
        return self.out / date / node_id(n)

    def attempts(self, n, stem, date=DAY):
        """[(time, by, state)] for the attempts that asked `stem`."""
        rows = []
        for a in layout.attempts(self.node(n, date)):
            if (a / f"{stem}.verification").is_file():
                at, by = layout.read_attempt(a)
                rows.append((at, by, layout.state((a / f"{stem}.verification").read_text())))
        return rows

    def line(self, n, stem, date=DAY):
        return (self.node(n, date) / f"{stem}.verification").read_text().strip()

    def readme_line(self, n, date=DAY):
        readme = (self.out / date / "README.md").read_text().splitlines()
        return next(l for l in readme if l.startswith(f"- `{node_id(n)}`"))

    def test_offline_then_online_the_same_day_ends_verified_with_every_attempt_kept(self):
        self.setup_nodes({node_id(1): both("unreachable")})
        flip = lambda what: lambda: self.console.behaviour.update({node_id(1): both(what)})  # noqa: E731
        self.clock.at += [(self.clock.t + dt.timedelta(hours=1), flip("no_answer")),
                          (self.clock.t + dt.timedelta(hours=3), flip("verified"))]
        self.run_day()
        for stem in layout.STEMS:
            self.assertEqual(self.attempts(1, stem), [
                ("2026-10-28T06:00:00Z", "run pass=1", "unreachable"),
                ("2026-10-28T07:00:00Z", "run pass=2", "timeout"),
                ("2026-10-28T09:00:00Z", "run pass=3", "verified"),
            ])
            self.assertTrue(self.line(1, stem).startswith("verified "))
            answered = layout.attempts(self.node(1))[-1]
            self.assertEqual((self.node(1) / f"{stem}.response.json").read_bytes(),
                             (answered / f"{stem}.response.json").read_bytes())
            self.assertEqual((self.node(1) / f"{stem}.response.json").read_bytes(),
                             (FIXTURES / "verified.json").read_bytes())
        self.assertEqual(self.clock.slept, [3600, 7200])  # answered at +3 h: no fourth attempt
        self.assertEqual((self.node(1) / "NODE").read_text(), "discloses: checkpoints, history_card/1\n")
        self.assertEqual(self.readme_line(1), f"- `{node_id(1)}`: discloses: checkpoints, history_card/1")
        self.assertEqual(check_reports.check(self.out)[0], [])

    def test_offline_all_day_is_an_absence_listing_each_attempt_time(self):
        self.setup_nodes({node_id(1): both("unreachable"), node_id(2): both("verified")})
        self.run_day()
        times = ["2026-10-28T06:00:00Z", "2026-10-28T07:00:00Z", "2026-10-28T09:00:00Z", "2026-10-28T12:00:00Z"]
        for stem in layout.STEMS:
            self.assertEqual([t for t, _, _ in self.attempts(1, stem)], times)
            line = self.line(1, stem)
            self.assertTrue(line.startswith('absence why="http 502: could not reach peer'), line)
            self.assertTrue(line.endswith(" attempts=" + ",".join(times)), line)
            text = (self.node(1) / f"{stem}.absence.txt").read_text()
            self.assertTrue(text.startswith("no answer after 4 attempts:\n"))
            for t in times:
                self.assertIn(f"{t} unreachable: http 502 could not reach peer", text)
            self.assertFalse((self.node(1) / f"{stem}.response.json").exists())
            self.assertEqual(len(self.attempts(2, stem)), 1)  # the node that answered was asked once
        self.assertEqual(self.clock.slept, [3600, 7200, 10800])
        self.assertEqual((self.node(1) / "NODE").read_text(), "joined, discloses nothing\n")
        self.assertEqual(self.readme_line(1), f"- `{node_id(1)}`: joined, discloses nothing; absent today")
        self.assertEqual(check_reports.check(self.out)[0], [])

    def test_a_refusal_is_never_asked_again_as_if_it_were_an_outage(self):
        self.setup_nodes({
            node_id(1): {"checkpoints": "refusal", "history_card": "unreachable"},
            node_id(2): both("refusal"),
            node_id(3): both("no_checkpoint_yet"),
        })
        self.run_day()
        self.assertEqual(self.console.times(node_id(1), "checkpoints"), 1)
        self.assertEqual(self.console.times(node_id(1), "history_card"), 4)
        self.assertEqual(self.line(1, "checkpoints"), f"refusal reason=policy_declined key={key_id(1)} listed_key=match")
        self.assertTrue(self.line(1, "history_card").startswith("absence "))
        for stem in layout.STEMS:
            self.assertEqual(self.console.times(node_id(2), stem), 1)
            self.assertTrue(self.line(2, stem).startswith("refusal "))
            # no_checkpoint_yet is a signed answer: not retried, not a refusal, not an absence.
            self.assertEqual(self.console.times(node_id(3), stem), 1)
            self.assertTrue(self.line(3, stem).startswith("no_checkpoint_yet reason=coverage_unsatisfiable "))
        self.assertEqual((self.node(3) / "NODE").read_text(), "joined, no checkpoint yet\n")
        self.assertEqual(self.readme_line(2), f"- `{node_id(2)}`: joined, discloses nothing")  # refused: not absent
        self.assertEqual(check_reports.check(self.out)[0], [])

    def test_nothing_waits_when_every_node_answered(self):
        self.setup_nodes({node_id(1): both("refusal"), node_id(2): both("verified")})
        self.run_day()
        self.assertEqual(self.clock.slept, [])

    def test_the_outcome_classes_come_from_what_the_route_returns(self):
        self.reporter.write_text(json.dumps(REPORTER))  # one attempt
        self.setup_nodes({
            node_id(1): both("unreachable"),
            node_id(2): both("no_answer"),
            node_id(3): both("malformed"),
            node_id(4): {"checkpoints": "hang", "history_card": "verified"},
        })
        self.run_day()
        state = lambda n, stem: self.attempts(n, stem)[0][2]  # noqa: E731
        self.assertEqual(state(1, "checkpoints"), "unreachable")
        self.assertEqual(state(2, "checkpoints"), "timeout")
        self.assertEqual(state(3, "checkpoints"), "not_evidence")  # an answer the plugin could not read
        self.assertEqual(state(4, "checkpoints"), "timeout")  # no reply from the reporter's own node in time
        self.assertTrue(self.line(3, "checkpoints").startswith('not_evidence why="http 502: peer returned malformed'))
        self.assertTrue((self.node(3) / "checkpoints.absence.txt").is_file())
        self.assertTrue(self.line(4, "checkpoints").startswith('absence why="no reply from the reporter\'s node within'))
        self.assertEqual(check_reports.check(self.out)[0], [])

    def test_a_malformed_answer_is_not_asked_again(self):
        self.setup_nodes({node_id(1): both("malformed")})
        self.run_day()
        self.assertEqual(self.console.times(node_id(1), "checkpoints"), 1)
        self.assertEqual(self.clock.slept, [])

    def test_asking_one_node_again_adds_an_attempt_and_changes_no_earlier_byte(self):
        self.setup_nodes({node_id(1): both("unreachable"), node_id(2): both("unreachable")})
        self.run_day()
        before = digests(self.out)
        self.clock.t = dt.datetime(2026, 10, 28, 15, 30, 0, tzinfo=dt.timezone.utc)
        self.console.behaviour[node_id(1)] = both("verified")
        code, out = self.ask("--node", node_id(1))
        self.assertEqual(code, 0, out)
        after = digests(self.out)
        self.assertEqual({k: v for k, v in after.items() if k in before}, before)  # every earlier file byte-identical
        added = sorted(set(after) - set(before))
        attempt = f"{DAY}/{node_id(1)}/attempts/20261028T153000Z"
        self.assertEqual(added, sorted(f"{attempt}/{f}" for f in [
            "ATTEMPT", "peer-key.json",
            "checkpoints.request.json", "checkpoints.response.json", "checkpoints.verification",
            "history_card.request.json", "history_card.response.json", "history_card.verification"]))
        self.assertEqual((self.out / attempt / "ATTEMPT").read_text(), "at=2026-10-28T15:30:00Z by=ask\n")
        day = layout.answers(self.node(1))
        self.assertEqual({s: day[s]["state"] for s in layout.STEMS}, {"checkpoints": "verified", "history_card": "verified"})
        self.assertFalse(layout.absent(self.node(1)))
        self.assertEqual(self.console.times(node_id(2), "checkpoints"), 4)  # the other node was not asked again
        self.assertEqual(check_reports.check(self.out)[0], [])
        # Answered now: asking again asks nothing and adds nothing.
        self.clock.t += dt.timedelta(minutes=5)
        self.assertEqual(self.ask("--node", node_id(1))[0], 0)
        self.assertEqual(digests(self.out), after)

    def test_asking_every_absent_node_again(self):
        self.reporter.write_text(json.dumps(REPORTER))
        self.setup_nodes({
            node_id(1): both("unreachable"),
            node_id(2): both("verified"),
            node_id(3): {"checkpoints": "refusal", "history_card": "no_answer"},
        })
        self.run_day()
        self.clock.t += dt.timedelta(hours=4)
        code, out = self.ask("--absent")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(layout.attempts(self.node(1))), 2)
        self.assertEqual(len(layout.attempts(self.node(2))), 1)  # answered: not asked again
        self.assertEqual(self.console.times(node_id(3), "checkpoints"), 1)  # its refusal stands
        self.assertEqual(self.console.times(node_id(3), "history_card"), 2)
        self.assertEqual(check_reports.check(self.out)[0], [])

    def test_ask_needs_a_settled_run_and_its_own_node(self):
        self.setup_nodes({node_id(1): both("unreachable")})
        code, out = self.ask("--node", node_id(1))
        self.assertEqual(code, 1)
        self.assertIn("no settled run", out)
        self.assertEqual(self.console.asked, [])
        self.reporter.write_text(json.dumps(REPORTER))
        self.run_day()
        self.assertEqual(self.ask("--node", "9" * 64)[0], 1)  # not asked that day
        before = digests(self.out)
        code, out = self.main("ask", "--node", node_id(1), "--date", DAY, "--console", "http://127.0.0.1:9",
                              "--nodes", str(self.nodes), "--out", str(self.out), "--reporter", str(self.reporter))
        self.assertEqual(code, 2)
        self.assertEqual(digests(self.out), before)  # no MISSED over a settled day

    def test_absent_days_running_is_a_fact_about_the_node(self):
        self.reporter.write_text(json.dumps(REPORTER))
        self.setup_nodes({node_id(1): both("unreachable"), node_id(2): both("verified")})
        for day in ("2026-10-26", "2026-10-27", "2026-10-28"):
            self.run_day(day)
        self.assertEqual(self.readme_line(1, "2026-10-26"), f"- `{node_id(1)}`: joined, discloses nothing; absent today")
        self.assertEqual(self.readme_line(1), f"- `{node_id(1)}`: joined, discloses nothing; absent 3 days running")
        self.assertEqual(self.readme_line(2), f"- `{node_id(2)}`: discloses: checkpoints, history_card/1")
        readme = (self.out / DAY / "README.md").read_text().lower()
        for word in ("score", "rank", "total", "%"):
            self.assertNotIn(word, readme)
        # An answer on a re-ask ends the run of days; a missed day breaks it.
        self.clock.t = dt.datetime(2026, 10, 28, 18, 0, 0, tzinfo=dt.timezone.utc)
        self.console.behaviour[node_id(1)] = both("refusal")
        self.assertEqual(self.ask("--node", node_id(1))[0], 0)
        self.assertEqual(layout.absent_days_running(self.out, node_id(1), dt.date(2026, 10, 28)), 0)
        self.assertEqual(layout.absent_days_running(self.out, node_id(1), dt.date(2026, 10, 27)), 2)
        self.console.behaviour[node_id(1)] = both("unreachable")
        self.run_day("2026-10-30")  # 2026-10-29 is marked MISSED
        self.assertEqual(self.readme_line(1, "2026-10-30"), f"- `{node_id(1)}`: joined, discloses nothing; absent today")

    def test_the_retry_schedule_is_configuration(self):
        committed = json.loads((ROOT / "reporter" / "reporter.json").read_text())
        self.assertEqual(committed["retry_after_s"], SCHEDULE)
        self.setup_nodes({node_id(1): both("unreachable")})
        self.reporter.write_text(json.dumps({**REPORTER, "retry_after_s": [600]}))
        self.run_day()
        self.assertEqual(self.clock.slept, [600])
        self.assertEqual(self.console.times(node_id(1), "checkpoints"), 2)
        for bad in ([600, 60], [0], "6h", [True]):
            self.reporter.write_text(json.dumps({**REPORTER, "retry_after_s": bad}))
            code, out = self.main("run", "--date", "2026-10-29", *self.common())
            self.assertEqual(code, 1, bad)
            self.assertIn("retry_after_s", out)
        self.assertFalse((self.out / "2026-10-29").exists())

    def test_check_reports_accepts_the_attempts_and_refuses_stray_files(self):
        self.reporter.write_text(json.dumps({**REPORTER, "retry_after_s": [600]}))
        self.setup_nodes({node_id(1): both("unreachable")})
        self.clock.at.append((self.clock.t, lambda: self.console.behaviour.update({node_id(1): both("verified")})))
        self.run_day()
        self.assertEqual(check_reports.check(self.out)[0], [])
        node = self.node(1)
        first, second = layout.attempts(node)
        where = f"{DAY}/{node_id(1)}/attempts"

        def refused(expected):
            self.assertIn(expected, check_reports.check(self.out)[0])

        (first / "score.json").write_text("{}")
        refused(f"{where}/{first.name}: unexpected file(s) score.json")
        (first / "score.json").unlink()
        (node / "attempts" / "ranking").mkdir()
        refused(f"{where}/ranking: not a <YYYYMMDDTHHMMSSZ> attempt directory")
        (node / "attempts" / "ranking").rmdir()
        (node / "attempts" / "notes.txt").write_text("")
        refused(f"{where}/notes.txt: not a <YYYYMMDDTHHMMSSZ> attempt directory")
        (node / "attempts" / "notes.txt").unlink()
        self.assertEqual(check_reports.check(self.out)[0], [])

        attempt_line = (first / "ATTEMPT").read_text()
        (first / "ATTEMPT").write_text("at=2026-10-28T05:00:00Z by=run pass=1\n")
        refused(f"{where}/{first.name}: ATTEMPT says at=2026-10-28T05:00:00Z, not the directory's time")
        (first / "ATTEMPT").write_text(attempt_line)

        # An attempt that says no answer came cannot hold an answer.
        (first / "checkpoints.response.json").write_bytes(b"{}")
        refused(f"{where}/{first.name}: needs exactly one of checkpoints.response.json or checkpoints.absence.txt")
        (first / "checkpoints.response.json").unlink()

        # The settled answer must be bytes some attempt received.
        settled = node / "checkpoints.response.json"
        kept = settled.read_bytes()
        settled.write_bytes(kept.replace(b"}", b' , "extra": 1}', 1))
        refused(f"{DAY}/{node_id(1)}: checkpoints.response.json is not the answer of any attempt")
        settled.write_bytes(kept)

        for p in sorted(second.iterdir()):
            p.unlink()
        second.rmdir()
        for p in sorted(first.iterdir()):
            p.unlink()
        first.rmdir()
        (node / "attempts").rmdir()
        refused(f"{DAY}/{node_id(1)}: no attempts/: every answer is kept with the attempt that got it")


if __name__ == "__main__":
    unittest.main()
