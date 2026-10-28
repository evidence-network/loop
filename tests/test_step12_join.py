"""steps/12-join: join, then a stranger's probe that verifies and matches the advertised policy, or FAIL.

The doctor backend needs the pinned capsulectl and capsulectl-book
(steps/12-join.setup.sh); without them those tests SKIP and say why, or, with
LOOP_REQUIRE_TOOLS=1 (CI, after the setups ran), fail to load. The
reporter backend runs against a fake reporter console on localhost.
Fixtures: tests/fixtures/step12/, made by make_fixtures.py.
"""
import contextlib
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

    def __init__(self, answers, announced, own=None):
        """`own` is the node_id of the node this console belongs to. Like the real one, the console does
        not answer for its own node: a 502 with the absence text, for the key and for each question."""
        fake = self
        self.answers, self.announced, self.own = answers, announced, own
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

    def run_lab(self, *flags, second=True):
        """Three lab nodes, lab-3 the reporter: --console is lab-3's, the second console lab-1's. Each
        console refuses its own node."""
        pop_dir = Path(self.tmp.name) / "lab"
        pop_dir.mkdir()
        (pop_dir / "population.json").write_text(json.dumps({"population": "live", "rail": None, "nodes": LAB}))
        reporter, other = FakeConsole(VERIFIED, LISTED, own="3" * 64), FakeConsole(VERIFIED, LISTED, own="1" * 64)
        argv = ["--population", str(pop_dir), "--out", str(self.out), "--console", reporter.url]
        for flag in flags:
            argv += {"self": ["--self-node", "lab-3"], "second": ["--second-console", other.url]}[flag]
        try:
            self.assertEqual(step.main(argv), 0)
        finally:
            reporter.close()
            other.close()
        self.assertEqual(finding.check(self.out.parent), [])
        self.asked = {"reporter": reporter.asked, "second": other.asked}
        return json.loads((self.out / "finding.json").read_text())

    def per_node(self, r):
        return [i for i in r["not_checked"] if i["what"].startswith("lab-")]

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
        self.assertIn("can't be asked through its own console", r["not_checked"][-1]["why"])
        self.assertIn("--second-console", r["not_checked"][-1]["why"])
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

    def test_live_population_uses_the_reporter_backend(self):
        self.assertEqual(step.backend({"population": "live"}), "reporter")
        self.assertEqual(step.backend({"population": "rehearsal"}), "doctor")
        self.assertEqual(step.backend({"population": "rehearsal", "probe": "reporter"}), "reporter")

    def test_verified_answers_still_read_not_checked_on_the_plugin_issues(self):
        r = self.run_live({"checkpoints": "verified", "history_card": "verified_history_card"})
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        issues = {i["issue"] for i in r["not_checked"]}
        self.assertTrue({"Mesh-LLM/capsules-plugin#53", "Mesh-LLM/capsules-plugin#51"} <= issues)
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


if __name__ == "__main__":
    unittest.main()
