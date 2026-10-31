"""steps/07-ask: a request sealed before it is sent, answered or recorded absent by its deadline, or FAIL.

The book backend needs the pinned capsulectl and capsulectl-book
(steps/07-ask.setup.sh); without them those tests SKIP and say why, or, with
LOOP_REQUIRE_TOOLS=1 (CI, after the setups ran), fail to load. The full
rehearsal run takes a little over a minute: one ask waits out its deadline.
The reporter backend runs against a fake reporter console on localhost.
Fixtures: tests/fixtures/step07/, made by make_fixtures.py; each planted
failure there was made by a misbehaving asker or responder during a real run.
"""
import base64
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

    def test_under_evidence_interop_1_a_policy_reason_is_policy(self):
        v1 = "evidence-interop/1"
        for reason in ("policy_declined", "not_authorized"):
            self.assertEqual(step.refusal_verdict(reason, v1), "fail")
            self.assertEqual(step.refusal_verdict(reason, "evidence-interop/0"), "not_checked")
        self.assertEqual(step.refusal_verdict("capacity_exceeded", v1), "not_checked")
        self.assertEqual(step.refusal_verdict("request_unauthenticated", v1), "ok")
        # A sealed request with no profile asks for /0, which registers neither -02 token.
        v0 = step.request_profile({"subject": {"checkpoints": None}})
        self.assertEqual(v0, "evidence-interop/0")
        for reason in ("capacity_exceeded", "request_unauthenticated"):
            self.assertEqual(step.refusal_verdict(reason, v0), "fail")
        self.assertEqual(step.request_profile({"profile": v1}), v1)
        self.assertIn("a limit of the node's own",
                      step.refusal_not_checked("x", "capacity_exceeded")["why"])

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
    elif outcome.startswith("refusal_") and outcome != "refusal_for_artifact":  # the plugin's policy_check: {status, why, policy_digest} (peer_commitments.rs)
        digest_pinned = ALICE["lcap"]["policy_digest"]
        check = {"refusal_mismatch": {"status": "MISMATCH", "why": "w", "policy_digest": digest_pinned},
                 "refusal_match": {"status": "MATCH", "why": "w", "policy_digest": digest_pinned},
                 "refusal_not_checked": {"status": "NOT_CHECKED", "why": "w", "policy_digest": digest_pinned},
                 "refusal_unknown_status": {"status": "PERHAPS", "why": "w", "policy_digest": digest_pinned},
                 "refusal_state_key": {"state": "MISMATCH"},
                 "refusal_other_policy": {"status": "MATCH", "why": "w", "policy_digest": "f" * 64}}[outcome]
        lines.append(ask_record(f"{n + 1:064x}", {
            "record_type": "evidence_response", "epistemic_type": "observed_event", "links": cites,
            "statement": {"outcome": "refusal", "request_digest": digest,
                          "verification": {"state": "refusal", "reason": "policy_declined", "policy_check": check}},
            "committed_at": iso(at)}))
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


def exchange_reply(kind, half):
    """The plugin's 200 body for an exchange ask: an artifact serving the records `kind` names."""
    cite = {"references": [{"type": "capsule", "digest": half, "citation_purpose": "counterparty_half"}]}
    records = {"exchange_verified": [cite], "exchange_other_half": [{"references": [
        {"type": "capsule", "digest": "e" * 64, "citation_purpose": "counterparty_half"}]}],
        "exchange_empty": [], "exchange_unreadable": []}[kind]
    artifact = {"records": [{"index": i, "body": json.dumps(r).encode().hex()} for i, r in enumerate(records)]}
    if kind == "exchange_unreadable":
        artifact = "x"
    encoded = base64.urlsafe_b64encode(json.dumps(artifact).encode()).decode().rstrip("=")
    return json.dumps({"answer": {"envelope": {"profile": "evidence-interop/0", "artifact": encoded, "sig": "s"}},
                       "held": [], "request_digest": "d" * 64,
                       "verification": {"anchor": "checkpoints[0]", "checkpoints": 0,
                                        "records": list(range(len(records))), "state": "artifact"}}).encode()


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
                stem = ("exchange" if "exchange" in body["request"].get("subject", {})
                        else "history_card" if body["request"].get("derivation") else "checkpoints")
                what = fake.answers.get(stem, "absence")
                if fake.ledger is not None:
                    seal_records(fake.ledger, body["peer_id"], body["request"], fake.sealed.get(stem, "response"))
                if what == "absence":
                    return self.reply(502, (REPORTER_FIX / "absence.txt").read_bytes())
                if what.startswith("exchange_"):
                    return self.reply(200, exchange_reply(what, body["request"]["subject"]["exchange"]))
                self.reply(200, (REPORTER_FIX / f"{what}.json").read_bytes())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


