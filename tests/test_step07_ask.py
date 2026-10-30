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
import time
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
        not_checked = []
        failures, answered, proof, lines = step.judge_run(out, {"bob": BOB}, CAPSULECTL, Path(self.tmp.name) / "w",
                                                          not_checked)
        r = step.conclude(out, "rehearsal", failures, not_checked, answered, proof, lines, finding.now())
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
    def test_a_refusal_of_a_stranger_safe_subject_for_a_reason_an_honest_node_sends_is_not_checked(self):
        failures, answered, r = self.judge("stranger-safe-refused")  # reason not_authorized
        self.assertEqual(failures, [])
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertEqual({i["issue"] for i in r["not_checked"]}, {"Mesh-LLM/capsules-plugin#114"})

    def carrier_log(self, edit):
        out = self.fixture("good")
        log = out / "bob" / "asks" / "alice" / "carrier.jsonl"
        lines = edit([json.loads(x) for x in log.read_text().splitlines()])
        log.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in lines))
        failures, _, _, _ = step.judge_run(out, {"bob": BOB}, CAPSULECTL, Path(self.tmp.name) / "w")
        return failures

    @needs_capsulectl
    def test_a_carrier_that_sent_other_bytes_than_the_request_record_holds_is_a_fail(self):
        def edit(lines):
            for e in lines:
                if e["ask"] == "history_card" and e["event"] == "sent":
                    e["request_sha256"] = "0" * 64
            return lines
        self.assertTrue(any("history_card: the carrier sent other bytes" in f for f in self.carrier_log(edit)))

    @needs_capsulectl
    def test_a_request_sent_twice_is_a_fail(self):
        def edit(lines):
            return lines + [e for e in lines if e["ask"] == "history_card" and e["event"] == "sent"]
        self.assertTrue(any("history_card: the carrier log has 2 sends" in f for f in self.carrier_log(edit)))

    @needs_capsulectl
    def test_a_withholding_refusal_of_a_stranger_safe_subject_is_a_fail(self):
        # The fixture's refusal is not_authorized (NOT_CHECKED); the same bundle with a withholding reason in its
        # disclosed header, judged with the verdict the unedited bundle got, breaches the policy.
        out = self.fixture("stranger-safe-refused")
        d = out / "bob" / "asks" / "alice" / "history_card"
        bundle_path = d / "outcome.bundle.json"
        verdict, errs = step.verify_bundle(bundle_path, CAPSULECTL)
        self.assertEqual(errs, [])
        bundle = json.loads(bundle_path.read_text())
        st = step.disclosed(bundle, bundle["root"])["statement"]
        self.assertEqual(st["reason"], "not_authorized")
        st["reason"] = "no_such_subject"
        bundle_path.write_text(json.dumps(bundle))
        adv, _ = step.advertisement(out / "alice" / "advertised")
        events = [json.loads(x) for x in (d.parent / "carrier.jsonl").read_text().splitlines()]
        events = [e for e in events if e["ask"] == "history_card"]
        verifier = step.Verifier(Path(self.tmp.name) / "w2", CAPSULECTL)
        not_checked = []
        errs, answered = step.judge_ask(d, adv, BOB, events, verdict, verifier, not_checked)
        self.assertTrue(any("is refused (no_such_subject), but the advertised policy lists it as stranger-safe" in e
                            for e in errs), errs)
        self.assertEqual(not_checked, [])

    @needs_capsulectl
    def test_an_answer_delivered_in_time_but_recorded_absent_is_a_fail(self):
        out = self.fixture("good")
        log = out / "bob" / "asks" / "alice" / "carrier.jsonl"
        lines = [json.loads(x) for x in log.read_text().splitlines()]
        for e in lines:
            if e["ask"] == "exchange_late" and e["event"] == "delivered":
                e["at"] = "2026-10-28T07:49:30.000000Z"  # the deadline is 07:50:09
        log.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in lines))
        failures, _, _, _ = step.judge_run(out, {"bob": BOB}, CAPSULECTL, Path(self.tmp.name) / "w")
        self.assertTrue(any("exchange_late: the reply was delivered by the deadline" in f for f in failures),
                        failures)

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

    def test_a_refusal_is_judged_by_its_reason(self):
        for reason in ("no_such_subject", "derivation_unsupported"):
            self.assertEqual(step.refusal_verdict(reason), "fail")
        for reason in ("policy_declined", "not_authorized"):
            self.assertEqual(step.refusal_verdict(reason), "not_checked")
        for reason in ("coverage_unsatisfiable", "deadline_unmet", "request_malformed", "retention_expired"):
            self.assertEqual(step.refusal_verdict(reason), "ok")
        for reason in ("request_expired", "made_up", None):  # not in the spec: a breach
            self.assertEqual(step.refusal_verdict(reason), "fail")

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


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def ask_record(rid, header):
    """One line of a node's ledger/capsules.jsonl holding an ask-book record (capsules-plugin ask_book.rs)."""
    return json.dumps({"capsule_id": rid, "model_attestation": {"compute_attestation": {
        step.BOOK_BLOCK: header}}}) + "\n"


