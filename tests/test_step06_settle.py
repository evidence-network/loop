"""steps/06-settle: the exchange's settlement object on the mock rail, checked by the rail's adapter.

The rehearsal tests need only python3 and openssl (3.0 or later), which the
mock rail's Ed25519 checks use; without openssl they SKIP, saying so, and with
LOOP_REQUIRE_TOOLS=1 a missing openssl is an error instead. Each planted
failure edits the proof bytes in the step's directory, re-signing with the
rail's or a node's TEST key where the plant needs a valid signature, then
decides again on them.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
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
    def live_population(self, lab, rail=None, checks=None):
        """The live population with each data_dir under $STEP06_LAB. `rail` None takes the generic live path (no
        rail named); "lightning" the ruled Lightning checks (loop#243), with `checks` as settlement_checks."""
        pop = Path(self.tmp.name) / "live"
        pop.mkdir()
        doc = json.loads((ROOT / "population" / "live" / "population.json").read_text())
        for i, n in enumerate(doc["nodes"], 1):
            n["data_dir"] = f"$STEP06_LAB/lab-{i}"
        doc["rail"] = rail
        doc["settlement_checks"] = checks
        dump(pop / "population.json", doc)
        return pop

    def test_live_without_node_data_is_not_checked_and_pays_nothing(self):
        # The committed live population names the Lightning rail (loop#243); with no LAB_DIR it reads nothing.
        r = self.run_step(ROOT / "population" / "live", env={k: v for k, v in os.environ.items() if k != "LAB_DIR"})
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertEqual(r["asserts"], step.LIGHTNING_ASSERTS)
        self.assertIn("Spend: none", r["detail"])
        self.assertIn(step.LIGHTNING_ISSUE, [n["issue"] for n in r["not_checked"]])
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

    def test_the_preimage_check_needs_lowercase_hex_that_hashes_to_the_payment_hash(self):
        pre = "11" * 32
        h = hashlib.sha256(bytes.fromhex(pre)).hexdigest()
        self.assertIsNone(step.preimage_check(None, h))
        self.assertEqual(step.preimage_check(pre, h), "verified")
        self.assertEqual(step.preimage_check("22" * 32, h), "mismatch")
        self.assertEqual(step.preimage_check(pre.upper().replace("1", "A"), h), "malformed")
        self.assertEqual(step.preimage_check(pre[:-2], h), "malformed")
        self.assertEqual(step.preimage_check(7, h), "malformed")
        self.assertEqual(step.preimage_check(pre, None), "no_payment_hash")
        self.assertEqual(step.preimage_check(pre, h.upper()), "no_payment_hash")
        self.assertEqual(step.preimage_check(pre, ""), "no_payment_hash")
        self.assertEqual(step.preimage_check("zz", None), "malformed")  # a garbage preimage, whatever the hash

    @staticmethod
    def observation(preimage, payment_hash, role="payer", cid="c1", **more):
        block = {"v": 1, "observed_by": role, "channel": "payment.lifecycle.v1", "exchange_id": "ex-paid-1",
                 "phase": "output_settlement_observed", "source": "wallet_reported", "settlement": "terminal",
                 "segment": 1, "payment_hash": payment_hash, "amount_msat": 2000, **more}
        if preimage is not None:
            block["preimage"] = preimage
        return {"capsule_id": cid, "model_attestation": {"compute_attestation": {"x-mesh-settlement-v1": block}}}

    def live_ledgers(self, rows, pop=None):
        """rows: {lab index: [ledger objects]}; every lab-1..3 ledger exists, empty unless named."""
        lab = Path(self.tmp.name) / "lab"
        shutil.rmtree(lab, ignore_errors=True)
        for i in (1, 2, 3):
            (lab / f"lab-{i}" / "ledger").mkdir(parents=True)
            (lab / f"lab-{i}" / "ledger" / "capsules.jsonl").write_text("".join(json.dumps(x) + "\n"
                                                                              for x in rows.get(i, [])))
        if pop is None:
            shutil.rmtree(Path(self.tmp.name) / "live", ignore_errors=True)
            pop = self.live_population(lab)
        return self.run_step(pop, env=dict(os.environ, STEP06_LAB=str(lab)))

    def live_with_preimage(self, preimage, payment_hash, role="payer"):
        return self.live_ledgers({1: [self.observation(preimage, payment_hash, role)]})

    # The live Lightning population (loop#243): the ruled checks decide, and a full set is a PASS.
    def paid(self, n=2, payer_fee=0, payee_fee=5, amount=1000, credited=995, payer_ex="pay-ex", payee_ex="srv-ex"):
        """{lab index: ledger objects}: n payments lab-2 -> lab-1, each with the payer's preimage."""
        rows = {1: [], 2: []}
        for k in range(n):
            pre = f"{k + 1:02x}" * 32
            h = hashlib.sha256(bytes.fromhex(pre)).hexdigest()
            rows[2].append(self.observation(pre, h, cid=f"p{k}", exchange_id=f"{payer_ex}-{k}", fee_msat=payer_fee,
                                            amount_msat=amount))
            rows[1].append(self.observation(pre, h, role="provider", cid=f"s{k}", exchange_id=f"{payee_ex}-{k}",
                                            amount_msat=amount, credited_msat=credited, fee_msat=payee_fee))
        return rows

    def checks_dir(self, status="PASS", paired=2,
                   pairs=(("stevenmih-1", "stevenmih-2"), ("stevenmih-2", "stevenmih-1"))):
        """lab.sh bundles' output: per (node, peer), the peer's bundle file and the node's check of it."""
        d = Path(self.tmp.name) / "bundles"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
        ids = {n["name"]: n["node_id"] for n in json.loads((ROOT / "population" / "live" / "population.json").read_text())["nodes"]}
        for me, peer in pairs:
            bundle = d / f"{peer}.for.{me}.json"
            bundle.write_text(json.dumps({"state": "bundle", "bundle": {"records": [{"preimage": "SECRET-IN-BUNDLE"}]}}))
            dump(d / f"check.{ids[me]}.{ids[peer]}.json", {
                "node_id": ids[me], "peer_node_id": ids[peer], "bundle_file": bundle.name,
                "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "report": {"counterparty_count": {"status": status, "method": "pairs", "paired": paired, "unpaired": []},
                           "disclosed_count": {"settlements": paired},
                           "settlements": [{"preimage": "SECRET-IN-BUNDLE-REPORT"}]}})
        return d

    def lightning(self, rows, checks=None):
        lab = Path(self.tmp.name) / "lab"
        shutil.rmtree(lab, ignore_errors=True)
        shutil.rmtree(Path(self.tmp.name) / "live", ignore_errors=True)
        for i in (1, 2, 3):
            (lab / f"lab-{i}" / "ledger").mkdir(parents=True)
            (lab / f"lab-{i}" / "ledger" / "capsules.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows.get(i, [])))
        pop = self.live_population(lab, rail="lightning", checks=str(checks) if checks else None)
        return self.run_step(pop, env=dict(os.environ, STEP06_LAB=str(lab)))

    def judged(self):
        return json.loads((self.out / "checks.json").read_text())

    def test_lightning_passes_on_the_ruled_checks_and_names_the_public_ledger_gap(self):
        r = self.lightning(self.paid(), self.checks_dir())
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertEqual(r["asserts"], step.LIGHTNING_ASSERTS)
        self.assertEqual(r["not_checked"], [])
        self.assertEqual(finding.gaps(r["detail"]), [(
            "a public ledger's count of each node's settlements (Lightning has none; the counterparty's signed book "
            "stands in)", step.PUBLIC_LEDGER_ISSUE)])
        checks = self.judged()
        self.assertEqual({k: v["ok"] for k, v in checks.items()}, {
            "reference": True, "finality": True, "band": True, "rail_fee": True, "exchange_match": True,
            "counterparty_count:stevenmih-1:stevenmih-2": True, "counterparty_count:stevenmih-2:stevenmih-1": True, "public_ledger_count": None})
        self.assertIn("stevenmih-2 payer BTC decade -8", checks["band"]["detail"])  # 1,000 msat = 1 sat, per exchange
        rows = json.loads((self.out / "counterparty.json").read_text())
        self.assertEqual({(x["node"], x["peer"], x["status"], x["paired"]) for x in rows},
                         {("stevenmih-1", "stevenmih-2", "PASS", 2), ("stevenmih-2", "stevenmih-1", "PASS", 2)})
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", x["bundle_sha256"]) for x in rows))
        out = "".join(f.read_text() for f in self.out.rglob("*") if f.is_file())
        self.assertNotIn("SECRET-IN-BUNDLE", out)  # only counts and the digest leave the bundle and check files
        self.assertNotIn("01" * 32, out)  # nor any preimage
        self.assert_clean()

    def test_lightning_without_the_counterparty_checks_is_not_checked(self):
        r = self.lightning(self.paid())
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertEqual(sorted(n["what"] for n in r["not_checked"]),
                         ["step 06 counterparty_count:stevenmih-1:stevenmih-2 on the Lightning rail",
                          "step 06 counterparty_count:stevenmih-2:stevenmih-1 on the Lightning rail"])
        self.assertIn("run lab/lab.sh bundles", r["not_checked"][0]["why"])

    def test_lightning_fails_on_each_ruled_check(self):
        repeated = self.paid()
        again = json.loads(json.dumps(repeated[2][0]))  # the same payment sealed again under another exchange
        again["capsule_id"] = "p-again"
        again["model_attestation"]["compute_attestation"]["x-mesh-settlement-v1"]["exchange_id"] = "other-ex"
        repeated[2].append(again)
        for rows, checks, failed in ((self.paid(payee_fee=0), "PASS", "rail_fee"),
                                     (self.paid(credited=990), "PASS", "exchange_match"),
                                     (self.paid(), "FAIL", "counterparty_count:stevenmih-1:stevenmih-2"),
                                     (repeated, "PASS", "reference")):
            with self.subTest(failed=failed):
                r = self.lightning(rows, self.checks_dir(status=checks))
                self.assertEqual(r["result"], "FAIL", r["detail"])
                self.assertIs(self.judged()[failed]["ok"], False, self.judged()[failed])

    def test_lightning_does_not_pass_on_a_check_older_than_the_books(self):
        # A third payment after lab.sh bundles: the check files still pair 2, so counterparty_count is not checked.
        r = self.lightning(self.paid(n=3), self.checks_dir(paired=2))
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIsNone(self.judged()["counterparty_count:stevenmih-1:stevenmih-2"]["ok"])
        self.assertIn("predates a payment", self.judged()["counterparty_count:stevenmih-1:stevenmih-2"]["detail"])

    def test_lightning_does_not_pass_on_a_bundle_changed_since_its_check(self):
        d = self.checks_dir()
        (d / "stevenmih-2.for.stevenmih-1.json").write_text("{}")
        r = self.lightning(self.paid(), d)
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("missing or changed", self.judged()["counterparty_count:stevenmih-1:stevenmih-2"]["detail"])

    def test_lightning_fails_on_any_counterparty_check_that_fails(self):
        # A failing check for a pair that has no payment in the books still counts.
        r = self.lightning(self.paid(), self.both_dirs())
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIs(self.judged()["counterparty_count:stevenmih-3:stevenmih-1"]["ok"], False)

    def both_dirs(self):
        """The good checks for lab-1 and lab-2, plus a failing one of lab-3's book against lab-1's bundle."""
        d = self.checks_dir()
        ids = {n["name"]: n["node_id"] for n in json.loads((ROOT / "population" / "live" / "population.json").read_text())["nodes"]}
        bundle = d / "stevenmih-1.for.stevenmih-3.json"
        bundle.write_text("{}")
        dump(d / f"check.{ids['stevenmih-3']}.{ids['stevenmih-1']}.json", {
            "node_id": ids["stevenmih-3"], "peer_node_id": ids["stevenmih-1"], "bundle_file": bundle.name,
            "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "report": {"counterparty_count": {"status": "FAIL", "paired": 0, "unpaired": [{"h": 1}]}}})
        return d

    def two_payers(self):
        """lab-2 and lab-3 each paid lab-1 twice, with distinct payments."""
        a, b = self.paid(), self.paid(payer_ex="p3", payee_ex="s3")
        for k in range(2):
            pre = f"{k + 0x40:02x}" * 32
            h = hashlib.sha256(bytes.fromhex(pre)).hexdigest()
            for o in (b[1][k], b[2][k]):
                o["capsule_id"] += "-3"
                o["model_attestation"]["compute_attestation"]["x-mesh-settlement-v1"].update(payment_hash=h, preimage=pre)
        return {1: a[1] + b[1], 2: a[2], 3: b[2]}

    def test_lightning_judges_each_peer_of_a_node_that_settled_with_two(self):
        pairs = (("stevenmih-1", "stevenmih-2"), ("stevenmih-2", "stevenmih-1"),
                 ("stevenmih-1", "stevenmih-3"), ("stevenmih-3", "stevenmih-1"))
        r = self.lightning(self.two_payers(), self.checks_dir(pairs=pairs))
        self.assertEqual(r["result"], "PASS", r["detail"])
        # lab-1's check against lab-2 failing is a FAIL although its check against lab-3 passes.
        d = self.checks_dir(pairs=pairs)
        ids = {n["name"]: n["node_id"] for n in json.loads((ROOT / "population" / "live" / "population.json").read_text())["nodes"]}
        bad = d / f"check.{ids['stevenmih-1']}.{ids['stevenmih-2']}.json"
        doc = json.loads(bad.read_text())
        doc["report"]["counterparty_count"]["status"] = "FAIL"
        dump(bad, doc)
        r = self.lightning(self.two_payers(), d)
        self.assertEqual(r["result"], "FAIL", r["detail"])
        self.assertIs(self.judged()["counterparty_count:stevenmih-1:stevenmih-2"]["ok"], False)
        self.assertIs(self.judged()["counterparty_count:stevenmih-1:stevenmih-3"]["ok"], True)
        bad.unlink()  # missing instead: not checked
        r = self.lightning(self.two_payers(), d)
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIsNone(self.judged()["counterparty_count:stevenmih-1:stevenmih-2"]["ok"])

    def test_lightning_a_provider_without_the_wallets_numbers_is_not_checked(self):
        rows = self.paid(n=1)
        rows[1][0]["model_attestation"]["compute_attestation"]["x-mesh-settlement-v1"].pop("credited_msat")
        r = self.lightning(rows, self.checks_dir(paired=1))
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIsNone(self.judged()["exchange_match"]["ok"])

    def test_lightning_fails_a_settlement_with_no_positive_amount(self):
        r = self.lightning(self.paid(amount=-1000, credited=-1005), self.checks_dir())
        self.assertEqual(r["result"], "FAIL")
        self.assertIs(self.judged()["band"]["ok"], False)

    def test_lightning_finality_comes_from_the_payers_preimage(self):
        rows = self.paid(n=1)
        rows[2][0]["model_attestation"]["compute_attestation"]["x-mesh-settlement-v1"].pop("preimage")
        r = self.lightning(rows, self.checks_dir())
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIsNone(self.judged()["finality"]["ok"])

    def test_the_band_is_the_adapters(self):
        self.assertEqual(step.band(1), {"currency": "BTC", "decade": -11})
        self.assertEqual(step.band(1000), {"currency": "BTC", "decade": -8})
        self.assertEqual(step.band(250_000), {"currency": "BTC", "decade": -6})
        self.assertEqual(step.band(100_000_000_000), {"currency": "BTC", "decade": 0})
        self.assertEqual(step.band(0), {"currency": "BTC", "decade": None})

    def files_out(self):
        return "".join(f.read_text() for f in self.out.rglob("*") if f.is_file())

    def test_a_preimage_that_hashes_to_the_payment_hash_proves_finality_and_is_never_copied(self):
        pre = "5a" * 32
        r = self.live_with_preimage(pre, hashlib.sha256(bytes.fromhex(pre)).hexdigest())
        self.assertEqual(r["result"], "NOT_CHECKED")  # the rail's object by reference is still missing
        self.assertIn("1 with a preimage that hashes to its payment_hash", r["detail"])
        self.assertIn("finality: 1 of 1 recorded payment hashes proven by a payer's preimage", r["detail"])
        self.assertFalse([n for n in r["not_checked"] if "no payer preimage proves" in n["what"]])
        rec = json.loads((self.out / "stevenmih-1" / "recorded.json").read_text())
        self.assertEqual(rec["settlement_observations"][0]["preimage_check"], "verified")
        self.assertNotIn(pre, self.files_out())
        self.assert_clean()

    def test_a_providers_preimage_does_not_prove_the_payment(self):
        pre = "5a" * 32
        r = self.live_with_preimage(pre, hashlib.sha256(bytes.fromhex(pre)).hexdigest(), role="provider")
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("finality: 0 of 1 recorded payment hashes proven", r["detail"])
        self.assertTrue([n for n in r["not_checked"] if "no payer preimage proves" in n["what"]])

    def test_a_payment_hash_with_no_preimage_leaves_finality_not_checked(self):
        r = self.live_with_preimage(None, "ab" * 32)
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("finality: 0 of 1 recorded payment hashes proven", r["detail"])
        gap = [n for n in r["not_checked"] if "no payer preimage proves" in n["what"]]
        self.assertEqual(len(gap), 1)
        self.assertEqual(gap[0]["issue"], step.ADAPTER_ISSUE)

    def test_a_hash_only_a_leg_carries_is_not_proven(self):
        pre = "5a" * 32
        h = hashlib.sha256(bytes.fromhex(pre)).hexdigest()
        leg = {"capsule_id": "c2", "settlement": {"leg": "terms", "payment_ref": {"type": "ln.payment_hash",
                                                                                  "value": "cd" * 32}}}
        r = self.live_ledgers({1: [self.observation(pre, h), leg]})
        self.assertIn("finality: 1 of 2 recorded payment hashes proven", r["detail"])
        self.assertTrue([n for n in r["not_checked"] if "no payer preimage proves" in n["what"]])

    def test_a_payer_preimage_on_one_node_proves_the_hash_the_provider_recorded_on_another(self):
        pre = "5a" * 32
        h = hashlib.sha256(bytes.fromhex(pre)).hexdigest()
        r = self.live_ledgers({1: [self.observation(pre, h)],
                               2: [self.observation(None, h, role="provider", cid="c3", credited_msat=1990,
                                                    fee_msat=10)]})
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("finality: 1 of 1 recorded payment hashes proven", r["detail"])
        self.assertFalse([n for n in r["not_checked"] if "no payer preimage proves" in n["what"]])

    def test_an_observation_with_no_payment_hash_to_check_is_not_a_fail(self):
        r = self.live_with_preimage("5a" * 32, None)
        self.assertEqual(r["result"], "NOT_CHECKED")
        rec = json.loads((self.out / "stevenmih-1" / "recorded.json").read_text())
        self.assertEqual(rec["settlement_observations"][0]["preimage_check"], "no_payment_hash")

    def test_the_demo_crosscheck_says_how_many_hashes_a_payer_preimage_proves(self):
        pre = "5a" * 32
        h = hashlib.sha256(bytes.fromhex(pre)).hexdigest()
        lab = Path(self.tmp.name) / "demo-lab"
        pop = self.demo_population(lab)
        names = [n["name"] for n in json.loads((pop / "population.json").read_text())["nodes"]]
        ts = {"timestamp": "2026-10-31T00:00:00Z"}
        rows = {names[0]: [dict(self.observation(pre, h), **ts)],
                names[1]: [dict(self.observation(None, h, role="provider", cid="c3", credited_msat=1990,
                                                 fee_msat=10), **ts)]}
        for name, objs in rows.items():
            (lab / name / "ledger").mkdir(parents=True)
            (lab / name / "ledger" / "capsules.jsonl").write_text("".join(json.dumps(x) + "\n" for x in objs))
        r = self.run_step(pop, env=dict(os.environ, STEP06_LAB=str(lab)))
        cross = [n for n in r["not_checked"] if n["what"].startswith("that the payment hashes")]
        self.assertEqual(len(cross), 1)
        self.assertIn("every recorded payment hash is proven by a payer's preimage", cross[0]["why"])
        self.assertNotIn(pre, "".join(f.read_text() for f in self.out.rglob("*") if f.is_file()))

    def test_a_preimage_that_does_not_prove_its_payment_fails(self):
        for pre, says, role in (("33" * 32, "does not hash to its payment_hash", "payer"),
                                ("33" * 31, "is not 32 bytes", "payer"),
                                ("33" * 32, "does not hash to its payment_hash", "provider")):
            with self.subTest(pre=pre, role=role):
                r = self.live_with_preimage(pre, "ab" * 32, role)
                self.assertEqual(r["result"], "FAIL")
                self.assertIn(f"node stevenmih-1: the preimage of settlement observation c1 (payment_hash {'ab' * 32}) "
                              f"{says}", r["detail"])
                self.assertNotIn(pre, self.files_out())

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
        self.assertIn("window from 2026-10-30T17:58:00Z: 1 settlement records before it", r["detail"])
        why = next(n["why"] for n in r["not_checked"] if n["what"].endswith("of node shockley"))
        self.assertIn("no settlement in the window from 2026-10-30T17:58:00Z (1 settlement records before it, 0 without", why)
        self.assertNotIn("no wallet", why)

    def test_records_that_are_not_settlement_records_are_not_counted_in_the_window(self):
        lab = Path(self.tmp.name) / "lab"
        self.demo_ledgers(lab, {"brattain": [("2026-10-30T18:00:00Z", "aa" * 32)], "shockley": []})
        pad = {"capsule_id": "p", "record_type": "padding"}
        with (lab / "brattain" / "ledger" / "capsules.jsonl").open("a") as f:
            f.write(json.dumps(pad) + "\n" + json.dumps(dict(pad, timestamp="2026-10-02T00:00:00Z")) + "\n")
        self.run_step(self.demo_population(lab), env=dict(os.environ, STEP06_LAB=str(lab)))
        rec = json.loads((self.out / "brattain" / "recorded.json").read_text())
        self.assertEqual((rec["outside_window"], rec["undated"], len(rec["settlement_legs"])), (0, 0, 1))

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

    def demo_observations(self, lab, rows):
        """rows: {node: [obs]}, each obs a dict merged into a settlement observation at 18:00Z."""
        for name, obs in rows.items():
            (lab / name / "ledger").mkdir(parents=True)
            lines = []
            for i, o in enumerate(obs):
                blk = {"phase": "output_settlement_observed", "source": "wallet_reported", **o}
                lines.append(json.dumps({"capsule_id": f"{name}-{i}", "timestamp": "2026-10-30T18:00:00Z",
                                         "model_attestation": {"compute_attestation": {"x-mesh-settlement-v1": blk}}})
                             + "\n")
            (lab / name / "ledger" / "capsules.jsonl").write_text("".join(lines))

    def crosscheck_of(self, rows):
        lab = Path(self.tmp.name) / "lab"
        shutil.rmtree(lab, ignore_errors=True)
        self.demo_observations(lab, rows)
        r = self.run_step(self.demo_population(lab), env=dict(os.environ, STEP06_LAB=str(lab)))
        shutil.rmtree(Path(self.tmp.name) / "demo")
        return r, json.loads((self.out / "crosscheck.json").read_text())

    def test_the_payment_hash_crosscheck_pairs_by_role_and_amount_and_stays_not_checked(self):
        h = lambda c: c * 64
        payer = lambda hash_, amt: {"observed_by": "payer", "payment_hash": hash_, "amount_msat": amt}
        prov = lambda hash_, cred, fee: {"observed_by": "provider", "payment_hash": hash_, "credited_msat": cred,
                                         "fee_msat": fee}
        r, cross = self.crosscheck_of({
            "brattain": [payer(h("a"), 1000), prov(h("b"), 495, 5), payer(h("c"), 700), payer(h("d"), 50),
                         payer(h("e"), 10)],
            "shockley": [prov(h("a"), 995, 5), payer(h("b"), 500), prov(h("c"), 600, 10), payer(h("d"), 50)]})
        self.assertEqual(cross["paired"], {h("a"): {"payer": "brattain", "payee": "shockley", "amount_msat": 1000},
                                           h("b"): {"payer": "shockley", "payee": "brattain", "amount_msat": 500}})
        self.assertIn("differ", cross["unpaired"][h("c")])       # 700 != 610
        self.assertIn("no payer observation or no provider", cross["unpaired"][h("d")])  # both payers
        self.assertEqual(cross["unpaired"][h("e")], "recorded by brattain only")
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("crosscheck.json", [p["path"] for p in r["proof"]])
        self.assertIn("2 paired", r["detail"])
        self.assertIn("3 unpaired", r["detail"])
        why = next(n["why"] for n in r["not_checked"] if "preimages" in n["what"])
        self.assertIn("leave some payment hashes unpaired", why)
        self.assertNotIn("pair every", why)

    def test_the_crosscheck_does_not_pair_a_hash_other_records_contradict(self):
        h = lambda c: c * 64
        payer = lambda hash_, amt: {"observed_by": "payer", "payment_hash": hash_, "amount_msat": amt}
        prov = lambda hash_, cred, fee: {"observed_by": "provider", "payment_hash": hash_, "credited_msat": cred,
                                         "fee_msat": fee}
        _, cross = self.crosscheck_of({
            "brattain": [payer(h("a"), 10), payer(h("b"), 10), payer(h("b"), 999), payer(h("c"), 10),
                         prov(h("c"), 9, 1), payer(h("d"), 10), payer(h("d"), 10)],
            "shockley": [prov(h("a"), 9, 1), payer(h("a"), 10), prov(h("b"), 9, 1),
                         prov(h("d"), 9, 1), prov(h("d"), 9, 1)]})
        self.assertIn("one node of each role is needed", cross["unpaired"][h("a")])  # payer on both nodes
        self.assertIn("conflicting amounts: payer [10, 999]", cross["unpaired"][h("b")])
        self.assertEqual(cross["unpaired"][h("c")], "recorded by brattain only")  # one node holding both roles is not a pair
        self.assertEqual(cross["paired"][h("d")], {"payer": "brattain", "payee": "shockley", "amount_msat": 10})

    def test_an_observation_without_integer_amounts_keeps_a_hash_unpaired(self):
        h = "a" * 64
        good = [{"observed_by": "payer", "payment_hash": h, "amount_msat": 10}]
        for bad in ({"observed_by": "payer", "payment_hash": h, "amount_msat": True},
                    {"observed_by": "payer", "payment_hash": h, "amount_msat": "10"},
                    {"observed_by": "payer", "payment_hash": h}, {"payment_hash": h}):
            with self.subTest(bad=bad):
                _, cross = self.crosscheck_of({
                    "brattain": good,
                    "shockley": [{"observed_by": "provider", "payment_hash": h, "credited_msat": 9, "fee_msat": 1},
                                 bad]})
                self.assertEqual(cross["paired"], {})
                self.assertIn("shockley has no payer or provider role or no integer amounts", cross["unpaired"][h])

    def test_the_crosscheck_says_so_when_every_hash_pairs_and_when_there_is_nothing_to_compare(self):
        both = [({"brattain": [{"observed_by": "payer", "payment_hash": "a" * 64, "amount_msat": 10}],
                  "shockley": [{"observed_by": "provider", "payment_hash": "a" * 64, "credited_msat": 9,
                                "fee_msat": 1}]}, "pair every payment hash"),
                ({"brattain": [], "shockley": []}, "hold no payment hash to compare")]
        for rows, expect in both:
            with self.subTest(expect=expect):
                r, _ = self.crosscheck_of(rows)
                self.assertIn(expect, next(n["why"] for n in r["not_checked"] if "preimages" in n["what"]))

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
