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
            "the controller declaration's format, which is the rehearsal's stand-in (steps/05-deal.d) until "
            "the capsules plugin's lands", "Mesh-LLM/capsules-plugin#69")])
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
        self.assertEqual(f["not_checked"][0]["issue"], "Mesh-LLM/capsules-plugin#69")
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
                self.assertEqual([n["issue"] for n in f["not_checked"]], ["Mesh-LLM/capsules-plugin#69"])
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

    def test_live_held_halves_check_and_the_rest_is_not_checked_under_its_issues(self):
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        issues = {n["issue"] for n in f["not_checked"]}
        for i in ("Mesh-LLM/capsules-plugin#49", "Mesh-LLM/capsules-plugin#68", "Mesh-LLM/capsules-plugin#69",
                  "action-state-group/agent-action-capsule#431"):
            self.assertIn(i, issues)
        checks = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertEqual(len(checks), 1)
        self.assertTrue(all(v["ok"] for v in checks[0].values() if isinstance(v, dict)), checks)
        self.assertIn("node lab-b: holds no pushed half", f["detail"])
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


if __name__ == "__main__":
    unittest.main()
