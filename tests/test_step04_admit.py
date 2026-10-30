"""steps/04-admit: the gate before the deal: a GO or NO decision in the plugin's shape, sealed in the buyer's book first.

The rehearsal runs on capsulectl and capsulectl-book (steps/04-admit.setup.sh,
through steps/12-join.setup.sh). Without them the tests that seal SKIP, saying
so: they never pass without the bundle verifier. With LOOP_REQUIRE_TOOLS=1 (CI,
after the setups ran) a missing tool is an error instead. The live tests need
nothing installed.

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

BIN = step.capsulectl()
HAS_BIN = BIN.is_file() and os.access(BIN, os.X_OK)
NO_BIN = ("SKIPPED LOUDLY: capsulectl is not installed, so the buyer's book cannot be sealed and no bundle "
          "verified; run steps/04-admit.setup.sh (needs go)")
if not HAS_BIN:
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_BIN}")
    print(f"\ntest_step04_admit: {NO_BIN}", file=sys.stderr)

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
        self.assertIn("Mesh-LLM/capsules-plugin#116", f["detail"])
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


class Live(unittest.TestCase):
    def live_run(self, lab):
        """Step 04 on `lab` (a dir holding population.json); a committed fixture dir is its own LAB_DIR."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "f" / "04-admit"
        r = run_step(lab, out, env=dict(os.environ, LAB_DIR=str(lab)))
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, load(out / "finding.json")

    def good(self):
        return [ln for ln in (LIVE / "nodes/lab-a/data/ledger/capsules.jsonl").read_text().splitlines()
                if "admission_decision" in ln]

    def lab_with(self, lines):
        """A two-node lab whose lab-a ledger holds `lines`; every node reads."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for n in ("lab-a", "lab-b"):
            d = Path(tmp.name) / "nodes" / n / "data" / "ledger"
            d.mkdir(parents=True)
            (d / "capsules.jsonl").write_text("\n".join(lines if n == "lab-a" else []) + "\n")
        (Path(tmp.name) / "population.json").write_text(json.dumps({"population": "live", "rail": None, "nodes": [
            {"name": n, "data_dir": f"$LAB_DIR/nodes/{n}/data", "book_dir": None} for n in ("lab-a", "lab-b")]}))
        return Path(tmp.name)

    def test_decisions_in_the_ledger_pass(self):
        out, f = self.live_run(self.lab_with((LIVE / 'nodes/lab-a/data/ledger/capsules.jsonl').read_text().splitlines()))
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertIn("node lab-a: 2 of 4 ledger records carry an admission decision (1 GO, 1 NO)", f["detail"])
        self.assertEqual([p["path"] for p in f["proof"]], ["lab-a/decisions.jsonl"])
        for issue in ("Mesh-LLM/capsules-plugin#116", "Mesh-LLM/mesh-llm#2061"):
            self.assertIn(issue, f["detail"])
        self.assertEqual(sorted(f["pinned"]), ["capsules", "mesh-llm"])
        self.assertEqual(finding.check(out.parent), [])

    def test_an_unreadable_node_keeps_it_not_checked(self):
        _, f = self.live_run(LIVE)
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("live node lab-c", [x["what"] for x in f["not_checked"]])
        self.assertIn("Mesh-LLM/capsules-plugin#116", [x["issue"] for x in f["not_checked"]])

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
