"""steps/07-ask: a request sealed before it is sent, answered or recorded absent by its deadline, or FAIL.

The book backend needs the pinned capsulectl and capsulectl-book
(steps/07-ask.setup.sh); without them those tests SKIP and say why, or, with
LOOP_REQUIRE_TOOLS=1 (CI, after the setups ran), fail to load. The full
rehearsal run takes a little over a minute: one ask waits out its deadline.
The reporter backend runs against a fake reporter console on localhost.
Fixtures: tests/fixtures/step07/, made by make_fixtures.py; each planted
failure there was made by a misbehaving asker or responder during a real run.
"""
import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "step07"
REPORTER_FIX = ROOT / "tests" / "reporter" / "fixtures"


def load_step():
    loader = importlib.machinery.SourceFileLoader("step07_ask", str(ROOT / "steps" / "07-ask"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


step = load_step()
finding = step.finding
CAPSULECTL = step.tools()
NO_TOOL = f"capsulectl not installed at {CAPSULECTL}: run steps/07-ask.setup.sh (CI does)"
BOB = (FIX / "bob.key_id").read_text().strip()
ALICE = json.loads((FIX / "good" / "alice" / "advertised" / "commitment.json").read_text())


def needs_capsulectl(test):
    if CAPSULECTL.is_file():
        return test
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":  # CI, after the setups: a missing tool is an error
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_TOOL}")
    print(f"\nSKIP {test.__name__}: {NO_TOOL}", file=sys.stderr)
    return unittest.skip(NO_TOOL)(test)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "findings" / "07-ask"
        self.out.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, name):
        """A copy of one fixture run (bob asked alice) as this step's findings directory."""
        shutil.copytree(FIX / name, self.out, dirs_exist_ok=True)
        return self.out

    def judge(self, name):
        """(failures, answered, proof, lines) for one fixture run, then its finding, which must be well formed."""
        out = self.fixture(name)
        failures, answered, proof, lines = step.judge_run(out, {"bob": BOB}, CAPSULECTL, Path(self.tmp.name) / "w")
        r = step.conclude(out, "rehearsal", failures, [], answered, proof, lines, finding.now())
        self.assertEqual(finding.check(out.parent), [])
        return failures, answered, r