VERIFIED = {"checkpoints": "verified", "history_card": "verified_history_card"}
LISTED = ALICE["key_id"]  # the fixture commitment's key stands in for a lab node's listed key


LIVE = FIX / "live"  # the lab's real signed commitments for stevenmih-1 (2026-10-30), before and after the plugin move


class LiveCommitmentTest(Tmp):
    """live_commitment() on real signed records: the join commitment from 02:31Z (policy 5a45151b..., the old
    plugin's) and the one published at 08:21:36Z (policy 8f10b44c..., d1f4edc's, a COSE checkpoint)."""
    OLD, NEW = "5a45151b78ff9a0a6f4ae396a7cd8212cd3088e0a313147064c12a631dbc8a5f", \
        "8f10b44c221629f88426c03386cff0bfdc64d329c1a7986ca28408d47fd22f73"

    def setUp(self):
        super().setUp()
        self.runs, self.published = Path(self.tmp.name) / "runs", Path(self.tmp.name) / "published"
        self.entry = json.loads((LIVE / "entry.json").read_text())
        self.node = f"{self.entry['node_id']}.json"
        for name, path in (("LAB_RUNS", self.runs), ("PUBLISHED", self.published)):
            patcher = mock.patch.object(step, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def put_old_join(self):
        shutil.copytree(LIVE / "old-join", self.runs / "2026-10-30" / "join" / "stevenmih-1")

    def put_published(self):
        shutil.copytree(LIVE / "published", self.published)

    def test_the_newer_published_commitment_wins_over_the_stale_join_record(self):
        self.put_old_join()
        self.put_published()
        adv, ncs = step.live_commitment(self.entry)
        self.assertEqual((adv["policy_digest"], ncs), (self.NEW, []))
        self.assertEqual(adv["commitment"], str(self.published / self.node))

    def test_the_stale_join_record_alone_is_still_used(self):
        self.put_old_join()
        adv, ncs = step.live_commitment(self.entry)
        self.assertEqual((adv["policy_digest"], ncs), (self.OLD, []))

    def test_a_newer_join_record_wins_over_an_older_published_commitment(self):
        # The same two records, swapped between the places: the newer one is chosen wherever it sits.
        shutil.copytree(LIVE / "published", self.runs / "2026-10-30" / "join" / "stevenmih-1")
        shutil.copytree(LIVE / "old-join", self.published)
        shutil.copy(self.published / "commitment.json", self.published / self.node)
        (self.published / "commitment.json").unlink()
        shutil.copy(self.runs / "2026-10-30" / "join" / "stevenmih-1" / self.node,
                    self.runs / "2026-10-30" / "join" / "stevenmih-1" / "commitment.json")
        adv, ncs = step.live_commitment(self.entry)
        self.assertEqual((adv["policy_digest"], ncs), (self.NEW, []))
        self.assertIn("join", adv["commitment"])

    def test_a_published_commitment_that_does_not_verify_is_skipped_and_named(self):
        self.put_old_join()
        self.put_published()
        path = self.published / self.node
        doc = json.loads(path.read_text())
        doc["answers_within"] = "PT9M"  # signed over: the signature no longer holds
        path.write_text(json.dumps(doc))
        adv, ncs = step.live_commitment(self.entry)
        self.assertEqual(adv["policy_digest"], self.OLD)
        self.assertEqual(len(ncs), 1)
        self.assertIn("does not verify", ncs[0]["why"])
        self.assertEqual(ncs[0]["issue"], step.NO_COMMITMENT_ISSUE)

    def test_an_older_join_record_that_does_not_verify_is_not_named(self):
        self.put_published()
        self.put_old_join()
        path = self.runs / "2026-10-30" / "join" / "stevenmih-1" / "commitment.json"
        doc = json.loads(path.read_text())
        doc["answers_within"] = "PT9M"  # older than the published one and no longer verifies: cannot change the outcome
        path.write_text(json.dumps(doc))
        adv, ncs = step.live_commitment(self.entry)
        self.assertEqual((adv["policy_digest"], ncs), (self.NEW, []))

    def test_an_unreadable_or_other_keys_published_record_is_named_when_a_join_record_is_used(self):
        self.put_old_join()
        self.published.mkdir()
        for text, why in (("{not json", "not readable"), (json.dumps([1]), "not a JSON object"),
                          (json.dumps({"key_id": "9" * 64}), "other than the node's listed key")):
            (self.published / self.node).write_text(text)
            adv, ncs = step.live_commitment(self.entry)
            self.assertEqual(adv["policy_digest"], self.OLD)
            self.assertEqual(len(ncs), 1, ncs)
            self.assertIn(why, ncs[0]["why"])

    def test_no_record_that_verifies_gives_no_deadline_and_names_each(self):
        self.put_published()
        (self.published / self.node).write_text("{}")
        self.assertEqual(step.live_commitment({**self.entry, "key_id": "9" * 64})[0], None)
        path = self.published / self.node
        doc = json.loads((LIVE / "published" / self.node).read_text())
        doc["answers_within"] = "PT9M"
        path.write_text(json.dumps(doc))
        adv, ncs = step.live_commitment(self.entry)
        self.assertIsNone(adv)
        self.assertEqual(len(ncs), 1)

    def test_an_issued_at_that_is_not_a_utc_timestamp_is_not_ranked(self):
        for bad in (None, 5, "2026-10-30", "2026-10-30T08:21:36+00:00", "2026-10-30 08:21:36Z"):
            self.assertIsNone(step.commitment_time({"issued_at": bad}), bad)
        self.assertEqual(step.commitment_time({"issued_at": "2026-10-30T08:21:36Z"}),
                         dt.datetime(2026, 10, 30, 8, 21, 36, tzinfo=dt.timezone.utc))

    def test_a_published_commitments_cose_checkpoint_gives_its_book_and_key(self):
        adv = step.step12.advertised(LIVE / "published", self.node)
        self.assertEqual(adv["book_id"], "capsules/511b5b4246cf58e7")
        self.assertEqual(adv["checkpoint_key"], self.entry["key_id"])
        self.assertIn(adv["book_id"], adv["streams"])

    def test_a_join_records_json_checkpoint_still_reads(self):
        adv = step.step12.advertised(LIVE / "old-join")
        self.assertEqual((adv["book_id"], adv["policy_digest"]), ("lab-1-book", self.OLD))

    def test_a_checkpoint_that_is_neither_form_does_not_read(self):
        doc = json.loads((LIVE / "published" / self.node).read_text())
        for bad in (b"\xd2\x84\x40\xa0\x40\x40", b"\xd2", b"not a checkpoint"):
            doc["checkpoint"]["bytes"] = base64.urlsafe_b64encode(bad).decode()
            with self.assertRaises(ValueError):
                step.step12.checkpoint_member(doc)


class ReporterBackendTest(Tmp):
    def setUp(self):
        super().setUp()
        # A lab run's join output: the node's signed commitment (answers_within PT1M) under lab/runs.
        runs = Path(self.tmp.name) / "lab" / "runs"
        shutil.copytree(FIX / "good" / "alice" / "advertised", runs / "2026-10-28" / "join" / "lab-1")
        self.published = Path(self.tmp.name) / "nodes" / "commitments"  # nothing published unless a test says so
        for name, path in (("LAB_RUNS", runs), ("PUBLISHED", self.published)):
            patcher = mock.patch.object(step, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_live(self, answers, announced=LISTED, key=LISTED, flags=(), ledger=None, sealed=None, extra_nodes=(),
                 data_dir_flag=True, node_extra=None):
        pop_dir = Path(self.tmp.name) / "live"
        pop_dir.mkdir()
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "rail": None, "nodes": [{
            "name": "lab-1", "node_id": "1" * 64, "key_id": key, **(node_extra or {}),
            "disclosure": {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]}}, *extra_nodes]}))
        console = FakeConsole(answers, announced, ledger, sealed)
        extra = ["--console-data-dir", str(ledger)] if ledger is not None and data_dir_flag else []
        try:
            self.assertEqual(step.main(["--population", str(pop_dir), "--out", str(self.out),
                                        "--console", console.url, *extra, *flags]), 0)
        finally:
            console.close()
        self.assertEqual(finding.check(self.out.parent), [])
        record = json.loads((self.out / "finding.json").read_text())
        self.assert_rederives(record, json.loads((pop_dir / "population.json").read_text()))
        return record

    def assert_rederives(self, record, pop):
        """judge_reporter on a copy of only the files the finding cites gives the recorded result and not_checked,
        and the same first-answered.json: what step 08 checks."""
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "07-ask"
            for p in record["proof"]:
                (work / p["path"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.out / p["path"], work / p["path"])
            (work / "first-answered.json").unlink()
            again = step.conclude(work, "live", *step.judge_reporter(pop, work), record["started"], "reporter")
            self.assertEqual(again["result"], record["result"])
            canon = lambda items: sorted(json.dumps(i, sort_keys=True) for i in items)  # noqa: E731
            self.assertEqual(canon(again["not_checked"]), canon(record["not_checked"]))
            self.assertEqual((work / "first-answered.json").read_bytes(),
                             (self.out / "first-answered.json").read_bytes())

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

    def test_a_reporter_refusal_is_read_by_its_requests_profile(self):
        entry = {"disclosure": {"stranger_safe": ["checkpoints"]}}
        line = "refusal reason=capacity_exceeded key=" + "3" * 64 + " listed_key=match"
        # The reporter's stranger-safe questions ask under /0, where the -02 tokens are not registered.
        fail, _ = step.judge_exchange_line(line, entry, "evidence-interop/0")
        self.assertIn("is refused (capacity_exceeded)", fail)
        fail, nc = step.judge_exchange_line(line, entry, "evidence-interop/1")
        self.assertIsNone(fail)
        self.assertIn("a limit of the node's own", nc["why"])
        refused = line.replace("capacity_exceeded", "not_authorized")
        fail, _ = step.judge_exchange_line(refused, entry, "evidence-interop/1")
        self.assertIn("is refused (not_authorized)", fail)

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

    # --- which commitment pins the policy: the newer of lab/runs and nodes/commitments (loop#179) ---
    def publish(self):
        """Alice's genuine commitment and policy, published under nodes/commitments/<node_id>.json."""
        src = FIX / "good" / "alice" / "advertised"
        doc = json.loads((src / "commitment.json").read_text())
        uri = self.published / doc["lcap"]["policy_uri"]
        uri.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / doc["lcap"]["policy_uri"], uri)
        (self.published / f"{'1' * 64}.json").write_text(json.dumps(doc))

    def age_join(self, issued_at):
        path = next(step.LAB_RUNS.glob("*/join/lab-1/commitment.json"))
        doc = json.loads(path.read_text())
        doc["issued_at"] = issued_at
        path.write_text(json.dumps(doc))

    def refusal_with_listed_commitment(self):
        return self.run_live({"checkpoints": "refusal", "history_card": "verified_history_card"},
                             ledger=self.ledger(), sealed={"checkpoints": "refusal_match"}, node_extra={"node_id": "1" * 64})

    def test_a_newer_published_commitment_wins_over_a_stale_join_record(self):
        self.age_join("2020-01-01T00:00:00Z")  # the stale join no longer verifies; the published one does
        self.publish()
        r = self.refusal_with_listed_commitment()
        self.assertNotEqual(r["result"], "FAIL", r["detail"])
        timing = json.loads(next(self.out.glob("lab-1/**/timing.json")).read_text())
        self.assertEqual(timing["commitment"], str(self.published / f"{'1' * 64}.json"))

    def test_a_newer_join_record_that_does_not_verify_cannot_hide_a_verifying_published_one(self):
        self.age_join("2999-01-01T00:00:00Z")  # newer, but its signature no longer holds
        self.publish()
        r = self.refusal_with_listed_commitment()
        self.assertNotEqual(r["result"], "FAIL", r["detail"])
        timing = json.loads(next(self.out.glob("lab-1/**/timing.json")).read_text())
        self.assertEqual(timing["commitment"], str(self.published / f"{'1' * 64}.json"))
        self.assertTrue(any("does not verify" in i["why"] and "join" in i["why"] for i in r["not_checked"]),
                        r["not_checked"])

    def test_the_published_commitment_alone_gives_the_deadline_and_pins_the_policy(self):
        shutil.rmtree(step.LAB_RUNS)
        self.publish()
        r = self.refusal_with_listed_commitment()
        self.assertNotEqual(r["result"], "FAIL", r["detail"])
        timing = json.loads(next(self.out.glob("lab-1/**/timing.json")).read_text())
        self.assertEqual(timing["answers_within"], "PT1M")

    def test_a_tie_in_issued_at_goes_to_the_published_commitment(self):
        self.publish()  # the same bytes as the join record: equal issued_at
        r = self.refusal_with_listed_commitment()
        self.assertNotEqual(r["result"], "FAIL", r["detail"])
        timing = json.loads(next(self.out.glob("lab-1/**/timing.json")).read_text())
        self.assertEqual(timing["commitment"], str(self.published / f"{'1' * 64}.json"))

    def test_a_refusal_naming_neither_digest_is_a_fail_with_a_published_commitment(self):
        self.age_join("2020-01-01T00:00:00Z")
        self.publish()
        r = self.run_live({"checkpoints": "refusal", "history_card": "verified_history_card"}, ledger=self.ledger(),
                          sealed={"checkpoints": "refusal_other_policy"}, node_extra={"node_id": "1" * 64})
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("not the", r["detail"])

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
                         ["lab-1: a later exchange request on a Mesh node"], r["not_checked"])

    def test_sealed_requests_and_their_responses_are_read(self):
        r = self.run_live(VERIFIED, ledger=self.ledger())
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.only_exchange_left(r)
        self.assertTrue(json.loads((self.out / "first-answered.json").read_text())["sealed"])
        kept = json.loads(next(self.out.glob("lab-1/attempts/*/checkpoints.ledger.json")).read_text())
        self.assertEqual([r["header"]["record_type"] for r in kept["requests"]], ["evidence_request"])
        self.assertEqual([r["header"]["record_type"] for r in kept["outcomes"]], ["evidence_response"])

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

    def refusal_run(self, sealed):
        return self.run_live({"checkpoints": "refusal", "history_card": "verified_history_card"},
                             ledger=self.ledger(), sealed={"checkpoints": sealed})

    def test_a_matching_refusal_check_is_read_clean(self):
        r = self.refusal_run("refusal_match")
        self.assertNotEqual(r["result"], "FAIL", r["detail"])

    def test_a_refusal_check_not_checked_in_the_record_is_named(self):
        r = self.refusal_run("refusal_not_checked")
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertTrue(any("policy check" in i["why"] and "NOT_CHECKED" in i["why"] for i in r["not_checked"]))

    def test_a_refusal_check_with_an_unknown_status_fails_closed(self):
        r = self.refusal_run("refusal_unknown_status")
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("no status the step knows", r["detail"])

    def test_a_refusal_check_in_the_old_state_shape_fails_closed(self):
        r = self.refusal_run("refusal_state_key")
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("no status the step knows", r["detail"])

    def test_a_refusal_check_against_another_policy_than_the_commitment_pins_is_a_fail(self):
        r = self.refusal_run("refusal_other_policy")
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("not the", r["detail"])

    # --- the exchange ask about a half step 05 left (evidence-network/loop#94) ---
    HALF = "a" * 64

    def exchange_dirs(self, held=True, cited=True, sent=True):
        """The asker's ledger (it sent HALF) and the asked node's data_dir (it holds HALF and cites it)."""
        asker, node = self.ledger(), Path(self.tmp.name) / "asked"
        (asker / "ledger").mkdir(parents=True, exist_ok=True)
        (node / "ledger").mkdir(parents=True, exist_ok=True)
        (asker / "ledger" / "capsules.jsonl").write_text(
            json.dumps({"capsule_id": self.HALF if sent else "c" * 64}) + "\n")
        (node / "ledger" / "received-capsules.jsonl").write_text(
            json.dumps({"capsule_id": self.HALF if held else "d" * 64}) + "\n")
        ref = {"type": "capsule", "digest": self.HALF if cited else "b" * 64, "citation_purpose": "counterparty_half"}
        (node / "ledger" / "capsules.jsonl").write_text(json.dumps({"capsule_id": "f" * 64, "references": [ref]}) + "\n")
        return asker, {"data_dir": str(node)}

    def run_exchange(self, exchange, sealed=None, **dirs):
        asker, extra = self.exchange_dirs(**dirs)
        return self.run_live({**VERIFIED, "exchange": exchange}, ledger=asker, node_extra=extra,
                             sealed={"exchange": sealed} if sealed else None)

    def test_the_exchange_ask_about_a_held_half_is_sealed_and_judged_like_the_others(self):
        r = self.run_exchange("exchange_verified")
        self.assertEqual(r["result"], "PASS", r["detail"])  # nothing left unchecked once the exchange is driven
        self.assertEqual(r["not_checked"], [])
        self.assertIn("exchange answered under the listed key", r["detail"])
        sent = json.loads(next(self.out.glob("lab-1/attempts/*/exchange.request.json")).read_text())
        self.assertEqual(sent["subject"], {"exchange": self.HALF})
        self.assertTrue(next(self.out.glob("lab-1/attempts/*/exchange.ledger.json")).is_file())
        self.assertTrue(json.loads((self.out / "first-answered.json").read_text())["sealed"])

    def test_an_exchange_answer_serving_no_record_is_a_fail(self):
        r = self.run_exchange("exchange_empty")
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("serves no record", r["detail"])

    def test_an_exchange_answer_serving_a_record_that_cites_another_half_is_a_fail(self):
        r = self.run_exchange("exchange_other_half")
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("do not cite the asked half", r["detail"])

    def test_an_exchange_answer_whose_records_cannot_be_read_is_not_checked(self):
        r = self.run_exchange("exchange_unreadable")
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn("the served exchange records could not be read from the answer",
                      [i["why"] for i in r["not_checked"]])

    def test_an_exchange_answer_whose_records_fail_the_citation_check_is_not_counted_as_answered(self):
        asker, extra = self.exchange_dirs()
        r = self.run_live({"checkpoints": "absence", "history_card": "absence", "exchange": "exchange_empty"},
                          ledger=asker, node_extra=extra)
        self.assertEqual(r["result"], "FAIL")
        self.assertIsNone(json.loads((self.out / "first-answered.json").read_text())["answered_at"])

    def test_an_unsatisfiable_coverage_refusal_of_the_exchange_ask_is_checked_for_its_key(self):
        asker, extra = self.exchange_dirs()
        r = self.run_live({**VERIFIED, "exchange": "no_checkpoint_yet"}, ledger=asker, node_extra=extra)
        self.assertIn(step.EXCHANGE_ISSUE, self.issues(r))
        self.assertNotEqual(r["result"], "FAIL", r["detail"])
        fail, _ = step.judge_exchange_line("no_checkpoint_yet reason=coverage_unsatisfiable key=" + "3" * 64
                                           + " listed_key=differs", {})
        self.assertIn("not the listed key", fail)

    def test_malformed_ledger_records_leave_the_exchange_ask_not_checked_not_fatal(self):
        asker, extra = self.exchange_dirs()
        node = Path(extra["data_dir"]) / "ledger"
        with (node / "capsules.jsonl").open("a") as f:
            f.write('{"capsule_id": [], "references": 1}\n{"references": [1, {"citation_purpose": "counterparty_half", "digest": []}]}\n')
        with (asker / "ledger" / "capsules.jsonl").open("a") as f:
            f.write('{"capsule_id": []}\n')
        half, why = step.exchange_subject(extra, asker)
        self.assertEqual(half, self.HALF, why)
        (node / "capsules.jsonl").write_text('{"references": 1, "capsule_id": {}}\n')
        self.assertEqual(step.exchange_subject(extra, asker)[0], None)

    def test_served_records_that_are_not_objects_fail_not_crash(self):
        path = Path(self.tmp.name) / "resp.json"
        artifact = base64.urlsafe_b64encode(json.dumps({"records": [{"body": b"[1]".hex()}]}).encode()).decode()
        path.write_text(json.dumps({"answer": {"artifact": artifact}}))
        errs, notes = step.exchange_records_cite(path, self.HALF)
        self.assertEqual((len(errs), notes), (1, []))
        artifact = base64.urlsafe_b64encode(json.dumps({"records": [{"body": b'{"references": 1}'.hex()}]}).encode()).decode()
        path.write_text(json.dumps({"answer": {"artifact": artifact}}))
        errs, notes = step.exchange_records_cite(path, self.HALF)
        self.assertEqual((len(errs), notes), (1, []))

    def test_the_exchange_ask_from_the_self_node_reads_the_second_data_dir_and_leaves_no_question_registered(self):
        asker, extra = self.exchange_dirs()
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        console = FakeConsole({**VERIFIED, "exchange": "exchange_verified"}, LISTED, asker)
        try:
            r = self.run_live(VERIFIED, ledger=other, data_dir_flag=False, node_extra=extra,
                              flags=("--self-node", "lab-1", "--second-console", console.url,
                                     "--second-data-dir", str(asker)))
        finally:
            console.close()
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertEqual(sorted(step.daily_report.QUESTIONS), ["checkpoints", "history_card"])
        self.assertNotIn("exchange", step.daily_report.QUESTION_NAMES)

    def test_a_withholding_refusal_of_the_exchange_ask_is_a_fail(self):
        r = self.run_exchange("refusal_withheld", sealed="refusal_match")
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("exchange answer is refused (no_such_subject)", r["detail"])

    def test_a_policy_declined_refusal_of_the_exchange_ask_is_a_fail_under_v1(self):
        # The exchange ask goes under evidence-interop/1, where policy_declined is policy: a half the node cites,
        # asked by the asker it names, is one the policy answers (capsules-plugin#114).
        asker, extra = self.exchange_dirs()
        r = self.run_live({"checkpoints": "absence", "history_card": "absence", "exchange": "refusal"},
                          ledger=asker, node_extra=extra, sealed={"exchange": "refusal_not_checked"})
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("the exchange answer is refused (policy_declined)", r["detail"])
        request = json.loads(next(self.out.glob("lab-1/attempts/*/exchange.request.json")).read_text())
        self.assertEqual(request["profile"], "evidence-interop/1")

    def test_an_earlier_v0_exchange_proof_is_judged_as_it_was_recorded(self):
        # A proof from a run that asked under /0 keeps its reading: its policy_declined is not checked.
        half = "a" * 64
        v0 = step.exchange_question(half, "evidence-interop/0")
        self.assertEqual(step.expected_question(step.EXCHANGE, half, v0), v0)
        line = "refusal reason=policy_declined key=" + "3" * 64 + " listed_key=match"
        fail, nc = step.judge_exchange_line(line, {}, step.request_profile(v0))
        self.assertIsNone(fail)
        self.assertEqual(nc["issue"], step.REFUSAL_ISSUE)
        # Any other request is not the question this step asks.
        other = dict(v0, profile="evidence-interop/9")
        self.assertNotEqual(step.expected_question(step.EXCHANGE, half, other), other)

    def test_an_exchange_refusal_signed_under_another_key_is_a_fail(self):
        r = self.run_live({**VERIFIED, "exchange": "refusal"}, announced="3" * 64, **dict(
            zip(("ledger", "node_extra"), self.exchange_dirs())), sealed={"exchange": "refusal_match"})
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("exchange refusal is signed under a key that is not the listed key", r["detail"])

    def test_no_half_the_asker_sent_and_the_node_cites_leaves_the_exchange_ask_not_checked_unasked(self):
        for dirs in ({"held": False}, {"cited": False}, {"sent": False}):
            with self.subTest(dirs=dirs):
                shutil.rmtree(Path(self.tmp.name) / "live", ignore_errors=True)
                r = self.run_exchange("exchange_verified", **dirs)
                self.assertIn(step.EXCHANGE_ISSUE, self.issues(r))
                self.assertFalse(list(self.out.glob("lab-1/attempts/*/exchange.request.json")), dirs)

    def test_a_node_with_no_readable_data_dir_leaves_the_exchange_ask_not_checked(self):
        r = self.run_live(VERIFIED, ledger=self.ledger())
        self.assertIn(step.EXCHANGE_GAP, [i["why"] for i in r["not_checked"]])

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
        with mock.patch.object(step, "ask_records", side_effect=lambda l, raw=None: altered(ask(l, raw))):
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
                extract = step.ledger_extract(ledger / "ledger", "peer-1", asked, at, at, None, dt.timedelta(minutes=5))
                errs, notes = step.judge_sealed(extract, "peer-1", asked, at, at, None, dt.timedelta(minutes=5))
        self.assertEqual(errs, [])
        self.assertTrue(any("not sealed yet" in n for n in notes), notes)

    def test_an_outcome_sealed_while_waiting_is_kept_with_the_ledger_it_was_read_from(self):
        ledger, asked = self.ledger(), {"profile": "p", "subject": {"checkpoints": None}}
        seal_records(ledger, "peer-1", asked, "none")
        path = ledger / "ledger" / "capsules.jsonl"
        req = json.loads(path.read_text().splitlines()[-1])
        rid, h = req["capsule_id"], req["model_attestation"]["compute_attestation"][step.BOOK_BLOCK]
        sealed = step.utc(h["committed_at"])
        window = {"opened": h["committed_at"], "closed": iso(sealed + dt.timedelta(seconds=60))}

        def seal_absence(_):  # the plugin seals the absence while the step waits
            with path.open("a") as f:
                f.write(ask_record("f" * 64, {
                    "record_type": "recorded_absence", "epistemic_type": "observed_event",
                    "links": [{"type": "cites", "target": rid}], "committed_at": window["closed"],
                    "statement": {"outcome": "recorded_absence", "request_digest": step.request_digest(
                        h["statement"]["request"]), "window": window}}))
        at = step.now()
        with mock.patch.object(step.time, "sleep", side_effect=seal_absence):
            extract = step.ledger_extract(ledger / "ledger", "peer-1", asked, at, at, None, None)
        self.assertEqual([o["record"] for o in extract["outcomes"]], ["f" * 64])
        raw = path.read_bytes()
        self.assertEqual(extract["held"], {"sha256": step.sha256(raw), "lines": len(raw.splitlines())})

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
