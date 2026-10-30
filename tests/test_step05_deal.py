"""steps/05-deal: both halves of one deal, pushed, held by digest and cited, checked on the proof bytes.

The rehearsal verdicts come from the driver steps/05-deal.setup.sh installs (a
Rust build of evidencebook's crate at the pinned tag); the live ones also need
the AAC Go verifier step 01's setup installs. Without them these tests SKIP,
saying so: they never pass without the verifier. With LOOP_REQUIRE_TOOLS=1
(CI, after the setups ran) a missing tool is an error instead.
"""
import copy
import hashlib
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
        self.assertIn("output digests differ or are missing", notes[0])

    def test_two_halves_with_no_output_digest_are_not_an_exchange(self):
        paired, notes = step.paired_exchanges({"n1": [("x1", "requested", None)], "n2": [("x1", "served", None)]})
        self.assertEqual(paired, set())
        self.assertIn("missing", notes[0])

    def test_a_malformed_half_is_named_not_raised_on(self):
        def half(**x):
            return {"model_attestation": {"compute_attestation": {"agent_output_digest": ["d"],
                                                                  "x-mesh-poc-v1": x}}}
        keys = [step.exchange_key(half(client_nonce=["a"], role={"r": 1})), step.exchange_key(half(client_nonce="")),
                step.exchange_key({}), step.exchange_key({"model_attestation": "x"}),
                step.exchange_key({"model_attestation": {"compute_attestation": []}})]
        self.assertEqual(keys, [(None, None, None)] * 5)
        paired, notes = step.paired_exchanges({"n1": keys})
        self.assertEqual((paired, len(notes)), (set(), 5))

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
        self.nodes = Path(self.tmp.name) / "nodes"
        self.nodes.mkdir()
        self.make_current()

    def tearDown(self):
        self.tmp.cleanup()

    def publish_binding(self, digest):
        """lab-b, the sender of the half lab-a holds, lists its key and publishes a commitment (loop#88)
        carrying this controller_binding.digest (None: a commitment with no binding)."""
        half = json.loads((self.lab / "nodes" / "lab-a" / "data" / "ledger" / "received-capsules.jsonl").read_text())
        node_id = "b1" * 32
        (self.nodes / f"{node_id}.json").write_text(json.dumps({"node_id": node_id, "key_id": half["key_id"]}))
        (self.nodes / "commitments").mkdir(exist_ok=True)
        doc = {} if digest is None else {"controller_binding": {"digest": digest}}
        (self.nodes / "commitments" / f"{node_id}.json").write_text(json.dumps(doc))

    def run_live(self, **env):
        r = run_step(self.lab, self.out, env=dict(os.environ, LAB_DIR=str(self.lab),
                                                  LOOP_NODES_DIR=str(self.nodes), **env))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((self.out / "finding.json").read_text())

    def ledger(self):
        return self.lab / "nodes" / "lab-a" / "data" / "ledger"

    def seal(self, body, key_id):
        r = subprocess.run([str(CROSS), "seal"], input=json.dumps(body).encode(), capture_output=True, check=True)
        return dict(json.loads(r.stdout), key_id=key_id)

    def lines(self):
        return [json.loads(ln) for ln in (self.ledger() / "capsules.jsonl").read_text().splitlines() if ln.strip()]

    def write_lines(self, records):
        (self.ledger() / "capsules.jsonl").write_text(
            "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))

    def body(self, record):
        return copy.deepcopy({k: v for k, v in record.items() if k not in ("capsule_id", "signature", "key_id", "chain")})

    def citing(self):
        return next(r for r in self.lines()
                    if any(x.get("citation_purpose") == "counterparty_half" for x in r.get("references") or []))

    def make_current(self):
        """Lab-a as the current build seals it: its own half of the exchange (nonce n, requested), a citing
        record naming it, and a counterparty_inclusion record citing the checkpoint the half was pushed under."""
        inc_path = self.ledger() / "received-inclusion.jsonl"
        inc = json.loads(inc_path.read_text())
        cp_file, proof_file = Path(self.tmp.name) / "cp.json", Path(self.tmp.name) / "proof.json"
        cp_file.write_text(json.dumps(inc["checkpoint"]))
        proof_file.write_text(json.dumps(inc["inclusion_proof"]))
        held = json.loads((self.ledger() / "received-capsules.jsonl").read_text())
        r = subprocess.run([str(BIN), "held", "--record-id", held["capsule_id"], "--key-id", held["key_id"],
                            "--checkpoint", str(cp_file), "--inclusion", str(proof_file)], capture_output=True, text=True)
        self.digest = json.loads(r.stdout)["checkpoint_digest"]
        inc["checkpoint_digest"] = self.digest
        inc_path.write_text(json.dumps(inc) + "\n")
        citing = self.citing()
        key_id = citing["key_id"]
        ca = citing["model_attestation"]["compute_attestation"]
        # the fixture's citing record carries a copy of the half's attestation; the plugin's has only these
        citing = self.seal({**self.body(citing), "model_attestation": {**citing["model_attestation"], "compute_attestation": {
            "received_half": ca["received_half"], "store_nonce": ca.get("store_nonce", "00" * 32)}}}, key_id)
        half = json.loads((self.ledger() / "received-capsules.jsonl").read_text())
        own_body = self.body(citing)
        own_body["action_id"] = "mesh-poc/own-half"
        own_body["references"] = []
        own_body["model_attestation"] = {"model_id": "m", "provider": "mesh-llm", "compute_attestation": {
            "agent_output_digest": half["model_attestation"]["compute_attestation"]["agent_output_digest"],
            "x-mesh-poc-v1": {"client_nonce": "n", "role": "requested"}}}
        self.own = self.seal(own_body, key_id)
        incl_body = self.body(citing)
        incl_body["action_id"] = "mesh-poc/counterparty-inclusion-citation/" + half["capsule_id"]
        incl_body["model_attestation"] = {"model_id": "n/a", "provider": "mesh-llm", "compute_attestation": {
            "counterparty_inclusion": {"half_capsule_id": half["capsule_id"]}}}
        incl_body["references"] = [{"type": "cll-checkpoint", "digest_alg": "SHA-256", "digest": self.digest,
                                    "citation_purpose": "counterparty_inclusion"}]
        self.write_lines([citing, self.own, self.seal(incl_body, key_id)])
        self.reseal_citing(own_half_capsule_id=self.own["capsule_id"])

    def reseal_citing(self, **received_half):
        """Seal lab-a's citing record again with these received_half members changed (None drops one)."""
        records = self.lines()
        old = self.citing()
        body = self.body(old)
        rh = body["model_attestation"]["compute_attestation"]["received_half"]
        for k, v in received_half.items():
            rh.pop(k, None) if v is None else rh.__setitem__(k, v)
        self.write_lines([self.seal(body, old["key_id"]) if r == old else r for r in records])
        return old

    def set_own(self, mutate):
        """Re-seal the own half after `mutate(body)` and point the citing record at it."""
        body = self.body(self.own)
        mutate(body)
        new = self.seal(body, self.own["key_id"])
        self.write_lines([new if r == self.own else r for r in self.lines()])
        self.own = new
        self.reseal_citing(own_half_capsule_id=new["capsule_id"])

    def acceptance_entries(self, f):
        return [n for n in f["not_checked"] if n["what"] in (step.ACCEPTANCE_WHAT, step.LIVE_UNBOUND_WHAT)]

    def test_live_held_halves_check_and_the_rest_is_not_checked_under_its_issues(self):
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        issues = {n["issue"] for n in f["not_checked"]}
        for i in (step.LIVE_UNBOUND_ISSUE, "action-state-group/agent-action-capsule#431"):
            self.assertIn(i, issues)
        # the closed refs are gone, and a tied, bound-or-unbound lab is not a plugin gap
        closed = json.loads((ROOT / "tests" / "fixtures" / "closed_issues.json").read_text())["closed"]
        self.assertEqual(issues & set(closed), set())
        self.assertIn("each node seals a record naming both halves and cites the other side's checkpoint", f["asserts"])
        self.assertNotIn(step.LIVE_NEVER_TIED, {n["what"] for n in f["not_checked"]})
        self.assertIn("1 tied to the node's own half", f["detail"])
        checks = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertEqual(len(checks), 1)
        self.assertTrue(all(v["ok"] for v in checks[0].values() if isinstance(v, dict)), checks)
        self.assertEqual(checks[0]["accepted"]["via"], "host_announced")
        self.assertIn("node lab-b: holds no pushed half", f["detail"])
        self.assertIn("accepted the half's key via host_announced", f["detail"])
        self.assertIn({"what": "lab-b: a pushed half held", "why": "the node holds no pushed half from the lab "
                       "run's exchanges", "issue": step.live.ISSUE}, f["not_checked"])
        default = next(n for n in f["not_checked"] if n["what"] == step.LIVE_DEFAULT_ABSENT["what"])
        self.assertIn("lab-a", default["why"])
        [acc] = self.acceptance_entries(f)
        self.assertEqual(acc["what"], step.LIVE_UNBOUND_WHAT)
        self.assertEqual(acc["issue"], step.LIVE_UNBOUND_ISSUE)
        self.assertIn("1 current-build halves accepted via host_announced", acc["why"])
        self.assertIn("the sender's missing binding, not a plugin gap", acc["why"])
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
        self.assertNotIn(step.LIVE_UNBOUND_ISSUE, {n["issue"] for n in f["not_checked"]})
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_host_announced_key_with_the_published_binding_digest_counts_as_bound(self):
        self.publish_binding("cd" * 32)
        self.reseal_citing(controller_binding_digest="cd" * 32, same_controller_as_receiver=False)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertEqual(self.acceptance_entries(f), [])
        self.assertIn(step.LIVE_DECLARATION, f["not_checked"])
        self.assertIn("accepted the half's key via host_announced", f["detail"])
        checks = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertTrue(checks[0]["accepted"]["bound"] and checks[0]["accepted"]["ok"])
        [node_file] = (self.nodes / "commitments").glob("*.json")
        self.assertEqual(checks[0]["accepted"]["commitment"],
                         {"path": f"nodes/commitments/{node_file.name}",
                          "sha256": hashlib.sha256(node_file.read_bytes()).hexdigest()})
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_bound_key_with_no_same_controller_member_still_counts(self):
        self.publish_binding("cd" * 32)
        self.reseal_citing(controller_binding_digest="cd" * 32)
        self.assertEqual(self.acceptance_entries(self.run_live()), [])

    def test_live_a_host_announced_key_with_no_digest_stays_not_checked(self):
        self.publish_binding("cd" * 32)
        f = self.run_live()
        self.assertEqual(len(self.acceptance_entries(f)), 1)

    def test_live_a_digest_the_commitment_does_not_carry_stays_not_checked(self):
        self.publish_binding("ab" * 32)
        self.reseal_citing(controller_binding_digest="cd" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertEqual(len(self.acceptance_entries(f)), 1)
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertFalse(c["accepted"]["bound"])
        self.assertIn("which the sender's published commitment does not carry", c["accepted"]["detail"])

    def test_live_a_digest_with_no_published_binding_or_commitment_stays_not_checked(self):
        self.reseal_citing(controller_binding_digest="cd" * 32)
        self.assertEqual(len(self.acceptance_entries(self.run_live())), 1)
        self.publish_binding(None)
        self.assertEqual(len(self.acceptance_entries(self.run_live())), 1)

    def test_live_a_counterparty_bound_to_the_receivers_own_controller_fails(self):
        self.publish_binding("cd" * 32)
        self.reseal_citing(controller_binding_digest="cd" * 32, same_controller_as_receiver=True)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a counterparty whose key is bound to the receiver's own controller", f["detail"])

    def test_live_a_same_controller_counterparty_fails_even_with_no_accepted_via(self):
        self.reseal_citing(accepted_via=None, same_controller_as_receiver=True)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a counterparty whose key is bound to the receiver's own controller", f["detail"])

    def test_live_a_node_entry_with_no_commitment_file_stays_not_checked(self):
        self.publish_binding("cd" * 32)
        for f in (self.nodes / "commitments").glob("*.json"):
            f.unlink()
        self.reseal_citing(controller_binding_digest="cd" * 32)
        self.assertEqual(len(self.acceptance_entries(self.run_live())), 1)

    def test_live_a_declaration_with_a_digest_and_no_anchor_key_is_accepted(self):
        self.reseal_citing(accepted_via="controller_declaration", controller_binding_digest="cd" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertEqual(self.acceptance_entries(f), [])
        self.assertIn(step.LIVE_DECLARATION, f["not_checked"])

    def test_live_a_declaration_with_a_malformed_anchor_key_fails(self):
        self.reseal_citing(accepted_via="controller_declaration", controller_binding_digest="cd" * 32,
                           controller_anchor_key_id="zz")
        self.assertEqual(self.run_live()["result"], "FAIL")

    def test_live_a_declaration_anchored_on_the_nodes_own_key_fails(self):
        own = self.citing()["key_id"]
        self.reseal_citing(accepted_via="controller_declaration", controller_binding_digest="cd" * 32,
                           controller_anchor_key_id=own)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a declaration anchored on the node's own key", f["detail"])

    def test_live_a_declaration_without_its_digest_fails(self):
        self.reseal_citing(accepted_via="controller_declaration", controller_anchor_key_id="ef" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("no binding digest or a malformed anchor key", f["detail"])

    def test_live_a_citing_record_with_no_accepted_via_is_not_checked_never_a_pass(self):
        self.reseal_citing(accepted_via=None, sender_key_id=None)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        [acc] = self.acceptance_entries(f)
        self.assertEqual(acc["issue"], step.LIVE_NO_VIA)
        self.assertIn("sealed by a build before the pinned one", acc["why"])

    # the tie, loop#107: a record naming both halves, the checkpoint cited, "held, never tied"
    def names(self, f):
        return {n["what"]: n for n in f["not_checked"]}

    def test_live_a_half_tied_to_its_own_half_leaves_no_untied_gap(self):
        f = self.run_live()
        self.assertNotIn(step.LIVE_NEVER_TIED, self.names(f))
        self.assertNotIn("a tie that is ambiguous at verify time", self.names(f))
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertEqual(c["tied"]["state"], "tied")
        self.assertTrue(c["inclusion_cited"]["ok"])
        self.assertIn("1 tied to the node's own half, 0 held, never tied, 0 ambiguous", f["detail"])

    def test_live_a_citing_record_with_no_own_half_is_held_never_tied(self):
        self.reseal_citing(own_half_capsule_id=None)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        gap = self.names(f)[step.LIVE_NEVER_TIED]
        self.assertIn("1 current-build halves are cited by a record with no own_half_capsule_id", gap["why"])
        self.assertIn("0 tied to the node's own half, 1 held, never tied", f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_tie_naming_an_own_half_the_chain_does_not_hold_fails(self):
        self.reseal_citing(own_half_capsule_id="ab" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("which the node's chain does not hold", f["detail"])

    def test_live_a_tie_to_an_own_half_of_the_same_role_or_another_nonce_fails(self):
        def role(body):
            body["model_attestation"]["compute_attestation"]["x-mesh-poc-v1"]["role"] = "served"
        self.set_own(role)
        self.assertIn("not the opposite role", self.run_live()["detail"])
        self.setUp()
        def nonce(body):
            body["model_attestation"]["compute_attestation"]["x-mesh-poc-v1"]["client_nonce"] = "other"
        self.set_own(nonce)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("not the opposite role", f["detail"])

    def test_live_a_second_own_half_under_the_nonce_makes_the_tie_ambiguous(self):
        twin = self.body(self.own)
        twin["action_id"] = "mesh-poc/own-half-twin"
        self.write_lines(self.lines() + [self.seal(twin, self.own["key_id"])])
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        gap = self.names(f)["a tie that is ambiguous at verify time"]
        self.assertIn("1 citing records name an own half", gap["why"])
        self.assertIn("1 ambiguous", f["detail"])

    def test_live_a_second_held_half_under_the_nonce_makes_the_tie_ambiguous(self):
        held = self.ledger() / "received-capsules.jsonl"
        first = json.loads(held.read_text())
        second = self.body(first)
        second["action_id"] = "mesh-poc/second"
        sealed = self.seal(second, first["key_id"])
        held.write_text(held.read_text() + json.dumps(sealed, separators=(",", ":")) + "\n")
        f = self.run_live()
        self.assertIn("a tie that is ambiguous at verify time", self.names(f))

    def test_live_an_inclusion_line_with_a_digest_the_checkpoint_does_not_have_fails(self):
        path = self.ledger() / "received-inclusion.jsonl"
        inc = json.loads(path.read_text())
        inc["checkpoint_digest"] = "ee" * 32
        path.write_text(json.dumps(inc) + "\n")
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("recomputed from the held checkpoint", f["detail"])

    def test_live_an_inclusion_record_altered_after_sealing_fails(self):
        records = self.lines()
        for r in records:
            if "counterparty_inclusion" in (r["model_attestation"].get("compute_attestation") or {}):
                r["action_id"] += "-altered"
        self.write_lines(records)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("held-1 inclusion_cited", f["detail"])
        self.assertIn("recomputed", f["detail"])

    def test_live_a_driver_that_prints_no_checkpoint_digest_stops_the_step(self):
        old = self.tmp.name + "/old-driver"
        Path(old).write_text("#!/bin/sh\ncase $1 in held) echo '{\"ok\": true, \"detail\": \"x\"}';; esac\n")
        os.chmod(old, 0o755)
        r = run_step(self.lab, self.out, env=dict(os.environ, LAB_DIR=str(self.lab), LOOP_NODES_DIR=str(self.nodes),
                                                  LOOP_STEP05_DEAL_BIN=old))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("steps/05-deal.setup.sh", r.stderr)

    def test_live_an_unknown_role_is_not_the_opposite_of_served(self):
        def role(body):
            body["model_attestation"]["compute_attestation"]["x-mesh-poc-v1"]["role"] = "observer"
        self.set_own(role)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("not the opposite role", f["detail"])

    def test_live_a_cutoff_that_is_not_an_rfc3339_time_with_a_zone_stops_the_step(self):
        for bad in ("yesterday", "2026-10-30T00:00:00", ""):
            r = run_step(self.lab, self.out, env=dict(os.environ, LAB_DIR=str(self.lab), LOOP_NODES_DIR=str(self.nodes),
                                                      LOOP_SEALED_SINCE=bad))
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("LOOP_SEALED_SINCE", r.stderr)

    def test_live_a_half_sealed_exactly_at_the_cutoff_is_current(self):
        stamp = self.citing()["timestamp"]
        f = self.run_live(LOOP_SEALED_SINCE=stamp)
        self.assertIn("1 halves, 1 tied", f["detail"])

    def test_live_no_counterparty_inclusion_record_for_a_current_half_fails(self):
        self.write_lines([r for r in self.lines() if "counterparty_inclusion" not in
                          (r["model_attestation"].get("compute_attestation") or {})])
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("held-1 inclusion_cited", f["detail"])

    # judged only under the current build
    def test_live_an_earlier_half_is_counted_and_not_judged_for_acceptance_or_tie(self):
        self.reseal_citing(own_half_capsule_id=None)
        stamp = self.citing()["timestamp"]
        f = self.run_live(LOOP_SEALED_SINCE="2099-01-01T00:00:00Z")
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        names = self.names(f)
        self.assertNotIn(step.LIVE_NEVER_TIED, names)
        [acc] = self.acceptance_entries(f)
        self.assertEqual(acc["what"], step.ACCEPTANCE_WHAT)
        self.assertIn("0 halves, 0 tied", f["detail"])
        self.assertIn("1 sealed earlier, not judged", f["detail"])
        self.assertIn("no node holds a current-build half", acc["why"])
        self.assertIn("sealed since 2099-01-01T00:00:00Z", f["detail"])
        self.assertEqual(json.loads((self.out / "lab-a" / "checks.json").read_text())["sealed_since"], "2099-01-01T00:00:00Z")
        # the same half is judged when the build started before it was sealed
        f = self.run_live(LOOP_SEALED_SINCE="2000-01-01T00:00:00Z")
        self.assertIn(step.LIVE_NEVER_TIED, self.names(f))
        self.assertTrue(stamp)

    def test_live_an_earlier_half_still_fails_on_its_bytes(self):
        held = self.ledger() / "received-capsules.jsonl"
        capsule = json.loads(held.read_text())
        capsule["model_attestation"]["model_id"] = "another-model"
        held.write_text(json.dumps(capsule, separators=(",", ":")) + "\n")
        self.assertEqual(self.run_live(LOOP_SEALED_SINCE="2099-01-01T00:00:00Z")["result"], "FAIL")

    # the node's own report of its sharing policy, loop#109
    def test_live_sharing_effective_is_read_as_node_reported_unsigned(self):
        (self.lab / "nodes" / "lab-a" / "data" / "sharing-effective.json").write_text(json.dumps(
            {"v": 1, "share_record_at_completion": "counterparty", "source": "default",
             "written_at": "2026-10-30T01:00:00Z"}))
        f = self.run_live()
        self.assertIn("node lab-a reports (node-reported, unsigned) share_record_at_completion counterparty from "
                      "default at 2026-10-30T01:00:00Z", f["detail"])
        self.assertIn("the push itself is judged from the sealed records", f["detail"])
        self.assertIn("lab-a/sharing-effective.json", [p["path"] for p in f["proof"]])
        self.assertEqual(self.names(f)[step.LIVE_DEFAULT_ABSENT["what"]]["why"].count("lab-b"), 1)
        self.assertNotIn("lab-a", self.names(f)[step.LIVE_DEFAULT_ABSENT["what"]]["why"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_an_unknown_value_the_plugin_resolved_to_off_shows_its_raw_value(self):
        (self.lab / "nodes" / "lab-a" / "data" / "sharing-effective.json").write_text(json.dumps(
            {"v": 1, "share_record_at_completion": "off", "source": "console", "written_at": "t", "raw_value": "everyone"}))
        self.assertIn("raw value 'everyone' is not one the plugin knows", self.run_live()["detail"])

    def test_live_an_unknown_sharing_value_is_not_read_as_a_report(self):
        (self.lab / "nodes" / "lab-a" / "data" / "sharing-effective.json").write_text(
            json.dumps({"v": 1, "share_record_at_completion": "everyone"}))
        self.assertNotIn("node-reported", self.run_live()["detail"])


if __name__ == "__main__":
    unittest.main()