class BookBackendTest(Tmp):
    @needs_capsulectl
    def test_rehearsal_population_asks_and_passes(self):
        pop = ROOT / "population" / "rehearsal"
        self.assertEqual(step.main(["--population", str(pop), "--out", str(self.out)]), 0)
        self.assertEqual(finding.check(self.out.parent), [])
        r = json.loads((self.out / "finding.json").read_text())
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertEqual(r["not_checked"], [])
        self.assertEqual(sorted(r["pinned"]), ["capsulectl", "capsulectl-book"])
        for pair in ("alice asked bob", "bob asked alice"):
            self.assertIn(f"{pair}: exchange refusal, exchange_late recorded_absence, history_card artifact",
                          r["detail"])
        first = json.loads((self.out / "first-answered.json").read_text())
        self.assertTrue(first["answered_at"] and first["ask"])

    @needs_capsulectl
    def test_the_unaltered_fixture_passes(self):
        failures, answered, r = self.judge("good")
        self.assertEqual(failures, [])
        self.assertEqual(r["result"], "PASS", r["detail"])
        # Two answers in time (an allowed refusal and an artifact); the late one is an absence, not an answer.
        self.assertEqual(sorted(n for _, n in answered), ["bob asked alice exchange", "bob asked alice history_card"])
        self.assertEqual(json.loads((self.out / "first-answered.json").read_text())["ask"],
                         "bob asked alice exchange")

    @needs_capsulectl
    def test_a_request_sealed_after_its_send_time_is_a_fail(self):
        failures, _, r = self.judge("sealed-after-send")
        self.assertEqual(r["result"], "FAIL")
        self.assertTrue(any("not before it was sent" in f for f in failures), failures)

    @needs_capsulectl
    def test_a_refusal_of_a_stranger_safe_subject_is_a_fail(self):
        failures, answered, r = self.judge("stranger-safe-refused")
        self.assertEqual(r["result"], "FAIL")
        self.assertTrue(any("refused (not_authorized), but the advertised policy lists it as stranger-safe" in f
                            for f in failures), failures)
        self.assertEqual(answered, [])

    @needs_capsulectl
    def test_an_answer_signed_by_a_key_other_than_the_listed_one_is_a_fail(self):
        failures, _, r = self.judge("wrong-key")
        self.assertEqual(r["result"], "FAIL")
        self.assertTrue(any("not the listed key" in f for f in failures), failures)
        self.assertTrue(any("does not verify again under the listed key" in f for f in failures), failures)

    @needs_capsulectl
    def test_an_answer_past_answers_within_counted_as_answered_is_a_fail(self):
        failures, answered, r = self.judge("late-counted")
        self.assertEqual(r["result"], "FAIL")
        self.assertTrue(any("after the deadline" in f and "answers_within PT1M" in f for f in failures), failures)
        self.assertEqual(answered, [])

    @needs_capsulectl
    def test_a_sent_ask_with_no_recorded_outcome_is_a_fail(self):
        out = self.fixture("good")
        shutil.rmtree(out / "bob" / "asks" / "alice" / "exchange_late")
        failures, _, _, _ = step.judge_run(out, {"bob": BOB}, CAPSULECTL, Path(self.tmp.name) / "w")
        self.assertIn("bob asked alice: exchange_late: sent, but neither answered nor recorded absent", failures)

    @needs_capsulectl
    def test_an_absence_closing_before_the_deadline_is_a_fail(self):
        # The carrier log says the late ask was sent later than it was: its absence window then closes too soon.
        out = self.fixture("good")
        log = out / "bob" / "asks" / "alice" / "carrier.jsonl"
        events = [json.loads(line) for line in log.read_text().splitlines()]
        for e in events:
            if e["ask"] == "exchange_late" and e["event"] == "sent":
                e["at"] = step.stamp(step.utc(e["at"]) + dt.timedelta(seconds=30))
        log.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in events))
        failures, _, _, _ = step.judge_run(out, {"bob": BOB}, CAPSULECTL, Path(self.tmp.name) / "w")
        self.assertTrue(any("exchange_late: the absence was recorded with its window closing" in f
                            for f in failures), failures)

    @needs_capsulectl
    def test_a_commitment_altered_after_signing_is_a_fail(self):
        out = self.fixture("good")
        c = out / "alice" / "advertised" / "commitment.json"
        doc = json.loads(c.read_text())
        doc["answers_within"] = "PT24H"  # a longer deadline than the node signed
        c.write_text(json.dumps(doc))
        failures, _, _, _ = step.judge_run(out, {"bob": BOB}, CAPSULECTL, Path(self.tmp.name) / "w")
        self.assertTrue(any("commitment does not verify" in f for f in failures), failures)


class PolicyTest(unittest.TestCase):
    ADV = {"subjects": ["checkpoints"], "derivations": ["history_card/1"], "policy": {"history_segments":
                                                                                     "counterparties"}}

    def test_answers_within_is_an_iso_8601_duration(self):
        self.assertEqual(step.duration("PT1M"), dt.timedelta(minutes=1))
        self.assertEqual(step.duration("P1DT2H"), dt.timedelta(days=1, hours=2))
        for bad in ("P", "PT", "1H", "PT1.5H", "", None):
            with self.assertRaises(ValueError):
                step.duration(bad)

    def test_what_the_advertised_policy_answers(self):
        hc = {"subject": {"checkpoints": None}, "derivation": "history_card/1"}
        exchange = {"subject": {"exchange": "a" * 64}}
        self.assertTrue(step.policy_answers(hc, self.ADV))
        self.assertTrue(step.policy_answers({"subject": {"checkpoints": None}}, self.ADV))
        self.assertFalse(step.policy_answers({"subject": {"full_history": None}}, self.ADV))
        self.assertFalse(step.policy_answers(exchange, self.ADV))  # counterparties only
        peers = {**self.ADV, "policy": {"history_segments": "peers"}}
        self.assertTrue(step.policy_answers(exchange, peers))
        self.assertFalse(step.policy_answers(exchange, peers, requester_authenticated=False))


