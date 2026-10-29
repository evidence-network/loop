"""steps/04-admit: GO or NO from the Terms Pack, sealed in the buyer's book before the deal.

The rehearsal runs on capsulectl and capsulectl-book (steps/04-admit.setup.sh,
through steps/12-join.setup.sh). Without them the tests that seal SKIP, saying
so: they never pass without the bundle verifier. With LOOP_REQUIRE_TOOLS=1 (CI,
after the setups ran) a missing tool is an error instead. The Terms Pack's
tests and the live tests need nothing installed.

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
terms = step.terms

BIN = step.capsulectl()
HAS_BIN = BIN.is_file() and os.access(BIN, os.X_OK)
NO_BIN = ("SKIPPED LOUDLY: capsulectl is not installed, so the buyer's book cannot be sealed and no bundle "
          "verified; run steps/04-admit.setup.sh (needs go)")
if not HAS_BIN:
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_BIN}")
    print(f"\ntest_step04_admit: {NO_BIN}", file=sys.stderr)

EXPECTED = {"go": "GO", "no-price": "NO", "no-amount": "NO", "go-adjusted": "GO"}
# Each planted failure, the scenario it is planted in, and the check that must catch it.
PLANTED = {
    "go-over-bound": ("no-price", "follows_terms"),
    "ask-outcome": ("go", "one_outcome"),
    "limit-outcome": ("go", "one_outcome"),
    "unsealed": ("go", "decision_sealed"),
    "sealed-after": ("go", "sealed_before_deal"),
    "deal-after-no": ("no-price", "no_deal_after_no"),
    "digest-mismatch": ("go", "pack_digest"),
}


def run_step(population, out, *extra, env=None):
    return subprocess.run([sys.executable, "-B", str(STEP), "--population", str(population), "--out", str(out),
                           *extra], capture_output=True, text=True, env=env)


def load(path):
    return json.loads(Path(path).read_text())


def failed_checks(out):
    return {(n, c["check"]) for n, cs in load(Path(out) / "checks.json").items() for c in cs if not c["ok"]}


def offer(price, amount, unit="USDC"):
    return {"deal_id": "t", "price_per_inference": {"value": price, "unit": unit},
            "deal_amount": {"value": amount, "unit": unit}}


class TermsPack(unittest.TestCase):
    """The Terms Pack on offers: nothing installed."""

    def setUp(self):
        self.pack, self.digest = terms.load()

    def unmet(self, o):
        d = terms.evaluate(self.pack, o)
        return d["outcome"], [r["id"] for r in d["rules"] if r["verdict"] == "unmet"]

    def test_each_scenario_gives_its_outcome(self):
        for name, want in EXPECTED.items():
            with self.subTest(name):
                d = POPULATION / "step04" / "scenarios" / name
                pack, _ = terms.load(d / "terms.json") if (d / "terms.json").is_file() else (self.pack, None)
                self.assertEqual(terms.evaluate(pack, load(d / "offer.json"))["outcome"], want)

    def test_the_default_bounds(self):
        self.assertEqual(self.unmet(offer("0.01", "10")), ("GO", []))
        self.assertEqual(self.unmet(offer("0.0100001", "10")), ("NO", ["price-usdc"]))
        self.assertEqual(self.unmet(offer("0.01", "10.01")), ("NO", ["amount-usdc"]))
        self.assertEqual(self.unmet(offer("10", "10000", "sats")), ("GO", []))
        self.assertEqual(self.unmet(offer("11", "10001", "sats")), ("NO", ["price-sats", "amount-sats"]))

    def test_every_rule_is_a_row_and_only_go_or_no(self):
        d = terms.evaluate(self.pack, offer("0.002", "2"))
        self.assertEqual([r["id"] for r in d["rules"]], ["price-usdc", "price-sats", "amount-usdc", "amount-sats"])
        self.assertEqual([r["verdict"] for r in d["rules"]], ["met", "not_applicable", "met", "not_applicable"])
        self.assertEqual(set(d), {"outcome", "rules"})
        self.assertEqual(terms.OUTCOMES, ("GO", "NO"))

    def test_a_number_the_pack_cannot_read_is_no(self):
        o = offer("0.002", "2")
        del o["deal_amount"]
        self.assertEqual(self.unmet(o), ("NO", ["deal_amount-stated"]))
        self.assertEqual(self.unmet(offer("1e-3", "2")), ("NO", ["price_per_inference-stated"]))
        self.assertEqual(self.unmet(offer("-1", "2")), ("NO", ["price_per_inference-stated"]))
        self.assertEqual(self.unmet(offer("0.002", "2", "EUR")), ("NO", ["price_per_inference-unit", "deal_amount-unit"]))

    def test_the_digest_is_of_the_pack_bytes(self):
        self.assertEqual(self.digest, step.sha256(terms.DEFAULT.read_bytes()))

    def test_a_malformed_pack_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ({"pack": "p", "version": "1", "rules": []},
                        {"pack": "p", "version": "1", "rules": [{"id": "a", "field": "f", "op": "~", "bound": "1",
                                                                  "unit": "USDC"}]},
                        {"pack": "p", "version": "1", "rules": [{"id": "a", "field": "f", "op": "<=", "bound": 1,
                                                                  "unit": "USDC"}]}):
                p = Path(tmp) / "t.json"
                p.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    terms.load(p)


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
            self.assertEqual([d["outcome"] for d in self.sealed(self.out, name)], [want], name)
        self.assertEqual(sorted(f["pinned"]), ["capsulectl", "capsulectl-book"])
        self.assertIn("would spend 0 USD", f["detail"])
        self.assertIn("Mesh-LLM/capsules-plugin#105", f["detail"])
        self.assertTrue(finding.gaps(f["detail"]))

    def test_the_adjusted_pack_is_the_one_named(self):
        default = step.sha256(terms.DEFAULT.read_bytes())
        adjusted = step.sha256((POPULATION / "step04/scenarios/go-adjusted/terms.json").read_bytes())
        self.assertNotEqual(default, adjusted)
        self.assertEqual(self.sealed(self.out, "go-adjusted")[0]["pack"]["digest"], adjusted)
        self.assertEqual(self.sealed(self.out, "no-price")[0]["pack"]["digest"], default)

    def test_a_no_has_no_deal_record(self):
        for name in ("no-price", "no-amount"):
            bundle = load(self.out / "scenarios" / name / "bundle.json")
            kinds = [d["agent_input"].get("kind") for d in bundle["disclosures"].values()]
            self.assertNotIn("deal_record", kinds, name)

    def test_counts_give_both_numbers(self):
        c = load(self.out / "counts.json")
        self.assertEqual((c["refused"], c["decisions"]), (2, 4))
        self.assertEqual(c["by_outcome"], {"GO": 2, "NO": 2})
        self.assertIn("counts.json", [p["path"] for p in load(self.out / "finding.json")["proof"]])

    def test_the_finding_passes_the_check(self):
        self.assertEqual(finding.check(self.out.parent), [])

    def test_the_repository_rehearsal_population_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "04-admit"
            r = run_step(ROOT / "population" / "rehearsal", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(load(out / "finding.json")["result"], "PASS")

    def test_terms_flag_turns_a_no_into_a_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            pop = Path(tmp) / "p"
            shutil.copytree(POPULATION, pop, ignore=lambda d, names: [
                n for n in names if Path(d).name == "scenarios" and n != "no-price"])
            out = Path(tmp) / "f" / "04-admit"
            adjusted = POPULATION / "step04/scenarios/go-adjusted/terms.json"
            r = run_step(pop, out, "--terms", str(adjusted))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(load(out / "finding.json")["result"], "PASS")
            self.assertEqual([d["outcome"] for d in self.sealed(out, "no-price")], ["GO"])
            self.assertEqual(self.sealed(out, "no-price")[0]["pack"]["digest"], step.sha256(adjusted.read_bytes()))

    def test_a_malformed_terms_file_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "t.json"
            bad.write_text('{"pack": "p", "version": "1", "rules": []}')
            r = run_step(POPULATION, Path(tmp) / "04-admit", "--terms", str(bad))
            self.assertEqual(r.returncode, 2)
            self.assertIn("--terms", r.stderr)

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

    def test_a_decision_altered_in_the_bundle_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = Path(tmp) / "no-price"
            shutil.copytree(self.out / "scenarios" / "no-price", sd)
            bundle = load(sd / "bundle.json")
            for d in bundle["disclosures"].values():
                if d["agent_input"].get("kind") == "admission_decision":
                    d["agent_input"]["outcome"] = "GO"
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
    def test_live_is_not_checked_under_its_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "f" / "04-admit"
            r = run_step(LIVE, out, env=dict(os.environ, LAB_DIR=str(LIVE)))
            self.assertEqual(r.returncode, 0, r.stderr)
            f = load(out / "finding.json")
            self.assertEqual(f["result"], "NOT_CHECKED")
            issues = [x["issue"] for x in f["not_checked"]]
            for issue in ("Mesh-LLM/capsules-plugin#105", "Mesh-LLM/mesh-llm#2061"):
                self.assertIn(issue, issues)
            self.assertIn("live node lab-c", [x["what"] for x in f["not_checked"]])
            self.assertIn("node lab-a: 0 of 2 ledger records", f["detail"])
            self.assertEqual(sorted(f["pinned"]), ["capsules", "mesh-llm", "mesh-llm-pr-2620"])
            self.assertEqual(finding.check(out.parent), [])


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

    def test_the_row_is_refused_on_terms(self):
        number = next(n for n in status_page.NUMBERS if n.key == "refused")
        self.assertEqual((number.label, number.step), ("Decisions refused on terms", 4))
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
