"""The Goose rehearsal driver (scripts/goose_rehearsal.py, loop#330): G1's two nodes, the mock rail paying their deal, step 01
judging the capsule the buyer sealed with loop_seal, step 03 the commitment each node signed with loop_advertise, and step 06 reading G1's own pay record and the nodes' own signed settlement disclosures against the rail (01, 02, 03, 06 and 07 pass). Skipped unless steps/goose.setup.sh has installed the
Goose tools; the step 01 test also needs steps/01-seal.setup.sh's verifier.

Run: python3 -m unittest tests.test_goose_rehearsal -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache" / "evidence-network-loop")) / "goose"


@unittest.skipUnless((TOOLS / "venv" / "bin" / "python").is_file(), "run steps/goose.setup.sh to install the Goose tools")
class GooseRehearsalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.tmp.name)
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/goose_rehearsal.py"), "--work", str(cls.work)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        cls.pop = cls.work / "population"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_session_seals_with_the_model_step_01_tells_its_producer(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("seal01", str(ROOT / "steps" / "01-seal"))
        seal01 = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(seal01)
        text = (ROOT / "scripts" / "goose_session.py").read_text()
        self.assertIn(f'provider="{seal01.MODEL["provider"]}", model_id="{seal01.MODEL["model_id"]}"', text)

    def test_population_names_the_two_goose_nodes_and_the_steps_with_an_input(self):
        doc = json.loads((self.pop / "population.json").read_text())
        self.assertEqual([n["name"] for n in doc["nodes"]], ["buyer", "seller"])
        self.assertEqual(doc["rail"], "mock")
        self.assertEqual(doc["goose"]["steps"], ["01", "02", "03", "06", "07"])

    def test_step_03_passes_on_the_commitments_the_goose_nodes_signed(self):
        out = self.work / "findings" / "03-advertise"
        r = subprocess.run([str(ROOT / "steps/03-advertise"), "--population", str(self.pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        found = json.loads((out / "finding.json").read_text())
        self.assertEqual(found["result"], "PASS", found["detail"])
        doc = json.loads((self.pop / "population.json").read_text())
        for n in json.loads((out / "verification.json").read_text())["nodes"]:
            # published byte for byte as loop_advertise wrote it, under the node's own key, stapling goose/node
            self.assertEqual(n["inputs"], "signed by the node")
            signed = doc["goose"]["commitments"][n["name"]]["commitment"]
            self.assertEqual((out / f"{n['name']}.commitment.json").read_bytes(), Path(signed).read_bytes())
            self.assertEqual([(c["stream"], c["community"]) for c in n["history_cards"]], [("goose/node", "goose")])

    @unittest.skipUnless((TOOLS.parent / "01-seal" / "bin" / "cross_language").is_file(), "run steps/01-seal.setup.sh")
    def test_step_01_passes_unchanged_on_the_capsule_the_goose_node_sealed(self):
        out = self.work / "findings" / "01-seal"
        r = subprocess.run([str(ROOT / "steps/01-seal"), "--population", str(self.pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        found = json.loads((out / "finding.json").read_text())
        self.assertEqual(found["result"], "PASS", found["detail"])
        # the capsule checked is the Goose node's own, not one step 01 sealed itself, and the finding says so
        self.assertEqual((out / "capsule.json").read_bytes(), (self.work / "run" / "sealed-buyer.json").read_bytes())
        sealed_by = json.loads((out / "checks.json").read_text())["sealed_by"]
        self.assertIn("Goose node buyer, tool loop_seal, goose-g1 ", sealed_by)
        self.assertEqual(sorted(found["pinned"]), ["aac-go", "goose-g1"])
        self.assertIn("sealed by the Goose node", found["asserts"])
        installed = json.loads((out / "checks.json").read_text())["installed"]
        self.assertRegex(installed["capsule-emit"], r"^\d+\.\d+\.\d+")  # the Goose venv's, which is not the mesh pin
        self.assertNotIn("capsule-emit", found["pinned"])
        self.assertTrue({"node-key.pub.pem", "goose-run.log"} <= {x["path"] for x in found["proof"]})

    @unittest.skipUnless((TOOLS.parent / "01-seal" / "bin" / "cross_language").is_file(), "run steps/01-seal.setup.sh")
    def test_an_explicit_sealed_capsule_is_never_labelled_as_the_goose_nodes(self):
        planted = self.work / "planted.json"
        planted.write_bytes((self.work / "run" / "sealed-buyer.json").read_bytes())
        out = self.work / "findings-planted" / "01-seal"
        r = subprocess.run([str(ROOT / "steps/01-seal"), "--population", str(self.pop), "--out", str(out),
                            "--sealed", str(planted)], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        found = json.loads((out / "finding.json").read_text())
        self.assertNotIn("goose-g1", found["pinned"])
        self.assertEqual(json.loads((out / "checks.json").read_text())["sealed_by"], "given capsule")

    def _step_02(self, pop, name):
        out = self.work / name / "02-book"
        r = subprocess.run([str(ROOT / "steps/02-book"), "--population", str(pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, json.loads((out / "finding.json").read_text())

    @unittest.skipUnless((TOOLS.parent / "02-book" / "bin" / "loop-step02-book").is_file(), "run steps/02-book.setup.sh")
    def test_step_02_passes_on_the_book_bundle_and_witness_receipt_the_goose_node_wrote(self):
        out, found = self._step_02(self.pop, "findings")
        self.assertEqual(found["result"], "PASS", found["detail"])
        checks = json.loads((out / "checks.json").read_text())["checks"]
        self.assertEqual(sorted(checks), ["bundle", "checkpoint", "membership", "position", "record", "signer",
                                          "witness_listed", "witness_not_before", "witness_not_node", "witness_signature"])
        # the bytes judged are the ones loop_book_bundle wrote, not ones the step made
        self.assertEqual((out / "bundle.json").read_bytes(), (self.work / "run" / "book-bundle-buyer.json").read_bytes())
        self.assertIn("goose-g1", found["pinned"])

    @unittest.skipUnless((TOOLS.parent / "02-book" / "bin" / "loop-step02-book").is_file(), "run steps/02-book.setup.sh")
    def test_step_02_fails_a_bundle_whose_record_was_altered_and_a_receipt_from_the_nodes_own_key(self):
        import shutil
        doc = json.loads((self.pop / "population.json").read_text())
        run = self.work / "planted-run"
        shutil.copytree(self.work / "run", run, ignore=shutil.ignore_patterns("buyer", "seller", "*.key", "witness-key.pem"))
        stem = run / "book-bundle-buyer"
        # a record altered after it was sealed
        bundle = json.loads((run / "book-bundle-buyer.json").read_text())
        rec = next(r for r in bundle["records"] if r["capsule_id"] == bundle["root"])
        rec["operator"] = "someone-else"
        (run / "book-bundle-buyer.json").write_text(json.dumps(bundle))
        doc["goose"]["book"] = {**doc["goose"]["book"], "bundle": str(run / "book-bundle-buyer.json"),
                                "witnesses": str(run / "witnesses.json")}
        pop = self.work / "planted-pop"
        pop.mkdir()
        (pop / "population.json").write_text(json.dumps(doc))
        _, found = self._step_02(pop, "findings-altered")
        self.assertEqual(found["result"], "FAIL", found["detail"])
        self.assertIn("bundle", found["detail"])
        # a witness listing that does not list the receipt's key
        shutil.copyfile(self.work / "run" / "book-bundle-buyer.json", run / "book-bundle-buyer.json")
        (run / "witnesses.json").write_text(json.dumps({"witnesses": [{"name": "w", "key_id": "00" * 32,
                                                                       "not_before": "2026-01-01T00:00:00Z"}]}))
        _, found = self._step_02(pop, "findings-unlisted")
        self.assertEqual(found["result"], "FAIL", found["detail"])
        self.assertIn("witness_listed", found["detail"])

    def test_step_02_is_not_checked_when_the_run_wrote_no_book_bundle(self):
        doc = json.loads((self.pop / "population.json").read_text())
        doc["goose"].pop("book")
        pop = self.work / "nobook-pop"
        pop.mkdir()
        (pop / "population.json").write_text(json.dumps(doc))
        _, found = self._step_02(pop, "findings-nobook")
        self.assertEqual(found["result"], "NOT_CHECKED", found["detail"])

    def test_step_07_passes_on_asks_the_goose_nodes_answer_by_polling_their_mailboxes(self):
        out = self.work / "findings" / "07-ask"
        r = subprocess.run([str(ROOT / "steps/07-ask"), "--population", str(self.pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        found = json.loads((out / "finding.json").read_text())
        self.assertEqual(found["result"], "PASS", found["detail"])
        self.assertIn("ask backend: goose", found["detail"])
        self.assertIn("goose-g1", found["pinned"])
        # both nodes asked each other all three asks: a signed refusal (a stranger on a mailbox), a signed
        # artifact, and an absence whose late reply the asker kept
        for asker, asked in (("buyer", "seller"), ("seller", "buyer")):
            self.assertIn(f"{asker} asked {asked}: exchange refusal, exchange_late recorded_absence, "
                          "history_card artifact", found["detail"])
        asks = self.work / "asks"
        for who in ("buyer", "seller"):
            node_dir = next((asks / "run" / who).glob("shelf.db"))
            self.assertTrue(node_dir.is_file())

    def _judged(self, mutate):
        """Step 07 on a copy of the asks with `mutate(copy)` applied: the finding."""
        import shutil
        work = Path(tempfile.mkdtemp(dir=self.work))
        pop = work / "population"
        shutil.copytree(self.work / "asks", work / "asks", ignore=shutil.ignore_patterns("run"))
        doc = json.loads((self.pop / "population.json").read_text())
        doc["goose"]["asks"] = {"run": str(work / "asks")}
        pop.mkdir()
        (pop / "population.json").write_text(json.dumps(doc))
        mutate(work / "asks")
        out = work / "findings" / "07-ask"
        r = subprocess.run([str(ROOT / "steps/07-ask"), "--population", str(pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((out / "finding.json").read_text())

    def test_step_07_fails_a_reply_that_is_not_the_one_the_response_record_keeps(self):
        def swap(asks):
            (asks / "buyer/asks/seller/history_card/answer.json").write_bytes(
                (asks / "buyer/asks/seller/exchange/answer.json").read_bytes())
        found = self._judged(swap)
        self.assertEqual(found["result"], "FAIL")
        self.assertIn("the reply bytes are not the ones the response record keeps", found["detail"])

    def test_step_07_fails_an_absence_when_a_reply_was_delivered_by_the_deadline(self):
        def early(asks):
            log = asks / "seller/asks/buyer/carrier.jsonl"
            events = [json.loads(line) for line in log.read_text().splitlines()]
            sent = next(e for e in events if e["ask"] == "exchange_late" and e["event"] == "sent")
            for e in events:
                if e["ask"] == "exchange_late" and e["event"] == "delivered":
                    e["at"] = sent["at"]
            log.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in events))
        found = self._judged(early)
        self.assertEqual(found["result"], "FAIL")
        self.assertIn("the reply was delivered by the deadline", found["detail"])

    def test_step_07_fails_a_request_whose_deadline_is_not_the_commitments_answers_within(self):
        """Judged against a commitment that advertises another answers_within, so only the deadline check can fail."""
        import importlib.machinery
        import importlib.util
        import shutil
        import datetime as dt
        loader = importlib.machinery.SourceFileLoader("ask07", str(ROOT / "steps" / "07-ask"))
        ask07 = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(ask07)
        work = Path(tempfile.mkdtemp(dir=self.work))
        shutil.copytree(self.work / "asks", work / "asks", ignore=shutil.ignore_patterns("run"))
        real = ask07.advertisement

        def other_window(adv_dir, name="commitment.json"):
            adv, errs = real(adv_dir, name)
            adv["within"], adv["answers_within"] = dt.timedelta(minutes=5), "PT5M"
            return adv, errs

        ask07.advertisement = other_window
        keys = json.loads((work / "asks" / "nodes.json").read_text())
        failures, _, _, _ = ask07.judge_run(work / "asks", {n: v["key_id"] for n, v in keys.items()},
                                            ask07.tools(), work / "tmp", mailbox=True)
        self.assertTrue(failures)
        self.assertTrue(all("is not the commitment's answers_within" in f for f in failures), failures)

    def judge06(self, pop):
        out = Path(tempfile.mkdtemp(dir=self.work)) / "06-settle"
        r = subprocess.run([str(ROOT / "steps/06-settle"), "--population", str(pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((out / "finding.json").read_text()), json.loads((out / "checks.json").read_text())["checks"], out

    def tampered_population(self, edit):
        """A copy of the rehearsal population whose step06 inputs `edit(step06_dir)` changes."""
        import shutil
        pop = Path(tempfile.mkdtemp(dir=self.work)) / "population"
        shutil.copytree(self.pop, pop)
        edit(pop / "step06")
        return pop

    def test_step_06_passes_on_the_exchange_read_from_g1s_pay_record_and_the_nodes_own_disclosures(self):
        deal = json.loads((self.work / "run" / "deal.json").read_text())
        found, checks, out = self.judge06(self.pop)
        self.assertEqual(found["result"], "PASS", found["detail"])
        self.assertTrue(all(c["ok"] for c in checks.values()), checks)
        self.assertIn("pay_record", checks)
        # no exchange or disclosure file is the driver's: the step derived the exchange from G1's halves
        self.assertFalse((self.pop / "step06" / "exchange.json").exists())
        exchange = json.loads((out / "exchange.json").read_text())
        self.assertEqual((exchange["exchange_id"], exchange["payer"], exchange["payee"]),
                         (deal["deal_id"], deal["buyer"], deal["seller"]))
        self.assertEqual(exchange["settlement_ref"]["settlement_id"], deal["settlement_id"])
        for n in ("buyer", "seller"):
            self.assertEqual(json.loads((self.pop / "step06" / f"disclosure-{n}.json").read_text())["node"], f"goose-{n}")

    def test_step_06_fails_when_the_opened_reference_is_not_the_one_the_pay_record_commits(self):
        def edit(d):
            doc = json.loads((d / "buyer-pay-opening.json").read_text())
            local = json.loads(doc["opening"]["text"])
            local["act"]["reference"] = "0" * 64
            doc["opening"]["text"] = json.dumps(local, sort_keys=True, separators=(",", ":"))
            (d / "buyer-pay-opening.json").write_text(json.dumps(doc))
        found, checks, _ = self.judge06(self.tampered_population(edit))
        self.assertEqual(found["result"], "FAIL")
        self.assertFalse(checks["pay_record"]["ok"])

    def test_step_06_fails_when_the_pay_record_names_another_settlement_than_the_rails(self):
        def edit(d):  # the buyer's half no longer holds a pay record
            half = json.loads((d / "buyer-half.json").read_text())
            half["steps"] = [s for s in half["steps"] if s["payload"].get("body", {}).get("action") != "pay"]
            (d / "buyer-half.json").write_text(json.dumps(half))
        found, checks, _ = self.judge06(self.tampered_population(edit))
        self.assertEqual(found["result"], "FAIL")
        self.assertFalse(checks["pay_record"]["ok"])

    def test_step_06_fails_halves_whose_producer_keys_are_not_the_populations_nodes(self):
        def other_reference(d):  # foreign producer keys; a reference that is not the rail's falls to reference and exchange_match
            half = json.loads((d / "buyer-half.json").read_text())
            for st in half["steps"]:
                st["capsule"]["key_id"] = "ab" * 32
            (d / "buyer-half.json").write_text(json.dumps(half))
        found, checks, _ = self.judge06(self.tampered_population(other_reference))
        self.assertEqual(found["result"], "FAIL")
        self.assertFalse(checks["pay_record"]["ok"])
        self.assertIn("two distinct nodes", checks["pay_record"]["detail"])

    def test_step_06_fails_a_pay_payload_its_header_does_not_commit(self):
        def edit(d):
            half = json.loads((d / "buyer-half.json").read_text())
            for st in half["steps"]:
                if st["payload"].get("body", {}).get("action") == "pay" and "reference_commitment" in st["payload"]["body"]:
                    st["payload"]["body"]["amount_minor"] += 1
            (d / "buyer-half.json").write_text(json.dumps(half))
        found, checks, _ = self.judge06(self.tampered_population(edit))
        self.assertEqual(found["result"], "FAIL")
        self.assertIn("not the one its header commits", checks["pay_record"]["detail"])

    def test_step_06_fails_a_node_disclosure_whose_count_or_signature_is_not_the_nodes_own(self):
        def edit(d):
            doc = json.loads((d / "disclosure-seller.json").read_text())
            doc["settlements"] = 2  # signed for 1: the signature no longer verifies
            (d / "disclosure-seller.json").write_text(json.dumps(doc))
        found, checks, _ = self.judge06(self.tampered_population(edit))
        self.assertEqual(found["result"], "FAIL")
        self.assertFalse(checks["ledger_count:seller"]["ok"])
        self.assertTrue(checks["ledger_count:buyer"]["ok"])


if __name__ == "__main__":
    unittest.main()
