"""steps/12-join: join, then a stranger's probe that verifies and matches the advertised policy, or FAIL.

The doctor backend needs the pinned capsulectl and capsulectl-book
(steps/12-join.setup.sh); without them those tests SKIP and say why, or, with
LOOP_REQUIRE_TOOLS=1 (CI, after the setups ran), fail to load. The
reporter backend runs against a fake reporter console on localhost.
Fixtures: tests/fixtures/step12/, made by make_fixtures.py.
"""
import base64
import contextlib
import copy
import functools
import hashlib
import io
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# A live step fetches origin/main before reading nodes/revocations/ (bv.snapshot); tests never touch the network.
os.environ.setdefault("LOOP_REVOCATIONS_FETCH", "0")

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "step12"
REPORTER_FIX = ROOT / "tests" / "reporter" / "fixtures"


def load_step():
    loader = importlib.machinery.SourceFileLoader("step12_join", str(ROOT / "steps" / "12-join"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


step = load_step()
finding = step.finding
FIXTURE_AT = "2026-11-01T00:00:00Z"  # inside the joined fixtures' binding window
CAPSULECTL = step.tools()
NO_TOOL = f"capsulectl not installed at {CAPSULECTL}: run steps/12-join.setup.sh (CI does)"


CROSS = step.cross_language()
NO_CROSS = f"cross_language not installed at {CROSS}: run steps/01-seal.setup.sh (CI does)"
needs_cross = unittest.skipUnless(CROSS.is_file() and os.access(CROSS, os.X_OK), NO_CROSS)


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
        self.out = Path(self.tmp.name) / "findings" / "12-join"
        self.out.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def joined(self):
        """A copy of alice as join.sh left her, in a fresh findings directory."""
        node = self.out / "alice"
        shutil.copytree(FIX / "joined" / "alice", node)
        return node

    def conclude(self, errs, not_checked, proof):
        return step.conclude(self.out, "rehearsal", [f"alice: {e}" for e in errs], not_checked,
                             [f"alice/{p}" for p in proof], ["alice: joined"], finding.now())


@needs_cross
class BindingRecordTest(Tmp):
    """check_binding_record: binding.json, as join.sh wrote it, is checked by the Go verifier."""

    def test_the_fixture_binding_applies(self):
        self.assertEqual(step.check_binding_record(self.joined(), FIXTURE_AT), [])

    def test_a_binding_outside_its_window_does_not_hold(self):
        errs = step.check_binding_record(self.joined(), "2031-01-01T00:00:00Z")
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("controller_binding_window FAIL", errs[0])

    def test_a_binding_naming_another_key_than_the_nodes_does_not_hold(self):
        node = self.joined()
        c = json.loads((node / "controller.json").read_text())
        c["node_key"] = "ab" * 32
        (node / "controller.json").write_text(json.dumps(c))
        errs = step.check_binding_record(node, FIXTURE_AT)
        self.assertIn("does not name the node's key", errs[0])

    def test_a_binding_altered_after_signing_does_not_hold(self):
        node = self.joined()
        b = json.loads((node / "binding.json").read_text())
        b["claim"] = "same_controller_asserted "
        (node / "binding.json").write_text(json.dumps(b))
        errs = step.check_binding_record(node, FIXTURE_AT)
        self.assertEqual(len(errs), 1, errs)

    def test_files_that_are_not_the_binding_join_writes_do_not_hold(self):
        node = self.joined()
        (node / "binding.json").write_text("not json")
        self.assertIn("does not hold: unread", step.check_binding_record(node, FIXTURE_AT)[0])
        (node / "binding.json").unlink()
        self.assertIn("not the controller binding join.sh writes", step.check_binding_record(node, FIXTURE_AT)[0])

    def test_a_method_the_verifier_lacks_does_not_hold_here(self):
        node = self.joined()
        b = json.loads((node / "binding.json").read_text())
        b["method"] = "x-unknown/0"
        (node / "binding.json").write_text(json.dumps(b))
        errs = step.check_binding_record(node, FIXTURE_AT)
        self.assertEqual(len(errs), 1, errs)


class DoctorBackendTest(Tmp):
    @needs_capsulectl
    def test_rehearsal_population_joins_and_passes_with_revocation_on_the_runs_snapshot(self):
        self.assertEqual(step.main(["--population", str(FIX / "population"), "--out", str(self.out)]), 0)
        r = json.loads((self.out / "finding.json").read_text())
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertEqual(r["not_checked"], [])
        # The record is checked by the Go verifier, the node key's role included (AAC#468), and its revocation PASSes
        # on the list the run declares complete (CB-1 option A, agent-action-capsule#495): nothing stays outside.
        self.assertEqual(finding.gaps(r["detail"]), [])
        self.assertIn("revocation PASS: no statement applies in the list evidence-network/loop step 12", r["detail"])
        self.assertEqual(set(r["pinned"]), {"capsulectl", "capsulectl-book", "aac-go"})
        for node in ("alice", "bob"):
            doctor = {f["check"]: f["state"] for f in
                      json.loads((self.out / node / "doctor.json").read_text())["findings"]}
            self.assertEqual((doctor["probe_checkpoints"], doctor["probe_history_card"]), ("match", "match"))
            self.assertIn(f"{node}/doctor.json", [p["path"] for p in r["proof"]])
            for probe in step.PROBES:
                v = json.loads((self.out / node / "probe" / f"{probe}.verified.json").read_text())
                self.assertEqual((v["exit"], v["result"]["outcome"]), (0, "artifact"))
            self.assertIn(f"{node}/probe/history_card.answer.json", [p["path"] for p in r["proof"]])
        self.assertEqual(finding.check(self.out.parent), [])

    def reproduce(self, mutate=None):
        """Step 08's way: only the cited proof files copied to a fresh dir, then decide() on them."""
        r = json.loads((self.out / "finding.json").read_text())
        work = Path(self.tmp.name) / "work" / "12-join"
        for p in r["proof"]:
            (work / p["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.out / p["path"], work / p["path"])
        if mutate:
            mutate(work)
        return r, step.decide(work, "rehearsal", finding.now(), FIX / "population")

    @needs_capsulectl
    def test_the_proof_cites_the_exit_code_and_the_strangers_book_and_reproduces_alone(self):
        self.assertEqual(step.main(["--population", str(FIX / "population"), "--out", str(self.out)]), 0)
        r, again = self.reproduce()
        cited = [p["path"] for p in r["proof"]]
        for node in ("alice", "bob"):
            self.assertIn(f"{node}.join.exit", cited)
            self.assertIn(f"{node}.join.log", cited)
            self.assertIn(f"{node}/probe/stranger/profile.json", cited)
            self.assertIn(f"{node}/probe/stranger/signing.seed", cited)
        self.assertEqual((again["result"], again["not_checked"]), (r["result"], r["not_checked"]))
        self.assertEqual(again["result"], "PASS")

    @needs_capsulectl
    def test_one_changed_proof_byte_reproduces_as_a_fail(self):
        self.assertEqual(step.main(["--population", str(FIX / "population"), "--out", str(self.out)]), 0)
        for rel in ("alice/probe/history_card.answer.json", "alice/probe/stranger/profile.json",
                    "alice/commitment.json"):
            def flip(work, rel=rel):
                f = work / rel
                b = bytearray(f.read_bytes())
                b[len(b) // 2] ^= 1
                f.write_bytes(bytes(b))
            _, again = self.reproduce(flip)
            self.assertEqual(again["result"], "FAIL", rel)

    @needs_capsulectl
    def test_a_missing_or_unreadable_exit_code_or_no_proof_at_all_reproduces_as_a_fail(self):
        self.assertEqual(step.main(["--population", str(FIX / "population"), "--out", str(self.out)]), 0)
        _, again = self.reproduce(lambda work: (work / "alice.join.exit").unlink())
        self.assertEqual(again["result"], "FAIL")
        _, again = self.reproduce(lambda work: (work / "alice.join.exit").write_text("x\n"))
        self.assertEqual(again["result"], "FAIL")
        shutil.rmtree(Path(self.tmp.name) / "work")
        _, again = self.reproduce(lambda work: [f.unlink() for f in work.glob("*.join.exit")])
        self.assertEqual(again["result"], "FAIL")

    @needs_capsulectl
    def test_a_judged_file_removed_or_corrupt_still_reproduces_as_a_fail_citing_what_remains(self):
        self.assertEqual(step.main(["--population", str(FIX / "population"), "--out", str(self.out)]), 0)
        _, again = self.reproduce(lambda work: (work / "alice/probe/stranger/profile.json").write_text("{"))
        self.assertEqual(again["result"], "FAIL")
        self.assertIn("alice/commitment.json", [p["path"] for p in again["proof"]])
        shutil.rmtree(Path(self.tmp.name) / "work")
        _, again = self.reproduce(lambda work: (work / "alice/commitment.json").unlink())
        self.assertEqual(again["result"], "FAIL")

    @needs_capsulectl
    def test_a_kept_nonzero_join_exit_code_reproduces_as_a_fail(self):
        self.assertEqual(step.main(["--population", str(FIX / "population"), "--out", str(self.out)]), 0)
        _, again = self.reproduce(lambda work: (work / "alice.join.exit").write_text("1\n"))
        self.assertEqual(again["result"], "FAIL")
        self.assertIn("alice: join.sh did not complete (exit 1)", again["detail"])

    @needs_capsulectl
    def test_the_unaltered_fixture_holds(self):
        node = self.joined()
        errs, nc, _ = step.judge(node, CAPSULECTL, Path(self.tmp.name) / "work", at=FIXTURE_AT)
        self.assertEqual((errs, nc), ([], []))

    def test_a_commitment_altered_after_signing_is_a_fail(self):
        node = self.joined()
        c = json.loads((node / "commitment.json").read_text())
        c["answers_within"] = "PT24H"
        (node / "commitment.json").write_text(json.dumps(c))
        errs = step.check_commitment(node)
        self.assertEqual(errs, ["the commitment does not verify: signature does not verify under spki"])
        self.assertEqual(self.conclude(errs, [], step.PROOF)["result"], "FAIL")
        self.assertEqual(finding.check(self.out.parent), [])

    def test_a_probe_doctor_did_not_find_matching_is_a_fail(self):
        node = self.joined()
        d = json.loads((node / "doctor.json").read_text())
        for f in d["findings"]:
            if f["check"] == "probe_history_card":
                f["state"] = "mismatch"
        (node / "doctor.json").write_text(json.dumps(d))
        errs = step.check_doctor(node, step.advertised(node))
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("probe_history_card: mismatch", errs[0])
        self.assertEqual(self.conclude(errs, [], step.PROOF)["result"], "FAIL")

    def test_a_binding_doctor_does_not_list_is_a_fail(self):
        node = self.joined()
        c = json.loads((node / "controller.json").read_text())
        c["binding_digest"] = "0" * 64
        (node / "controller.json").write_text(json.dumps(c))
        errs = step.check_doctor(node, step.advertised(node))
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("does not list the controller binding", errs[0])

    def test_the_fixture_commitment_and_doctor_hold(self):
        node = self.joined()
        self.assertEqual(step.check_commitment(node), [])
        self.assertEqual(step.check_doctor(node, step.advertised(node)), [])

    @needs_capsulectl
    def test_a_probe_answer_signed_by_the_wrong_key_is_a_fail(self):
        node = self.joined()
        # bob's book answered the request alice's stranger sent.
        shutil.copy(FIX / "wrong-key" / "checkpoints.answer.json", node / "probe" / "checkpoints.answer.json")
        errs, nc, proof = step.judge(node, CAPSULECTL, Path(self.tmp.name) / "work", at=FIXTURE_AT)
        self.assertTrue(any("does not verify under the advertised" in e for e in errs), errs)
        self.assertEqual(self.conclude(errs, nc, proof)["result"], "FAIL")
        self.assertEqual(finding.check(self.out.parent), [])

    def test_an_answer_under_a_policy_other_than_the_advertised_one_is_a_fail(self):
        node = self.joined()
        # The node answers under a policy whose digest is not the advertised one.
        (node / "policy.json").write_text(
            '{"adjudications":"off","history_segments":"off","record_at_completion":"off"}\n')
        errs = step.check_policy_match(node, step.advertised(node))
        self.assertEqual(errs, ["the node answers under a policy that is not the one it advertises"])
        self.assertEqual(self.conclude(errs, [], step.PROOF)["result"], "FAIL")
        self.assertEqual(finding.check(self.out.parent), [])

    def test_a_refusal_of_a_subject_the_policy_answers_to_anyone_is_a_fail(self):
        node = self.joined()
        (node / "probe" / "history_card.answer.json").write_text(json.dumps(
            {"key_id": step.advertised(node)["node_key"], "reason": "not_authorized", "sig": "00"}))
        errs = step.check_policy_match(node, step.advertised(node))
        self.assertTrue(any("refused (not_authorized)" in e for e in errs), errs)

    def test_the_fixture_matches_its_advertised_policy(self):
        node = self.joined()
        self.assertEqual(step.check_policy_match(node, step.advertised(node)), [])


class FakeConsole:
    """The reporter node's console: peer-key and mesh_evidence_request, from the reporter's fixtures."""

    def __init__(self, answers, announced, own=None, doctor=None, committed=None, doctor_raw=None, problems=(), me=None, key_id=None, announced_key=None):
        """`own` is the node_id of the node this console belongs to. Like the real one, the console does
        not answer for its own node: a 502 with the absence text, for the key and for each question."""
        fake = self
        self.answers, self.announced, self.own = answers, announced, own
        self.doctor, self.committed = doctor, committed
        self.doctor_raw, self.problems = doctor_raw, list(problems)
        self.doctor_asked, self.fetched, self.doctor_bodies, self.joins = 0, [], [], []
        self.me, self.key_id, self.announced_key = me, key_id or LISTED, announced_key
        self.asked = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, body):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if fake.own is not None and parse_qs(urlparse(self.path).query).get("peer") == [fake.own]:
                    return self.reply(502, (REPORTER_FIX / "absence.txt").read_bytes())
                self.reply(200, json.dumps({"announced_key_id": fake.announced}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.endswith("/tools/commitment"):
                    if fake.me is None:
                        return self.reply(404, b"no such tool")
                    return self.reply(200, json.dumps({"commitment": {"node_id": fake.me}}).encode())
                if self.path.endswith("/tools/join"):
                    fake.joins.append(body)
                    return self.reply(404, b"no such tool")
                if self.path.endswith("/tools/doctor"):
                    fake.doctor_asked += 1
                    fake.doctor_bodies.append(body)
                    if fake.doctor_raw is not None:
                        return self.reply(200, json.dumps(fake.doctor_raw).encode())
                    if fake.doctor is None:
                        return self.reply(404, b"no such tool")
                    state, digest = fake.doctor
                    f = {"check": "controller_binding", "state": state, "detail": "d"}
                    if digest:
                        f["records"] = [digest]
                    return self.reply(200, json.dumps({"findings": [{"check": "self_attested", "state": "x"}, f]}).encode())
                if self.path.endswith("/tools/mesh_commitment_fetch"):
                    fake.fetched.append(body["peer_id"])
                    c = {"node_id": body["peer_id"], "key_id": fake.key_id}
                    digest = fake.committed.get(body["peer_id"]) if isinstance(fake.committed, dict) else fake.committed
                    if digest:
                        c["controller_binding"] = {"digest": digest}
                    out = {"commitment": c, "problems": fake.problems}
                    if fake.announced_key:
                        out["announced_key"] = fake.announced_key
                    return self.reply(200, json.dumps(out).encode())
                fake.asked.append(body["peer_id"])
                if fake.own is not None and body["peer_id"] == fake.own:
                    return self.reply(502, (REPORTER_FIX / "absence.txt").read_bytes())
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


LISTED = "2" * 64
LAB = [{"name": f"lab-{i}", "node_id": str(i) * 64, "key_id": LISTED,
        "disclosure": {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]}}
       for i in (1, 2, 3)]
VERIFIED = {"checkpoints": "verified", "history_card": "verified_history_card"}


class ReporterBackendTest(Tmp):
    def run_live(self, answers, announced=LISTED):
        pop_dir = Path(self.tmp.name) / "live"
        pop_dir.mkdir()
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "rail": None, "nodes": [{
            "name": "lab-1", "node_id": "1" * 64, "key_id": LISTED,
            "disclosure": {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]}}]}))
        console = FakeConsole(answers, announced)
        try:
            self.assertEqual(step.main(["--population", str(pop_dir), "--out", str(self.out),
                                        "--console", console.url]), 0)
        finally:
            console.close()
        self.assertEqual(finding.check(self.out.parent), [])
        return json.loads((self.out / "finding.json").read_text())

    def run_lab(self, *flags, second=True, doctor=None, committed=None, node_consoles=(), doctor_raw=None, problems=(),
                 env=None, own_consoles=None, key_id=None, announced_key=None):
        """Three lab nodes, lab-3 the reporter: --console is lab-3's, the second console lab-1's. Each
        console refuses its own node."""
        pop_dir = Path(self.tmp.name) / "lab"
        pop_dir.mkdir()
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "rail": None, "nodes": LAB}))
        reporter = FakeConsole(VERIFIED, LISTED, own="3" * 64, doctor=doctor, doctor_raw=doctor_raw,
                               committed=committed, me="3" * 64, key_id=key_id)
        other = FakeConsole(VERIFIED, LISTED, own="1" * 64, committed=committed, problems=problems, key_id=key_id, announced_key=announced_key)
        self.consoles = {"reporter": reporter, "second": other}
        argv = ["--population", str(pop_dir), "--out", str(self.out), "--console", reporter.url]
        for flag in flags:
            argv += {"self": ["--self-node", "lab-3"], "second": ["--second-console", other.url]}[flag]
        own = {n: FakeConsole(VERIFIED, LISTED, doctor=d, me=str(n[-1]) * 64) for n, d in (own_consoles or {}).items()}
        self.own = own
        for n in node_consoles:
            argv += ["--node-console", f"{n}={reporter.url}"]
        for n, c in own.items():
            argv += ["--node-console", f"{n}={c.url}"]
        old = os.environ.get("LOOP_NODE_CONSOLES")
        if env is not None:
            os.environ["LOOP_NODE_CONSOLES"] = env(own, reporter) if callable(env) else env
        try:
            self.assertEqual(step.main(argv), 0)
        finally:
            if env is not None:
                os.environ.pop("LOOP_NODE_CONSOLES", None)
                if old is not None:
                    os.environ["LOOP_NODE_CONSOLES"] = old
            reporter.close()
            other.close()
            for c in own.values():
                c.close()
        self.assertEqual(finding.check(self.out.parent), [])
        self.asked = {"reporter": reporter.asked, "second": other.asked}
        return json.loads((self.out / "finding.json").read_text())

    def per_node(self, r):
        """The probe answers' per-node entries: not the binding's, nor the plugin's doctor --probe and join's."""
        return [i for i in r["not_checked"] if i["what"].startswith("lab-") and "binding" not in i["what"]
                and "doctor --probe" not in i["what"] and "the plugin's" not in i["what"]
                and "commitment/1" not in i["what"] and step.SIGNED_WHAT not in i["what"]]

    def test_with_both_flags_the_reporter_node_is_asked_through_the_second_console(self):
        r = self.run_lab("self", "second")
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])  # the plugin issues only
        self.assertEqual(self.per_node(r), [])
        self.assertEqual(set(self.asked["second"]), {"3" * 64})
        self.assertEqual(set(self.asked["reporter"]), {"1" * 64, "2" * 64})
        for n in ("lab-1", "lab-2"):
            self.assertIn(f"{n} (asked through the reporter's console): checkpoints answered under the listed key; "
                          "history_card/1 answered under the listed key", r["detail"])
        self.assertIn("lab-3 (asked through a second node's console): checkpoints answered under the listed key",
                      r["detail"])
        self.assertNotIn("127.0.0.1", r["detail"])

    def test_a_self_node_without_a_second_console_is_not_checked_never_fail(self):
        r = self.run_lab("self")
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertEqual(self.per_node(r), [step.no_second_console("lab-3")])
        self.assertIn("can't be asked through its own console", " ".join(i["why"] for i in r["not_checked"]))
        self.assertIn("--second-console", " ".join(i["why"] for i in r["not_checked"]))
        self.assertNotIn("3" * 64, self.asked["reporter"])
        self.assertIn("lab-3: not asked, the reporter's own node and no second console", r["detail"])

    def test_with_neither_flag_every_node_is_asked_through_the_console_as_before(self):
        r = self.run_lab()
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertEqual(set(self.asked["reporter"]), {"1" * 64, "2" * 64, "3" * 64})
        self.assertEqual(self.asked["second"], [])
        self.assertIn("lab-1: checkpoints answered under the listed key", r["detail"])
        self.assertNotIn("asked through", r["detail"])
        # lab-3 is asked through its own console, which does not answer for it, as on the first lab run.
        self.assertEqual([i["what"] for i in self.per_node(r)],
                         ["lab-3: the checkpoints probe answer", "lab-3: the history_card/1 probe answer"])

    def test_a_self_node_not_in_the_population_is_refused(self):
        # A stale or misspelt name (a host name in place of the operator's label) would quietly ask
        # the reporter's node through its own console again.
        pop_dir = Path(self.tmp.name) / "lab"
        pop_dir.mkdir()
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "rail": None, "nodes": LAB}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = step.main(["--population", str(pop_dir), "--out", str(self.out), "--console", "http://127.0.0.1:1",
                            "--self-node", "lab-9", "--second-console", "http://127.0.0.1:1"])
        self.assertEqual(rc, 2)
        self.assertIn("--self-node 'lab-9' is not a node in the population", err.getvalue())

    def test_the_flags_come_from_the_environment_too(self):
        env = {"LOOP_SELF_NODE": "lab-3", "LOOP_SECOND_CONSOLE": "http://127.0.0.1:1"}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            r = self.run_lab()
        finally:
            for k, v in old.items():
                os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(self.asked["second"], [])
        self.assertNotIn("3" * 64, self.asked["reporter"])
        self.assertIn("lab-3 (asked through a second node's console)", r["detail"])

    D = "d" * 64

    def test_a_bound_node_whose_commitment_names_the_same_digest_is_shown_bound(self):
        r = self.run_lab("self", "second", doctor=("bound", self.D), committed=self.D)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn(f"lab-3: doctor reports the controller binding bound ({self.D}); its signed commitment, fetched through the console that asks it, names the same digest", r["detail"])
        # No data_dir in the population: the record behind the digest cannot be read.
        self.assertIn("lab-3: the controller binding", " ".join(i["what"] for i in r["not_checked"]))

    def test_an_unbound_node_with_no_digest_in_its_commitment_holds_the_binding_is_optional(self):
        r = self.run_lab("self", "second", doctor=("unbound", None))
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn("lab-3: doctor reports no controller binding held, and the signed commitment names none", r["detail"])

    def test_a_commitment_digest_other_than_the_held_binding_is_a_fail(self):
        r = self.run_lab("self", "second", doctor=("bound", self.D), committed="e" * 64)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("the commitment names the controller binding " + "e" * 64, r["detail"])

    def test_a_digest_in_the_commitment_with_no_binding_held_is_a_fail(self):
        r = self.run_lab("self", "second", doctor=("unbound", None), committed=self.D)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("no binding held", r["detail"])

    def test_a_held_binding_the_commitment_does_not_name_is_a_fail(self):
        r = self.run_lab("self", "second", doctor=("bound", self.D))
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("names the controller binding none", r["detail"])

    def test_a_doctor_without_the_finding_is_not_checked_never_fail(self):
        r = self.run_lab("self", "second")
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn("lab-3: the controller binding", " ".join(i["what"] for i in r["not_checked"]))

    def test_other_nodes_binding_without_a_console_is_not_checked(self):
        r = self.run_lab("self", "second", doctor=("unbound", None))
        whats = [i["what"] for i in r["not_checked"]]
        self.assertIn("lab-1: the controller binding", whats)
        self.assertIn("lab-2: the controller binding", whats)

    def test_a_node_with_its_own_console_has_its_binding_read_too(self):
        r = self.run_lab("self", "second", doctor=("unbound", None),
                         own_consoles={"lab-1": ("unbound", None), "lab-2": ("unbound", None)})
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        for n in ("lab-1", "lab-2", "lab-3"):
            self.assertIn(f"{n}: doctor reports no controller binding held, and the signed commitment names none", r["detail"])
        self.assertEqual([i["what"] for i in r["not_checked"] if i["what"].startswith("lab-") and "binding" in i["what"]],
                         [f"{n}: the controller binding" for n in ("lab-1", "lab-2", "lab-3")])
        self.assertTrue(all(i["why"].startswith("no controller binding recorded") for i in r["not_checked"]
                            if i["what"].startswith("lab-") and "binding" in i["what"]))

    def test_each_node_doctor_is_asked_through_its_own_console_and_its_commitment_through_the_asker(self):
        d1, d2 = "1" * 64, "2" * 64
        r = self.run_lab("self", "second", doctor=("bound", self.D), committed={"1" * 64: d1, "2" * 64: d2, "3" * 64: self.D},
                         own_consoles={"lab-1": ("bound", d1), "lab-2": ("bound", d2)})
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        # Each node's doctor: the binding read, then --probe before join, then the binding after join.
        for c in (self.own["lab-1"], self.own["lab-2"], self.consoles["reporter"]):  # the self node's is --console
            self.assertEqual(c.doctor_bodies, [{}, {"probe": True}, {}])
            self.assertEqual(c.joins, [{}])
        self.assertEqual(sorted(self.consoles["reporter"].fetched), ["1" * 64, "2" * 64])
        self.assertEqual(self.consoles["second"].fetched, ["3" * 64])
        for n, d in (("lab-1", d1), ("lab-2", d2), ("lab-3", self.D)):
            self.assertIn(f"{n}: doctor reports the controller binding bound ({d}); its signed commitment, fetched through the console that asks it, names the same digest", r["detail"])

    def test_a_node_whose_doctor_digest_differs_from_its_commitment_fails_on_that_node(self):
        r = self.run_lab("self", "second", doctor=("unbound", None), committed={"1" * 64: "9" * 64},
                         own_consoles={"lab-1": ("bound", "1" * 64)})
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("lab-1: the commitment names the controller binding " + "9" * 64, r["detail"])

    def assert_unreadable_doctor(self, raw):
        r = self.run_lab("self", "second", doctor_raw=raw)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn("lab-3: the controller binding", " ".join(i["what"] for i in r["not_checked"]))

    def test_a_doctor_with_null_findings_is_not_checked_never_a_crash(self):
        self.assert_unreadable_doctor({"findings": None})

    def test_a_doctor_with_findings_that_are_not_objects_is_not_checked(self):
        self.assert_unreadable_doctor({"findings": [1, "x"]})

    def test_a_doctor_answer_that_is_not_an_object_is_not_checked(self):
        self.assert_unreadable_doctor([])

    def test_a_bound_finding_with_records_that_is_not_a_list_is_a_fail_not_a_crash(self):
        r = self.run_lab("self", "second", doctor_raw={"findings": [
            {"check": "controller_binding", "state": "bound", "records": 5}]})
        self.assertEqual(r["result"], "FAIL")

    def test_bound_without_a_digest_is_a_fail(self):
        r = self.run_lab("self", "second", doctor_raw={"findings": [{"check": "controller_binding", "state": "bound"}]})
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("bound without a sha256 digest", r["detail"])

    def test_a_commitment_fetch_with_problems_is_not_checked_never_fail(self):
        r = self.run_lab("self", "second", doctor=("bound", self.D), committed=self.D, problems=["bad signature"])
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn("bad signature", " ".join(i["why"] for i in r["not_checked"]))

    def test_a_flag_overrides_the_environment_for_the_same_node(self):
        r = self.run_lab("self", "second", doctor=("unbound", None), own_consoles={"lab-1": ("bound", "1" * 64)},
                         committed={"1" * 64: "1" * 64}, env="lab-1=http://127.0.0.1:9")
        self.assertEqual(self.own["lab-1"].doctor_asked, 3)
        self.assertIn("lab-1: doctor reports the controller binding bound", r["detail"])

    def test_a_node_console_for_a_node_not_in_the_population_is_refused(self):
        pop_dir = Path(self.tmp.name) / "lab2"
        pop_dir.mkdir()
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "rail": None, "nodes": LAB}))
        self.assertEqual(step.main(["--population", str(pop_dir), "--out", str(self.out), "--node-console",
                                    "lab-9=http://127.0.0.1:9"]), 2)

    def test_a_console_that_answers_as_another_node_is_not_checked_never_holds(self):
        # --node-console lab-1 pointed at lab-3's console: both unbound would otherwise "hold".
        r = self.run_lab("self", "second", doctor=("unbound", None), node_consoles=("lab-1",))
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertNotIn("lab-1: doctor reports", r["detail"])
        self.assertIn("answers as " + "3" * 64 + ", not as " + "1" * 64,
                      " ".join(i["why"] for i in r["not_checked"]))

    def test_a_commitment_under_a_key_other_than_the_listed_one_is_not_checked(self):
        r = self.run_lab("self", "second", doctor=("unbound", None), key_id="7" * 64)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn("not under the listed key", " ".join(i["why"] for i in r["not_checked"]))

    def test_an_announced_key_does_not_stand_in_for_the_listed_key(self):
        r = self.run_lab("self", "second", doctor=("unbound", None), key_id="7" * 64, announced_key="matches")
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn("not under the listed key", " ".join(i["why"] for i in r["not_checked"]))

    def test_a_doctor_finding_whose_check_is_not_a_string_is_not_checked(self):
        self.assert_unreadable_doctor({"findings": [{"check": []}]})

    def test_an_own_commitment_that_is_not_an_object_is_not_checked(self):
        real = step.post_json
        step.post_json = lambda url, body, timeout: {"commitment": [1]}
        try:
            fail, nc, _, _, named = step.check_binding("lab-1", "1" * 64, LISTED, "http://x", "http://y", 1)
        finally:
            step.post_json = real
        self.assertIsNone(fail)
        self.assertIsNone(named)
        self.assertIn("answers as no node", nc["why"])

    def test_a_malformed_node_console_is_refused(self):
        self.assertEqual(step.main(["--population", str(self.out), "--out", str(self.out), "--node-console", "lab-1"]), 2)

    def test_live_population_uses_the_reporter_backend(self):
        self.assertEqual(step.backend({"population": "live"}), "reporter")
        self.assertEqual(step.backend({"population": "rehearsal"}), "doctor")
        self.assertEqual(step.backend({"population": "rehearsal", "probe": "reporter"}), "reporter")

    def test_verified_answers_still_read_not_checked_on_the_open_gaps(self):
        r = self.run_live({"checkpoints": "verified", "history_card": "verified_history_card"})
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        issues = {i["issue"] for i in r["not_checked"]}
        self.assertNotIn("evidence-network/loop#161", issues)  # matched per node against the signed commitment
        self.assertIn({"what": f"lab-1: {step.SIGNED_WHAT}",
                       "why": "its commitment and signed policy come from join on the node's own console, which this "
                              "run does not hold, so the answers are matched against the nodes/ entry",
                       "issue": step.NODE_CONSOLE_ISSUE}, r["not_checked"])
        self.assertFalse([i for i in issues if i.endswith("agent-action-capsule#432")])
        # The plugin's join and doctor --probe are no longer loop-wide gaps: per node, and here no console is held.
        self.assertNotIn("one command that binds the key, creates the book and publishes the commitment on a Mesh "
                         "node", [i["what"] for i in r["not_checked"]])
        self.assertNotIn("doctor --probe on a Mesh node", [i["what"] for i in r["not_checked"]])
        self.assertIn({"what": "lab-1: the plugin's doctor --probe and join",
                       "why": "they are reachable only through the node's own console, which this run does not hold "
                              "(--node-console)", "issue": step.NODE_CONSOLE_ISSUE}, r["not_checked"])
        self.assertIn("lab-1/attempts", " ".join(p["path"] for p in r["proof"]))

    def test_an_answer_verified_under_a_key_other_than_the_listed_one_is_a_fail(self):
        r = self.run_live({"checkpoints": "verified", "history_card": "verified_history_card"}, announced="3" * 64)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("not the listed key", r["detail"])

    def test_a_withholding_refusal_of_a_listed_stranger_safe_subject_is_a_fail(self):
        r = self.run_live({"checkpoints": "refusal_withheld", "history_card": "verified_history_card"})
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("lists it as stranger-safe", r["detail"])

    def test_an_ambiguous_refusal_of_a_listed_stranger_safe_subject_is_not_checked(self):
        r = self.run_live({"checkpoints": "refusal", "history_card": "verified_history_card"})
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertTrue(any("policy_declined" in i["why"] for i in r["not_checked"]))

    def test_no_answer_is_not_checked_never_a_pass(self):
        r = self.run_live({"checkpoints": "absence", "history_card": "absence"})
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertTrue(any("no verifiable answer" in i["why"] for i in r["not_checked"]))


