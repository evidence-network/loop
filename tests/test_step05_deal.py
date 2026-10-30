"""steps/05-deal: both halves of one deal, pushed, held by digest and cited, checked on the proof bytes.

The rehearsal verdicts come from the driver steps/05-deal.setup.sh installs (a
Rust build of evidencebook's crate at the pinned tag); the live ones also need
the AAC Go verifier step 01's setup installs. Without them these tests SKIP,
saying so: they never pass without the verifier. With LOOP_REQUIRE_TOOLS=1
(CI, after the setups ran) a missing tool is an error instead.
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

ROOT = Path(__file__).resolve().parent.parent
STEP = ROOT / "steps" / "05-deal"
FIXTURES = ROOT / "tests" / "fixtures" / "step05"
POPULATION = FIXTURES / "rehearsal"
LIVE = FIXTURES / "live"

sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("step05_deal", str(STEP))
_spec = importlib.util.spec_from_loader("step05_deal", _loader)
step = importlib.util.module_from_spec(_spec)
_loader.exec_module(step)

BIN = step.driver()
NO_DRIVER = ("SKIPPED LOUDLY: step 05's driver is not installed, so evidencebook's verifier cannot run; "
             "run steps/05-deal.setup.sh (needs cargo and crates.io)")
CROSS = step.cross_language()
NO_CROSS = ("SKIPPED LOUDLY: the AAC Go verifier is not installed, so a held half's capsule_id cannot be "
            "recomputed; run steps/01-seal.setup.sh (needs go)")
HAS_CROSS = CROSS.is_file() and os.access(CROSS, os.X_OK)
for missing, why in ((BIN is None, NO_DRIVER), (not HAS_CROSS, NO_CROSS)):
    if missing:
        if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
            raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {why}")
        print(f"\ntest_step05_deal: {why}", file=sys.stderr)


def run_step(population, out, env=None):
    r = subprocess.run([sys.executable, "-B", str(STEP), "--population", str(population), "--out", str(out)],
                       capture_output=True, text=True, env=env)
    return r


class Step05ExchangePairingTest(unittest.TestCase):
    """Halves join on client_nonce, never on agent_input_digest (loop#66); no verifier needed."""
    OUT_A, OUT_B = "a" * 64, "b" * 64

    def test_two_exchanges_sharing_a_prompt_count_as_two(self):
        halves = {"n1": [("x1", "requested", self.OUT_A), ("x2", "requested", self.OUT_B)],
                  "n2": [("x1", "served", self.OUT_A)],
                  "n3": [("x2", "served", self.OUT_B)]}
        paired, notes = step.paired_exchanges(halves)
        self.assertEqual(paired, {"x1", "x2"})
        self.assertEqual(notes, [])

    def test_a_half_on_one_node_only_is_not_an_exchange(self):
        paired, notes = step.paired_exchanges({"n1": [("x1", "requested", self.OUT_A)], "n2": []})
        self.assertEqual(paired, set())
        self.assertIn("nonce x1 is not one exchange held on two nodes: n1 requested", notes)

    def test_two_halves_of_one_role_are_not_an_exchange(self):
        paired, notes = step.paired_exchanges({"n1": [("x1", "served", self.OUT_A)],
                                               "n2": [("x1", "served", self.OUT_A)]})
        self.assertEqual(paired, set())
        self.assertEqual(len(notes), 1)

    def test_two_halves_with_different_outputs_are_not_an_exchange(self):
        paired, notes = step.paired_exchanges({"n1": [("x1", "requested", self.OUT_A)],
                                               "n2": [("x1", "served", self.OUT_B)]})
        self.assertEqual(paired, set())
        self.assertIn("output digests differ", notes[0])

    def test_a_half_with_no_nonce_is_named_not_dropped(self):
        paired, notes = step.paired_exchanges({"n1": [(None, "requested", self.OUT_A)]})
        self.assertEqual(paired, set())
        self.assertEqual(notes, ["a half held by n1 carries no client_nonce"])

    def test_the_2026_10_29_live_halves_are_two_exchanges(self):
        root = ROOT / "findings" / "2026-10-29-live" / "05-deal"
        files = sorted(root.glob("stevenmih-*/held-?.json"))
        if not files:
            self.skipTest("the 2026-10-29 live proof files are not in this checkout")
        halves = {}
        for f in files:
            halves.setdefault(f.parent.name, []).append(step.exchange_key(json.loads(f.read_text())))
        paired, notes = step.paired_exchanges(halves)
        self.assertEqual(paired, {"26c09646-1582-4ee4-a0f8-f030163f917f", "de9cf398-1203-47d5-abc5-5ea0d7b5c25a"})
        self.assertEqual(notes, [])


@unittest.skipIf(BIN is None, NO_DRIVER)
class Step05DealRehearsalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.findings = Path(self.tmp.name) / "findings"
        self.out = self.findings / "05-deal"
        self.population, self.keys = step.nodes(POPULATION)

    def tearDown(self):
        self.tmp.cleanup()

    def run_step(self, population=POPULATION):
        r = run_step(population, self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((self.out / "finding.json").read_text())

    def dealt(self, settings=None, decls=None, plant=None):
        """The proof files of one deal, made the way the step makes them."""
        started = finding.now()
        step.clean(self.out)
        if decls is None:
            decls = step.declarations(BIN, POPULATION, self.keys, started)
        step.deal(BIN, self.population, self.keys, settings or step.settings(POPULATION), self.out, started,
                  decls=decls, plant=plant)
        return started

    def decide(self, started):
        return step.decide(BIN, self.out, population=self.population, keys=self.keys, started=started)

    def failed(self):
        v = json.loads((self.out / "verify.json").read_text())
        return {(c["side"], c["check"]) for c in v["checks"] if not c["ok"]}

    def assert_clean(self):
        self.assertEqual(finding.check(self.findings), [])

    def with_settings(self, **sides):
        s = step.settings(POPULATION)
        for side, values in sides.items():
            s[side].update(values)
        return s

    # (a) the rehearsal population
    def test_rehearsal_passes_with_acceptance_under_a_controller_declaration(self):
        f = self.run_step()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(f["not_checked"], [])
        self.assertEqual(self.failed(), set())
        v = json.loads((self.out / "verify.json").read_text())
        self.assertEqual(v["acceptance_not_checked"], [])
        self.assertIn("v0.8.1", v["verifier"])
        for c in ("half_cites_counterparty_checkpoint", "pushed_at_completion", "holds_foreign_half",
                  "cites_foreign_half", "accepted_under_controller_declaration", "counterparty_distinct",
                  "share_record_at_completion", "halves_of_one_exchange"):
            self.assertEqual(sum(1 for x in v["checks"] if x["check"] == c and x["ok"]),
                             1 if c == "halves_of_one_exchange" else 2, c)
            self.assertIn(c, f["detail"])
        self.assertEqual(finding.gaps(f["detail"]), [(
            "the controller declaration's format, which is the rehearsal's stand-in (steps/05-deal.d), not the "
            "controller-binding/0 the pinned capsules plugin reads, whose signatures no released verifier checks",
            "action-state-group/agent-action-capsule#432")])
        self.assertIn("would spend 0 USD", f["detail"])
        self.assertEqual(set(f["pinned"]), {"evidencebook", "checkpointed-local-log"})
        paths = {p["path"] for p in f["proof"]}
        for side in ("alice", "bob"):
            for p in ("half.json", "push.json", "held/received-records.jsonl", "held/received-provenance.jsonl",
                      "citing.json", "checkpoint-open.json"):
                self.assertIn(f"{side}/{p}", paths)
            prov = json.loads((self.out / side / "held" / "received-provenance.jsonl").read_text())
            self.assertEqual(prov["accepted_via"], "controller_declaration")
            self.assertFalse((self.out / side / "refusal.json").exists())
        self.assert_clean()  # (c)

    def test_the_repository_rehearsal_population_passes(self):
        f = self.run_step(ROOT / "population" / "rehearsal")
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assert_clean()

    def test_a_second_run_into_the_same_directory_is_clean(self):
        self.run_step()
        self.assertEqual(self.run_step()["result"], "PASS")
        self.assert_clean()

    # (b) planted failures
    def test_a_half_citing_the_wrong_counterparty_checkpoint_fails(self):
        started = self.dealt(plant="wrong-checkpoint")
        f = self.decide(started)
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.failed(), {("alice", "half_cites_counterparty_checkpoint")})
        self.assertIn("rehearsal-bob-other", f["detail"])
        self.assert_clean()

    def test_a_held_half_whose_bytes_do_not_match_its_digest_fails(self):
        started = self.dealt()
        held = self.out / "bob" / "held" / "received-records.jsonl"
        record = json.loads(held.read_text())
        record["header"]["statement"]["role"] = "provider"
        held.write_text(json.dumps(record, separators=(",", ":")) + "\n")
        f = self.decide(started)
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.failed(), {("bob", "holds_foreign_half"), ("bob", "cites_foreign_half")})
        self.assertIn("none of the 1 held records recomputes to it", f["detail"])
        self.assert_clean()

    def test_a_counterparty_bound_to_the_receivers_own_controller_fails(self):
        started = finding.now()
        ctl = POPULATION / "keys" / "controller-alice" / "controller-key.pem"
        both = ",".join(step.raw_public_hex(self.keys[s][1]) for s in ("alice", "bob"))
        r = subprocess.run([str(BIN), "declare", "--controller-key", str(ctl), "--controller-id",
                            "rehearsal-controller-alice", "--keys", both, "--time", started],
                           capture_output=True, text=True, check=True)
        self.dealt(decls=[json.loads(r.stdout)])
        f = self.decide(started)
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.failed(), {("alice", "counterparty_distinct"), ("bob", "counterparty_distinct")})
        self.assertIn("not a different counterparty", f["detail"])
        self.assert_clean()

    def test_a_declaration_whose_signature_does_not_verify_fails(self):
        started = finding.now()
        decls = step.declarations(BIN, POPULATION, self.keys, started)
        decls[0]["controller_id"] = "rehearsal-controller-other"
        self.dealt(decls=decls)
        f = self.decide(started)
        # Bob finds no verifying declaration for Alice's key and falls back to the stopgap.
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(f["not_checked"][0]["issue"], step.ACCEPTANCE_ISSUE)
        self.assertIn("operator_stopgap (bob)", f["detail"])
        self.assert_clean()

    def test_a_side_that_does_not_push_fails(self):
        started = self.dealt(settings=self.with_settings(bob={"share_record_at_completion": "off"}))
        f = self.decide(started)
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.failed(), {("bob", "share_record_at_completion"), ("bob", "pushed_at_completion"),
                                         ("alice", "holds_foreign_half"), ("alice", "cites_foreign_half"),
                                         ("bob", "holds_foreign_half"), ("bob", "cites_foreign_half")})
        refusal = json.loads((self.out / "bob" / "refusal.json").read_text())
        self.assertEqual(refusal["reason"], "policy_decline")
        self.assertEqual(refusal["key_id"], step.raw_public_hex(self.keys["alice"][1]))
        self.assertNotIn("signature", refusal)
        self.assert_clean()

    def test_a_population_without_settings_is_off_and_fails(self):
        pop = Path(self.tmp.name) / "no-settings"
        shutil.copytree(POPULATION, pop)
        (pop / "step05" / "settings.json").unlink()
        f = self.run_step(pop)
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("share_record_at_completion off", f["detail"])
        self.assert_clean()

    # acceptance other than under a controller declaration is never a pass
    def test_acceptance_via_the_stopgap_or_the_host_is_not_checked(self):
        for via in ("operator_stopgap", "host_announced"):
            with self.subTest(via=via):
                started = self.dealt(settings=self.with_settings(alice={"accepted_via": via}))
                f = self.decide(started)
                self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
                self.assertEqual([n["issue"] for n in f["not_checked"]], [step.ACCEPTANCE_ISSUE])
                self.assertIn(f"{via} (alice)", f["not_checked"][0]["why"])
                self.assertEqual(self.failed(), set())
                self.assert_clean()

    def test_no_declarations_at_all_is_not_checked(self):
        started = self.dealt(decls=[])
        f = self.decide(started)
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("operator_stopgap (alice)", f["detail"])
        self.assertIn("operator_stopgap (bob)", f["detail"])
        self.assert_clean()


