"""steps/04-admit: the gate before the deal: a GO or NO decision in the plugin's shape, sealed in the buyer's book first.

The rehearsal runs on capsulectl and capsulectl-book (steps/04-admit.setup.sh,
through steps/12-join.setup.sh). Without them the tests that seal SKIP, saying
so: they never pass without the bundle verifier. With LOOP_REQUIRE_TOOLS=1 (CI,
after the setups ran) a missing tool is an error instead. The live tests check
each decision record in the AAC Go verifier, as step 04 does (loop#135): they
seal each one from a real live record (live/decision-template.json) under a
rehearsal TEST key with steps/01-seal.setup.sh's capsule-emit and aac-go, and
SKIP without them.

Fixtures: tests/fixtures/step04/make_fixtures.sh.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
STEP = ROOT / "steps" / "04-admit"
FIXTURES = ROOT / "tests" / "fixtures" / "step04"
POPULATION = FIXTURES / "rehearsal"
LIVE = FIXTURES / "live"

sys.path.insert(0, str(ROOT / "steps"))
sys.path.insert(0, str(ROOT / "status"))
import finding  # noqa: E402
import status_page  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("step04_admit", str(STEP))
_spec = importlib.util.spec_from_loader("step04_admit", _loader)
step = importlib.util.module_from_spec(_spec)
_loader.exec_module(step)

BIN = step.capsulectl()
HAS_BIN = BIN.is_file() and os.access(BIN, os.X_OK)
NO_BIN = ("SKIPPED LOUDLY: capsulectl is not installed, so the buyer's book cannot be sealed and no bundle "
          "verified; run steps/04-admit.setup.sh (needs go)")
if not HAS_BIN:
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_BIN}")
    print(f"\ntest_step04_admit: {NO_BIN}", file=sys.stderr)

SEAL_PY, CROSS = step.seal01.tools()
HAS_SIGNER = SEAL_PY.is_file() and CROSS.is_file()
NO_SIGNER = ("SKIPPED LOUDLY: steps/01-seal.setup.sh's capsule-emit and aac-go are not installed, so the live "
             "decision records cannot be sealed and verified")
if not HAS_SIGNER:
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_SIGNER}")
    print(f"\ntest_step04_admit: {NO_SIGNER}", file=sys.stderr)
TEST_KEY = POPULATION / "keys" / "alice"  # a rehearsal TEST key, standing in for a lab node's plugin key
NODE_IDS = {"lab-a": "a" * 64, "lab-b": "b" * 64}


def sealed(line):
    """The line as a lab node's plugin seals it: a line carrying an admission decision becomes a real capsule
    (the template's) with that block, its capsule_id recomputed and signed under TEST_KEY; others stay as written."""
    try:
        doc = json.loads(line)
    except ValueError:
        return line
    if not isinstance(doc, dict) or step.decision_block(line) is step.ABSENT:
        return line
    block = doc["model_attestation"]["compute_attestation"]["admission_decision"]
    r = subprocess.run([str(SEAL_PY), "-I", str(FIXTURES / "sign_decision.py"), str(CROSS),
                        str(TEST_KEY / "node-key.pem")], input=json.dumps(block), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


EXPECTED = {"go": "GO", "go-allow-list": "GO", "no-deny-list": "NO", "no-default": "NO", "no-unreadable-policy": "NO",
            "no-unreadable-target": "NO"}
# Each planted failure, the scenario it is planted in, and the check that must catch it.
PLANTED = {
    "ask-outcome": ("go", "one_outcome"),
    "limit-outcome": ("go", "one_outcome"),
    "unsealed": ("go", "decision_sealed"),
    "sealed-after": ("go", "sealed_before_deal"),
    "deal-after-no": ("no-deny-list", "no_deal_after_no"),
}


def run_step(population, out, *extra, env=None):
    return subprocess.run([sys.executable, "-B", str(STEP), "--population", str(population), "--out", str(out),
                           *extra], capture_output=True, text=True, env=env)


def load(path):
    return json.loads(Path(path).read_text())


def failed_checks(out):
    return {(n, c["check"]) for n, cs in load(Path(out) / "checks.json").items() for c in cs if not c["ok"]}


@unittest.skipUnless(HAS_BIN, NO_BIN)
class Rehearsal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "findings" / "04-admit"
        cls.r = run_step(POPULATION, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def sealed(self, out, name):
        bundle = load(out / "scenarios" / name / "bundle.json")
        return [d["agent_input"] for d in bundle["disclosures"].values()
                if d["agent_input"].get("kind") == "admission_decision"]

    def test_rehearsal_passes_with_each_outcome(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr)
        f = load(self.out / "finding.json")
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(failed_checks(self.out), set())
        for name, want in EXPECTED.items():
            self.assertEqual([d["admission_decision"]["verdict"] for d in self.sealed(self.out, name)], [want], name)
        self.assertEqual(sorted(f["pinned"]), ["capsulectl", "capsulectl-book"])
        self.assertIn("would spend 0 USD", f["detail"])
        self.assertIn("evidence-network/loop#81", f["detail"])
        self.assertTrue(finding.gaps(f["detail"]))

    def test_a_no_has_no_deal_record(self):
        for name in ("no-deny-list", "no-default", "no-unreadable-policy", "no-unreadable-target"):
            bundle = load(self.out / "scenarios" / name / "bundle.json")
            kinds = [d["agent_input"].get("kind") for d in bundle["disclosures"].values()]
            self.assertNotIn("deal_record", kinds, name)

    def test_counts_give_both_numbers(self):
        c = load(self.out / "counts.json")
        self.assertEqual((c["refused"], c["decisions"]), (4, 6))
        self.assertEqual(c["by_outcome"], {"GO": 2, "NO": 4})
        self.assertIn("counts.json", [p["path"] for p in load(self.out / "finding.json")["proof"]])

    def test_the_finding_passes_the_check(self):
        self.assertEqual(finding.check(self.out.parent), [])

    def test_the_repository_rehearsal_population_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "04-admit"
            r = run_step(ROOT / "population" / "rehearsal", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(load(out / "finding.json")["result"], "PASS")

    def test_each_planted_failure_fails_on_its_check(self):
        for plant, (scenario, check) in PLANTED.items():
            with self.subTest(plant), tempfile.TemporaryDirectory() as tmp:
                # Only the planted scenario, beside the population's keys.
                pop = Path(tmp) / "p"
                shutil.copytree(POPULATION, pop, ignore=lambda d, names: [
                    n for n in names if Path(d).name == "scenarios" and n != scenario])
                out = Path(tmp) / "f" / "04-admit"
                r = run_step(pop, out, "--plant", plant)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(load(out / "finding.json")["result"], "FAIL")
                self.assertIn((scenario, check), failed_checks(out))
                self.assertEqual(finding.check(out.parent), [])

    def test_a_decision_not_in_the_plugins_shape_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            pop = Path(tmp) / "p"
            shutil.copytree(POPULATION, pop, ignore=lambda d, names: [
                n for n in names if Path(d).name == "scenarios" and n != "go"])
            f = pop / "step04/scenarios/go/decision.json"
            d = load(f)
            del d["source"]
            for bad in (d, None, ["GO"], "GO"):
                f.write_text(json.dumps(bad))
                out = Path(tmp) / "f" / "04-admit"
                r = run_step(pop, out)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(load(out / "finding.json")["result"], "FAIL", bad)
                self.assertIn(("go", "plugin_shape"), failed_checks(out))

    def test_a_decision_altered_in_the_bundle_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = Path(tmp) / "no-deny-list"
            shutil.copytree(self.out / "scenarios" / "no-deny-list", sd)
            bundle = load(sd / "bundle.json")
            for d in bundle["disclosures"].values():
                if d["agent_input"].get("kind") == "admission_decision":
                    d["agent_input"]["admission_decision"]["verdict"] = "GO"
            (sd / "bundle.json").write_text(json.dumps(bundle))
            verdict = step.verify_bundle(BIN, tmp, sd / "bundle.json")
            buyer = step.book_keys.pub_hex(POPULATION / "keys" / "bob" / "node-key.pem")
            checks, _ = step.judge(sd, verdict, buyer)
            failed = {c["check"] for c in checks if not c["ok"]}
            self.assertIn("bundle_verifies", failed)
            self.assertIn("decision_sealed", failed)

    def test_rehearsal_without_capsulectl_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_step(POPULATION, Path(tmp) / "04-admit",
                         env=dict(os.environ, LOOP_TOOLS=tmp, CAPSULECTL=str(Path(tmp) / "none")))
            self.assertEqual(r.returncode, 2)
            self.assertIn("steps/04-admit.setup.sh", r.stderr)


@unittest.skipUnless(HAS_SIGNER, NO_SIGNER)
class Live(unittest.TestCase):
    def live_run(self, lab):
        """Step 04 on `lab` (a dir holding population.json and, for its listed keys, listed/)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "f" / "04-admit"
        env = {"LAB_DIR": str(lab), "LOOP_NODES_DIR": str(Path(lab) / "listed")}
        r = run_step(lab, out, env=dict(os.environ, **env))
        self.assertEqual(r.returncode, 0, r.stderr)
        record = load(out / "finding.json")
        with mock.patch.dict(os.environ, env):
            self.assert_rederives(record, load(Path(lab) / "population.json"), out)
        return out, record

    def assert_rederives(self, record, pop, out):
        """judge_live on a copy of only the files the finding cites gives the recorded result and not_checked:
        what step 08 checks (loop#188)."""
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "04-admit"
            for p in record["proof"]:
                (work / p["path"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(out / p["path"], work / p["path"])
            again = step.judge_live(pop, work, record["started"])
            self.assertEqual(again["result"], record["result"])
            canon = lambda items: sorted(json.dumps(i, sort_keys=True) for i in items)  # noqa: E731
            self.assertEqual(canon(again["not_checked"]), canon(record["not_checked"]))
            self.assertEqual(again["detail"], record["detail"])  # record numbers in problems included

    def test_a_malformed_block_is_named_by_its_ledger_record_number(self):
        # Records 1 and 3 carry no decision; record 2 is a malformed one: the problem names record 2, as before.
        bad = json.dumps({"model_attestation": {"compute_attestation": {"admission_decision": {"verdict": "GO"}}}})
        _, f = self.live_run(self.lab_with(['{"capsule_id":"00"}', bad, "", '{"capsule_id":"01"}']))
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("node lab-a record 2:", f["detail"])

    def test_nodes_json_the_collector_cannot_write_fails(self):
        out, f = self.live_run(self.lab_with(self.good()))
        cases = {"an unknown read": {"read": "maybe"},
                 "numbers out of range": {"read": "ok", "records": 1, "kept": [1, 5]},
                 "numbers repeated": {"read": "ok", "records": 9, "kept": [2, 2]},
                 "a count that is not whole": {"read": "ok", "records": True, "kept": []},
                 "kept emptied beside a decisions file": {"read": "ok", "records": 9, "kept": []},
                 "an entry that is not an object": 5,
                 "a null entry": None}
        for why, node in cases.items():
            with self.subTest(why):
                with tempfile.TemporaryDirectory() as tmp:
                    work = Path(tmp) / "04-admit"
                    shutil.copytree(out, work)
                    nodes = load(work / "nodes.json")
                    # A null entry is planted on lab-b, so lab-a's good decisions alone would read PASS.
                    nodes["lab-b" if node is None else "lab-a"] = node
                    (work / "nodes.json").write_text(json.dumps(nodes))
                    pop = {"population": "live", "nodes": [{"name": "lab-a"}, {"name": "lab-b"}]}
                    again = step.judge_live(pop, work, f["started"])
                    self.assertEqual(again["result"], "FAIL", again["detail"])

    def good(self):
        return [ln for ln in (LIVE / "nodes/lab-a/data/ledger/capsules.jsonl").read_text().splitlines()
                if "admission_decision" in ln]

    def lab_with(self, lines, unreadable=False, listed=True):
        """A two-node lab whose lab-a ledger holds `lines`, each decision line sealed as the plugin does;
        every node reads, and with `unreadable` a third, lab-c, has no data_dir. Each node is listed under the TEST
        key in listed/ (`listed`), as nodes/<node_id>.json lists a lab node's key."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lab = Path(tmp.name)
        for n in ("lab-a", "lab-b"):
            d = lab / "nodes" / n / "data" / "ledger"
            d.mkdir(parents=True)
            (d / "capsules.jsonl").write_text("\n".join([sealed(x) for x in lines] if n == "lab-a" else []) + "\n")
        (lab / "listed").mkdir()
        key = step.seal01.raw_public_hex(TEST_KEY / "node-key.pub.pem")
        for node_id in NODE_IDS.values():
            if listed:
                (lab / "listed" / f"{node_id}.json").write_text(json.dumps({"node_id": node_id, "key_id": key}))
        nodes = [{"name": n, "node_id": NODE_IDS[n], "data_dir": f"$LAB_DIR/nodes/{n}/data", "book_dir": None}
                 for n in ("lab-a", "lab-b")] + ([{"name": "lab-c", "data_dir": None, "book_dir": None}]
                                                 if unreadable else [])
        (lab / "population.json").write_text(json.dumps({"population": "live", "rail": None, "nodes": nodes}))
        return lab

    def test_decisions_in_the_ledger_pass(self):
        out, f = self.live_run(self.lab_with((LIVE / 'nodes/lab-a/data/ledger/capsules.jsonl').read_text().splitlines()))
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertIn("node lab-a: 2 of 4 ledger records carry an admission decision (1 GO, 1 NO)", f["detail"])
        self.assertEqual([p["path"] for p in f["proof"]], ["nodes.json", "lab-a/decisions.jsonl"])
        for issue in ("evidence-network/loop#81", "Mesh-LLM/mesh-llm#2061"):
            self.assertIn(issue, f["detail"])
        self.assertEqual(sorted(f["pinned"]), ["aac-go", "capsules", "mesh-llm"])
        self.assertIn("its producer signature verifies, in the AAC Go verifier", f["detail"])
        self.assertNotIn("evidence-network/loop#135", f["detail"])
        self.assertEqual(finding.check(out.parent), [])

    def test_a_record_changed_after_it_was_sealed_fails(self):
        # loop#135: each decision record is checked in the AAC Go verifier, as step 01 checks its seal.
        lab = self.lab_with(self.good())
        led = lab / "nodes" / "lab-a" / "data" / "ledger" / "capsules.jsonl"
        lines = led.read_text().splitlines()
        n, ln = next((i, x) for i, x in enumerate(lines) if "admission_decision" in x)
        cap = json.loads(ln)
        cap["timestamp"] = "2026-01-01T00:00:00Z"  # outside the block: the shape checks still hold
        lines[n] = json.dumps(cap)
        led.write_text("\n".join(lines) + "\n")
        _, f = self.live_run(lab)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn(f"node lab-a record {n + 1}: capsule_id carried", f["detail"])

    def test_a_record_under_another_key_than_the_listed_one_fails(self):
        lab = self.lab_with(self.good())
        other = step.seal01.raw_public_hex(POPULATION / "keys" / "bob" / "node-key.pub.pem")
        for f in (lab / "listed").glob("*.json"):
            f.write_text(json.dumps(dict(json.loads(f.read_text()), key_id=other)))
        _, f = self.live_run(lab)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn(f"not the key {other} the node publishes", f["detail"])

    def test_a_record_with_no_producer_signature_is_not_checked(self):
        lab = self.lab_with(self.good())
        led = lab / "nodes" / "lab-a" / "data" / "ledger" / "capsules.jsonl"
        lines = [json.dumps({k: v for k, v in json.loads(x).items() if k not in ("signature", "key_id")})
                 if "admission_decision" in x else x for x in led.read_text().splitlines()]
        led.write_text("\n".join(lines) + "\n")
        _, f = self.live_run(lab)
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        why = {x["what"]: x["why"] for x in f["not_checked"]}
        self.assertEqual(why[f"node lab-a: {step.RECORD_WHAT}"], "2 carry no producer signature")
        self.assertIn(step.UNVERIFIED_ISSUE, [x["issue"] for x in f["not_checked"]])

    def test_a_record_that_is_not_an_object_is_a_capsule_id_problem(self):
        checks, capsule = step.seal01.id_checks(b"[]", CROSS, {})
        self.assertIsNone(capsule)
        self.assertFalse(checks["capsule_id"]["ok"])

    def test_no_tool_entry_for_a_node_with_no_record(self):
        # Without aac-go only lab-a, which holds decision records, gets the tool entry; lab-b holds none.
        lab = self.lab_with(self.good())
        with tempfile.TemporaryDirectory() as tools, mock.patch.dict(os.environ, {"LOOP_TOOLS": tools}):
            self.assertTrue(step.tool_gaps())
            _, f = self.live_run(lab)
        whats = [x["what"] for x in f["not_checked"]]
        self.assertIn(f"node lab-a: {step.RECORD_WHAT}", whats)
        self.assertFalse([w for w in whats if w.startswith("node lab-b")], whats)

    def test_a_node_with_no_listing_is_not_checked(self):
        _, f = self.live_run(self.lab_with(self.good(), listed=False))
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertIn(f"node lab-a: {step.RECORD_WHAT}", [x["what"] for x in f["not_checked"]])

    def test_no_verifier_is_not_checked_never_a_pass(self):
        lab = self.lab_with(self.good())
        with tempfile.TemporaryDirectory() as tools:
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            out = Path(tmp.name) / "f" / "04-admit"
            r = run_step(lab, out, env=dict(os.environ, LAB_DIR=str(lab), LOOP_NODES_DIR=str(lab / "listed"),
                                            LOOP_TOOLS=tools))
        self.assertEqual(r.returncode, 0, r.stderr)
        f = load(out / "finding.json")
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertIn(step.TOOLS_ISSUE, [x["issue"] for x in f["not_checked"]])

    def test_an_unreadable_node_keeps_it_not_checked(self):
        _, f = self.live_run(self.lab_with(self.good(), unreadable=True))
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("live node lab-c", [x["what"] for x in f["not_checked"]])
        self.assertIn("evidence-network/loop#81", [x["issue"] for x in f["not_checked"]])

    def test_an_unreadable_policy_no_passes(self):
        cap = json.loads(self.good()[0])
        d = cap["model_attestation"]["compute_attestation"]["admission_decision"]
        d.update(verdict="NO", source="unreadable_policy")
        d["reads"][0]["digest"] = None
        _, f = self.live_run(self.lab_with([json.dumps(cap)]))
        self.assertEqual(f["result"], "PASS", f["detail"])

    def test_an_unreadable_target_no_passes(self):
        cap = json.loads(self.good()[0])
        d = cap["model_attestation"]["compute_attestation"]["admission_decision"]
        d.update(verdict="NO", source="unreadable_target", peer_commitment=None)
        d["reads"][0]["digest"] = None
        _, f = self.live_run(self.lab_with([json.dumps(cap)]))
        self.assertEqual(f["result"], "PASS", f["detail"])

    def test_an_explicit_null_decision_fails(self):
        cap = json.loads(self.good()[0])
        cap["model_attestation"]["compute_attestation"]["admission_decision"] = None
        _, f = self.live_run(self.lab_with(self.good() + [json.dumps(cap)]))
        self.assertEqual(f["result"], "FAIL")

    def test_an_unreadable_ledger_file_keeps_it_not_checked(self):
        lab = self.lab_with(self.good())
        (lab / "nodes/lab-b/data/ledger/capsules.jsonl").unlink()
        _, f = self.live_run(lab)
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("live node lab-b", [x["what"] for x in f["not_checked"]])

    def test_no_decision_is_not_checked(self):
        _, f = self.live_run(self.lab_with(['{"capsule_id":"00","action_type":"fyi"}']))
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("an admission decision on a lab node", [x["what"] for x in f["not_checked"]])

    def with_rows(self, card=None, rows=None):
        """A good decision as a build with capsules-plugin PR #116 seals it: five rows and the history-card read."""
        cap = json.loads(self.good()[0])
        d = cap["model_attestation"]["compute_attestation"]["admission_decision"]
        d["reads"][0]["policy"] = "base/1"
        cited = card is None or "commitment" in card
        d["rows"] = rows if rows is not None else [{"row": r, "result": x} for r, x in zip(step.ROWS, (
            ("pass", "pass") if cited else ("not_read", "not_read")) + ("fail", "pass", "pass"))]
        d["reads"].append(card if card is not None else
                          {"kind": "history_card", "derivation": "history_card/1",
                           "commitment": {"alg": "SHA-256", "digest": "c" * 64}})
        return cap

    def test_a_decision_with_the_five_rows_passes(self):
        for card in (None, {"kind": "history_card", "state": "not_read"}):
            _, f = self.live_run(self.lab_with([json.dumps(self.with_rows(card))]))
            self.assertEqual(f["result"], "PASS", f["detail"])

    def test_rows_and_decisions_from_an_earlier_build_pass_together(self):
        _, f = self.live_run(self.lab_with(self.good() + [json.dumps(self.with_rows())]))
        self.assertEqual(f["result"], "PASS", f["detail"])

    def test_malformed_rows_fail(self):
        rows = [{"row": r, "result": "pass"} for r in step.ROWS]
        for edit, why in ((lambda c: self.dec(c).update(rows=rows[::-1]), "five base-policy rows"),
                          (lambda c: self.dec(c).update(rows=rows[:4]), "five base-policy rows"),
                          (lambda c: self.dec(c)["rows"][2].update(result="maybe"), "five base-policy rows"),
                          (lambda c: self.dec(c)["rows"][0].update(peer="a" * 64), "five base-policy rows"),
                          (lambda c: self.dec(c)["reads"].pop(), "history card"),
                          (lambda c: self.dec(c)["reads"].__setitem__(1, {"kind": "history_card"}), "history card"),
                          (lambda c: self.dec(c)["reads"][1].update(peer="a" * 64), "history card"),
                          (lambda c: self.dec(c).update(peer_commitment=None, verdict="NO",
                                                        source="unreadable_target"), "names no peer")):
            cap = self.with_rows()
            edit(cap)
            _, f = self.live_run(self.lab_with([json.dumps(cap)]))
            self.assertEqual(f["result"], "FAIL", why)
            self.assertIn(why, f["detail"])

    @staticmethod
    def dec(cap):
        return cap["model_attestation"]["compute_attestation"]["admission_decision"]

    def test_a_malformed_decision_fails(self):
        good = self.good()
        for edit, why in ((lambda d: d.update(verdict="ASK"), "is not GO or NO"),
                          (lambda d: d.update(reads=[]), "operator policy"),
                          (lambda d: d.update(peer_commitment="a" * 64), "peer_commitment"),
                          (lambda d: d.pop("source"), "no source"),
                          (lambda d: d["reads"][0].update(digest={}), "operator policy"),
                          (lambda d: d["peer_commitment"].update(digest=""), "peer_commitment"),
                          (lambda d: d.update(peer_commitment=None), "peer_commitment"),
                          (lambda d: d.update(peer="a" * 64), "unexpected members peer"),
                          (lambda d: d["peer_commitment"].update(peer="a" * 64), "peer_commitment"),
                          (lambda d: d["reads"][0].update(peer="a" * 64), "operator policy"),
                          (lambda d: d["reads"][0].update(digest=None), "operator policy"),
                          (lambda d: d["reads"].append({"peer": "a" * 64}), "operator policy")):
            cap = json.loads(good[0])
            edit(cap["model_attestation"]["compute_attestation"]["admission_decision"])
            _, f = self.live_run(self.lab_with([json.dumps(cap)]))
            self.assertEqual(f["result"], "FAIL")
            self.assertIn(why, f["detail"])


ROWS116 = load(LIVE / "rows116.json")


def case(name, **rows):
    """A copy of one rows116.json case, with the rows named here set to the results given."""
    b = json.loads(json.dumps(ROWS116[name]))
    for r in b["rows"]:
        r["result"] = rows.get(r["row"], r["result"])
    return b


def capsule(n, block):
    return json.dumps({"capsule_id": f"{n:02x}", "action_type": "fyi",
                       "model_attestation": {"compute_attestation": {"admission_decision": block}}})


@unittest.skipUnless(HAS_SIGNER, NO_SIGNER)
class Rows(unittest.TestCase):
    """Each row recomputed from what a decision in the #116 record shape sealed (tests/fixtures/step04/live/rows116.json)."""
    live_run, lab_with, assert_rederives = Live.live_run, Live.lab_with, Live.assert_rederives

    def run_blocks(self, *blocks):
        return self.live_run(self.lab_with([capsule(i, b) for i, b in enumerate(blocks)]))

    def test_every_case_the_rules_give_passes(self):
        out, f = self.run_blocks(*ROWS116.values())
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertIn("lab-a/rows.json", [p["path"] for p in f["proof"]])
        self.assertEqual(finding.check(out.parent), [])
        rows = load(out / "lab-a" / "rows.json")
        self.assertEqual(len(rows), len(ROWS116))
        by = {name: {c["row"]: c for c in cs} for name, cs in zip(ROWS116, rows.values())}
        # The record fixes identity on a cited card and key age on none; everything else it leaves open.
        self.assertEqual(by["all-pass"]["identity"]["check"], "PASS")
        self.assertEqual(by["no-card-yet"]["key_age"]["check"], "PASS")
        for name, row in (("all-pass", "key_age"), ("no-card-yet", "identity"), ("all-pass", "corroboration"),
                          ("no-snapshot-yet", "disputes")):
            self.assertEqual(by[name][row]["check"], "NOT_CHECKED", (name, row))
            self.assertTrue(by[name][row]["why"])
        self.assertNotIn("FAIL", {c["check"] for cs in rows.values() for c in cs})
        self.assertIn("evidence-network/loop#81", f["detail"])

    def test_each_row_passes_and_fails(self):
        # Each row a value the rules give (it stays a PASS) and one they cannot give (a FAIL naming the row).
        cases = {
            "identity": (("all-pass", "pass"), ("all-pass", "fail")),
            "key_age": (("key-age-fail", "fail"), ("no-card-yet", "pass")),
            "performed_and_settled": (("settled-fail", "fail"), ("no-snapshot-yet", "pass")),
            "disputes": (("disputes-fail", "fail"), ("no-snapshot-yet", "fail")),
            "corroboration": (("corroboration-fail", "fail"), ("all-pass", "not_read")),
        }
        for row, ((ok_case, ok), (bad_case, bad)) in cases.items():
            with self.subTest(row=row):
                _, f = self.run_blocks(case(ok_case, **{row: ok}))
                self.assertEqual(f["result"], "PASS", f["detail"])
                _, f = self.run_blocks(case(bad_case, **{row: bad}))
                self.assertEqual(f["result"], "FAIL", f["detail"])
                self.assertIn(f"row {row} is {bad}", f["detail"])

    def test_a_cited_card_with_identity_not_read_fails(self):
        _, f = self.run_blocks(case("all-pass", identity="not_read"))
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("row identity is not_read; the rules give pass with a cited card", f["detail"])

    def test_a_missing_reordered_or_malformed_row_fails(self):
        for edit in (lambda b: b["rows"].pop(2), lambda b: b["rows"].reverse(),
                     lambda b: b["rows"].__setitem__(1, {"row": "key_age"}),
                     lambda b: b["rows"][3].update(result="PASS")):
            b = case("all-pass")
            edit(b)
            _, f = self.run_blocks(b)
            self.assertEqual(f["result"], "FAIL")
            self.assertIn("five base-policy rows", f["detail"])

    def test_a_bad_second_read_fails(self):
        for card in ({"kind": "history_card", "state": "read"}, {"kind": "operator_policy", "state": "not_read"},
                     {**ROWS116["all-pass"]["reads"][1], "derivation": "history_card/2"}, None):
            b = case("all-pass")
            if card is None:
                b["reads"].pop()
            else:
                b["reads"][1] = card
            _, f = self.run_blocks(b)
            self.assertEqual(f["result"], "FAIL", card)

    def test_base_1_naming_a_peer_without_rows_is_a_fail(self):
        dropped = json.loads(json.dumps(ROWS116["all-pass"]))
        del dropped["rows"]
        dropped["reads"].pop()  # the one-read shape, so the shape check passes and the rows rule is reached
        _, f = self.run_blocks(dropped)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("names a peer but seals no rows", f["detail"])

    # The shape the pinned plugin seals now (capsules-plugin #178 and #165): each row carries the evaluator's
    # effect, and count and denominator where it counts; reads cite the base terms pack; a price member records
    # the price rule. The digests here are synthetic.
    @staticmethod
    def current(price="not_checked", **edits):
        b = json.loads(json.dumps(ROWS116["all-pass"]))
        for r in b["rows"]:
            r["effect"] = "none"
        b["rows"][2].update(count=3, denominator=26)
        b["rows"][3].update(count=0, denominator=2)
        b["rows"][4].update(count=15, denominator=26)
        # The plugin's order: the price pack when the price rule ran, then the base pack (admission.rs).
        base_pack = {"kind": "terms_pack", "role": "base", "pack": "asg/mesh_admission_base/1.0.0",
                     "definition_digest": {"alg": "SHA-256", "digest": "d" * 64}}
        if price == "not_checked":
            b["price"] = {"state": "not_checked", "reason": "host_lacks_backend_pricing"}
        elif price in ("allow", "deny"):
            b["price"] = {"state": "checked", "outcome": price, "input_msat_per_million": 10,
                          "output_msat_per_million": 20,
                          "checks": [{"check": "rate_cap", "result": "pass" if price == "allow" else "fail"}]}
            b["reads"].append({"kind": "terms_pack", "role": "price", "pack": "asg/inference/1.0.0",
                               "definition_digest": {"alg": "SHA-256", "digest": "e" * 64},
                               "profile": {"alg": "SHA-256", "commitment": "f" * 64}})
            if price == "deny":
                b["verdict"], b["source"] = "NO", "price_rule"
        b["reads"].append(base_pack)
        b.update(edits)
        return b

    def test_the_current_plugin_shape_passes(self):
        for price in (None, "not_checked", "allow", "deny"):
            with self.subTest(price=price):
                _, f = self.run_blocks(self.current(price))
                self.assertEqual(f["result"], "PASS", f["detail"])

    def test_a_price_refusal_does_not_disagree_with_an_admitting_decision_for_the_same_peer(self):
        _, f = self.run_blocks(self.current("allow"), self.current("deny"))
        self.assertEqual(f["result"], "PASS", f["detail"])

    def test_an_unavailable_price_is_a_price_rule_no(self):
        for reason in ("host_lacks_backend_pricing", "no_price_without_model", "guard_error"):
            b = self.current(None, verdict="NO", source="price_rule")
            b["price"] = {"state": "unavailable", "reason": reason}
            _, f = self.run_blocks(b)
            self.assertEqual(f["result"], "PASS", (reason, f["detail"]))

    def test_what_the_engine_decides_is_not_judged(self):
        # A refusal can come from a check the engine did not evaluate, which the plugin keeps out of `checks`.
        for outcome, checks in (("deny", [{"check": "rate_cap", "result": "pass"}]), ("escalate", []),
                                ("allow", [{"check": "rate_cap", "result": "not_evaluated"}])):
            b = self.current("deny")
            b["price"].update(outcome=outcome, checks=checks)
            if outcome == "allow":
                b.update(verdict="GO", source="default")
            _, f = self.run_blocks(b)
            self.assertEqual(f["result"], "PASS", (outcome, f["detail"]))

    def test_a_malformed_price_or_pack_or_row_member_fails(self):
        def drop_price_pack(b):
            b["reads"].pop(2)

        cases = (
            (lambda b: b["price"].update(state="maybe"), "price state", "not_checked"),
            (lambda b: b["price"].update(reason="because"), "price not_checked", "not_checked"),
            (lambda b: b["price"].update(peer="a" * 64), "price not_checked", "not_checked"),
            (lambda b: b["price"].update(outcome="maybe"), "price outcome", "allow"),
            (lambda b: b["price"].update(input_msat_per_million=-1), "neither a rate nor null", "allow"),
            (lambda b: b["price"].update(checks=[{"check": "rate_cap"}]), "price checks", "allow"),
            (lambda b: b.update(price={"state": "not_checked", "reason": "guard_error"}),
             "price not_checked", "not_checked"),
            (lambda b: b.update(price={"state": "unavailable", "reason": "because"}), "price unavailable",
             "not_checked"),
            (lambda b: b["reads"].reverse(), "operator policy", "allow"),
            (lambda b: b["reads"].insert(2, b["reads"].pop()), "price pack, if it ran", "allow"),
            (lambda b: b.update(price={"state": "checked"}), "does not have the plugin's members", "allow"),
            (drop_price_pack, "go together", "allow"),
            (lambda b: b["reads"].append(b["reads"][-1]), "price pack, if it ran", "allow"),
            (lambda b: b["reads"][2].update(profile={"alg": "SHA-256"}), "profile", "allow"),
            (lambda b: b["reads"][2].update(profile={"alg": "SHA-256", "commitment": {"alg": "SHA-256",
                                                                                       "digest": "f" * 64}}),
             "profile", "allow"),
            (lambda b: b["reads"][2].update(role="other"), "terms pack read is not the plugin's shape", "allow"),
            (lambda b: b["reads"].pop(2), "go together", "not_checked"),
            (lambda b: b["rows"][0].pop("effect"), "some rows carry", "not_checked"),
            (lambda b: b["rows"][0].update(effect="NO"), "record rows", "not_checked"),
            (lambda b: b["rows"][2].update(count=27), "over its denominator", "not_checked"),
            (lambda b: b["rows"][2].update(count="3"), "not a count", "not_checked"),
            (lambda b: b.update(source="allow_list", verdict="GO") or b.update(source="deny_list", verdict="NO"),
             "decided", "not_checked"),
            (lambda b: b.update(source="price_rule", verdict="NO"), "price_rule with no price that refuses",
             "not_checked"),
            (lambda b: b.update(source="unreadable_profile", verdict="NO"), "unreadable profile with a price",
             "not_checked"),
            (lambda b: b.update(source="default", verdict="GO"), "not price_rule", "deny"),
            (lambda b: b.update(peer_commitment=None, source="unreadable_target", verdict="NO"), "", "not_checked"),
        )
        for edit, why, price in cases:
            with self.subTest(why=why, price=price):
                b = self.current(price)
                edit(b)
                _, f = self.run_blocks(b)
                self.assertEqual(f["result"], "FAIL", f["detail"])
                self.assertIn(why, f["detail"])

    def test_the_read_rule_holds_on_both_shapes(self):
        # Rows: the policy read first, the card second. An earlier build: the policy read alone.
        earlier = json.loads(json.dumps(ROWS116["all-pass"]))
        del earlier["rows"]
        earlier["reads"].pop()
        earlier["reads"][0]["policy"] = "basic/1"
        earlier["peer_commitment"]["digest"] = "e" * 64
        _, f = self.run_blocks(ROWS116["all-pass"], earlier)
        self.assertEqual(f["result"], "PASS", f["detail"])
        swapped = case("all-pass")
        swapped["reads"].reverse()
        two = json.loads(json.dumps(earlier))
        two["reads"].append(ROWS116["all-pass"]["reads"][1])
        for b in (swapped, two):
            _, f = self.run_blocks(b)
            self.assertEqual(f["result"], "FAIL")

    def test_rows_under_the_earlier_policy_fail(self):
        b = case("all-pass")
        b["reads"][0]["policy"] = "basic/1"
        _, f = self.run_blocks(b)
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("rows under policy 'basic/1'", f["detail"])


@unittest.skipUnless(HAS_SIGNER, NO_SIGNER)
class Verdicts(unittest.TestCase):
    """The rows decide nothing: GO or NO follows from the source alone, the same for one peer under one policy."""
    live_run, lab_with, run_blocks, assert_rederives = Live.live_run, Live.lab_with, Rows.run_blocks, Live.assert_rederives

    def test_a_verdict_its_source_does_not_give_fails(self):
        for source, verdict in (("allow_list", "NO"), ("deny_list", "GO"), ("unreadable_policy", "GO"),
                                ("unreadable_target", "GO")):
            b = case("all-pass")
            b.update(source=source, verdict=verdict)
            if source == "unreadable_policy":
                b["reads"][0]["digest"] = None
            _, f = self.run_blocks(b)
            self.assertEqual(f["result"], "FAIL", source)
            # A GO with no readable policy already fails the shape (it is a NO with no digest, or cites one).
            self.assertIn("operator policy" if source == "unreadable_policy" else f"verdict {verdict} from {source}",
                          f["detail"])

    def test_an_unknown_source_fails(self):
        b = case("all-pass")
        b["source"] = "rows"
        _, f = self.run_blocks(b)
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("source 'rows' is none the plugin gives", f["detail"])

    def test_an_unreadable_policy_with_a_digest_fails(self):
        b = case("all-pass")
        b.update(source="unreadable_policy", verdict="NO")
        _, f = self.run_blocks(b)
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("an unreadable policy cited by digest", f["detail"])

    def test_rows_that_fail_do_not_change_the_verdict(self):
        # A NO from the deny list and a GO from the default, each with every row failing or passing: all hold.
        no = case("all-pass")
        no.update(source="deny_list", verdict="NO")
        go = case("card-unverifiable", key_age="not_read", performed_and_settled="fail", disputes="fail",
                       corroboration="fail")
        _, f = self.run_blocks(no, go)
        self.assertEqual(f["result"], "PASS", f["detail"])

    def test_one_peer_two_verdicts_under_one_policy_fails(self):
        a, b = case("all-pass"), case("settled-fail")
        b["peer_commitment"] = a["peer_commitment"]
        b.update(source="deny_list", verdict="NO")
        _, f = self.run_blocks(a, b)
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("one peer gets GO (default) and NO (deny_list) under one policy digest", f["detail"])
        b["reads"][0]["digest"] = {"alg": "SHA-256", "digest": "a" * 64}
        _, f = self.run_blocks(a, b)
        self.assertEqual(f["result"], "PASS", f["detail"])

    def test_the_default_giving_both_verdicts_under_one_policy_fails(self):
        a, b = case("all-pass"), case("settled-fail")
        b["verdict"] = "NO"
        _, f = self.run_blocks(a, b)
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("the default gives both GO and NO under one policy digest", f["detail"])


class StatusRefusedRow(unittest.TestCase):
    """The status page's `refused` number reads step 04's counts.json, only from a valid PASS: both numbers or neither."""

    def run_with(self, result, counts, proof=("counts.json",)):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run_dir = Path(tmp.name) / "2026-10-28-rehearsal"
        d = run_dir / "04-admit"
        d.mkdir(parents=True)
        for p in proof:
            (d / p).write_text(json.dumps(counts) if p == "counts.json" else "proof\n")
        nc = [{"what": "a", "why": "b", "issue": "c"}] if result == "NOT_CHECKED" else []
        finding.write(d, result=result, population="rehearsal", asserts="step 4 holds", pinned=[], proof=list(proof),
                      detail="", not_checked=nc, started="2026-10-28T00:00:00Z")
        run = status_page.read_run("rehearsal", run_dir)
        number = next(n for n in status_page.NUMBERS if n.key == "refused")
        return status_page.number_value(number, run)

    def test_the_row_is_the_no_decisions(self):
        number = next(n for n in status_page.NUMBERS if n.key == "refused")
        self.assertEqual((number.label, number.step), ("Admission decisions that were NO", 4))
        self.assertNotIn("limited", [n.key for n in status_page.NUMBERS])

    def test_a_pass_shows_both_numbers(self):
        self.assertEqual(self.run_with("PASS", {"refused": 2, "decisions": 4}), ("2 of 4 decisions", True))

    def test_anything_but_a_valid_pass_shows_neither(self):
        placeholder = ("not measured yet (M3)", False)
        self.assertEqual(self.run_with("FAIL", {"refused": 2, "decisions": 4}), placeholder)
        self.assertEqual(self.run_with("NOT_CHECKED", {"refused": 2, "decisions": 4}, proof=()), placeholder)
        self.assertEqual(self.run_with("PASS", {"decisions": 4}), placeholder)
        self.assertEqual(self.run_with("PASS", {"limited_or_refused": 2, "decisions": 4}), placeholder)
        self.assertEqual(self.run_with("PASS", {"refused": 5, "decisions": 4}), placeholder)
        self.assertEqual(self.run_with("PASS", {"refused": 0, "decisions": 0}), placeholder)
        self.assertEqual(self.run_with("PASS", {}, proof=("other.txt",)), placeholder)


if __name__ == "__main__":
    unittest.main()