ALICE = FIX / "joined" / "alice"
ALICE_DOC = json.loads((ALICE / "commitment.json").read_text())
ALICE_ID = ALICE_DOC["node_id"]
BINDING_DIGEST = json.loads((ALICE / "controller.json").read_text())["binding_digest"]
ALICE_ENTRY = {"name": "alice", "node_id": ALICE_ID, "key_id": ALICE_DOC["key_id"],
               "disclosure": {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]}}
ALICE_KEY = FIX / "population" / "keys" / "alice" / "node-key.pem"
OPENSSL_ = shutil.which("openssl")
NO_OPENSSL = "openssl not on PATH: step 03's checker verifies the commitment's signature with it"


# The pinned plugin's disclosure policy, as a live node signs it (findings/2026-10-30-live-5, stevenmih-1).
PLUGIN_POLICY = {"advertise_evidence_requests": "off", "share_adjudications": "off",
                 "share_exchange_corroboration": "off", "share_history_segments": "counterparties",
                 "share_record_at_completion": "counterparty", "share_standard_history": "off",
                 "stranger_safe": ["checkpoints", "history_card/1"]}


@functools.cache
def plugin_pair(stranger_safe=tuple(PLUGIN_POLICY["stranger_safe"]), subjects=("checkpoints", "full_history")):
    """(commitment, policy bytes) for alice as the pinned plugin builds them: a policy carrying stranger_safe, and a
    commitment that also advertises full_history, the subject of the reporter's history_card/1 question
    (daily_report.QUESTIONS). Both signed again under alice's TEST key."""
    cc = step.check_commitment_
    policy = cc.sign({"key_id": ALICE_DOC["key_id"], "profile": "disclosure-policy/0",
                      "policy": {**PLUGIN_POLICY, "stranger_safe": list(stranger_safe)}}, ALICE_KEY)
    raw = cc.canonical(policy)
    doc = copy.deepcopy(ALICE_DOC)
    doc["answers"]["subjects"] = list(subjects)
    doc["lcap"]["policy_digest"] = hashlib.sha256(raw).hexdigest()
    return cc.sign(doc, ALICE_KEY), raw