def seal_records(ledger, peer, asked, outcome):
    """What the pinned plugin appends to the asker's ledger for one ask: the sealed evidence_request, then, as
    `outcome` says, the record of what came of it. Whole-second times, as the plugin writes them."""
    at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    sent = {**asked, "audience": peer, "nonce": f"n-{time.time_ns()}", "requester_auth": {"sig": "x"}}
    path = ledger / "ledger" / "capsules.jsonl"
    n = len(path.read_text().splitlines()) if path.exists() else 0
    rid = f"{n:064x}"
    lines = [ask_record(rid, {"record_type": "evidence_request", "epistemic_type": "observed_event", "links": [],
                              "statement": {"request": sent, "responder_ref": peer}, "committed_at": iso(at)})]
    digest = step.request_digest(sent)
    cites = [{"type": "cites", "target": rid}]
    close = at + dt.timedelta(seconds=60)
    window = {"opened": iso(at), "closed": iso(close)}
    verification = {"state": "artifact"}
    if outcome in ("response", "wrong_digest"):
        lines.append(ask_record(f"{n + 1:064x}", {
            "record_type": "evidence_response", "epistemic_type": "observed_event", "links": cites,
            "statement": {"outcome": "artifact", "request_digest": "0" * 64 if outcome == "wrong_digest" else digest,
                          "verification": verification}, "committed_at": iso(at)}))
    elif outcome == "refusal_mismatch":
        lines.append(ask_record(f"{n + 1:064x}", {
            "record_type": "evidence_response", "epistemic_type": "observed_event", "links": cites,
            "statement": {"outcome": "refusal", "request_digest": digest,
                          "verification": {"state": "refusal", "reason": "policy_declined",
                                           "policy_check": {"state": "MISMATCH"}}}, "committed_at": iso(at)}))
    elif outcome in ("artifact_for_refusal", "refusal_for_artifact"):
        lines.append(ask_record(f"{n + 1:064x}", {
            "record_type": "evidence_response", "epistemic_type": "observed_event", "links": cites,
            "statement": {"outcome": "artifact" if outcome == "artifact_for_refusal" else "refusal",
                          "request_digest": digest, "verification": verification}, "committed_at": iso(at)}))
    elif outcome == "junk":
        lines.insert(0, "[]\n1\nnull\n" + json.dumps({"capsule_id": "x", "model_attestation": []}) + "\n")
        lines.append(ask_record(f"{n + 1:064x}", {
            "record_type": "evidence_response", "epistemic_type": "observed_event", "links": cites,
            "statement": {"outcome": "artifact", "request_digest": digest, "verification": verification},
            "committed_at": iso(at)}))
    elif outcome in ("absence", "early_absence", "late_open"):
        if outcome == "early_absence":
            window["closed"] = iso(at + dt.timedelta(seconds=20))
        lines.append(ask_record(f"{n + 1:064x}", {
            "record_type": "recorded_absence", "epistemic_type": "observed_event", "links": cites,
            "statement": {"outcome": "recorded_absence", "request_digest": digest, "window": window,
                          "recorded_at": iso(close)}, "committed_at": iso(close)}))
    ledger.joinpath("ledger").mkdir(parents=True, exist_ok=True)
    with ledger.joinpath("ledger", "capsules.jsonl").open("a") as f:
        f.write("".join(lines))


