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
import io
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
from urllib.parse import parse_qs, urlparse

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
CAPSULECTL = step.tools()
NO_TOOL = f"capsulectl not installed at {CAPSULECTL}: run steps/12-join.setup.sh (CI does)"


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


class DoctorBackendTest(Tmp):
    @needs_capsulectl
    def test_rehearsal_population_joins_and_passes_citing_the_binding_gap(self):
        self.assertEqual(step.main(["--population", str(FIX / "population"), "--out", str(self.out)]), 0)
        r = json.loads((self.out / "finding.json").read_text())
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertEqual(r["not_checked"], [])
        # The binding's own verification is outside the assertion: one cited gap, AAC #432.
        self.assertEqual([i for _, i in finding.gaps(r["detail"])], ["action-state-group/agent-action-capsule#432"])
        self.assertEqual(set(r["pinned"]), {"capsulectl", "capsulectl-book"})
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

    @needs_capsulectl
    def test_the_unaltered_fixture_holds(self):
        node = self.joined()
        errs, nc, _ = step.judge(node, CAPSULECTL, Path(self.tmp.name) / "work")
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
        errs, nc, proof = step.judge(node, CAPSULECTL, Path(self.tmp.name) / "work")
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
                and "commitment/1" not in i["what"]]

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
        self.assertNotIn("lab-3: the controller binding", " ".join(i["what"] for i in r["not_checked"]))

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
            fail, nc, _ = step.check_binding("lab-1", "1" * 64, LISTED, "http://x", "http://y", 1)
        finally:
            step.post_json = real
        self.assertIsNone(fail)
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
        self.assertTrue({"action-state-group/agent-action-capsule#432", "no open issue yet (to be filed)"} <= issues,
                        issues)
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

    def test_a_refusal_of_a_listed_stranger_safe_subject_is_a_fail(self):
        r = self.run_live({"checkpoints": "refusal", "history_card": "verified_history_card"})
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("lists it as stranger-safe", r["detail"])

    def test_no_answer_is_not_checked_never_a_pass(self):
        r = self.run_live({"checkpoints": "absence", "history_card": "absence"})
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertTrue(any("no verifiable answer" in i["why"] for i in r["not_checked"]))


ALICE = FIX / "joined" / "alice"
ALICE_DOC = json.loads((ALICE / "commitment.json").read_text())
ALICE_ID = ALICE_DOC["node_id"]
ALICE_ENTRY = {"name": "alice", "node_id": ALICE_ID, "key_id": ALICE_DOC["key_id"],
               "disclosure": {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]}}
OPENSSL_ = shutil.which("openssl")
NO_OPENSSL = "openssl not on PATH: step 03's checker verifies the commitment's signature with it"