def plugin_doc():
    return plugin_pair()[0]


def plugin_fixture(name, digest=None):
    """tests/fixtures/step12/plugin/<name>.json with its markers filled: alice's signed commitment pair."""
    text = (FIX / "plugin" / f"{name}.json").read_text()
    for marker, value in (("@KEY_ID@", ALICE_DOC["key_id"]), ("@ANCHOR@", "a" * 64), ("@BOOK@", "/book"),
                          ("@POLICY_DIGEST@", plugin_doc()["lcap"]["policy_digest"])):
        text = text.replace(marker, value)
    reply = json.loads(text)
    if "commitment" in reply:
        reply["commitment"] = {"commitment": copy.deepcopy(plugin_doc()),
                               "policy": base64.b64encode(plugin_pair()[1]).decode()}
    return reply


def with_binding(reply, state):
    """`reply` with its controller_binding finding set to `state`: ("bound", digest), ("unbound", None), or None
    for no such finding."""
    findings = [f for f in reply["findings"] if f["check"] != "controller_binding"]
    if state is not None:
        f = {"check": "controller_binding", "state": state[0], "detail": "d"}
        if state[1]:
            f["records"] = [state[1]]
        findings.append(f)
    return {**reply, "findings": findings}


class PluginConsole:
    """A node's own console, as the pinned capsules plugin answers on it: commitment, doctor (with and without
    probe) and join, from tests/fixtures/step12/plugin/. `before` is the binding doctor reports until join is
    called, `after` the one it reports from then on; `doctor`, `join` and `commitment` change those replies."""

    def __init__(self, node_id=ALICE_ID, before=("unbound", None), after="same", doctor=None, join=None,
                 key_id=None):
        fake = self
        self.node_id, self.before = node_id, before
        self.after = before if after == "same" else after
        self.doctor_change, self.join_change = doctor, join
        self.joins, self.doctor_bodies, self.key_id = [], [], key_id or ALICE_DOC["key_id"]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, body):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.endswith("/tools/commitment"):
                    return self.reply(200, json.dumps({"commitment": {"node_id": fake.node_id}}).encode())
                if self.path.endswith("/tools/mesh_commitment_fetch"):  # as the asker of the self node
                    return self.reply(200, json.dumps({"commitment": {"node_id": body["peer_id"],
                                                                      "key_id": fake.key_id}}).encode())
                if self.path.endswith("/tools/doctor"):
                    fake.doctor_bodies.append(body)
                    reply = plugin_fixture("doctor")
                    if not body.get("probe"):
                        reply["findings"] = [f for f in reply["findings"] if not f["check"].startswith("probe_")]
                    reply = with_binding(reply, fake.after if fake.joins else fake.before)
                    if body.get("probe") and fake.doctor_change:
                        reply = fake.doctor_change(reply)
                    if reply is None:
                        return self.reply(500, b"doctor task did not complete")
                    return self.reply(200, json.dumps(reply).encode())
                if self.path.endswith("/tools/join"):
                    fake.joins.append(body)
                    reply = with_binding(plugin_fixture("join"), fake.before)
                    if fake.join_change:
                        reply = fake.join_change(reply)
                    return self.reply(200, json.dumps(reply).encode())
                self.reply(404, b"no such tool")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def probe_state(check, state, detail="d"):
    def change(reply):
        for f in reply["findings"]:
            if f["check"] == check:
                f["state"], f["detail"] = state, detail
        return reply
    return change