class FakeConsole:
    """The reporter node's console: peer-key and mesh_evidence_request, from the reporter's fixtures."""

    def __init__(self, answers, announced, ledger=None, sealed=None):
        fake = self
        self.answers, self.announced = answers, announced
        self.ledger, self.sealed = ledger, sealed or {}

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
                if fake.ledger is not None:
                    seal_records(fake.ledger, body["peer_id"], body["request"], fake.sealed.get(stem, "response"))
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

    def run_live(self, answers, announced=LISTED, key=LISTED, flags=(), ledger=None, sealed=None, extra_nodes=(),
                 data_dir_flag=True):
        pop_dir = Path(self.tmp.name) / "live"
        pop_dir.mkdir()
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "rail": None, "nodes": [{
            "name": "lab-1", "node_id": "1" * 64, "key_id": key,
            "disclosure": {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]}}, *extra_nodes]}))
        console = FakeConsole(answers, announced, ledger, sealed)
        extra = ["--console-data-dir", str(ledger)] if ledger is not None and data_dir_flag else []
        try:
            self.assertEqual(step.main(["--population", str(pop_dir), "--out", str(self.out),
                                        "--console", console.url, *extra, *flags]), 0)
        finally:
            console.close()
        self.assertEqual(finding.check(self.out.parent), [])
        return json.loads((self.out / "finding.json").read_text())

    def issues(self, r):
        return sorted({i["issue"] for i in r["not_checked"]})

    def test_verified_answers_in_time_read_not_checked_on_the_sealing_issue_and_count_as_answered(self):
        r = self.run_live(VERIFIED)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertEqual(self.issues(r), sorted([step.SEAL_ISSUE, step.EXCHANGE_ISSUE]))
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

    def test_a_withholding_refusal_of_a_listed_stranger_safe_subject_is_a_fail(self):
        r = self.run_live({"checkpoints": "refusal_withheld", "history_card": "verified_history_card"})
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("refused (no_such_subject), but the node lists it as stranger-safe", r["detail"])

    def test_a_policy_declined_refusal_of_a_listed_stranger_safe_subject_is_not_checked(self):
        r = self.run_live({"checkpoints": "refusal", "history_card": "verified_history_card"})
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn("Mesh-LLM/capsules-plugin#114", self.issues(r))
        # an unjudged refusal is not an answer: the first answer is the history card's
        first = json.loads((self.out / "first-answered.json").read_text())
        self.assertEqual(first["ask"], "lab-1 history_card/1")

    def test_a_refusal_signed_under_a_key_other_than_the_listed_one_is_a_fail(self):
        r = self.run_live({"checkpoints": "refusal_withheld", "history_card": "verified_history_card"},
                          announced="3" * 64)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("refusal is signed under a key that is not the listed key", r["detail"])

    def test_an_unjudged_refusal_signed_under_another_key_is_a_fail_too(self):
        r = self.run_live({"checkpoints": "refusal", "history_card": "verified_history_card"}, announced="3" * 64)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("refusal is signed under a key that is not the listed key", r["detail"])

    def test_a_refusal_of_a_question_the_node_does_not_list_is_still_checked_for_its_key(self):
        pop = Path(self.tmp.name) / "p"
        pop.mkdir()
        (pop / "population.json").write_text("{}")
        line = "refusal reason=policy_declined key=" + "3" * 64 + " listed_key=differs"
        entry = {"disclosure": {"stranger_safe": ["checkpoints"]}}
        fail, _ = step.judge_live_line("history_card", line, entry)
        self.assertIn("not the listed key", fail)
        ok = line.replace("differs", "match")
        self.assertEqual(step.judge_live_line("history_card", ok, entry), (None, None))

    def test_the_deadline_runs_from_the_request_not_from_the_key_lookup(self):
        # A slow key lookup (GET) is not part of the answer time: only the POST's send time starts it.
        real = step.daily_report.http

        def slow_get(method, url, body, timeout):
            if method == "GET":
                time.sleep(2.5)
            return real(method, url, body, timeout)

        with mock.patch.object(step.daily_report, "http", slow_get):
            self.run_live(VERIFIED)
        t = json.loads(next((self.out / "lab-1" / "attempts").iterdir()).joinpath("timing.json").read_text())
        gap = step.utc(t["back_at"]) - step.utc(t["asked_at"])
        self.assertLess(gap, dt.timedelta(seconds=2))

    def test_each_question_has_its_own_attempt_and_answer_time(self):
        self.run_live(VERIFIED)
        attempts = sorted((self.out / "lab-1" / "attempts").iterdir())
        self.assertEqual(len(attempts), 2)
        for a in attempts:
            self.assertTrue(json.loads((a / "timing.json").read_text())["back_at"])

    def test_no_answer_is_not_checked_and_not_answered(self):
        r = self.run_live({"checkpoints": "absence", "history_card": "absence"})
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIsNone(json.loads((self.out / "first-answered.json").read_text())["answered_at"])

    def test_a_node_with_no_signed_commitment_has_no_deadline_and_no_answer_counted(self):
        r = self.run_live(VERIFIED, announced="4" * 64, key="4" * 64)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn(step.NO_COMMITMENT_ISSUE, self.issues(r))
        self.assertIsNone(json.loads((self.out / "first-answered.json").read_text())["answered_at"])

    def test_a_self_node_without_a_second_console_is_not_checked_never_asked(self):
        r = self.run_live(VERIFIED, flags=("--self-node", "lab-1"))
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("lab-1: not asked, the reporter's own node and no second console", r["detail"])

    # --- the asker's sealed records, read from its ledger (evidence-network/loop#80) ---
    def ledger(self):
        d = Path(self.tmp.name) / "asker"
        d.mkdir(exist_ok=True)
        return d

    def only_exchange_left(self, r):
        self.assertEqual([i["what"] for i in r["not_checked"] if i["issue"] in (step.SEAL_ISSUE, step.EXCHANGE_ISSUE)],
                         [step.LIVE_NOT_CHECKED[0]["what"]], r["not_checked"])

    def test_sealed_requests_and_their_responses_are_read(self):
        r = self.run_live(VERIFIED, ledger=self.ledger())
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.only_exchange_left(r)
        self.assertTrue(json.loads((self.out / "first-answered.json").read_text())["sealed"])
        kept = json.loads(next(self.out.glob("lab-1/attempts/*/checkpoints.ledger.json")).read_text())
        self.assertEqual(kept["outcome"]["header"]["record_type"], "evidence_response")

    def test_a_response_for_another_request_digest_is_a_fail(self):
        r = self.run_live(VERIFIED, ledger=self.ledger(), sealed={"checkpoints": "wrong_digest"})
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("names another request than the one sealed", r["detail"])

    def test_an_ask_with_no_sealed_request_is_a_fail(self):
        with mock.patch.object(step, "ask_records", return_value=[]):
            r = self.run_live(VERIFIED, ledger=self.ledger())
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("holds 0 sealed evidence_request records for this ask", r["detail"])

    def test_a_request_sealed_but_never_given_an_outcome_is_a_fail_once_its_window_closed(self):
        late = step.now() + dt.timedelta(minutes=5)
        with mock.patch.object(step, "now", side_effect=lambda: late):
            r = self.run_live(VERIFIED, ledger=self.ledger(), sealed={"checkpoints": "none"})
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("no outcome sealed for the request", r["detail"])

    def test_an_open_window_is_not_checked_not_failed(self):
        with mock.patch.object(step, "OPEN_WINDOW_WAIT", dt.timedelta(0)):
            r = self.run_live({"checkpoints": "absence", "history_card": "absence"}, ledger=self.ledger(),
                              sealed={"checkpoints": "none", "history_card": "none"})
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertTrue(any("not sealed yet" in i["why"] for i in r["not_checked"]))

    def test_a_recorded_absence_for_an_ask_the_reporter_saw_unanswered_is_read(self):
        r = self.run_live({"checkpoints": "absence", "history_card": "absence"}, ledger=self.ledger(),
                          sealed={"checkpoints": "absence", "history_card": "absence"})
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.only_exchange_left(r)

    def test_an_absence_that_closes_its_window_early_is_a_fail(self):
        r = self.run_live({"checkpoints": "absence", "history_card": "absence"}, ledger=self.ledger(),
                          sealed={"checkpoints": "early_absence", "history_card": "absence"})
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("absence closes its window", r["detail"])

    def test_an_absence_recorded_for_an_ask_the_reporter_saw_answered_is_a_fail(self):
        r = self.run_live(VERIFIED, ledger=self.ledger(), sealed={"checkpoints": "absence"})
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("recorded an absence, but the reporter saw a signed answer", r["detail"])

    def test_a_refusal_the_sealed_record_checks_against_the_pinned_policy_and_finds_mismatched_is_a_fail(self):
        r = self.run_live({"checkpoints": "refusal", "history_card": "verified_history_card"},
                          ledger=self.ledger(), sealed={"checkpoints": "refusal_mismatch"})
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("does not match", r["detail"])

    def test_the_console_data_dir_defaults_to_the_self_nodes_data_dir(self):
        ledger = self.ledger()
        (ledger / "ledger").mkdir()
        (ledger / "ledger" / "capsules.jsonl").touch()  # the node's data_dir is readable only with its ledger
        r = self.run_live(VERIFIED, ledger=ledger, extra_nodes=[{"name": "lab-0", "data_dir": str(ledger)}],
                          data_dir_flag=False, flags=("--self-node", "lab-0"))
        self.assertNotIn(step.LEDGER_GAP, [i["why"] for i in r["not_checked"]])
        self.assertTrue(list(self.out.glob("lab-1/attempts/*/checkpoints.ledger.json")))

    def test_the_self_node_is_asked_through_the_second_console_and_read_from_the_second_data_dir(self):
        ledger = self.ledger()
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        console = FakeConsole(VERIFIED, LISTED, ledger)
        try:
            self.run_live(VERIFIED, ledger=other, data_dir_flag=False,
                          flags=("--self-node", "lab-1", "--second-console", console.url,
                                 "--second-data-dir", str(ledger)))
        finally:
            console.close()
        self.assertTrue(json.loads((self.out / "first-answered.json").read_text())["sealed"])

    def test_a_response_of_another_type_than_the_reporter_saw_is_a_fail(self):
        r = self.run_live({"checkpoints": "refusal", "history_card": "verified_history_card"}, ledger=self.ledger(),
                          sealed={"checkpoints": "artifact_for_refusal"})
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("recorded as artifact, but the reporter saw refusal", r["detail"])

    def test_a_refusal_recorded_for_an_answer_the_reporter_saw_verified_is_a_fail(self):
        r = self.run_live(VERIFIED, ledger=self.ledger(), sealed={"checkpoints": "refusal_for_artifact"})
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("recorded as refusal, but the reporter saw verified", r["detail"])

    def test_malformed_ledger_lines_are_skipped_not_fatal(self):
        r = self.run_live(VERIFIED, ledger=self.ledger(), sealed={"checkpoints": "junk"})
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.only_exchange_left(r)

    def test_a_request_sealed_with_other_coverage_is_not_the_ask(self):
        def altered(records):
            return [(rid, {**h, "statement": {**h["statement"], "request": {
                **h["statement"]["request"], "coverage": {"min_freshness": 99}}}}) if
                    h.get("record_type") == "evidence_request" else (rid, h) for rid, h in records]
        ask = step.ask_records
        with mock.patch.object(step, "ask_records", side_effect=lambda l: altered(ask(l))):
            r = self.run_live(VERIFIED, ledger=self.ledger())
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("holds 0 sealed evidence_request records for this ask", r["detail"])

    def test_a_window_the_commitments_answers_within_keeps_open_is_not_checked_not_failed(self):
        ledger, asked = self.ledger(), {"profile": "p", "subject": {"checkpoints": None}}
        seal_records(ledger, "peer-1", asked, "none")
        at = step.now()
        with mock.patch.object(step, "OPEN_WINDOW_WAIT", dt.timedelta(0)):
            late = at + dt.timedelta(seconds=100)  # past sealed + 60s, inside sealed + answers_within
            with mock.patch.object(step, "now", side_effect=lambda: late):
                errs, notes, _ = step.judge_sealed(ledger / "ledger", "peer-1", asked, at, at, None,
                                                   dt.timedelta(minutes=5))
        self.assertEqual(errs, [])
        self.assertTrue(any("not sealed yet" in n for n in notes), notes)

    def test_an_unreadable_ledger_is_not_checked_on_the_issue(self):
        r = self.run_live(VERIFIED)
        self.assertIn(step.LEDGER_GAP, [i["why"] for i in r["not_checked"]])
        self.assertFalse(json.loads((self.out / "first-answered.json").read_text())["sealed"])

    def test_a_self_node_not_in_the_population_is_refused(self):
        pop_dir = Path(self.tmp.name) / "live"
        pop_dir.mkdir()
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "nodes": [{"name": "lab-1"}]}))
        with mock.patch("sys.stderr"):
            self.assertEqual(step.main(["--population", str(pop_dir), "--out", str(self.out),
                                        "--self-node", "lab-9"]), 2)


if __name__ == "__main__":
    unittest.main()
