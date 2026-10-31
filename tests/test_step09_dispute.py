"""steps/09-dispute: the referee's signed Result on a twin pair, held by each node it concerns with the same bytes, or FAIL.

The step needs only the openssl command (3.0 or later) beside the standard
library; without it these tests SKIP and say why, or, with LOOP_REQUIRE_TOOLS=1
(CI, after the setups ran), fail to load. The rehearsal acts out one
adjudication per test on the TEST keys; each planted failure is one participant
misbehaving (steps/09-dispute's act(plant=...)). The live path runs on
tests/fixtures/step09/live, made by make_fixtures.py.
"""
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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "step09"
POP = ROOT / "population" / "rehearsal"
STRANGER = FIX / "keys" / "stranger"


def load_step():
    loader = importlib.machinery.SourceFileLoader("step09_dispute", str(ROOT / "steps" / "09-dispute"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


step = load_step()
finding, aac = step.finding, step.aac


def openssl_3():
    if shutil.which("openssl") is None:
        return False
    v = subprocess.run(["openssl", "version"], capture_output=True, text=True).stdout.split()
    return len(v) > 1 and v[1][:1].isdigit() and int(v[1].split(".")[0]) >= 3


NO_TOOL = "openssl 3 is not installed: run steps/09-dispute.setup.sh (CI does)"
if not openssl_3():
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":  # CI, after the setups: a missing tool is an error
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_TOOL}")
    print(f"\nSKIP tests.test_step09_dispute: {NO_TOOL}", file=sys.stderr)
    raise unittest.SkipTest(NO_TOOL)


def tree_digest(d):
    h = hashlib.sha256()
    for p in sorted(Path(d).rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(d)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "findings" / "09-dispute"

    def tearDown(self):
        self.tmp.cleanup()

    def finding(self):
        self.assertEqual(finding.check(self.out.parent), [])
        return json.loads((self.out / "finding.json").read_text())

    def rehearse(self, plant=None):
        """Act out one adjudication with `plant`, decide, and return (finding, the failed (party, check) pairs)."""
        step.clean(self.out)
        step.act(POP, self.out, "2026-10-28T12:00:00Z", plant=plant, stranger=STRANGER)
        step.decide(self.out, population="rehearsal", started="2026-10-28T12:00:00Z")
        checks = json.loads((self.out / "checks.json").read_text())
        failed = {(p, k) for p, rs in checks.items() for r in rs for k, c in r["checks"].items()
                  if c is not None and not c["ok"]}
        return self.finding(), failed


class VerifierTest(unittest.TestCase):
    """The step's verifier, against a verdict the pinned capsules plugin's own tests sign."""

    def setUp(self):
        fx = json.loads((FIX / "referee-verdict.json").read_text())
        self.capsule, self.key = fx["verdict_capsule"], fx["referee_key_id"]

    def test_the_plugins_verdict_verifies_under_its_referees_key(self):
        signer, detail = aac.verify(self.capsule)
        self.assertEqual(signer, self.key, detail)

    def test_an_altered_verdict_does_not_verify(self):
        altered = json.loads(json.dumps(self.capsule).replace("contradicted:", "contradicted:x"))
        self.assertIsNone(aac.verify(altered)[0])
        resealed = dict(altered, capsule_id=aac.capsule_id(altered))
        signer, detail = aac.verify(resealed)
        self.assertIsNone(signer)
        self.assertIn("signs another capsule_id", detail)

    def test_a_signature_by_another_key_does_not_verify_as_the_named_one(self):
        forged = aac.seal(self.capsule, STRANGER)
        self.assertEqual(aac.verify(forged)[0], aac.key_id_of_pem(STRANGER / "node-key.pub.pem"))
        self.assertIsNone(aac.verify(dict(forged, key_id=self.key))[0])

    def test_a_missing_or_truncated_signature_does_not_verify(self):
        for sig in (None, "", "d284", "d28443a10127", 7):
            capsule = dict(self.capsule, signature=sig)
            self.assertIsNone(aac.verify(capsule)[0], sig)
        self.assertIsNone(aac.verify({k: v for k, v in self.capsule.items() if k != "signature"})[0])

    def test_the_plugins_verdict_passes_the_adjudication_check_with_the_plugins_fact_keys(self):
        self.assertTrue(step.is_adjudication(self.capsule)["ok"], step.is_adjudication(self.capsule))
        self.assertEqual(set(step.facts_block(self.capsule)),
                         {"verdict", "verdict_capsule_id", "referee_node_id", "halves", "half_node_ids",
                          "twin_bracket_id"})

    def test_a_result_with_both_halves_from_one_node_is_not_an_adjudication(self):
        v = json.loads(json.dumps(self.capsule))
        b = v["model_attestation"]["compute_attestation"]["adjudication"]
        b["half_b_node_id"] = b["half_a_node_id"]
        self.assertFalse(step.is_adjudication(v)["ok"])
        del b["half_b_node_id"]
        self.assertFalse(step.is_adjudication(v)["ok"])

    def test_the_outcome_puts_each_twin_in_the_plugins_bucket(self):
        b = self.capsule["model_attestation"]["compute_attestation"]["adjudication"]
        a_id, b_id = b["half_a_node_id"], b["half_b_node_id"]
        self.assertEqual(step.outcome(self.capsule), {a_id: "corroborated", b_id: "contradicted"})
        v = json.loads(json.dumps(self.capsule))
        for ruling in ("corroborated", "inconclusive", "not_comparable"):
            v["model_attestation"]["compute_attestation"]["adjudication"]["verdict"] = ruling
            self.assertEqual(step.outcome(v), {a_id: ruling, b_id: ruling})
        v["model_attestation"]["compute_attestation"]["adjudication"]["verdict"] = "contradicted:" + "9" * 64
        self.assertEqual(step.outcome(v), {})

    def test_the_facts_carry_the_model_the_verdict_seals(self):
        v = json.loads(json.dumps(self.capsule))
        self.assertNotIn("model_hash", step.facts_block(v))
        v["model_attestation"]["compute_attestation"]["adjudication"]["model_hash"] = "ab" * 32
        self.assertEqual(step.facts_block(v)["model_hash"], "ab" * 32)

    def test_a_seal_reads_back_as_the_plugins_envelope(self):
        sealed = aac.seal(self.capsule, STRANGER)
        protected = aac._enc({3: aac.CONTENT_TYPE, 4: bytes.fromhex(sealed["key_id"]), 1: aac.EDDSA})
        self.assertTrue(sealed["signature"].startswith("d284" + aac._head(2, len(protected)).hex() + protected.hex()))
        orig = self.capsule["signature"]
        self.assertTrue(orig.startswith("d284" + aac._head(2, len(protected)).hex() + aac._enc(
            {3: aac.CONTENT_TYPE, 4: bytes.fromhex(self.key), 1: aac.EDDSA}).hex()))

    def test_numbers_take_their_jcs_form(self):
        for v, want in ((0.7, "0.7"), (1e-7, "1e-7"), (1e21, "1e+21"), (1e20, "100000000000000000000"),
                        (123.0, "123"), (-0.5, "-0.5"), (0.000001, "0.000001")):
            self.assertEqual(aac.canonical(v).decode(), want)


class RehearsalTest(Tmp):
    def test_rehearsal_population_passes(self):
        self.assertEqual(step.main(["--population", str(POP), "--out", str(self.out)]), 0)
        r = self.finding()
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertEqual(r["not_checked"], [])
        self.assertEqual(r["pinned"], {})
        for c in step.CHECKS:
            self.assertIn(c, r["detail"])
        self.assertEqual(sorted(i for _, i in finding.gaps(r["detail"])),
                         ["Mesh-LLM/capsules-plugin#126", "Mesh-LLM/capsules-plugin#49"])
        self.assertIn("Spend: none", r["detail"])
        self.assertIn("adjudications only", r["detail"])
        proof = {p["path"] for p in r["proof"]}
        for f in ("requester/ledger/received-adjudications.jsonl", "bob/ledger/capsules.jsonl",
                  "referee/ledger/issued-adjudications.jsonl", "outcome.json", "checks.json"):
            self.assertIn(f, proof)

    def test_the_outcome_is_what_the_result_rules_about_each_twin(self):
        self.rehearse()
        doc = json.loads((POP / "step09" / "dispute.json").read_text())
        alice, bob = doc["nodes"]["alice"]["node_id"], doc["nodes"]["bob"]["node_id"]
        rows = json.loads((self.out / "outcome.json").read_text())["results"]
        self.assertEqual({(x["party"], json.dumps(x["twins"], sort_keys=True), x["all_checks_held"]) for x in rows},
                         {(p, json.dumps({alice: "corroborated", bob: "contradicted"}, sort_keys=True), True)
                          for p in ("requester", "alice", "bob")})

    def test_each_party_holds_the_referees_bytes(self):
        self.rehearse()
        issued = json.loads((self.out / "referee" / "ledger" / "issued-adjudications.jsonl").read_text())
        for party in ("requester", "alice", "bob"):
            held = json.loads((self.out / party / "ledger" / "received-adjudications.jsonl").read_text())
            self.assertEqual(aac.canonical(held["verdict_capsule"]), aac.canonical(issued["verdict_capsule"]))


class PlantedFailureTest(Tmp):
    """Each planted failure turns the finding FAIL, on the check that owns it."""

    def assert_fails(self, plant, check, parties=("requester", "alice", "bob")):
        r, failed = self.rehearse(plant)
        self.assertEqual(r["result"], "FAIL", r["detail"])
        for p in parties:
            self.assertIn((p, check), failed, f"{plant}: {p} {check} did not fail")
        self.assertIn(check, r["detail"])
        return r

    def test_an_adjudication_that_cites_no_halves_fails(self):
        self.assert_fails("uncited", "cites_halves")

    def test_an_adjudication_citing_halves_not_in_the_book_fails(self):
        r = self.assert_fails("foreign_exchange", "cites_halves")
        self.assertIn("is not in the party's book", r["detail"])

    def test_an_adjudication_signed_by_a_key_not_the_referees_fails(self):
        r = self.assert_fails("wrong_key", "referee_signed")
        self.assertIn("not the key", r["detail"])

    def test_a_received_result_whose_bytes_differ_from_the_issued_one_fails(self):
        r = self.assert_fails("received_differs", "matches_issued")
        self.assertIn("the referee holds no Result with these bytes", r["detail"])

    def test_a_party_sealed_close_presented_as_the_adjudication_fails(self):
        r = self.assert_fails("party_close", "result_is_adjudication", parties=("requester",))
        self.assertIn("party-path block close", r["detail"])

    def test_altered_proof_bytes_fail(self):
        self.rehearse()
        path = self.out / "alice" / "ledger" / "received-adjudications.jsonl"
        path.write_text(path.read_text().replace('"status":"CONTRADICTED"', '"status":"CORROBORATED"'))
        step.decide(self.out, population="rehearsal", started="2026-10-28T12:00:00Z")
        r = self.finding()
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("alice Result", r["detail"])
        self.assertIn("result_signed", r["detail"])

    def test_an_issued_record_stating_other_facts_than_the_result_fails(self):
        self.rehearse()
        path = self.out / "referee" / "ledger" / "capsules.jsonl"
        recs = [json.loads(ln) for ln in path.read_text().splitlines()]
        for i, r in enumerate(recs):
            blk = r.get("model_attestation", {}).get("compute_attestation", {}).get("adjudication_issued")
            if blk:
                blk["verdict"] = "corroborated"
                recs[i] = aac.seal(r, POP / "step09" / "keys" / "referee")
        path.write_text("".join(json.dumps(r) + "\n" for r in recs))
        step.decide(self.out, population="rehearsal", started="2026-10-28T12:00:00Z")
        r = self.finding()
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("adjudication_issued facts differ from the Result's: verdict", r["detail"])

    def test_the_rehearsal_status_is_the_plugins_for_each_ruling(self):
        self.assertEqual(step.STATUS["corroborated"], "SATISFIED")
        self.assertEqual({step.STATUS[r] for r in ("inconclusive", "not_comparable")}, {"UNKNOWN"})
        self.rehearse()
        held = json.loads((self.out / "alice" / "ledger" / "received-adjudications.jsonl").read_text())
        b = held["verdict_capsule"]["model_attestation"]["compute_attestation"]["adjudication"]
        self.assertEqual(b["status"], step.STATUS[b["verdict"].split(":")[0]])

    def test_a_judged_twin_holds_only_its_own_half_and_passes(self):
        r, failed = self.rehearse()
        self.assertEqual(r["result"], "PASS", r["detail"])
        checks = json.loads((self.out / "checks.json").read_text())
        self.assertIn("its own half", checks["bob"][0]["checks"]["cites_halves"]["detail"])
        self.assertIn("both halves", checks["requester"][0]["checks"]["cites_halves"]["detail"])

    def test_the_requester_missing_a_half_fails(self):
        self.rehearse()
        p = self.out / "requester" / "ledger" / "received-capsules.jsonl"
        p.write_text(p.read_text().splitlines()[0] + "\n")
        step.decide(self.out, population="rehearsal", started="2026-10-28T12:00:00Z")
        r = self.finding()
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("is not in the party's book", r["detail"])

    def test_a_twin_holding_none_of_the_cited_halves_fails(self):
        self.rehearse()
        p = self.out / "bob" / "ledger" / "capsules.jsonl"  # keep the adjudication_received record only
        p.write_text("\n".join(ln for ln in p.read_text().splitlines() if "adjudication_received" in ln) + "\n")
        step.decide(self.out, population="rehearsal", started="2026-10-28T12:00:00Z")
        r = self.finding()
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("none of the cited halves is in the party's book", r["detail"])

    def test_a_party_holding_no_result_fails(self):
        self.rehearse()
        (self.out / "alice" / "ledger" / "received-adjudications.jsonl").unlink()
        step.decide(self.out, population="rehearsal", started="2026-10-28T12:00:00Z")
        r = self.finding()
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("alice holds no Result", r["detail"])


class LiveTest(Tmp):
    def live(self, fixture, pop=None):
        with mock.patch.dict(os.environ, {"LAB_DIR": str(fixture)}):
            self.assertEqual(step.main(["--population", str(pop or fixture), "--out", str(self.out)]), 0)
        record = self.finding()
        # judge_live on a copy of only the cited files gives the same finding, with no LAB_DIR: what 08 checks.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "09-dispute"
            for p in record["proof"]:
                (work / p["path"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.out / p["path"], work / p["path"])
            with mock.patch.dict(os.environ, {"LAB_DIR": ""}):
                again = step.judge_live({"population": "live"}, work, record["started"])
            self.assertEqual((again["result"], again["not_checked"], again["detail"]),
                             (record["result"], record["not_checked"], record["detail"]))
        return record

    def copy(self):
        dest = Path(self.tmp.name) / "live"
        shutil.copytree(FIX / "live", dest)
        return dest

    def test_held_results_are_checked_read_only(self):
        before = tree_digest(FIX / "live")
        r = self.live(FIX / "live")
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertEqual(sorted(r["pinned"]), ["capsules"])
        self.assertEqual(tree_digest(FIX / "live"), before)
        self.assertIn("lab-1/ledger/received-adjudications.jsonl", {p["path"] for p in r["proof"]})

    def test_the_lab_nodes_as_they_are_here_are_not_checked(self):
        with mock.patch.dict(os.environ, {"LAB_DIR": ""}):
            self.assertEqual(step.main(["--population", str(ROOT / "population" / "live"), "--out", str(self.out)]), 0)
        r = self.finding()
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertEqual(r["pinned"]["capsules"]["ref"], finding.pins()["capsules"][1])
        issues = [n["issue"] for n in r["not_checked"]]
        self.assertIn("Mesh-LLM/capsules-plugin#126", issues)
        self.assertEqual(sum(i == step.live.ISSUE for i in issues), 3)

    def test_nodes_holding_no_result_are_not_checked_on_the_live_twin_trigger(self):
        d = self.copy()
        for f in d.rglob("*adjudications.jsonl"):
            f.unlink()
        r = self.live(d)
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertEqual(r["not_checked"], [step.NO_RESULT])

    def test_a_result_needing_an_unreadable_referee_is_not_checked(self):
        d = self.copy()
        pop = json.loads((d / "population.json").read_text())
        pop["nodes"][3]["data_dir"] = None
        (d / "population.json").write_text(json.dumps(pop))
        r = self.live(d)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        whats = " ".join(n["what"] for n in r["not_checked"])
        self.assertIn("matches_issued", whats)
        self.assertIn("referee_signed", whats)

    def test_a_result_the_referee_holds_with_an_inexact_number_is_a_failed_match_not_a_crash(self):
        d = self.copy()
        path = d / "nodes" / "lab-1" / "data" / "ledger" / "received-adjudications.jsonl"
        held = json.loads(path.read_text())
        held["verdict_capsule"]["big"] = 2 ** 60
        path.write_text(json.dumps(held) + "\n")
        r = self.live(d)
        self.assertEqual(r["result"], "FAIL")

    def test_an_injected_difference_is_labelled_plainly_in_the_finding(self):
        d = self.copy()
        held = json.loads((d / "nodes" / "lab-1" / "data" / "ledger" / "received-adjudications.jsonl").read_text())
        bracket = held["verdict_capsule"]["model_attestation"]["compute_attestation"]["adjudication"]["twin_bracket_id"]
        fault = {"injected_difference": {"node": "lab-4", "kind": "system_prompt", "value": "Answer in French."},
                 "roles": {"requester": "lab-3", "twin_a": "lab-1", "twin_b": "lab-4", "referee": "lab-2"},
                 "twin_bracket_id": "some-other-day"}
        (d / "twin-fault.json").write_text(json.dumps(fault))
        r = self.live(d)  # a label whose bracket no held Result carries is not applied
        self.assertNotIn("INJECTED DIFFERENCE", r["detail"])
        self.assertIn("not applied to this finding", r["detail"])
        (d / "twin-fault.json").write_text(json.dumps(dict(fault, twin_bracket_id=bracket)))
        r = self.live(d)
        self.assertIn("INJECTED DIFFERENCE", r["detail"])
        self.assertIn("system_prompt on lab-4 = 'Answer in French.'", r["detail"])
        self.assertIn("twin-fault.json", {p["path"] for p in r["proof"]})

    def test_no_injected_difference_is_claimed_without_the_lab_file(self):
        self.assertNotIn("INJECTED", self.live(self.copy())["detail"])
        with mock.patch.dict(os.environ, {"LAB_DIR": ""}):  # no LAB_DIR: never read a file from the working directory
            self.assertEqual(step.injected_difference(self.out, {"b"}), "")

    def test_empty_received_files_hold_no_result_and_are_not_checked(self):
        d = self.copy()
        for f in d.rglob("received-adjudications.jsonl"):
            f.write_text("")
        r = self.live(d)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])

    def test_a_referee_whose_key_is_unreadable_is_not_checked_not_failed(self):
        d = self.copy()
        key = d / "nodes" / "lab-4" / "data" / "keys" / "node-key.pub.pem"
        key.unlink()
        r = self.live(d)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])

    def test_a_held_result_that_is_not_the_referees_fails(self):
        d = self.copy()
        path = d / "nodes" / "lab-1" / "data" / "ledger" / "received-adjudications.jsonl"
        held = json.loads(path.read_text())
        v = held["verdict_capsule"]
        v["model_attestation"]["compute_attestation"]["adjudication"]["verdict"] = "corroborated"
        held["verdict_capsule"] = aac.seal(v, POP / "step09" / "keys" / "referee")
        path.write_text(json.dumps(held) + "\n")
        r = self.live(d)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("lab-1 Result", r["detail"])
        self.assertIn("matches_issued", r["detail"])


if __name__ == "__main__":
    unittest.main()