def commitment_change(change):
    def join(reply):
        change(reply["commitment"])
        return reply
    return join


@unittest.skipUnless(OPENSSL_, NO_OPENSSL)
class PluginToolsTest(Tmp):
    """The pinned plugin's doctor --probe and join, through the node's own console (check_node_tools)."""

    def setUp(self):
        super().setUp()
        self.published = Path(self.tmp.name) / "published"
        self.published.mkdir()
        self.real_published, step.PUBLISHED = step.PUBLISHED, self.published

    def tearDown(self):
        step.PUBLISHED = self.real_published
        super().tearDown()

    def tools(self, entry=ALICE_ENTRY, fetched=None, **kw):
        kw.setdefault("before", ("bound", "d" * 64))  # an unbound node is NOT_CHECKED, which the tests below pin
        console = PluginConsole(**kw)
        node_dir = self.out / "alice"
        node_dir.mkdir(exist_ok=True)
        try:
            fails, nc, proof, lines, self.signed = step.check_node_tools("alice", entry, console.url, node_dir, 5,
                                                                         fetched)
        finally:
            console.close()
        self.console = console
        for p in proof:
            self.assertTrue((node_dir / p).is_file(), p)
        return fails, nc, proof, lines

    def whats(self, nc):
        return [i["what"] for i in nc]

    def test_a_healthy_bound_node_holds_on_every_part(self):
        fails, nc, proof, lines = self.tools(before=("bound", "d" * 64))
        self.assertEqual((fails, nc), ([], []))
        self.assertEqual(self.console.doctor_bodies, [{"probe": True}, {}])
        self.assertEqual(self.console.joins, [{}])  # join with no arguments: it binds nothing
        self.assertEqual(proof, ["doctor-probe.json", "join.json", "commitment.json", "disclosure-policy.json",
                                 "doctor-after.json"])
        self.assertEqual(json.loads((self.out / "alice" / "commitment.json").read_text()), plugin_doc())
        self.assertIn("; the re-join reports it joined, and its commitment holds under the listed key", " ".join(lines))
        self.assertIn("the binding is bound before the re-join and bound after", " ".join(lines))

    def test_a_probe_doctor_finds_mismatched_is_a_fail(self):
        for check in step.DOCTOR_PROBES:
            fails, nc, _, _ = self.tools(doctor=probe_state(check, "mismatch", "the answer does not verify"))
            self.assertEqual(fails, [f"alice: doctor --probe {check}: mismatch (the answer does not verify)"])

    def test_a_probe_the_plugin_did_not_run_is_not_checked(self):
        fails, nc, _, _ = self.tools(doctor=probe_state("probe_checkpoints", "NOT_CHECKED", "there is no checkpoint"))
        self.assertEqual(fails, [])
        self.assertEqual(self.whats(nc), ["alice: doctor --probe probe_checkpoints"])
        self.assertIn("there is no checkpoint", nc[0]["why"])

    def test_a_doctor_with_no_probe_findings_is_not_checked_never_fail(self):
        def older(reply):
            reply["findings"] = [f for f in reply["findings"] if not f["check"].startswith("probe_")]
            return reply
        fails, nc, _, _ = self.tools(doctor=older, before=("bound", "d" * 64))
        self.assertEqual(fails, [])
        self.assertEqual(self.whats(nc), ["alice: doctor --probe probe_checkpoints",
                                          "alice: doctor --probe probe_history_card"])

    def test_a_served_commitment_that_does_not_carry_the_policy_is_a_fail(self):
        fails, _, _, _ = self.tools(doctor=probe_state(
            "policy_in_commitment", "missing", "the commitment served on commitment/1 advertises policy x, but "
                                               "this node answers under y"))
        self.assertEqual(len(fails), 1, fails)
        self.assertIn("alice: doctor policy_in_commitment: missing (the commitment served", fails[0])

    def test_no_commitment_served_yet_is_not_checked(self):
        fails, nc, _, _ = self.tools(doctor=probe_state("policy_in_commitment", "missing",
                                                        "no commitment is served: no checkpoint yet"))
        self.assertEqual(fails, [])
        self.assertIn("alice: the commitment the node serves on commitment/1, carrying the policy it answers under",
                      self.whats(nc))

    def test_a_join_commitment_altered_after_signing_is_a_fail(self):
        fails, _, _, _ = self.tools(join=commitment_change(lambda pair: pair["commitment"].update(
            answers_within="PT24H")))
        self.assertEqual(fails, ["alice: join's commitment does not verify: signature does not verify under spki"])

    def test_policy_bytes_other_than_the_signed_digest_are_a_fail(self):
        def other_policy(pair):
            pair["policy"] = base64.b64encode(b'{"policy": {}}').decode()
        fails, _, _, _ = self.tools(join=commitment_change(other_policy))
        self.assertTrue(fails, fails)
        self.assertTrue(all("join's commitment does not verify: lcap.policy_digest" in f for f in fails), fails)

    def test_a_join_commitment_under_another_key_or_for_another_node_is_a_fail(self):
        fails, _, _, _ = self.tools(entry={**ALICE_ENTRY, "key_id": "7" * 64})
        self.assertEqual(fails, [f"alice: join's commitment is under key {ALICE_DOC['key_id']}, not the listed key"])
        fails, _, _, _ = self.tools(entry={**ALICE_ENTRY, "node_id": "8" * 64}, node_id="8" * 64)
        self.assertEqual(fails, [f"alice: join's commitment is for node {ALICE_ID}, not this node"])

    def test_a_commitment_pair_that_is_not_one_is_a_fail(self):
        fails, _, _, _ = self.tools(join=lambda reply: {**reply, "commitment": {"commitment": [1], "policy": "x"}})
        self.assertEqual(len(fails), 1, fails)
        self.assertIn("join's commitment pair is not {commitment, policy}", fails[0])

    def test_join_returning_no_commitment_is_not_checked(self):
        fails, nc, proof, _ = self.tools(join=lambda reply: {**reply, "commitment": None})
        self.assertEqual(fails, [])
        self.assertEqual(self.whats(nc), ["alice: the commitment join returns"])
        self.assertNotIn("commitment.json", proof)

    def test_a_pending_join_is_not_checked_never_a_pass(self):
        pending = "the first checkpoint: the cadence cuts it after this node's first exchange; run join again then"
        fails, nc, _, _ = self.tools(join=lambda reply: {**reply, "joined": False, "pending": [pending]})
        self.assertEqual(fails, [])
        self.assertEqual(self.whats(nc), ["alice: the plugin's join"])
        self.assertEqual(nc[0]["why"], "join reports pending: " + pending)

    def test_a_console_that_does_not_answer_is_not_checked(self):
        console = PluginConsole()
        console.close()  # nothing listens: the call fails
        node_dir = self.out / "alice"
        node_dir.mkdir()
        fails, nc, _, _, signed = step.check_node_tools("alice", ALICE_ENTRY, console.url, node_dir, 2)
        self.assertIsNone(signed)
        self.assertEqual(fails, [])
        self.assertEqual(self.whats(nc), ["alice: the plugin's doctor --probe and join"])

    # --- Bindings: join must not drop one -----------------------------------------
    D = "d" * 64

    def test_a_bound_node_that_stays_bound_holds(self):
        fails, nc, _, lines = self.tools(before=("bound", self.D))
        self.assertEqual((fails, nc), ([], []))
        self.assertIn("the binding is bound before the re-join and bound after", " ".join(lines))

    def test_a_bound_node_that_comes_back_unbound_is_a_fail(self):
        fails, _, _, _ = self.tools(before=("bound", self.D), after=("unbound", None))
        self.assertEqual(fails, [f"alice: the node was bound ({self.D}) before join, and doctor after join reports "
                                 "it unbound"])

    def test_a_bound_node_that_comes_back_bound_by_another_binding_is_a_fail(self):
        fails, _, _, _ = self.tools(before=("bound", self.D), after=("bound", "e" * 64))
        self.assertEqual(fails, [f"alice: the node was bound ({self.D}) before join, and doctor after join reports "
                                 f"it bound as {'e' * 64}"])

    UNBOUND_NC = {"what": "alice: the controller binding", "why": "no controller binding recorded: the node was "
                  "unbound before the re-join and is after it (join {} sends no binding), and this run did not read "
                  "a signed commitment naming none", "issue": step.live.ISSUE}

    def binding_ncs(self, nc):
        return [x for x in nc if x["what"] == "alice: the controller binding"]

    def test_a_node_unbound_before_and_after_with_no_fetched_commitment_read_is_not_checked(self):
        fails, nc, _, lines = self.tools(before=("unbound", None))
        self.assertEqual(fails, [])
        self.assertEqual(self.binding_ncs(nc), [self.UNBOUND_NC])
        self.assertNotIn("the binding is", " ".join(lines))

    # loop#201: the binding is optional (u137); unbound is the optional case only on a signed commitment naming none
    def test_unbound_with_a_holding_commitment_naming_none_is_the_optional_case(self):
        fails, nc, _, lines = self.tools(before=("unbound", None), fetched="")
        self.assertEqual((fails, nc), ([], []))
        self.assertIn("alice: unbound before and after the re-join (optional; its signed commitment, from join and "
                      "fetched through the asking console, carries no binding)", lines)
        self.assertIn("the binding is unbound before the re-join and unbound after", " ".join(lines))

    def test_unbound_with_a_join_commitment_that_does_not_hold_stays_not_checked(self):
        fails, nc, _, _ = self.tools(before=("unbound", None), fetched="",
                                     join=commitment_change(lambda pair: pair["commitment"].update(answers_within="PT24H")))
        self.assertEqual(fails, ["alice: join's commitment does not verify: signature does not verify under spki"])
        self.assertEqual(self.binding_ncs(nc), [self.UNBOUND_NC])

    def signed_naming(self, digest):
        def change(pair):
            pair["commitment"] = step.check_commitment_.sign(
                {**pair["commitment"], "controller_binding": {"digest": digest}}, ALICE_KEY)
        return commitment_change(change)

    def test_unbound_with_a_join_commitment_naming_a_binding_is_a_fail(self):
        fails, nc, _, _ = self.tools(before=("unbound", None), fetched="", join=self.signed_naming(self.D))
        self.assertEqual(fails, [f"alice: join's commitment names the controller binding {self.D}, but doctor "
                                 "reports no binding held"])
        self.assertEqual(self.binding_ncs(nc), [])

    def test_the_same_binding_named_by_the_fetched_commitment_is_reported_once(self):
        # check_binding already failed on the fetched commitment naming it; join's names the same: no second FAIL
        fails, nc, _, _ = self.tools(before=("unbound", None), fetched=self.D, join=self.signed_naming(self.D))
        self.assertEqual(fails, [])
        fails, _, _, _ = self.tools(before=("unbound", None), fetched="e" * 64, join=self.signed_naming(self.D))
        self.assertEqual(len(fails), 1, fails)

    def test_only_a_failed_fetched_digest_or_an_agreed_none_is_settled(self):
        D = self.D
        self.assertEqual(step.settled_binding(None, ""), "")
        self.assertEqual(step.settled_binding("alice: the commitment names ...", D), D)
        # a binding that agreed in check_binding is not settled: join naming it while doctor says unbound FAILs
        self.assertIsNone(step.settled_binding(None, D))
        self.assertIsNone(step.settled_binding("alice: ... names none, but doctor reports it bound as d", ""))
        self.assertIsNone(step.settled_binding(None, None))
        fails, _, _, _ = self.tools(before=("unbound", None), fetched=step.settled_binding(None, D),
                                    join=self.signed_naming(D))
        self.assertEqual(len(fails), 1, fails)

    def test_a_fetched_digest_that_failed_with_join_naming_none_adds_no_misleading_entry(self):
        fails, nc, _, _ = self.tools(before=("unbound", None), fetched=self.D)
        self.assertEqual((fails, self.binding_ncs(nc)), ([], []))

    def test_an_unbound_node_that_comes_back_bound_without_a_digest_is_a_fail(self):
        fails, _, _, _ = self.tools(before=("unbound", None), after=("bound", "not-a-digest"))
        self.assertEqual(fails, ["alice: doctor after join reports the node bound without a sha256 digest"])

    def test_an_unbound_node_that_comes_back_bound_is_not_a_fail(self):
        fails, _, _, _ = self.tools(before=("unbound", None), after=("bound", self.D))
        self.assertEqual(fails, [])

    def test_a_probe_mismatch_join_files_under_pending_is_a_fail(self):
        def bad(reply):
            reply["joined"] = False
            reply["pending"] = ["probe_checkpoints: mismatch"]
            for f in reply["findings"]:
                if f["check"] == "probe_checkpoints":
                    f["state"] = "mismatch"
            return reply
        fails, nc, _, _ = self.tools(join=bad)
        self.assertEqual(len(fails), 1)
        self.assertTrue(fails[0].startswith("alice: join: doctor --probe probe_checkpoints: mismatch"))

    def test_join_is_not_called_when_the_binding_state_cannot_be_read_first(self):
        fails, nc, proof, lines = self.tools(before=None)
        self.assertEqual(self.console.joins, [])
        self.assertEqual(fails, [])
        self.assertIn({"what": "alice: the plugin's join",
                       "why": "the binding state could not be read from doctor first, so join was not called",
                       "issue": step.live.ISSUE}, nc)
        self.assertNotIn("join.json", proof)
        self.assertEqual(lines, ["alice: join not called, the binding state was not read first"])

    def test_join_is_not_called_when_doctor_does_not_answer(self):
        fails, nc, _, _ = self.tools(doctor=lambda reply: None)
        self.assertEqual(self.console.joins, [])
        self.assertIn("alice: the plugin's join", self.whats(nc))

    def test_a_binding_unreadable_after_join_is_not_checked(self):
        fails, nc, _, _ = self.tools(before=("bound", self.D), after=None)
        self.assertEqual(fails, [])
        self.assertIn("alice: the controller binding after join", self.whats(nc))

    def test_join_is_never_sent_to_a_console_that_answers_as_another_node(self):
        fails, nc, proof, _ = self.tools(node_id="9" * 64)
        self.assertEqual((self.console.joins, self.console.doctor_bodies, fails, proof), ([], [], [], []))
        self.assertIn(f"answers as {'9' * 64}, not as {ALICE_ID}", nc[0]["why"])

    # --- The published commitment (loop#88): a change is reported, never a FAIL ---
    def test_with_no_published_commitment_nothing_is_compared(self):
        _, _, _, lines = self.tools()
        self.assertIn("alice: no commitment is published under nodes/commitments/ for it yet; nothing is compared",
                      lines)

    def test_a_published_commitment_join_returns_unchanged_is_said_so(self):
        (self.published / f"{ALICE_ID}.json").write_text(json.dumps(plugin_doc(), indent=2))
        _, _, _, lines = self.tools()
        self.assertIn("alice: join's commitment is the one published under nodes/commitments/", lines)
        self.assertNotIn(step.CHANGED, " ".join(lines))

    def test_a_changed_commitment_is_reported_as_a_change_not_a_fail(self):
        older = copy.deepcopy(plugin_doc())
        older["issued_at"] = "2026-01-01T00:00:00Z"
        (self.published / f"{ALICE_ID}.json").write_text(json.dumps(older))
        fails, nc, _, lines = self.tools(before=("bound", "d" * 64))
        self.assertEqual((fails, nc), ([], []))
        self.assertIn("alice: commitment changed: join's commitment is not the one published under "
                      "nodes/commitments/, so the findings run republishes it", lines)
        # The change stays in detail when another check fails, so a findings run still sees it.
        r = step.conclude(self.out, "live", ["alice: a planted failure"], [], [], lines, finding.now(), "reporter")
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("alice: commitment changed", r["detail"])
        self.assertEqual(finding.check(self.out.parent), [])