def plugin_fixture(name, digest=None):
    """tests/fixtures/step12/plugin/<name>.json with its markers filled: alice's signed commitment pair."""
    text = (FIX / "plugin" / f"{name}.json").read_text()
    for marker, value in (("@KEY_ID@", ALICE_DOC["key_id"]), ("@ANCHOR@", "a" * 64), ("@BOOK@", "/book"),
                          ("@POLICY_DIGEST@", ALICE_DOC["lcap"]["policy_digest"])):
        text = text.replace(marker, value)
    reply = json.loads(text)
    if "commitment" in reply:
        reply["commitment"] = {"commitment": copy.deepcopy(ALICE_DOC),
                               "policy": base64.b64encode((ALICE / "disclosure-policy.json").read_bytes()).decode()}
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

    def tools(self, entry=ALICE_ENTRY, **kw):
        kw.setdefault("before", ("bound", "d" * 64))  # an unbound node is NOT_CHECKED, which the tests below pin
        console = PluginConsole(**kw)
        node_dir = self.out / "alice"
        node_dir.mkdir(exist_ok=True)
        try:
            fails, nc, proof, lines = step.check_node_tools("alice", entry, console.url, node_dir, 5)
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
        self.assertEqual(json.loads((self.out / "alice" / "commitment.json").read_text()), ALICE_DOC)
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
        fails, nc, _, _ = step.check_node_tools("alice", ALICE_ENTRY, console.url, node_dir, 2)
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

    def test_a_node_unbound_before_and_after_is_not_checked_not_held(self):
        fails, nc, _, lines = self.tools(before=("unbound", None))
        self.assertEqual(fails, [])
        self.assertEqual([x for x in nc if x["what"] == "alice: the controller binding"],
                         [{"what": "alice: the controller binding", "why": "no controller binding recorded: the node "
                           "was unbound before the re-join and is after it (join {} sends no binding)",
                           "issue": step.live.ISSUE}])
        self.assertNotIn("the binding is", " ".join(lines))

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
        (self.published / f"{ALICE_ID}.json").write_text(json.dumps(ALICE_DOC, indent=2))
        _, _, _, lines = self.tools()
        self.assertIn("alice: join's commitment is the one published under nodes/commitments/", lines)
        self.assertNotIn(step.CHANGED, " ".join(lines))

    def test_a_changed_commitment_is_reported_as_a_change_not_a_fail(self):
        older = copy.deepcopy(ALICE_DOC)
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
    """The binding gap names only what the pinned plugin leaves unchecked: revocation, and a check outside the
    node. The plugin's join and doctor --probe are no longer loop-wide gaps."""

    def test_the_binding_gap_is_narrowed_to_revocation_and_an_outside_check(self):
        self.assertEqual(step.NO_BINDING_VERIFIER, {
            "what": "the controller binding's revocation, and a check of the binding record by a verifier outside "
                    "the node",
            "why": "the pinned plugin checks the binding's form, both signatures, its window and that it binds the "
                   "node key before doctor reports it bound, but reads no revocation (CB section 5); doctor names "
                   "the binding by digest only, so the loop holds no record to check itself",
            "issue": "action-state-group/agent-action-capsule#432"})

    def test_the_live_gaps_are_the_binding_and_the_signed_commitment_only(self):
        self.assertEqual([i["what"] for i in step.LIVE_NOT_CHECKED], [
            step.NO_BINDING_VERIFIER["what"], "the signed commitment and the policy digest the answers are matched "
                                              "against"])


@unittest.skipUnless(OPENSSL_, NO_OPENSSL)
class PluginToolsRunTest(Tmp):
    """The whole step on a live population of one node, with its own console given."""

    def run_one(self, **kw):
        pop_dir = Path(self.tmp.name) / "live"
        pop_dir.mkdir()
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "rail": None,
                                                             "nodes": [ALICE_ENTRY]}))
        asker = FakeConsole(VERIFIED, ALICE_DOC["key_id"], key_id=ALICE_DOC["key_id"],
                            committed=kw.pop("committed", None))
        own = PluginConsole(**kw)
        real, step.PUBLISHED = step.PUBLISHED, Path(self.tmp.name) / "none"
        try:
            self.assertEqual(step.main(["--population", str(pop_dir), "--out", str(self.out), "--console",
                                        asker.url, "--node-console", f"alice={own.url}"]), 0)
        finally:
            step.PUBLISHED = real
            asker.close()
            own.close()
        self.assertEqual(finding.check(self.out.parent), [])
        return json.loads((self.out / "finding.json").read_text())

    def test_a_healthy_node_reads_not_checked_on_the_binding_and_signed_commitment_gaps_only(self):
        r = self.run_one(before=("bound", "d" * 64), committed="d" * 64)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertEqual(r["not_checked"], step.LIVE_NOT_CHECKED)
        self.assertIn("; the re-join reports it joined, and its commitment holds under the listed key", r["detail"])
        for p in ("alice/doctor-probe.json", "alice/join.json", "alice/commitment.json",
                  "alice/disclosure-policy.json", "alice/doctor-after.json"):
            self.assertIn(p, [x["path"] for x in r["proof"]])

    def test_an_unbound_node_reads_not_checked_in_a_whole_run(self):
        r = self.run_one()
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertIn("alice: the controller binding", [i["what"] for i in r["not_checked"]])

    def test_a_bound_node_join_unbinds_is_a_fail(self):
        D = "d" * 64
        r = self.run_one(before=("bound", D), after=("unbound", None), committed=D)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn(f"alice: the node was bound ({D}) before join, and doctor after join reports it unbound",
                      r["detail"])


if __name__ == "__main__":
    unittest.main()