class FakeConsole:
    """The reporter node's console: peer-key and mesh_evidence_request, from the reporter's fixtures."""

    def __init__(self, answers, announced):
        fake = self
        self.answers, self.announced = answers, announced

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, body):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.reply(200, json.dumps({"announced_key_id": fake.announced}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stem = "history_card" if body["request"].get("derivation") else "checkpoints"
                what = fake.answers[stem]
                if what == "absence":
                    return self.reply(502, (REPORTER_FIX / "absence.txt").read_bytes())
                self.reply(200, (REPORTER_FIX / f"{what}.json").read_bytes())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


VERIFIED = {"checkpoints": "verified", "history_card": "verified_history_card"}
LISTED = ALICE["key_id"]  # the fixture commitment's key stands in for a lab node's listed key


class ReporterBackendTest(Tmp):
    def setUp(self):
        super().setUp()
        # A lab run's join output: the node's signed commitment (answers_within PT1M) under lab/runs.
        runs = Path(self.tmp.name) / "lab" / "runs"
        shutil.copytree(FIX / "good" / "alice" / "advertised", runs / "2026-10-28" / "join" / "lab-1")
        patcher = mock.patch.object(step, "LAB_RUNS", runs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_live(self, answers, announced=LISTED, key=LISTED, flags=()):
        pop_dir = Path(self.tmp.name) / "live"
        pop_dir.mkdir()
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "rail": None, "nodes": [{
            "name": "lab-1", "node_id": "1" * 64, "key_id": key,
            "disclosure": {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]}}]}))
        console = FakeConsole(answers, announced)
        try:
            self.assertEqual(step.main(["--population", str(pop_dir), "--out", str(self.out),
                                        "--console", console.url, *flags]), 0)
        finally:
            console.close()
        self.assertEqual(finding.check(self.out.parent), [])
        return json.loads((self.out / "finding.json").read_text())

    def issues(self, r):
        return sorted({i["issue"] for i in r["not_checked"]})

    def test_verified_answers_in_time_read_not_checked_on_the_sealing_issue_and_count_as_answered(self):
        r = self.run_live(VERIFIED)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertEqual(self.issues(r), ["Mesh-LLM/capsules-plugin#56"])
        self.assertEqual(sorted(r["pinned"]), ["capsules", "mesh-llm", "mesh-llm-pr-2620", "reporter"])
        first = json.loads((self.out / "first-answered.json").read_text())
        self.assertTrue(first["answered_at"])
        self.assertIn(first["ask"], ("lab-1 checkpoints", "lab-1 history_card/1"))
        timing = json.loads(next(self.out.glob("lab-1/**/timing.json")).read_text())
        self.assertEqual(timing["answers_within"], "PT1M")
        self.assertFalse(timing["late"])

    def test_an_answer_after_answers_within_is_not_counted_as_answered(self):
        late = step.now() + dt.timedelta(minutes=5)
        with mock.patch.object(step, "now", return_value=late):
            r = self.run_live(VERIFIED)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn("checkpoints back after the deadline, counted absent", r["detail"])
        self.assertIsNone(json.loads((self.out / "first-answered.json").read_text())["answered_at"])

    def test_an_answer_verified_under_a_key_other_than_the_listed_one_is_a_fail(self):
        r = self.run_live(VERIFIED, announced="3" * 64)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("not the listed key", r["detail"])

    def test_a_refusal_of_a_listed_stranger_safe_subject_is_a_fail(self):
        r = self.run_live({"checkpoints": "refusal", "history_card": "verified_history_card"})
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("refused, but the node lists it as stranger-safe", r["detail"])

    def test_no_answer_is_not_checked_and_not_answered(self):
        r = self.run_live({"checkpoints": "absence", "history_card": "absence"})
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIsNone(json.loads((self.out / "first-answered.json").read_text())["answered_at"])

    def test_a_node_with_no_signed_commitment_has_no_deadline_and_no_answer_counted(self):
        r = self.run_live(VERIFIED, announced="4" * 64, key="4" * 64)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn("Mesh-LLM/capsules-plugin#51", self.issues(r))
        self.assertIsNone(json.loads((self.out / "first-answered.json").read_text())["answered_at"])

    def test_a_self_node_without_a_second_console_is_not_checked_never_asked(self):
        r = self.run_live(VERIFIED, flags=("--self-node", "lab-1"))
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("lab-1: not asked, the reporter's own node and no second console", r["detail"])

    def test_a_self_node_not_in_the_population_is_refused(self):
        pop_dir = Path(self.tmp.name) / "live"
        pop_dir.mkdir()
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "nodes": [{"name": "lab-1"}]}))
        with mock.patch("sys.stderr"):
            self.assertEqual(step.main(["--population", str(pop_dir), "--out", str(self.out),
                                        "--self-node", "lab-9"]), 2)


if __name__ == "__main__":
    unittest.main()