class Step05DealNoDriverTest(unittest.TestCase):
    def test_rehearsal_without_the_driver_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, LOOP_TOOLS=tmp)
            env.pop("LOOP_STEP05_DEAL_BIN", None)
            r = run_step(POPULATION, Path(tmp) / "05-deal", env=env)
            self.assertEqual(r.returncode, 2)
            self.assertIn("05-deal.setup.sh", r.stderr)

    def test_live_without_the_tools_is_not_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, LOOP_TOOLS=tmp, LAB_DIR=str(LIVE))
            env.pop("LOOP_STEP05_DEAL_BIN", None)
            r = run_step(LIVE, Path(tmp) / "f" / "05-deal", env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            f = json.loads((Path(tmp) / "f" / "05-deal" / "finding.json").read_text())
            self.assertEqual(f["result"], "NOT_CHECKED")
            self.assertIn(step.LIVE_SETUP, {n["issue"] for n in f["not_checked"]})
            self.assertEqual(finding.check(Path(tmp) / "f"), [])


@unittest.skipIf(BIN is None or not HAS_CROSS, NO_DRIVER if BIN is None else NO_CROSS)
class Step05DealLiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = Path(self.tmp.name) / "lab"
        shutil.copytree(LIVE, self.lab)
        self.findings = Path(self.tmp.name) / "findings"
        self.out = self.findings / "05-deal"

    def tearDown(self):
        self.tmp.cleanup()

    def run_live(self):
        r = run_step(self.lab, self.out, env=dict(os.environ, LAB_DIR=str(self.lab)))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((self.out / "finding.json").read_text())

    def reseal_citing(self, **received_half):
        """Seal lab-a's citing record again with these received_half members changed (None drops one)."""
        path = self.lab / "nodes" / "lab-a" / "data" / "ledger" / "capsules.jsonl"
        old = json.loads(path.read_text())
        body = {k: v for k, v in old.items() if k not in ("capsule_id", "signature", "key_id", "chain")}
        rh = body["model_attestation"]["compute_attestation"]["received_half"]
        for k, v in received_half.items():
            rh.pop(k, None) if v is None else rh.__setitem__(k, v)
        r = subprocess.run([str(CROSS), "seal"], input=json.dumps(body).encode(), capture_output=True, check=True)
        citing = dict(json.loads(r.stdout), key_id=old["key_id"])
        path.write_text(json.dumps(citing, separators=(",", ":")) + "\n")
        return old

    def acceptance_entries(self, f):
        return [n for n in f["not_checked"] if n["what"] == step.ACCEPTANCE_WHAT]

    def test_live_held_halves_check_and_the_rest_is_not_checked_under_its_issues(self):
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        issues = {n["issue"] for n in f["not_checked"]}
        for i in ("Mesh-LLM/capsules-plugin#49", step.ACCEPTANCE_ISSUE,
                  "action-state-group/agent-action-capsule#431", step.live.NO_ISSUE_YET):
            self.assertIn(i, issues)
        checks = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertEqual(len(checks), 1)
        self.assertTrue(all(v["ok"] for v in checks[0].values() if isinstance(v, dict)), checks)
        self.assertEqual(checks[0]["accepted"]["via"], "host_announced")
        self.assertIn("node lab-b: holds no pushed half", f["detail"])
        self.assertIn("accepted the half's key via host_announced", f["detail"])
        self.assertIn({"what": "lab-b: a pushed half held", "why": "the node holds no pushed half from the lab "
                       "run's exchanges", "issue": step.live.ISSUE}, f["not_checked"])
        default = next(n for n in f["not_checked"] if n["what"] == step.LIVE_DEFAULT["what"])
        self.assertIn("capsules-plugin PR #72, in the pin", default["why"])
        [acc] = self.acceptance_entries(f)
        self.assertEqual(acc["issue"], step.ACCEPTANCE_ISSUE)
        self.assertIn("capsules-plugin PR #77, in the pin", acc["why"])
        self.assertIn("say host_announced: no node sends a controller declaration yet", acc["why"])
        self.assertIn("node lab-c: not checked", f["detail"])
        self.assertIn("would spend 0 USD", f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_held_half_whose_bytes_do_not_match_its_digest_fails(self):
        held = self.lab / "nodes" / "lab-a" / "data" / "ledger" / "received-capsules.jsonl"
        capsule = json.loads(held.read_text())
        capsule["model_attestation"]["model_id"] = "another-model"
        held.write_text(json.dumps(capsule, separators=(",", ":")) + "\n")
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("held-1 held_by_digest", f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_held_half_no_record_cites_fails(self):
        (self.lab / "nodes" / "lab-a" / "data" / "ledger" / "capsules.jsonl").write_text("")
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("held-1 cited", f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    # acceptance, read from the citing record the pinned plugin seals (capsules-plugin PR #77)
    def test_live_a_citing_record_accepting_another_key_than_the_halfs_fails(self):
        self.reseal_citing(sender_key_id="ab" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("held-1 accepted (the citing record accepted key " + "ab" * 32, f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_an_unknown_accepted_via_fails(self):
        self.reseal_citing(accepted_via="transport_id")
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("unknown accepted_via 'transport_id'", f["detail"])

    def test_live_acceptance_under_a_declaration_leaves_only_the_declarations_signatures(self):
        self.reseal_citing(accepted_via="controller_declaration", controller_binding_digest="cd" * 32,
                           controller_anchor_key_id="ef" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertEqual(self.acceptance_entries(f), [])
        self.assertIn(step.LIVE_DECLARATION, f["not_checked"])
        self.assertNotIn(step.ACCEPTANCE_ISSUE, {n["issue"] for n in f["not_checked"]})
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_declaration_anchored_on_the_nodes_own_key_fails(self):
        own = json.loads((self.lab / "nodes" / "lab-a" / "data" / "ledger" / "capsules.jsonl").read_text())["key_id"]
        self.reseal_citing(accepted_via="controller_declaration", controller_binding_digest="cd" * 32,
                           controller_anchor_key_id=own)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a declaration anchored on the node's own key", f["detail"])

    def test_live_a_declaration_without_its_digest_fails(self):
        self.reseal_citing(accepted_via="controller_declaration", controller_anchor_key_id="ef" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("no binding digest or anchor key", f["detail"])

    def test_live_a_citing_record_with_no_accepted_via_is_not_checked_never_a_pass(self):
        self.reseal_citing(accepted_via=None, sender_key_id=None)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        [acc] = self.acceptance_entries(f)
        self.assertEqual(acc["issue"], step.LIVE_NO_VIA)
        self.assertIn("sealed by a build before the pinned one", acc["why"])


if __name__ == "__main__":
    unittest.main()
