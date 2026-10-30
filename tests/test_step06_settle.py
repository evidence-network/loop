"""steps/06-settle: the exchange's settlement object on the mock rail, checked by the rail's adapter.

The rehearsal tests need only python3 and openssl (3.0 or later), which the
mock rail's Ed25519 checks use; without openssl they SKIP, saying so, and with
LOOP_REQUIRE_TOOLS=1 a missing openssl is an error instead. Each planted
failure edits the proof bytes in the step's directory, re-signing with the
rail's or a node's TEST key where the plant needs a valid signature, then
decides again on them.
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
STEP = ROOT / "steps" / "06-settle"
FIXTURES = ROOT / "tests" / "fixtures" / "step06"
POPULATION = FIXTURES / "rehearsal"
RAIL_KEY = POPULATION / "rail" / "keys" / "rail-key.pem"

sys.path.insert(0, str(ROOT / "steps"))
sys.path.insert(0, str(ROOT / "status"))
import finding  # noqa: E402
import status_page  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("step06_settle", str(STEP))
_spec = importlib.util.spec_from_loader("step06_settle", _loader)
step = importlib.util.module_from_spec(_spec)
_loader.exec_module(step)
mock_rail, cc = step.mock_rail, step.cc


def openssl_ok():
    try:
        v = subprocess.run(["openssl", "version"], capture_output=True, text=True).stdout.split()
    except OSError:
        return False
    return len(v) > 1 and v[1][:1].isdigit() and int(v[1].split(".")[0]) >= 3


NO_OPENSSL = "SKIPPED LOUDLY: openssl 3.0 or later is not installed, so the mock rail's Ed25519 checks cannot run"
if not openssl_ok():
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_OPENSSL}")
    print(f"\ntest_step06_settle: {NO_OPENSSL}", file=sys.stderr)


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


@unittest.skipUnless(openssl_ok(), NO_OPENSSL)
class Step06SettleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.findings = Path(self.tmp.name) / "findings"
        self.out = self.findings / "06-settle"
        self.nodes = step.nodes_of(POPULATION)

    def tearDown(self):
        self.tmp.cleanup()

    def run_step(self, population=POPULATION, env=None):
        r = subprocess.run([sys.executable, "-B", str(STEP), "--population", str(population), "--out", str(self.out)],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((self.out / "finding.json").read_text())

    def checks(self):
        return json.loads((self.out / "checks.json").read_text())["checks"]

    def assert_only_failed(self, failed):
        c = self.checks()
        self.assertEqual({k for k, v in c.items() if not v["ok"]}, set(failed), c)

    def assert_clean(self):
        self.assertEqual(finding.check(self.findings), [])

    # Planting: edit the proof bytes the step decides on, then decide again.
    def gathered(self):
        self.out.mkdir(parents=True)
        step.gather(POPULATION, self.out)

    def decide(self):
        return step.decide(self.out, population="rehearsal", nodes=self.nodes, started=finding.now())

    def ledger(self):
        return [json.loads(ln) for ln in (self.out / "ledger.jsonl").read_bytes().splitlines()]

    def write_ledger(self, objs):
        (self.out / "ledger.jsonl").write_bytes(b"".join(cc.canonical(o) + b"\n" for o in objs))

    def reissue(self, index, **change):
        """Reissue ledger line `index` with `change`, signed by the rail's TEST key; the exchange follows its new id
        when it referred to the old one."""
        objs = self.ledger()
        old = objs[index]
        fields = {k: old[k] for k in ("sequence", "exchange_id", "payer", "payee", "currency", "amount_atomic",
                                      "decimals", "fee_atomic", "final", "issued_at")}
        objs[index] = mock_rail.issue(RAIL_KEY, **{**fields, **change})
        self.write_ledger(objs)
        ex = self.exchange()
        if ex["settlement_ref"]["settlement_id"] == old["settlement_id"]:
            ex["settlement_ref"]["settlement_id"] = objs[index]["settlement_id"]
            dump(self.out / "exchange.json", ex)
        return objs[index]

    def exchange(self):
        return json.loads((self.out / "exchange.json").read_text())

    def disclose(self, name, resign=True, **change):
        p = self.out / f"disclosure-{name}.json"
        d = {**json.loads(p.read_text()), **change}
        dump(p, cc.sign(d, POPULATION / "keys" / name / "node-key.pem") if resign else d)

    # (a) the rehearsal population
    def test_rehearsal_passes(self):
        r = self.run_step()
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertEqual(r["not_checked"], [])
        self.assertTrue(all(c["ok"] for c in self.checks().values()), self.checks())
        self.assertEqual(set(self.checks()), {"ledger", "reference", "adapter", "finality", "rail_fee", "band",
                                              "exchange_match", "ledger_count:alice", "ledger_count:bob"})
        self.assertIn("exchange rehearsal-ex-0004", r["detail"])
        self.assertIn("Band USDC:e-3.", r["detail"])
        self.assertIn("3 filtered of 4 raw settlements", r["detail"])
        self.assertEqual(finding.gaps(r["detail"]), [
            ("the exchange's sealed halves, which this step reads as an unsigned stand-in, and the controller "
             "declarations, which are rehearsal stand-ins", "Mesh-LLM/capsules-plugin#49"),
            ("a settlement object on a real rail, verified with finality by its adapter", "Mesh-LLM/capsules-plugin#83")])
        v = json.loads((self.out / "volume.json").read_text())
        self.assertEqual((v["raw"], v["filtered"]), (4, 3))
        self.assertEqual(v["raw_by_band"], {"USDC:e-2": 1, "USDC:e-4": 1, "USDC:e0": 1, "USDC:e-3": 1})
        self.assertNotIn("USDC:e0", v["filtered_by_band"])
        a = json.loads((self.out / "adapter.json").read_text())
        self.assertEqual(a["settlement"]["line"], 4)
        self.assertEqual({x["property"]: x["status"] for x in a["reports"]},
                         {p: "PASS" for p in mock_rail.METHODS})
        paths = [p["path"] for p in r["proof"]]
        for p in ("ledger.jsonl", "rail.json", "rail-key.pub.pem", "exchange.json", "disclosure-alice.json",
                  "disclosure-bob.json", "controllers.json", "adapter.json", "checks.json", "volume.json"):
            self.assertIn(p, paths)
        self.assertEqual(r["pinned"], {})
        self.assert_clean()  # (c)

    def test_the_repository_rehearsal_population_passes(self):
        r = self.run_step(ROOT / "population" / "rehearsal")
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assert_clean()

    def test_a_second_run_into_the_same_directory_is_clean(self):
        self.run_step()
        self.assertEqual(self.run_step()["result"], "PASS")
        self.assert_clean()

    def test_the_rail_files_are_what_make_rail_writes(self):
        """The committed rail and inputs, under population/ and in the fixtures, are make_rail.py's output."""
        for pop in (ROOT / "population" / "rehearsal", POPULATION):
            copy = Path(self.tmp.name) / pop.parent.name
            (copy / "rail").mkdir(parents=True)
            shutil.copyfile(pop / "population.json", copy / "population.json")
            shutil.copytree(pop / "rail" / "keys", copy / "rail" / "keys")
            sys.path.insert(0, str(ROOT / "population" / "rehearsal" / "rail"))
            import make_rail
            make_rail.make(copy)
            for f in ("rail/rail.json", "rail/ledger.jsonl", "step06/exchange.json", "step06/controllers.json",
                      "step06/disclosure-alice.json", "step06/disclosure-bob.json"):
                self.assertEqual((copy / f).read_bytes(), (pop / f).read_bytes(), f"{pop}/{f}")

    def test_the_band_is_derived_from_the_integers(self):
        self.assertEqual(mock_rail.band("USDC", 4200, 6), "USDC:e-3")
        self.assertEqual(mock_rail.band("USDC", 9000000, 6), "USDC:e0")
        self.assertEqual(mock_rail.band("USDC", 999999, 6), "USDC:e-1")
        self.assertEqual(mock_rail.band("USD", 15000, 2), "USD:e+2")
        self.assertIsNone(mock_rail.band("USDC", 0, 6))
        self.assertIsNone(mock_rail.band("USDC", -5, 6))

    # (b) planted failures
    def test_a_settlement_that_is_not_final_fails(self):
        self.gathered()
        self.reissue(3, final=False)
        f = self.decide()
        self.assertEqual(f["result"], "FAIL")
        # The disclosures count it; the ledger, counting final settlements only, does not.
        self.assert_only_failed(["finality", "ledger_count:alice", "ledger_count:bob"])
        self.assertIn("final false", self.checks()["finality"]["detail"])
        self.assert_clean()

    def test_a_settlement_whose_amount_is_not_the_exchanges_fails(self):
        self.gathered()
        ex = self.exchange()
        ex["amount_atomic"] = 4300
        dump(self.out / "exchange.json", ex)
        f = self.decide()
        self.assertEqual(f["result"], "FAIL")
        self.assert_only_failed(["exchange_match"])
        self.assertIn("amount_atomic: settlement 4200, exchange 4300", f["detail"])
        self.assert_clean()

    def test_a_reference_to_another_exchanges_settlement_fails(self):
        self.gathered()
        ex = self.exchange()
        ex["settlement_ref"]["settlement_id"] = self.ledger()[0]["settlement_id"]
        dump(self.out / "exchange.json", ex)
        f = self.decide()
        self.assertEqual(f["result"], "FAIL")
        self.assert_only_failed(["exchange_match"])
        self.assertIn("exchange_id: settlement 'rehearsal-ex-0001', exchange 'rehearsal-ex-0004'", f["detail"])
        self.assert_clean()

    def test_a_settlement_id_not_on_the_ledger_fails(self):
        self.gathered()
        ex = self.exchange()
        ex["settlement_ref"]["settlement_id"] = "0" * 64
        dump(self.out / "exchange.json", ex)
        self.assertEqual(self.decide()["result"], "FAIL")
        self.assert_only_failed(["reference", "adapter", "finality", "rail_fee", "band", "exchange_match"])
        self.assert_clean()

    def test_an_exchange_carrying_a_copy_of_the_settlement_fails(self):
        self.gathered()
        ex = self.exchange()
        ex["settlement"] = self.ledger()[3]
        dump(self.out / "exchange.json", ex)
        self.assertEqual(self.decide()["result"], "FAIL")
        self.assert_only_failed(["reference"])
        self.assert_clean()

    def test_a_disclosed_count_that_is_not_the_ledgers_fails(self):
        self.gathered()
        self.disclose("alice", settlements=5)
        f = self.decide()
        self.assertEqual(f["result"], "FAIL")
        self.assert_only_failed(["ledger_count:alice"])
        self.assertIn("alice discloses 5 through sequence 4 (signature verifies); the public ledger counts 4",
                      f["detail"])
        self.assert_clean()

    def test_a_disclosure_altered_after_signing_fails(self):
        self.gathered()
        self.disclose("bob", resign=False, settlements=3, through_sequence=4, rail="mock", node="bob")
        self.assertEqual(self.decide()["result"], "PASS")  # unchanged values keep the signature
        self.disclose("bob", resign=False, settlements=2, through_sequence=3)
        self.assertEqual(self.decide()["result"], "FAIL")
        self.assert_only_failed(["ledger_count:bob"])
        self.assertIn("signature fails", self.checks()["ledger_count:bob"]["detail"])

    def test_a_node_that_made_no_disclosure_fails(self):
        self.gathered()
        (self.out / "disclosure-bob.json").unlink()
        self.assertEqual(self.decide()["result"], "FAIL")
        self.assert_only_failed(["ledger_count:bob"])
        self.assertIn("made no disclosure", self.checks()["ledger_count:bob"]["detail"])
        self.assert_clean()

    def test_a_settlement_signed_for_another_rail_fails(self):
        self.gathered()
        objs = self.ledger()
        body = {k: v for k, v in objs[3].items() if k not in ("settlement_id", "sig")}
        body["rail"] = "other"
        body["settlement_id"] = mock_rail.body_digest(body)
        old = objs[3]["settlement_id"]
        objs[3] = cc.sign(body, RAIL_KEY)
        self.write_ledger(objs)
        ex = self.exchange()
        ex["settlement_ref"]["settlement_id"] = objs[3]["settlement_id"]
        dump(self.out / "exchange.json", ex)
        self.assertNotEqual(old, objs[3]["settlement_id"])
        self.assertEqual(self.decide()["result"], "FAIL")
        self.assertIn("line 4: object_form", self.checks()["ledger"]["detail"])
        self.assert_clean()

    def test_an_amount_beyond_the_canonicalizer_is_a_fail_not_a_crash(self):
        self.gathered()
        lines = (self.out / "ledger.jsonl").read_bytes().splitlines()
        obj = json.loads(lines[3])
        obj["amount_atomic"] = 2 ** 53
        lines[3] = json.dumps(obj).encode()
        (self.out / "ledger.jsonl").write_bytes(b"\n".join(lines) + b"\n")
        self.assertEqual(self.decide()["result"], "FAIL")
        self.assertIn("line 4: object_form", self.checks()["ledger"]["detail"])
        self.assert_clean()

    def test_a_settlement_altered_after_the_rail_signed_it_fails(self):
        self.gathered()
        objs = self.ledger()
        objs[3]["amount_atomic"] = 42000
        self.write_ledger(objs)
        self.assertEqual(self.decide()["result"], "FAIL")
        c = self.checks()
        self.assertFalse(c["ledger"]["ok"])
        self.assertIn("line 4: rail_signature, settlement_id", c["ledger"]["detail"])
        self.assertFalse((self.out / "volume.json").exists())
        self.assert_clean()

    def test_a_repeated_settlement_on_the_ledger_fails(self):
        self.gathered()
        objs = self.ledger()
        self.write_ledger(objs + [objs[3]])
        self.assertEqual(self.decide()["result"], "FAIL")
        self.assertIn(f"duplicate settlement {objs[3]['settlement_id']}", self.checks()["ledger"]["detail"])

    def test_a_settlement_without_a_rail_fee_fails(self):
        self.gathered()
        self.reissue(3, fee_atomic=0)
        f = self.decide()
        self.assertEqual(f["result"], "FAIL")
        self.assert_only_failed(["rail_fee"])
        self.assertEqual(json.loads((self.out / "volume.json").read_text())["filtered"], 2)
        self.assert_clean()

    def test_a_settlement_on_a_rail_that_charges_no_fee_fails(self):
        self.gathered()
        d = json.loads((self.out / "rail.json").read_text())
        dump(self.out / "rail.json", {**d, "fee_paying": False})
        self.assertEqual(self.decide()["result"], "FAIL")
        self.assert_only_failed(["rail_fee"])

    def test_a_settlement_with_no_positive_amount_has_no_band_and_fails(self):
        self.gathered()
        self.reissue(3, amount_atomic=0)
        ex = self.exchange()
        ex["amount_atomic"] = 0
        dump(self.out / "exchange.json", ex)
        self.assertEqual(self.decide()["result"], "FAIL")
        self.assert_only_failed(["band"])

    # The live population: read what the nodes recorded, never pay, never PASS.
    def live_population(self, lab):
        pop = Path(self.tmp.name) / "live"
        pop.mkdir()
        doc = json.loads((ROOT / "population" / "live" / "population.json").read_text())
        for i, n in enumerate(doc["nodes"], 1):
            n["data_dir"] = f"$STEP06_LAB/lab-{i}"
        dump(pop / "population.json", doc)
        return pop

    def test_live_without_node_data_is_not_checked_and_pays_nothing(self):
        r = self.run_step(ROOT / "population" / "live", env={k: v for k, v in os.environ.items() if k != "LAB_DIR"})
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("Spend: none", r["detail"])
        self.assertIn(step.WALLET_ISSUE, [n["issue"] for n in r["not_checked"]])
        self.assertEqual(len([n for n in r["not_checked"] if n["what"].startswith("live node")]), 3)
        self.assert_clean()

    def test_live_reads_what_the_nodes_recorded_and_is_not_checked(self):
        lab = Path(self.tmp.name) / "lab"
        hash_ = "ab" * 32
        observed = {"capsule_id": "c1", "model_attestation": {"compute_attestation": {"x-mesh-settlement-v1": {
            "v": 1, "observed_by": "payer", "channel": "payment.lifecycle.v1", "exchange_id": "ex-paid-1",
            "phase": "output_settlement_observed", "source": "wallet_reported", "settlement": "terminal",
            "segment": 1, "payment_hash": hash_, "amount_msat": 123457}}}}
        leg = {"capsule_id": "c2", "settlement": {"leg": "terms", "sealer_role": "payer",
                                                  "payment_ref": {"type": "ln.payment_hash", "value": hash_}}}
        for i, lines in ((1, [observed, leg]), (2, [{"capsule_id": "c3"}])):
            (lab / f"lab-{i}" / "ledger").mkdir(parents=True)
            (lab / f"lab-{i}" / "ledger" / "capsules.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
        env = dict(os.environ, STEP06_LAB=str(lab), LOOP_LIVE_SPEND="1")
        r = self.run_step(self.live_population(lab), env=env)
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("node stevenmih-1: 1 settlement legs and 1 wallet-reported settlement observations", r["detail"])
        self.assertIn("node stevenmih-2: no settlement recorded", r["detail"])
        self.assertIn("node stevenmih-3: not checked", r["detail"])
        self.assertIn("Spend: none", r["detail"])
        rec = json.loads((self.out / "stevenmih-1" / "recorded.json").read_text())
        self.assertEqual(rec["settlement_observations"][0]["payment_hash"], hash_)
        self.assertEqual(rec["settlement_legs"][0]["leg"], "terms")
        issues = [n["issue"] for n in r["not_checked"]]
        self.assertIn(step.ADAPTER_ISSUE, issues)  # node 1 recorded settlements: the adapter gap
        self.assertIn(step.WALLET_ISSUE, issues)   # node 2 recorded none: no wallet yet
        # a node recorded legs, so the closing rail entry is not appended
        self.assertFalse([n for n in r["not_checked"] if n["what"].startswith("the live rail's")])
        self.assertEqual(set(r["pinned"]), {"capsules", "mesh-llm-pr-2620"})
        self.assert_clean()

    # The demo population: a named live-kind population takes the live path, with a window and a cross-check.
    def demo_population(self, lab, **extra):
        pop = Path(self.tmp.name) / "demo"
        pop.mkdir()
        doc = json.loads((ROOT / "population" / "demo" / "population.json").read_text())
        for n in doc["nodes"]:
            n["data_dir"] = f"$STEP06_LAB/{n['name']}"
        doc.update(extra)
        dump(pop / "population.json", doc)
        return pop

    def demo_ledgers(self, lab, rows):
        """rows: {node: [(timestamp or None, payment_hash)]}; one terms leg per row."""
        for name, recs in rows.items():
            (lab / name / "ledger").mkdir(parents=True)
            lines = []
            for i, (ts, h) in enumerate(recs):
                rec = {"capsule_id": f"{name}-{i}", "settlement": {"leg": "terms", "payment_ref": {
                    "type": "ln.payment_hash", "value": h}}}
                if ts:
                    rec["timestamp"] = ts
                lines.append(json.dumps(rec) + "\n")
            (lab / name / "ledger" / "capsules.jsonl").write_text("".join(lines))

    def test_the_demo_population_files_pin_the_demo_build(self):
        doc = json.loads((ROOT / "population" / "demo" / "population.json").read_text())
        self.assertEqual((doc["population"], doc["kind"], doc["rail"]), ("demo", "live", None))
        self.assertEqual(doc["window"], {"from": "2026-10-30T17:58:00Z"})
        pins = finding.pins()
        self.assertEqual(pins["demo-capsules"][1], "f494845bb9e003d100429fc4c081fc11322551cc")
        self.assertEqual(pins["demo-mesh-llm"], ("ethanyzhang/mesh-llm", "3ffa5c4f1d7d162c964fc7fb24937c7ed5a12301"))

    def test_a_named_live_kind_population_takes_the_live_path(self):
        lab = Path(self.tmp.name) / "lab"
        self.demo_ledgers(lab, {"brattain": [("2026-10-30T18:00:00Z", "aa" * 32)],
                                "shockley": [("2026-10-30T18:00:01Z", "aa" * 32)]})
        r = self.run_step(self.demo_population(lab), env=dict(os.environ, STEP06_LAB=str(lab)))
        self.assertEqual((r["result"], r["population"]), ("NOT_CHECKED", "demo"))
        self.assertEqual(set(r["pinned"]), {"demo-capsules", "demo-mesh-llm"})
        self.assertIn("Spend: none", r["detail"])
        self.assertIn(step.ADAPTER_ISSUE, [n["issue"] for n in r["not_checked"]])
        self.assert_clean()

    def test_a_population_that_is_not_live_kind_still_needs_the_mock_rail(self):
        pop = Path(self.tmp.name) / "other"
        pop.mkdir()
        dump(pop / "population.json", {"population": "demo", "nodes": [], "rail": None})
        p = subprocess.run([str(STEP), "--population", str(pop), "--out", str(self.out)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_the_window_leaves_out_older_and_undated_records(self):
        lab = Path(self.tmp.name) / "lab"
        old, new = "11" * 32, "22" * 32
        self.demo_ledgers(lab, {"brattain": [("2026-10-30T17:57:59Z", old), ("2026-10-30T17:58:00Z", new),
                                             ("2026-10-30T13:00:00-05:00", new), (None, old)],
                                "shockley": [("2026-10-02T00:00:00Z", old)]})
        r = self.run_step(self.demo_population(lab), env=dict(os.environ, STEP06_LAB=str(lab)))
        rec = json.loads((self.out / "brattain" / "recorded.json").read_text())
        # 13:00-05:00 is 18:00Z: in; 17:57:59Z is out; no timestamp is left out and counted
        self.assertEqual([l["payment_ref"]["value"] for l in rec["settlement_legs"]], [new, new])
        self.assertEqual((rec["outside_window"], rec["undated"], rec["window_from"]), (1, 1, "2026-10-30T17:58:00Z"))
        self.assertIn("node shockley: no settlement recorded", r["detail"])
        self.assertIn("window from 2026-10-30T17:58:00Z: 1 records before it", r["detail"])
        why = next(n["why"] for n in r["not_checked"] if n["what"].endswith("of node shockley"))
        self.assertIn("no settlement in the window from 2026-10-30T17:58:00Z (1 records before it, 0 without", why)
        self.assertNotIn("no wallet", why)

    def test_no_window_counts_every_record(self):
        lab = Path(self.tmp.name) / "lab"
        self.demo_ledgers(lab, {"brattain": [("2026-10-02T00:00:00Z", "11" * 32), (None, "22" * 32)],
                                "shockley": []})
        pop = self.demo_population(lab)
        doc = json.loads((pop / "population.json").read_text())
        del doc["window"]
        dump(pop / "population.json", doc)
        self.run_step(pop, env=dict(os.environ, STEP06_LAB=str(lab)))
        rec = json.loads((self.out / "brattain" / "recorded.json").read_text())
        self.assertEqual(len(rec["settlement_legs"]), 2)
        self.assertNotIn("outside_window", rec)

    def test_a_malformed_window_exits_2(self):
        lab = Path(self.tmp.name) / "lab"
        for bad in ({"from": "yesterday"}, {"from": "2026-10-30T17:58:00"}, {"since": "2026-10-30T17:58:00Z"}, "now"):
            with self.subTest(window=bad):
                pop = self.demo_population(lab)
                doc = json.loads((pop / "population.json").read_text())
                doc["window"] = bad
                dump(pop / "population.json", doc)
                p = subprocess.run([str(STEP), "--population", str(pop), "--out", str(self.out)],
                                   capture_output=True, text=True, env=dict(os.environ, STEP06_LAB=str(lab)))
                self.assertEqual(p.returncode, 2)
                self.assertIn("window", p.stderr)
                shutil.rmtree(pop)

    def test_the_payment_hash_crosscheck_compares_and_stays_not_checked(self):
        lab = Path(self.tmp.name) / "lab"
        both, only = "aa" * 32, "bb" * 32
        self.demo_ledgers(lab, {"brattain": [("2026-10-30T18:00:00Z", both), ("2026-10-30T18:01:00Z", only)],
                                "shockley": [("2026-10-30T18:00:01Z", both)]})
        r = self.run_step(self.demo_population(lab), env=dict(os.environ, STEP06_LAB=str(lab)))
        cross = json.loads((self.out / "crosscheck.json").read_text())
        self.assertEqual(cross["recorded_by_all"], {both: ["brattain", "shockley"]})
        self.assertEqual(cross["recorded_by_some"], {only: ["brattain"]})
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("crosscheck.json", [p["path"] for p in r["proof"]])
        self.assertIn("1 recorded by every node, 1 by some only", r["detail"])
        why = next(n["why"] for n in r["not_checked"] if "preimages" in n["what"])
        self.assertIn("do not all record the same payment hashes", why)
        self.assertNotIn("agree on", why)

    def test_the_crosscheck_says_so_when_the_nodes_agree_and_when_there_is_nothing_to_compare(self):
        for rows, expect in (({"brattain": [("2026-10-30T18:00:00Z", "aa" * 32)],
                               "shockley": [("2026-10-30T18:00:01Z", "aa" * 32)]}, "agree on every payment hash"),
                             ({"brattain": [], "shockley": []}, "hold no payment hash to compare")):
            with self.subTest(expect=expect):
                lab = Path(self.tmp.name) / "lab"
                shutil.rmtree(lab, ignore_errors=True)
                self.demo_ledgers(lab, rows)
                r = self.run_step(self.demo_population(lab), env=dict(os.environ, STEP06_LAB=str(lab)))
                self.assertIn(expect, next(n["why"] for n in r["not_checked"] if "preimages" in n["what"]))
                shutil.rmtree(Path(self.tmp.name) / "demo")

    def test_a_requested_crosscheck_with_one_node_unread_says_it_did_not_run(self):
        lab = Path(self.tmp.name) / "lab"
        self.demo_ledgers(lab, {"brattain": [("2026-10-30T18:00:00Z", "aa" * 32)]})  # shockley has no data dir
        r = self.run_step(self.demo_population(lab), env=dict(os.environ, STEP06_LAB=str(lab)))
        self.assertFalse((self.out / "crosscheck.json").exists())
        self.assertIn("fewer than two nodes were read", r["detail"])
        self.assertTrue([n for n in r["not_checked"] if "preimages" in n["what"] and "no comparison ran" in n["why"]])

    def test_a_rerun_does_not_keep_an_earlier_crosscheck(self):
        lab = Path(self.tmp.name) / "lab"
        self.demo_ledgers(lab, {"brattain": [("2026-10-30T18:00:00Z", "aa" * 32)],
                                "shockley": [("2026-10-30T18:00:01Z", "aa" * 32)]})
        pop = self.demo_population(lab)
        env = dict(os.environ, STEP06_LAB=str(lab))
        self.run_step(pop, env=env)
        self.assertTrue((self.out / "crosscheck.json").exists())
        doc = json.loads((pop / "population.json").read_text())
        del doc["crosscheck"]
        dump(pop / "population.json", doc)
        self.run_step(pop, env=env)
        self.assertFalse((self.out / "crosscheck.json").exists())

    def test_no_crosscheck_unless_the_population_asks(self):
        lab = Path(self.tmp.name) / "lab"
        self.demo_ledgers(lab, {"brattain": [("2026-10-30T18:00:00Z", "aa" * 32)],
                                "shockley": [("2026-10-30T18:00:01Z", "aa" * 32)]})
        pop = self.demo_population(lab)
        doc = json.loads((pop / "population.json").read_text())
        del doc["crosscheck"]
        dump(pop / "population.json", doc)
        self.run_step(pop, env=dict(os.environ, STEP06_LAB=str(lab)))
        self.assertFalse((self.out / "crosscheck.json").exists())

    def test_a_node_with_no_settlement_cites_the_open_adapter_issue(self):
        self.assertEqual(step.WALLET_ISSUE, step.ADAPTER_ISSUE)

    def test_the_step_never_reaches_for_a_wallet_or_the_network(self):
        source = STEP.read_text()
        for word in ("urllib", "socket", "http.client", "requests", "subprocess"):
            self.assertNotIn(f"import {word}", source)

    # The status page's filtered volume row reads this step's volume.json.
    def page_number(self, findings):
        runs = {p: status_page.read_run(p, d) for p, d in status_page.latest_runs(findings).items()}
        nb = next(n for n in status_page.NUMBERS if n.key == "filtered")
        return status_page.number_value(nb, runs["rehearsal"])

    def test_the_status_page_shows_filtered_beside_raw_from_a_pass(self):
        self.findings = Path(self.tmp.name) / "2026-10-28-rehearsal"
        self.out = self.findings / "06-settle"
        self.run_step()
        self.assertEqual(self.page_number(Path(self.tmp.name)), ("3 filtered of 4 raw records", True))

    def test_the_status_page_shows_no_volume_without_a_pass(self):
        self.findings = Path(self.tmp.name) / "2026-10-28-rehearsal"
        self.out = self.findings / "06-settle"
        self.gathered()
        self.disclose("alice", settlements=5)
        self.decide()
        self.assertEqual(self.page_number(Path(self.tmp.name)), ("not measured yet (M2)", False))

    def test_the_status_page_never_shows_raw_alone(self):
        self.findings = Path(self.tmp.name) / "2026-10-28-rehearsal"
        self.out = self.findings / "06-settle"
        self.run_step()
        run = status_page.read_run("rehearsal", self.findings)
        for bad in ({"raw": 4}, {"raw": 4, "filtered": 5}, {"raw": 4, "filtered": "3"}, {"raw": -1, "filtered": 0}):
            with mock.patch.object(status_page.json, "loads", side_effect=[bad]):
                self.assertEqual(status_page.settled_volume(run, 6), (None, None), bad)


if __name__ == "__main__":
    unittest.main()