class BindingGapTest(unittest.TestCase):
    """The binding gap left is a revocation declaration the verifier rejects (loop#240; CB-1 narrowed by
    AAC#495, which left a registered source and a signed declaration open). The key's role (AAC#468) is checked from its report, and no gap is loop-wide any more: the signed
    commitment (loop#161) is matched per node."""

    def test_the_binding_gap_is_a_declaration_that_does_not_count(self):
        self.assertEqual(step.BINDING_REVOCATION["issue"], "evidence-network/loop#240")
        self.assertFalse(hasattr(step, "LIVE_NOT_CHECKED"))


@unittest.skipUnless(OPENSSL_, NO_OPENSSL)
class PluginToolsRunTest(Tmp):
    """The whole step on a live population of one node, with its own console given."""

    def run_one(self, data_dir=None, run_at=None, **kw):
        pop_dir = Path(self.tmp.name) / "live"
        pop_dir.mkdir()
        node = {**ALICE_ENTRY, **({"data_dir": str(data_dir)} if data_dir else {})}
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "rail": None, "nodes": [node]}))
        asker = FakeConsole(VERIFIED, ALICE_DOC["key_id"], key_id=ALICE_DOC["key_id"],
                            committed=kw.pop("committed", None))
        own = PluginConsole(**kw)
        real, step.PUBLISHED = step.PUBLISHED, Path(self.tmp.name) / "none"
        real_now = step.daily_report.utc_now
        if run_at:  # the run's one time, which the binding window is held to
            step.daily_report.utc_now = lambda: datetime.strptime(run_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        try:
            self.assertEqual(step.main(["--population", str(pop_dir), "--out", str(self.out), "--console",
                                        asker.url, "--node-console", f"alice={own.url}"]), 0)
        finally:
            step.PUBLISHED = real
            step.daily_report.utc_now = real_now
            asker.close()
            own.close()
        self.assertEqual(finding.check(self.out.parent), [])
        return json.loads((self.out / "finding.json").read_text())

    def test_a_healthy_node_reads_not_checked_on_the_binding_record_only(self):
        r = self.run_one(before=("bound", "d" * 64), committed="d" * 64)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        # The population has no data_dir, so the record behind the digest cannot be read. The answers are matched
        # against join's signed commitment, so that is no gap (loop#161).
        self.assertEqual([i["what"] for i in r["not_checked"]], ["alice: the controller binding"])
        self.assertIn("; the re-join reports it joined, and its commitment holds under the listed key", r["detail"])
        for p in ("alice/doctor-probe.json", "alice/join.json", "alice/commitment.json",
                  "alice/disclosure-policy.json", "alice/doctor-after.json"):
            self.assertIn(p, [x["path"] for x in r["proof"]])

    # Step 08 re-derives live 12 from its proof (loop#188).
    def verify08(self):
        """Step 08 on the run self.out sits in (12 only), with the run's population: (finding, manifest entry)."""
        run = self.out.parent
        (run / "run.json").write_text(json.dumps({"population": "live", "stage": "1", "started": "2026-10-31T00:00:00Z",
                                                  "steps": ["12", "08"]}))
        loader = importlib.machinery.SourceFileLoader("step08_for_12", str(ROOT / "steps" / "08-verify"))
        v = importlib.util.module_from_spec(importlib.util.spec_from_loader("step08_for_12", loader))
        loader.exec_module(v)
        shutil.rmtree(run / "08-verify", ignore_errors=True)
        v.main(["--population", str(Path(self.tmp.name) / "live"), "--out", str(run / "08-verify")])
        f = json.loads((run / "08-verify" / "finding.json").read_text())
        return f, {e["dir"]: e for e in json.loads((run / "08-verify" / "verified.json").read_text())["steps"]}["12-join"]

    def rehash(self, drop=()):
        rec = json.loads((self.out / "finding.json").read_text())
        rec["proof"] = [p for p in rec["proof"] if p["path"] not in drop]
        for p in rec["proof"]:
            p["sha256"] = finding.sha256(self.out / p["path"])
        (self.out / "finding.json").write_text(json.dumps(rec))

    def test_08_reproduces_live_12_from_its_proof(self):
        r = self.run_one(before=("bound", "d" * 64), committed="d" * 64)
        f, e = self.verify08()
        self.assertEqual(e["status"], "reproduced", e["problems"])
        self.assertEqual(e["rederived"]["result"], r["result"])

    def test_08_fails_a_live_12_whose_kept_replies_were_altered(self):
        self.run_one(before=("bound", "d" * 64), committed="d" * 64)
        w = json.loads((self.out / "alice" / "wire.json").read_text())
        w["binding-doctor"]["reply"]["findings"] = [{"check": "controller_binding", "state": "bound",
                                                     "records": ["e" * 64]}]
        (self.out / "alice" / "wire.json").write_text(json.dumps(w))
        self.rehash()
        f, e = self.verify08()
        self.assertEqual(e["status"], "failed", f["detail"])

    def test_08_does_not_reproduce_an_old_shape_live_12(self):
        self.run_one(before=("bound", "d" * 64), committed="d" * 64)
        (self.out / "nodes.json").unlink()
        self.rehash(drop=("nodes.json",))
        f, e = self.verify08()
        self.assertEqual(e["status"], "not_reproduced", e["problems"])
        self.assertIn("its proof holds no nodes.json", e["not_checked"]["why"])

    def test_08_does_not_reproduce_a_live_12_whose_binding_time_left_the_run(self):
        self.run_one(before=("bound", "d" * 64), committed="d" * 64)
        k = json.loads((self.out / "nodes.json").read_text())
        k["bind_at"] = "2099-01-01T00:00:00Z"
        (self.out / "nodes.json").write_text(json.dumps(k))
        self.rehash()
        f, e = self.verify08()
        self.assertEqual(e["status"], "failed", f["detail"])
        self.assertIn("bind_at", " ".join(e["problems"]))

    def test_08_names_the_consoles_the_run_held_when_a_node_was_not_asked(self):
        self.run_one(before=("bound", "d" * 64), committed="d" * 64)
        k = json.loads((self.out / "nodes.json").read_text())
        k["node_consoles"] = []
        (self.out / "nodes.json").write_text(json.dumps(k))
        self.rehash()
        f, e = self.verify08()
        self.assertTrue(any("which consoles the run held" in r["what"] for r in e["relies_on"]), e)

    def test_08_does_not_take_a_revocations_commit_that_is_no_commit_id(self):
        self.run_one(before=("bound", "d" * 64), committed="d" * 64)
        k = json.loads((self.out / "nodes.json").read_text())
        k["revocations_main"] = {"commit": "--output=x"}
        (self.out / "nodes.json").write_text(json.dumps(k))
        self.rehash()
        f, e = self.verify08()
        self.assertEqual(e["status"], "failed", f["detail"])

    def test_a_malformed_502_line_with_no_reply_replays_as_the_line_kept(self):
        self.run_one(before=("bound", "d" * 64), committed="d" * 64)
        att = next((self.out / "alice" / "attempts").glob("*"))
        (att / "checkpoints.response.json").unlink()
        (att / "checkpoints.verification").write_text("not_evidence why=http 502: malformed\n")
        self.rehash(drop=[str(p.relative_to(self.out)) for p in [att / "checkpoints.response.json"]])
        f, e = self.verify08()
        self.assertNotIn("does not hold", " ".join(e["problems"]), e)

    def data_dir(self, binding=None):
        """A data_dir holding `binding` (default: alice's, as join.sh wrote it) as controller-binding.json."""
        d = Path(self.tmp.name) / "data"
        d.mkdir(exist_ok=True)
        (d / step.BINDING_FILE).write_text(binding if binding is not None else (ALICE / "binding.json").read_text())
        return d

    def run_bound(self, binding=None, digest=BINDING_DIGEST):
        return self.run_one(data_dir=self.data_dir(binding), before=("bound", digest), committed=digest,
                            run_at=FIXTURE_AT)

    @needs_cross
    def test_a_held_binding_that_applies_passes_with_revocation_on_the_runs_snapshot(self):
        r = self.run_bound()
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertIn("revocation PASS: no statement applies in the list evidence-network/loop step 12", r["detail"])
        self.assertIn(f"alice: alice's binding {BINDING_DIGEST} names the node key (same_controller_asserted: no "
                      f"role) and applies at {FIXTURE_AT}", r["detail"])
        self.assertEqual(r["not_checked"], [])

    def judged_again(self, r, edit=None):
        """judge_live on a copy of only the cited files (step 08's view, loop#188), after `edit(work)`: its finding."""
        work = Path(self.tmp.name) / "again" / "12-join"
        shutil.rmtree(work.parent, ignore_errors=True)
        for p in r["proof"]:
            (work / p["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.out / p["path"], work / p["path"])
        if edit:
            edit(work)
        pop = json.loads((Path(self.tmp.name) / "live" / "population.json").read_text())
        real, step.PUBLISHED = step.PUBLISHED, Path(self.tmp.name) / "none"
        try:
            return step.judge_live(pop, work, r["started"])
        finally:
            step.PUBLISHED = real

    @needs_cross
    def test_the_live_finding_is_decided_again_from_its_proof_alone(self):
        r = self.run_bound()
        self.assertEqual(r["result"], "PASS", r["detail"])
        for p in ("nodes.json", "alice/wire.json", "alice/controller-binding.json"):
            self.assertIn(p, [x["path"] for x in r["proof"]])
        again = self.judged_again(r)
        self.assertEqual((again["result"], again["not_checked"], again["detail"]),
                         (r["result"], r["not_checked"], r["detail"]))

    @needs_cross
    def test_an_edited_proof_does_not_decide_the_same(self):
        r = self.run_bound()

        def wire(change):
            def edit(work):
                w = json.loads((work / "alice" / "wire.json").read_text())
                change(w)
                (work / "alice" / "wire.json").write_text(json.dumps(w))
            return edit

        def doctor_unbound(w):
            w["binding-doctor"]["reply"]["findings"] = [{"check": "controller_binding", "state": "unbound"}]

        def line(work):
            v = next((work / "alice" / "attempts").glob("*/checkpoints.verification"))
            v.write_text("verified listed_key=match forged\n")

        cases = {"doctor reports another binding state": wire(doctor_unbound),
                 "a kept verification line its reply does not give": line,
                 "no nodes.json": lambda work: (work / "nodes.json").unlink(),
                 "no wire.json": lambda work: (work / "alice" / "wire.json").unlink(),
                 "a kept reply file removed": lambda work: (work / "alice" / "doctor-probe.json").unlink()}
        for what, edit in cases.items():
            with self.subTest(what):
                again = self.judged_again(r, edit)
                self.assertEqual(again["result"], "FAIL", again["detail"])

    @needs_cross
    def test_the_record_that_was_checked_is_kept_as_proof(self):
        r = self.run_bound()
        self.assertIn("alice/controller-binding.json", [x["path"] for x in r["proof"]])
        self.assertEqual((self.out / "alice" / step.BINDING_FILE).read_text(), (ALICE / "binding.json").read_text())

    def test_a_missing_verifier_stops_the_step_in_both_backends(self):
        real = os.environ.get("LOOP_TOOLS")
        os.environ["LOOP_TOOLS"] = str(Path(self.tmp.name) / "empty")
        try:
            with self.assertRaises(SystemExit) as live:
                step.binding_check("alice", (ALICE / "binding.json").read_text(), BINDING_DIGEST, ALICE_DOC["key_id"], FIXTURE_AT)
            with self.assertRaises(SystemExit):
                step.check_binding_record(ALICE, FIXTURE_AT)
        finally:
            os.environ.pop("LOOP_TOOLS") if real is None else os.environ.__setitem__("LOOP_TOOLS", real)
        self.assertIn("steps/01-seal.setup.sh", str(live.exception))

    # Step 08 holds the revocations commit live 12 read to main outside the run (loop#281).
    def revocations_main(self):
        """A scratch main live 12 reads nodes/revocations/ from, with one commit before the binding time, as if
        pushed. Returns a function that commits the folder's state on main at `date` and moves origin/main to it."""
        repo = Path(self.tmp.name) / "revocations"
        (repo / "nodes" / step.bv.REVOCATIONS).mkdir(parents=True)
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)

        def commit(date, files=None):
            for name, text in (files or {}).items():
                (repo / "nodes" / step.bv.REVOCATIONS / name).write_text(text)
            subprocess.run([*git, "add", "-A"], check=True)
            subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", date], check=True,
                           env=dict(os.environ, GIT_COMMITTER_DATE=date, GIT_AUTHOR_DATE=date))
            subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
        commit("2026-10-20T00:00:00Z")
        patch = unittest.mock.patch.dict(os.environ, {"LOOP_REVOCATIONS_GIT": str(repo)})
        patch.start()
        self.addCleanup(patch.stop)
        return commit

    def bound_run_for_08(self):
        """A live 12 whose binding applies (revocation PASS at the scratch main's commit), with the finding's run
        window around the binding time, as a run made at that time would have it."""
        r = self.run_bound()
        self.assertEqual(r["result"], "PASS", r["detail"])
        rec = json.loads((self.out / "finding.json").read_text())
        rec["started"], rec["finished"] = "2026-10-31T00:00:00Z", "2026-11-02T00:00:00Z"
        (self.out / "finding.json").write_text(json.dumps(rec))
        return r

    @needs_cross
    def test_08_holds_the_revocations_to_this_clones_main_when_the_step_is_unpublished(self):
        commit = self.revocations_main()
        self.bound_run_for_08()
        commit("2026-11-03T00:00:00Z")  # main moves on, nodes/revocations/ the same
        f, e = self.verify08()
        self.assertEqual(e["status"], "reproduced", e["problems"])
        tip = subprocess.run(["git", "-C", os.environ["LOOP_REVOCATIONS_GIT"], "rev-parse", "origin/main"],
                             capture_output=True, text=True, check=True).stdout.strip()
        # One entry says what the step was not held to at a publication, and where its revocations were read.
        [gap] = [x for x in e["relies_on"] if "read at the pull request merge on main" in x["what"]]
        self.assertIn("join commitments and revocation statements it is held to", gap["what"])
        self.assertIn(f"origin/main as last fetched, {tip}, committed 2026-11-03T00:00:00", gap["why"])

    @needs_cross
    def test_08_fails_a_live_12_whose_list_main_has_changed_for_its_binding(self):
        # The run read main before a change to its binding's list; main since holds a file for it that is not a
        # revocations file, so the binding does not apply there (loop#194): judged again at main, FAIL, not PASS.
        commit = self.revocations_main()
        self.bound_run_for_08()
        kept = json.loads((self.out / "nodes.json").read_text())["revocations_main"]["commit"]
        commit("2026-11-03T00:00:00Z", {f"{BINDING_DIGEST}.json": "not json"})
        f, e = self.verify08()
        self.assertEqual(e["status"], "failed", e)
        [problem] = e["problems"]
        self.assertIn("re-derived with nodes/revocations/ as main held it at", problem)
        self.assertIn(f"FAIL, not_checked []; recorded PASS, not_checked [] with the list at {kept[:12]}", problem)
        # The cited bytes are still the run's at its own commit: only the result decided at main differs.
        self.assertNotIn("re-derived is not the recorded", " ".join(e["problems"]))

    @needs_cross
    def test_08_reproduces_a_live_12_when_main_changed_only_another_bindings_list(self):
        commit = self.revocations_main()
        self.bound_run_for_08()
        commit("2026-11-03T00:00:00Z", {f"{'e' * 64}.json": "not json"})
        f, e = self.verify08()
        self.assertEqual(e["status"], "reproduced", e["problems"])

    @needs_cross
    def test_08_reproduces_no_kept_revocations_commit_as_revocation_not_checked(self):
        # No commit kept (the run's fetch failed): no list is declared, so revocation is open, never PASS.
        self.revocations_main()
        self.bound_run_for_08()
        k = json.loads((self.out / "nodes.json").read_text())
        k["revocations_main"] = {"commit": None, "why": "origin/main could not be fetched"}
        (self.out / "nodes.json").write_text(json.dumps(k))
        rec = json.loads((self.out / "finding.json").read_text())
        again = self.judged_again(rec)
        self.assertEqual(again["result"], "NOT_CHECKED", again["detail"])
        self.assertIn(step.BINDING_REVOCATION["what"], " ".join(x["what"] for x in again["not_checked"]))
        rec["result"], rec["not_checked"] = again["result"], again["not_checked"]
        (self.out / "finding.json").write_text(json.dumps(rec))
        self.rehash()
        f, e = self.verify08()
        self.assertEqual(e["status"], "reproduced", e["problems"])
        self.assertEqual(e["rederived"]["result"], "NOT_CHECKED")

    @needs_cross
    def test_a_held_binding_that_does_not_name_the_nodes_key_is_a_fail(self):
        raw = json.loads((ALICE / "binding.json").read_text())
        raw["keys"] = [raw["keys"][0], "ab" * 32]
        text = json.dumps(raw)
        digest = step.bv.verify(step.cross_language(), text, raw["keys"][0], [FIXTURE_AT])[1]["binding_digest"]
        r = self.run_bound(text, digest)
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("alice: alice's binding", r["detail"])

    @needs_cross
    def test_a_bip340_binding_of_the_nodes_ed25519_key_applies(self):
        # aac-go go/v0.17.0 (AAC#458): the node key is keys[1] of the vector, the controller key bip340.
        case = json.loads((ROOT / "tests/fixtures/step05/controller-bindings.json").read_text())["cases"]["valid-nostr-key"]
        digest = step.bv.verify(step.cross_language(), case["binding"], case["keys"][1], [FIXTURE_AT])[1]["binding_digest"]
        state, detail = step.binding_check("alice", case["binding"], digest, case["keys"][1], FIXTURE_AT)
        self.assertEqual(state, "ok", detail)
        self.assertIn("signatures and window PASS", detail)

    @needs_cross
    def test_a_held_binding_outside_its_window_is_a_fail(self):
        r = self.run_one(data_dir=self.data_dir(), before=("bound", BINDING_DIGEST), committed=BINDING_DIGEST,
                         run_at="2030-01-01T00:00:00Z")
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("controller_binding_window FAIL", r["detail"])

    @needs_cross
    def test_another_binding_than_doctors_is_not_checked_never_a_fail(self):
        r = self.run_bound(digest="e" * 64)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        entry = next(i for i in r["not_checked"] if "second verifier" in i["what"])
        self.assertIn(f"not the {'e' * 64} it is held as", entry["why"])

    def test_a_node_with_no_readable_record_is_not_checked(self):
        r = self.run_one(before=("bound", BINDING_DIGEST), committed=BINDING_DIGEST)
        entry = next(i for i in r["not_checked"] if i["what"] == "alice: the controller binding")
        self.assertIn("no readable controller-binding.json", entry["why"])

    def test_a_failing_binding_record_is_not_taken_for_a_settled_fetched_digest(self):
        # check_binding agreed on D, then the record check FAILed: join must still be handed None, not D
        D = "d" * 64
        seen = {}
        real = step.check_node_tools

        def spy(*a, **kw):
            seen["fetched"] = a[5] if len(a) > 5 else kw.get("fetched")
            return real(*a, **kw)
        with unittest.mock.patch.object(step, "check_binding", return_value=(None, None, "alice: agreed", D, D)), \
                unittest.mock.patch.object(step, "live_binding_record", return_value=("alice: the record fails", [], None, [])), \
                unittest.mock.patch.object(step, "check_node_tools", side_effect=spy):
            r = self.run_one()
        self.assertIsNone(seen["fetched"])
        self.assertEqual(r["result"], "FAIL")

    def test_an_unbound_node_whose_signed_commitments_name_none_passes_a_whole_run(self):
        # loop#201: the binding is optional (u137); doctor and both commitments agree there is none
        r = self.run_one()
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertNotIn("alice: the controller binding", [i["what"] for i in r.get("not_checked", [])])
        self.assertIn("alice: unbound before and after the re-join (optional;", r["detail"])

    def test_a_bound_node_join_unbinds_is_a_fail(self):
        D = "d" * 64
        r = self.run_one(before=("bound", D), after=("unbound", None), committed=D)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn(f"alice: the node was bound ({D}) before join, and doctor after join reports it unbound",
                      r["detail"])


    # --- The signed commitment the answers are matched against (loop#161) ---------
    def test_a_commitment_that_does_not_advertise_a_listed_question_is_a_listing_misstatement(self):
        r = self.run_one(join=signed_pair(subjects=("checkpoints",)))
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("alice: nodes/ lists history_card/1 as stranger-safe, but the node's signed commitment does not "
                      "advertise it (subjects ['full_history'], derivation history_card/1)", r["detail"])

    def test_a_signed_policy_other_than_the_listing_is_a_fail(self):
        r = self.run_one(join=signed_pair(stranger_safe=("checkpoints",)))
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("alice: nodes/ lists ['checkpoints', 'history_card/1'] as stranger-safe, but the node's signed "
                      "policy lists ['checkpoints']", r["detail"])

    def test_a_healthy_node_says_its_answers_are_matched_against_the_signed_commitment(self):
        r = self.run_one(before=("bound", "d" * 64), committed="d" * 64)
        self.assertIn("alice: the answers are matched against its signed commitment and policy, and its nodes/ entry "
                      "lists the same stranger-safe subjects", r["detail"])
        self.assertNotIn(step.SIGNED_WHAT, " ".join(i["what"] for i in r["not_checked"]))

    def test_a_probe_mismatch_join_reports_does_not_unverify_the_commitment(self):
        def mismatch(reply):
            for f in reply["findings"]:
                if f["check"] == "probe_checkpoints":
                    f["state"] = "mismatch"
            return reply
        r = self.run_one(join=mismatch)
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("alice: join: doctor --probe probe_checkpoints: mismatch", r["detail"])
        # The commitment held, so the answers were matched against it: no claim that none was verified.
        self.assertNotIn(step.SIGNED_WHAT, " ".join(i["what"] for i in r["not_checked"]))

    def test_signed_terms_that_cannot_be_read_are_a_fail_not_a_missing_commitment(self):
        def no_stranger_safe(reply):
            doc, _ = plugin_pair()
            cc = step.check_commitment_
            policy = cc.sign({"key_id": ALICE_DOC["key_id"], "profile": "disclosure-policy/0",
                              "policy": {k: v for k, v in PLUGIN_POLICY.items() if k != "stranger_safe"}}, ALICE_KEY)
            raw = cc.canonical(policy)
            doc = cc.sign({**copy.deepcopy(doc), "lcap": {**doc["lcap"], "policy_digest": hashlib.sha256(raw).hexdigest()}},
                          ALICE_KEY)
            return {**reply, "commitment": {"commitment": doc, "policy": base64.b64encode(raw).decode()}}
        r = self.run_one(join=no_stranger_safe)
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIn("alice: join's commitment and policy do not carry answers.subjects and stranger_safe as lists "
                      "of names", r["detail"])
        self.assertNotIn(step.SIGNED_WHAT, " ".join(i["what"] for i in r["not_checked"]))

    def test_join_returning_no_commitment_leaves_the_match_not_checked(self):
        r = self.run_one(join=lambda reply: {**reply, "commitment": None})
        self.assertIn({"what": f"alice: {step.SIGNED_WHAT}",
                       "why": "this run verified no commitment from join under the listed key with its policy, so the "
                              "answers are matched against the nodes/ entry",
                       "issue": step.live.ISSUE}, r["not_checked"])


def signed_pair(**kw):
    """A join change: the commitment pair plugin_pair(**kw) builds, in place of the healthy one."""
    def join(reply):
        doc, raw = plugin_pair(**kw)
        return {**reply, "commitment": {"commitment": copy.deepcopy(doc), "policy": base64.b64encode(raw).decode()}}
    return join


class SignedTermsTest(unittest.TestCase):
    """The reporter's answers judged against the signed policy, not the nodes/ entry (loop#161)."""

    SIGNED = {"subjects": ["checkpoints", "full_history"], "derivations": ["history_card/1"],
              "stranger_safe": ["checkpoints"]}
    REFUSED = "refusal reason=no_such_subject listed_key=match"

    def test_the_signed_policy_decides_which_refusal_fails(self):
        entry = {"disclosure": {"stranger_safe": ["checkpoints", "history_card/1"]}}
        self.assertEqual(step.judge_reporter_line("history_card", self.REFUSED, entry, self.SIGNED), (None, None))
        fail, _ = step.judge_reporter_line("checkpoints", self.REFUSED, entry, self.SIGNED)
        self.assertIn("is refused, but the node's signed policy lists it as stranger-safe", fail)
        # Without signed terms the entry decides, as before (and as step 07 calls it).
        fail, _ = step.judge_reporter_line("history_card", self.REFUSED, entry)
        self.assertIn("is refused, but the node lists it as stranger-safe", fail)

    def test_a_refusal_is_judged_by_its_reason(self):
        """The split settled on Mesh-LLM/capsules-plugin#112 and #114, as step 07 judges a refusal (loop#61)."""
        entry = {"disclosure": {"stranger_safe": ["checkpoints"]}}
        line = "refusal reason={} key=" + "ab" * 32 + " listed_key=match"
        for reason in ("no_such_subject", "derivation_unsupported", "made_up_reason"):
            fail, nc = step.judge_reporter_line("checkpoints", line.format(reason), entry, self.SIGNED)
            self.assertIn(f"is refused, but the node's signed policy lists it as stranger-safe: refusal reason={reason}",
                          fail)
            self.assertIsNone(nc)
        for reason in ("policy_declined", "not_authorized", "retention_expired", "deadline_unmet", "request_malformed",
                       "coverage_unsatisfiable"):
            fail, nc = step.judge_reporter_line("checkpoints", line.format(reason), entry, self.SIGNED)
            self.assertIsNone(fail)
            self.assertIn(reason, nc["why"])

    def test_a_refusal_under_another_key_is_a_fail_whatever_its_reason(self):
        entry = {"disclosure": {"stranger_safe": ["checkpoints"]}}
        for reason in ("policy_declined", "deadline_unmet"):
            for listed in ("differs", "unknown"):
                fail, _ = step.judge_reporter_line(
                    "checkpoints", f"refusal reason={reason} key={'ab' * 32} listed_key={listed}", entry, self.SIGNED)
                self.assertIn("not the listed key", fail)

    def test_signed_terms_need_lists_of_names(self):
        doc = {"answers": {"subjects": ["checkpoints"], "derivations": ["history_card/1"]}}
        policy = json.dumps({"policy": {"stranger_safe": ["checkpoints"]}}).encode()
        self.assertEqual(step.signed_terms(doc, policy), {"subjects": ["checkpoints"],
                                                          "derivations": ["history_card/1"],
                                                          "stranger_safe": ["checkpoints"]})
        self.assertIsNone(step.signed_terms(doc, json.dumps({"policy": {}}).encode()))
        self.assertIsNone(step.signed_terms(doc, json.dumps({"policy": {"stranger_safe": "checkpoints"}}).encode()))
        self.assertIsNone(step.signed_terms({"answers": {"subjects": [1]}}, policy))
        self.assertIsNone(step.signed_terms(doc, b"not json"))


@needs_cross
class BindingRoleTest(unittest.TestCase):
    """The node key's role in its controller binding (AAC#468): in delegated_by it is the delegate, never the
    delegator. The record is the AAC vector valid-delegated-by (tests/fixtures/step12/delegated-by.json)."""

    CASE = json.loads((FIX / "delegated-by.json").read_text())

    def check(self, key):
        c = self.CASE
        digest = step.bv.verify(step.cross_language(), c["binding"], key, [c["evaluation_time"]])[1]["binding_digest"]
        return step.binding_check("node", c["binding"], digest, key, c["evaluation_time"])

    def test_the_delegate_holds(self):
        delegate = next(k for k in self.CASE["keys"] if k != self.CASE["delegator"])
        state, detail = self.check(delegate)
        self.assertEqual(state, "ok", detail)
        self.assertIn("names the node key as the delegate and applies", detail)

    def test_the_delegator_is_a_fail(self):
        state, detail = self.check(self.CASE["delegator"])
        self.assertEqual(state, "bad", detail)
        self.assertIn("names the node key as the delegator", detail)

@needs_cross
class BindingRevocationTest(unittest.TestCase):
    """The statements published at nodes/revocations/<digest>.json (loop#138) go to the verifier with the binding."""

    BINDINGS = json.loads((ROOT / "tests/fixtures/step05/controller-bindings.json").read_text())
    CASE = BINDINGS["cases"]["valid-same-controller"]
    STATEMENT = BINDINGS["revocations"]["valid-same-controller"]  # revoked_at 2026-10-12

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = unittest.mock.patch.dict(os.environ, {"LOOP_NODES_DIR": self.tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.key = self.CASE["keys"][0]
        self.digest = step.bv.verify(step.cross_language(), self.CASE["binding"], self.key,
                                     ["2026-10-11T00:00:00Z"])[1]["binding_digest"]

    def check(self, at):
        return step.binding_check("node", self.CASE["binding"], self.digest, self.key, at)

    def publish(self):
        folder = Path(self.tmp.name) / step.bv.REVOCATIONS
        folder.mkdir()
        (folder / f"{self.digest}.json").write_text(json.dumps([json.loads(self.STATEMENT)]))

    def test_with_none_published_revocation_passes_on_the_runs_declared_empty_list(self):
        state, detail = self.check("2026-10-13T00:00:00Z")
        self.assertEqual(state, "ok", detail)
        self.assertIn("revocation PASS: no statement applies in the list evidence-network/loop step 12 (checking at "
                      "2026-10-13T00:00:00Z), nodes/revocations/ in this checkout declared complete as of "
                      f"2026-10-13T00:00:00Z under profile {step.bv.PROFILE} (list sha256 "
                      f"{hashlib.sha256(b'[]').hexdigest()}); a statement made after 2026-10-13T00:00:00Z is not "
                      "excluded", detail)

    def test_a_live_check_reads_the_list_main_held_at_the_check_time(self):
        repo = Path(self.tmp.name) / "repo"
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false"]
        (repo / "nodes" / step.bv.REVOCATIONS).mkdir(parents=True)
        (repo / "nodes" / step.bv.REVOCATIONS / f"{self.digest}.json").write_text(json.dumps([json.loads(self.STATEMENT)]))
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        subprocess.run([*git, "add", "nodes"], check=True)
        date = "2026-10-12T12:00:00Z"
        subprocess.run([*git, "commit", "-qm", "publish"], check=True,
                       env=dict(os.environ, GIT_COMMITTER_DATE=date, GIT_AUTHOR_DATE=date))
        subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)  # as if pushed
        with unittest.mock.patch.dict(os.environ, {"LOOP_REVOCATIONS_GIT": str(repo)}):
            for at in ("2026-10-13T00:00:00Z", "2026-10-12T00:00:00Z"):
                snap = step.bv.snapshot(repo, at)
                state, detail = step.binding_check("node", self.CASE["binding"], self.digest, self.key, at,
                                                   from_main=snap)
                if at == "2026-10-13T00:00:00Z":
                    self.assertEqual(state, "bad", detail)  # the checkout (LOOP_NODES_DIR) holds none: main does
                    self.assertIn("controller_binding_revocation FAIL", detail)
                else:
                    self.assertEqual(state, "open", detail)  # origin/main holds no commit that old
                    self.assertIn("no origin/main commit at or before 2026-10-12T00:00:00Z", detail)
            self.assertEqual(self.check("2026-10-13T00:00:00Z")[0], "ok")  # a rehearsal reads its checkout

    def test_a_list_read_at_a_later_main_commit_applies_by_revoked_at(self):
        # Step 08's second run (loop#281) reads the list at a main commit newer than the time asked about, with the
        # run's own declaration time: a statement merged since applies when its revoked_at is at or before that time,
        # and with a later revoked_at the binding still applies with revocation PASS (the declaration counts).
        repo = Path(self.tmp.name) / "repo"
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false"]
        (repo / "nodes" / step.bv.REVOCATIONS).mkdir(parents=True)
        (repo / "nodes" / step.bv.REVOCATIONS / f"{self.digest}.json").write_text(json.dumps([json.loads(self.STATEMENT)]))
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        subprocess.run([*git, "add", "nodes"], check=True)
        date = "2026-11-05T00:00:00Z"  # merged long after both times asked about
        subprocess.run([*git, "commit", "-qm", "publish"], check=True,
                       env=dict(os.environ, GIT_COMMITTER_DATE=date, GIT_AUTHOR_DATE=date))
        subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
        later = {"commit": subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True,
                                          check=True).stdout.strip()}
        with unittest.mock.patch.dict(os.environ, {"LOOP_REVOCATIONS_GIT": str(repo)}):
            state, detail = step.binding_check("node", self.CASE["binding"], self.digest, self.key,
                                               "2026-10-13T00:00:00Z", from_main=later)
            self.assertEqual(state, "bad", detail)  # revoked_at 2026-10-12 is before the time asked about
            self.assertIn("controller_binding_revocation FAIL", detail)
            state, detail = step.binding_check("node", self.CASE["binding"], self.digest, self.key,
                                               "2026-10-11T00:00:00Z", from_main=later)
            self.assertEqual(state, "ok", detail)  # revoked after the time asked about: an honest run still holds
            self.assertIn(f"revocation PASS: no statement applies in the list evidence-network/loop step 12 (checking "
                          f"at 2026-10-11T00:00:00Z), nodes/revocations/ at main {later['commit']}", detail)

    def test_a_declaration_that_does_not_count_leaves_revocation_open(self):
        with unittest.mock.patch.object(step.bv, "MAX_AGE", -1):  # the verifier refuses the declaration
            state, detail = self.check("2026-10-13T00:00:00Z")
        self.assertEqual(state, "open", detail)
        self.assertIn("revocation NOT_CHECKED: the verifier rejected the declaration", detail)
        self.assertEqual(step.revocation_open(detail)["issue"], step.BINDING_REVOCATION_ISSUE)

    def test_a_published_statement_that_applies_makes_the_binding_not_apply(self):
        self.publish()
        state, detail = self.check("2026-10-13T00:00:00Z")
        self.assertEqual(state, "bad", detail)
        self.assertIn("controller_binding_revocation FAIL", detail)

    def test_an_unreadable_revocations_file_is_a_fail_not_a_crash(self):
        folder = Path(self.tmp.name) / step.bv.REVOCATIONS
        folder.mkdir()
        (folder / f"{self.digest}.json").write_text("[]")
        state, detail = self.check("2026-10-13T00:00:00Z")
        self.assertEqual(state, "bad", detail)
        self.assertIn("the published revocation statements are unreadable", detail)

    def test_a_statement_dated_after_the_time_asked_leaves_it_applying(self):
        self.publish()
        state, detail = self.check("2026-10-11T00:00:00Z")
        self.assertEqual(state, "ok", detail)
        self.assertIn("revocation PASS", detail)

if __name__ == "__main__":
    unittest.main()
