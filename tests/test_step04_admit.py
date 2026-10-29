"""steps/04-admit: GO, ASK, LIMIT or NO from the seller's history, sealed in the buyer's book before the deal.

The rehearsal runs on capsulectl and capsulectl-book (steps/04-admit.setup.sh,
through steps/12-join.setup.sh). Without them the tests that seal SKIP, saying
so: they never pass without the bundle verifier. With LOOP_REQUIRE_TOOLS=1 (CI,
after the setups ran) a missing tool is an error instead. The base policy's
tests and the live tests need only openssl.

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
base_policy, standin = step.base_policy, step.standin

BIN = step.capsulectl()
HAS_BIN = BIN.is_file() and os.access(BIN, os.X_OK)
NO_BIN = ("SKIPPED LOUDLY: capsulectl is not installed, so the buyer's book cannot be sealed and no bundle "
          "verified; run steps/04-admit.setup.sh (needs go)")
if not HAS_BIN:
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_BIN}")
    print(f"\ntest_step04_admit: {NO_BIN}", file=sys.stderr)

EXPECTED = {"t0-no-card": "GO", "go": "GO", "ask": "ASK", "no-half": "LIMIT", "self-dealt": "LIMIT",
            "adverse-with-buyer": "NO", "adverse-other": "LIMIT"}
# Each planted failure, the scenario it is planted in, and the check that must catch it.
PLANTED = {
    "go-no-half": ("no-half", "no_half_above_t0"),
    "ask-two": ("ask", "ask_is_one_request"),
    "ask-none": ("ask", "ask_is_one_request"),
    "unsealed": ("go", "decision_sealed"),
    "sealed-after": ("go", "sealed_before_deal"),
    "self-dealt-counted": ("self-dealt", "self_dealt_excluded"),
    "adjudication-ignored": ("adverse-with-buyer", "adjudication_read"),
}


def run_step(population, out, *extra, env=None):
    return subprocess.run([sys.executable, "-B", str(STEP), "--population", str(population), "--out", str(out),
                           *extra], capture_output=True, text=True, env=env)


def load(path):
    return json.loads(Path(path).read_text())


def failed_checks(out):
    return {(n, c["check"]) for n, cs in load(Path(out) / "checks.json").items() for c in cs if not c["ok"]}


class BasePolicy(unittest.TestCase):
    """The base policy on the fixture stand-ins: openssl only."""

    def setUp(self):
        self.policy, _ = base_policy.load_policy()
        self.s04 = POPULATION / "step04"
        self.decls, self.referees = step.trust(self.s04)
        self.buyer = standin.pub_hex(POPULATION / "keys" / "bob" / "node-key.pem")

    def decide(self, name, card=None, adjs=None):
        deal, c, a = step.scenario_inputs(self.s04 / "scenarios" / name)
        return base_policy.evaluate(self.policy, deal, c if card is None else card, a if adjs is None else adjs,
                                    self.decls, self.referees, self.buyer)

    def test_each_scenario_gives_its_outcome(self):
        for name, want in EXPECTED.items():
            with self.subTest(name):
                d = self.decide(name)
                self.assertEqual(d["outcome"], want)
                self.assertEqual(len(d["asks"]), 1 if want == "ASK" else 0)

    def test_no_half_above_t0_limits_to_t0(self):
        d = self.decide("no-half")
        self.assertEqual((d["tier"], d["outcome"], d["limit_to"], d["claims_held"]), ("T1", "LIMIT", "T0", 0))

    def test_self_dealt_halves_are_not_claims_held(self):
        d = self.decide("self-dealt")
        self.assertEqual((d["claims_held"], d["self_dealt_excluded"], d["outcome"]), (0, 2, "LIMIT"))

    def test_an_adjudication_from_a_key_that_is_not_a_referee_is_not_counted(self):
        d = self.decide("go")
        self.assertEqual(d["outcome"], "GO")
        self.assertEqual(len(d["facts"]["adjudications_not_counted"]), 1)
        self.assertEqual(len(d["facts"]["adjudications_counted"]), 1)

    def test_a_card_whose_signature_does_not_hold_is_read_as_absent(self):
        _, card, _ = step.scenario_inputs(self.s04 / "scenarios" / "go")
        card = json.loads(json.dumps(card))
        card["body"]["checkpoint_at"] = "2026-10-28T07:00:00Z"
        self.assertFalse(standin.verify(card)[0])
        d = self.decide("go", card=card)
        self.assertEqual((d["outcome"], d["claims_held"]), ("LIMIT", 0))

    def test_an_adverse_adjudication_with_a_forged_signature_is_not_counted(self):
        _, _, adjs = step.scenario_inputs(self.s04 / "scenarios" / "adverse-with-buyer")
        forged = json.loads(json.dumps(adjs))
        forged[0]["body"]["result"]["outcome"] = "for_seller"
        self.assertEqual(self.decide("adverse-with-buyer", adjs=forged)["outcome"], "GO")

    def test_the_stand_ins_reproduce_from_their_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(POPULATION, Path(tmp) / "p")
            pop = load(POPULATION / "population.json")
            subprocess.run([sys.executable, "-B", str(ROOT / "population/rehearsal/step04/make_standins.py"),
                            str(Path(tmp) / "p")], check=True, capture_output=True)
            for f in (POPULATION / "step04").rglob("*.json"):
                self.assertEqual(f.read_bytes(), (Path(tmp) / "p" / "step04" / f.relative_to(POPULATION / "step04"))
                                 .read_bytes(), f)
            self.assertEqual(pop["population"], "rehearsal")


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

    def test_rehearsal_passes_with_each_outcome(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr)
        f = load(self.out / "finding.json")
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(failed_checks(self.out), set())
        for name, want in EXPECTED.items():
            bundle = load(self.out / "scenarios" / name / "bundle.json")
            outcomes = [d["agent_input"].get("outcome") for d in bundle["disclosures"].values()
                        if d["agent_input"].get("kind") == "admission_decision"]
            self.assertEqual(outcomes, [want], name)
        self.assertEqual(sorted(f["pinned"]), ["capsulectl", "capsulectl-book"])
        self.assertIn("would spend 0 USD", f["detail"])
        self.assertTrue(finding.gaps(f["detail"]))

    def test_counts_give_both_numbers(self):
        c = load(self.out / "counts.json")
        self.assertEqual((c["limited_or_refused"], c["decisions"]), (4, 7))
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
                # Only the planted scenario, beside the population's trust files and keys.
                pop = Path(tmp) / "p"
                shutil.copytree(POPULATION, pop, ignore=lambda d, names: [
                    n for n in names if Path(d).name == "scenarios" and n != scenario])
                out = Path(tmp) / "f" / "04-admit"
                r = run_step(pop, out, "--plant", plant)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(load(out / "finding.json")["result"], "FAIL")
                self.assertIn((scenario, check), failed_checks(out))
                self.assertEqual(finding.check(out.parent), [])

    def test_a_decision_altered_in_the_bundle_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = Path(tmp) / "no-half"
            shutil.copytree(self.out / "scenarios" / "no-half", sd)
            bundle = load(sd / "bundle.json")
            for d in bundle["disclosures"].values():
                if d["agent_input"].get("kind") == "admission_decision":
                    d["agent_input"]["outcome"] = "GO"
            (sd / "bundle.json").write_text(json.dumps(bundle))
            verdict = step.verify_bundle(BIN, tmp, sd / "bundle.json")
            policy, digest = load(self.out / "policy.json"), step.sha256((self.out / "policy.json").read_bytes())
            decls, referees = step.trust(self.out)
            buyer = standin.pub_hex(POPULATION / "keys" / "bob" / "node-key.pem")
            checks, _ = step.judge(sd, verdict, policy, digest, buyer, decls, referees)
            failed = {c["check"] for c in checks if not c["ok"]}
            self.assertIn("bundle_verifies", failed)
            self.assertIn("decision_sealed", failed)

    def test_rehearsal_without_capsulectl_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_step(POPULATION, Path(tmp) / "04-admit",
                         env=dict(os.environ, LOOP_TOOLS=tmp, CAPSULECTL=str(Path(tmp) / "none")))
            self.assertEqual(r.returncode, 2)
            self.assertIn("steps/04-admit.setup.sh", r.stderr)


class Live(unittest.TestCase):
    def test_live_is_not_checked_under_its_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "f" / "04-admit"
            r = run_step(LIVE, out, env=dict(os.environ, LAB_DIR=str(LIVE)))
            self.assertEqual(r.returncode, 0, r.stderr)
            f = load(out / "finding.json")
            self.assertEqual(f["result"], "NOT_CHECKED")
            issues = [x["issue"] for x in f["not_checked"]]
            for issue in ("Mesh-LLM/capsules-plugin#55", "Mesh-LLM/capsules-plugin#64",
                          "action-state-group/evidencebook-skills#51"):
                self.assertIn(issue, issues)
            self.assertIn("live node lab-c", [x["what"] for x in f["not_checked"]])
            self.assertIn("node lab-a: 0 of 2 ledger records", f["detail"])
            self.assertEqual(sorted(f["pinned"]), ["capsules", "mesh-llm", "mesh-llm-pr-2620"])
            self.assertEqual(finding.check(out.parent), [])


class StatusLimitedRow(unittest.TestCase):
    """The status page's `limited` number reads step 04's counts.json, only from a valid PASS: both numbers or neither."""

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
        number = next(n for n in status_page.NUMBERS if n.key == "limited")
        return status_page.number_value(number, run)

    def test_a_pass_shows_both_numbers(self):
        self.assertEqual(self.run_with("PASS", {"limited_or_refused": 4, "decisions": 7}), ("4 of 7 decisions", True))

    def test_anything_but_a_valid_pass_shows_neither(self):
        placeholder = ("not measured yet (M3)", False)
        self.assertEqual(self.run_with("FAIL", {"limited_or_refused": 4, "decisions": 7}), placeholder)
        self.assertEqual(self.run_with("NOT_CHECKED", {"limited_or_refused": 4, "decisions": 7}, proof=()),
                         placeholder)
        self.assertEqual(self.run_with("PASS", {"decisions": 7}), placeholder)
        self.assertEqual(self.run_with("PASS", {"limited_or_refused": 8, "decisions": 7}), placeholder)
        self.assertEqual(self.run_with("PASS", {"limited_or_refused": 0, "decisions": 0}), placeholder)
        self.assertEqual(self.run_with("PASS", {}, proof=("other.txt",)), placeholder)


if __name__ == "__main__":
    unittest.main()
