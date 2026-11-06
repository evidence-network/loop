"""The Goose rehearsal driver (scripts/goose_rehearsal.py, loop#330): G1's two nodes, the mock rail paying their deal, step 01
judging the capsule the buyer sealed with loop_seal, step 03 the commitment each node signed with loop_advertise, and step 06 reading G1's own pay record and the nodes' own signed settlement disclosures against the rail (01, 02, 03, 04, 05, 06, 07 and 12 pass). Skipped unless steps/goose.setup.sh has installed the
Goose tools; the step 01 test also needs steps/01-seal.setup.sh's verifier, step 04 capsulectl, and the step 02 tests the step 02 driver, capsulectl and
openssl 3 (steps/02-book.setup.sh, steps/12-join.setup.sh).

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
        self.assertEqual(doc["goose"]["steps"], ["01", "02", "03", "04", "05", "06", "07", "12"])

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

    def _step_02_ready(self):
        sys.path.insert(0, str(ROOT / "steps"))
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("step02_book_tests", str(ROOT / "steps" / "02-book"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        _, missing = mod.capsulectl()
        if mod.driver() is None or missing or not mod.openssl_ed25519():
            if os.environ.get("LOOP_REQUIRE_TOOLS"):
                self.fail(f"step 02 tools missing: driver {mod.driver()}, {missing}")
            self.skipTest("run steps/02-book.setup.sh and steps/12-join.setup.sh (and have openssl 3)")

    def test_step_02_passes_on_the_book_bundle_and_witness_receipt_the_goose_node_wrote(self):
        self._step_02_ready()
        out, found = self._step_02(self.pop, "findings")
        self.assertEqual(found["result"], "PASS", found["detail"])
        checks = json.loads((out / "checks.json").read_text())["checks"]
        self.assertEqual(sorted(checks), ["bundle", "checkpoint", "membership", "position", "record", "signer",
                                          "witness_listed", "witness_not_before", "witness_not_node", "witness_signature"])
        # the bytes judged are the ones loop_book_bundle wrote, not ones the step made
        self.assertEqual((out / "bundle.json").read_bytes(), (self.work / "run" / "book-bundle-buyer.json").read_bytes())
        self.assertIn("goose-g1", found["pinned"])

    def _planted(self, name, mutate):
        """Step 02's checks.json on a copy of the run with `mutate(run, book)` applied: (finding, checks)."""
        import shutil
        doc = json.loads((self.pop / "population.json").read_text())
        run = self.work / f"{name}-run"
        shutil.copytree(self.work / "run", run, ignore=shutil.ignore_patterns("buyer", "seller", "*.key", "witness-key.pem"))
        book = {**doc["goose"]["book"], "bundle": str(run / "book-bundle-buyer.json"),
                "witnesses": str(run / "witnesses.json")}
        mutate(run, book)
        doc["goose"]["book"] = book
        pop = self.work / f"{name}-pop"
        pop.mkdir()
        (pop / "population.json").write_text(json.dumps(doc))
        out, found = self._step_02(pop, name)
        return found, json.loads((out / "checks.json").read_text())["checks"]

    def test_step_02_fails_each_check_a_planted_bundle_or_receipt_breaks(self):
        self._step_02_ready()

        def bundle(run):
            return json.loads((run / "book-bundle-buyer.json").read_text())

        def save(run, b):
            (run / "book-bundle-buyer.json").write_text(json.dumps(b))

        def altered(run, book):  # a record altered after it was sealed
            b = bundle(run)
            next(r for r in b["records"] if r["capsule_id"] == b["root"])["operator"] = "someone-else"
            save(run, b)

        def unlisted(run, book):  # a witness listing that does not list the receipt's key
            (run / "witnesses.json").write_text(json.dumps({"witnesses": [{
                "name": "w", "key_id": "00" * 32, "not_before": "2026-01-01T00:00:00Z"}]}))

        def not_yet(run, book):  # a listing whose entry counts receipts only from after this one
            w = json.loads((run / "witnesses.json").read_text())
            w["witnesses"][0]["not_before"] = "2999-01-01T00:00:00Z"
            (run / "witnesses.json").write_text(json.dumps(w))

        def signed_by(pem):  # a receipt really signed by a key of one of the run's nodes, and that key listed
            def mutate(run, book):
                driver = TOOLS.parent / "02-book" / "bin" / "loop-step02-book"
                got = json.loads(subprocess.run(
                    [str(driver), "lab-receipt", "--key", str(pem), "--statement", str(run / "book-bundle-buyer.checkpoint.cose"),
                     "--at", "1793937188"], capture_output=True, text=True, check=True).stdout)
                b = bundle(run)
                b["checkpoint"]["witnesses"] = [{"ts_url": "http://127.0.0.1:1", "entry_hash": got["entry_hash"],
                                                 "receipt_b64": got["receipt_b64"], "leaf_index": got["leaf_index"],
                                                 "tree_size": got["tree_size"]}]
                save(run, b)
                pub = subprocess.run(["openssl", "pkey", "-in", str(pem), "-pubout", "-outform", "DER"],
                                     capture_output=True, check=True).stdout[-32:].hex()
                (run / "witnesses.json").write_text(json.dumps({"witnesses": [{
                    "name": "w", "key_id": pub, "not_before": "2026-01-01T00:00:00Z"}]}))
            return mutate

        def other_checkpoint_key(run, book):
            book["checkpoint_key"] = "11" * 32

        def other_book(run, book):
            book["book_id"] = "goose/other"

        def other_capsule(run, book):
            c = json.loads(Path(book["capsule"]).read_text())
            c["capsule_id"] = "22" * 32
            (run / "other-capsule.json").write_text(json.dumps(c))
            book["capsule"] = str(run / "other-capsule.json")

        def altered_receipt(run, book):  # the receipt's entry hash is not the bundle checkpoint's
            b = bundle(run)
            b["checkpoint"]["witnesses"][0]["entry_hash"] = "33" * 32
            save(run, b)

        for name, mutate, broken in (("altered", altered, {"bundle"}), ("unlisted", unlisted, {"witness_listed"}),
                                     ("notyet", not_yet, {"witness_not_before"}),
                                     ("buyerkey", signed_by(self.work / "keys" / "buyer" / "node-key.pem"), {"witness_not_node"}),
                                     ("sellerkey", signed_by(self.work / "keys" / "seller" / "node-key.pem"), {"witness_not_node"}),
                                     ("sellercheckpointkey", signed_by(self.work / "run" / "seller" / "checkpoint.key"), {"witness_not_node"}),
                                     ("cpkey", other_checkpoint_key, {"checkpoint"}), ("otherbook", other_book, {"position"}),
                                     ("othercapsule", other_capsule, {"record"}), ("receipt", altered_receipt, {"witness_signature"})):
            with self.subTest(name):
                found, checks = self._planted(name, mutate)
                self.assertEqual(found["result"], "FAIL", found["detail"])
                self.assertLessEqual(broken, {n for n, c in checks.items() if not c["ok"]}, checks)

    def test_step_02_is_not_checked_when_the_run_wrote_no_book_bundle(self):
        doc = json.loads((self.pop / "population.json").read_text())
        doc["goose"].pop("book", None)
        pop = self.work / "nobook-pop"
        pop.mkdir()
        (pop / "population.json").write_text(json.dumps(doc))
        _, found = self._step_02(pop, "findings-nobook")
        self.assertEqual(found["result"], "NOT_CHECKED", found["detail"])

    def test_step_02_is_not_checked_on_a_checkout_other_than_the_pin(self):
        doc = json.loads((self.pop / "population.json").read_text())
        doc["goose"]["rev"] = "0" * 40
        pop = self.work / "otherrev-pop"
        pop.mkdir()
        (pop / "population.json").write_text(json.dumps(doc))
        _, found = self._step_02(pop, "findings-otherrev")
        self._step_02_ready()
        self.assertEqual(found["result"], "NOT_CHECKED", found["detail"])
        self.assertEqual(found["not_checked"][0]["what"], "goose-g1")

    def test_a_run_without_a_usable_step_02_driver_still_passes_step_03_and_step_02_says_not_checked(self):
        for name, bin_ in (("missing", self.work / "no-such-driver"), ("plain-file", self.work / "plain")):
            with self.subTest(name):
                if name == "plain-file":
                    bin_.write_text("")  # exists, but is not executable
                work = self.work / f"nodriver-{name}"
                env = {**os.environ, "LOOP_STEP02_BOOK_BIN": str(bin_)}
                r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/goose_rehearsal.py"), "--work", str(work)],
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
                self.assertEqual(r.returncode, 0, r.stderr)
                pop = work / "population"
                self.assertNotIn("book", json.loads((pop / "population.json").read_text())["goose"])
                for step, result in (("02-book", "NOT_CHECKED"), ("03-advertise", "PASS")):
                    out = work / "findings" / step
                    done = subprocess.run([str(ROOT / "steps" / step), "--population", str(pop), "--out", str(out)],
                                          stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertEqual(json.loads((out / "finding.json").read_text())["result"], result)

    def _step_04(self, pop, name):
        out = self.work / name / "04-admit"
        r = subprocess.run([str(ROOT / "steps/04-admit"), "--population", str(pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, json.loads((out / "finding.json").read_text())

    def _step_04_ready(self):
        if not (TOOLS.parent / "bin" / "capsulectl").is_file():
            if os.environ.get("LOOP_REQUIRE_TOOLS"):
                self.fail("capsulectl is not installed")
            self.skipTest("run steps/12-join.setup.sh")

    def test_step_04_passes_on_the_admissions_and_the_deal_opening_the_goose_buyer_sealed(self):
        self._step_04_ready()
        out, found = self._step_04(self.pop, "findings")
        self.assertEqual(found["result"], "PASS", found["detail"])
        checks = json.loads((out / "checks.json").read_text())
        self.assertEqual({n: [c["check"] for c in cs] for n, cs in checks.items()}, {
            "go": ["bundle_verifies", "decision_sealed", "one_outcome", "goose_shape", "peer_named", "deal_sealed",
                   "sealed_before_deal", "deal_cites_decision", "first_step_cites_decision"],
            "no": ["bundle_verifies", "decision_sealed", "one_outcome", "goose_shape", "no_deal_after_no", "gate_refuses"]})
        self.assertEqual(json.loads((out / "counts.json").read_text())["by_outcome"], {"GO": 1, "NO": 1})
        # the bytes judged are the ones loop_book_bundle wrote
        self.assertEqual((out / "scenarios" / "go" / "bundle.json").read_bytes(),
                         (self.work / "run" / "admission-go-disclosed.json").read_bytes())

    def test_step_04_fails_each_check_a_planted_population_breaks(self):
        self._step_04_ready()
        import shutil

        def planted(name, mutate):
            doc = json.loads((self.pop / "population.json").read_text())
            run = self.work / f"{name}-run4"
            shutil.copytree(self.work / "run", run, ignore=shutil.ignore_patterns("buyer", "seller", "*.key", "witness-key.pem"))
            doc["goose"]["admit"].update(go=str(run / "admission-go-disclosed.json"), no=str(run / "admission-no-disclosed.json"),
                                         half=str(run / "buyer-half.json"), refused=str(run / "deal-refused.json"))
            mutate(doc, run)
            pop = self.work / f"{name}-pop4"
            pop.mkdir()
            (pop / "population.json").write_text(json.dumps(doc))
            out, found = self._step_04(pop, f"{name}-f4")
            failed = {f"{s}.{c['check']}" for s, cs in json.loads((out / "checks.json").read_text()).items() for c in cs if not c["ok"]}
            return found, failed

        def swapped(doc, run):  # the NO and the GO presented as each other
            a = doc["goose"]["admit"]
            a["go"], a["no"] = a["no"], a["go"]

        def other_buyer(doc, run):  # the decisions were not sealed by the population's buyer
            doc["goose"]["admit"]["node"] = "seller"
            doc["goose"]["admit"]["peer"] = "buyer"

        def other_peer(doc, run):  # the GO names a peer other than the seller
            doc["goose"]["admit"]["peer"] = "buyer"

        def half_without_cite(doc, run):  # the deal's first step cites nothing
            h = json.loads((run / "buyer-half.json").read_text())
            h["steps"][0]["capsule"]["evidence_record_header"]["links"] = []
            (run / "buyer-half.json").write_text(json.dumps(h))

        def refusal_missing(doc, run):  # the gate was never asked: the kept answer is a success
            (run / "deal-refused.json").write_text(json.dumps({"ok": True, "peer": "ab" * 32}))

        def half_forged(doc, run):  # the deal's first half altered after it was sealed
            h = json.loads((run / "buyer-half.json").read_text())
            h["steps"][0]["payload"]["body"]["terms"]["price_minor"] += 1
            (run / "buyer-half.json").write_text(json.dumps(h))

        for name, mutate, broken in (("swapped", swapped, {"go.decision_sealed", "no.decision_sealed"}),
                                     ("norefusal", refusal_missing, {"no.gate_refuses"}),
                                     ("forgedhalf", half_forged, {"go.first_step_cites_decision"}),
                                     ("otherbuyer", other_buyer, {"go.decision_sealed", "no.decision_sealed"}),
                                     ("otherpeer", other_peer, {"go.peer_named"}),
                                     ("nocite", half_without_cite, {"go.first_step_cites_decision"})):
            with self.subTest(name):
                found, failed = planted(name, mutate)
                self.assertEqual(found["result"], "FAIL", found["detail"])
                self.assertLessEqual(broken, failed)

    def test_step_04_judge_fails_a_no_that_a_record_cites_and_a_deal_sealed_before_its_decision(self):
        self._step_04_ready()
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("step04_admit_tests", str(ROOT / "steps" / "04-admit"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        admit = json.loads((self.pop / "population.json").read_text())["goose"]["admit"]
        keys = {n: mod.book_keys.pub_hex(self.work / "keys" / n / "node-key.pem") for n in ("buyer", "seller")}
        half = json.loads(Path(admit["half"]).read_text())

        def judged(name, path, mutate):
            b = json.loads(Path(path).read_text())
            mutate(b)
            verdict = {"verdict": "VALID", "disclosures": [{"capsule_id": c, "status": "disclosure_match"} for c in b["disclosures"]]}
            half_verdict = {"ok": True, "key_id": keys["buyer"], "deal_id": half["deal_id"]}
            refusal = json.loads(Path(admit["refused"]).read_text())
            checks, _ = mod.goose_judge(name[:2], b, verdict, keys["buyer"], keys["seller"], half, half_verdict, refusal,
                                        "GO" if name[:2] == "go" else "NO")
            return {c["check"] for c in checks if not c["ok"]}

        def cited(b):  # a later record's capsule cites the NO
            last = next(r for r in b["records"] if r["capsule_id"] != b["root"])
            last["references"] = [{"digest": b["root"], "digest_alg": "SHA-256", "type": "agent-action-capsule"}]
        self.assertEqual(judged("no-cited", admit["no"], cited), {"no_deal_after_no"})

        def first_cut(b):  # the checkpoint's last leaf is not in the bundle's range
            b["completeness_certificate"]["range_proof"]["to_index"] -= 1
        self.assertEqual(judged("no-short", admit["no"], first_cut), {"no_deal_after_no"})

        def reordered(b):  # the opening was committed before the admission
            heads = {c: d["agent_input"] for c, d in b["disclosures"].items()}
            dec = next(c for c, h in heads.items() if h["record_type"] == "goose_admitted")
            heads[dec]["seq"], heads[b["root"]]["seq"] = heads[b["root"]]["seq"], heads[dec]["seq"]
        self.assertEqual(judged("go-late", admit["go"], reordered), {"sealed_before_deal"})

    def _step_05(self, pop, name):
        out = self.work / name / "05-deal"
        r = subprocess.run([str(ROOT / "steps/05-deal"), "--population", str(pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, json.loads((out / "finding.json").read_text())

    def test_step_05_passes_on_the_halves_the_goose_nodes_pushed_and_the_records_that_hold_them(self):
        self._step_04_ready()
        out, found = self._step_05(self.pop, "findings")
        self.assertEqual(found["result"], "PASS", found["detail"])
        checks = json.loads((out / "checks.json").read_text())
        for side in ("buyer", "seller"):
            self.assertEqual([c["check"] for c in checks[side]], [
                "halves_verify", "one_deal", "final_extends_pushed", "pushed_at_completion",
                "close_cites_counterparty_checkpoint", "receiver_record", "holds_bytes_by_record_id",
                "cites_request_and_checkpoint"])
            self.assertTrue((out / "verify" / f"{side}-held-half-bundle.json").is_file())  # capsulectl's verdict is kept
        # the halves judged are the bytes the nodes wrote
        self.assertEqual((out / "halves" / "seller-exchange-half.json").read_bytes(),
                         (self.work / "run" / "seller-exchange-half.json").read_bytes())
        self.assertIn("not by a second implementation", found["detail"])

    def test_step_05_fails_each_clause_a_planted_exchange_breaks(self):
        self._step_04_ready()
        import shutil

        def planted(name, mutate):
            doc = json.loads((self.pop / "population.json").read_text())
            run = self.work / f"{name}-run5"
            shutil.copytree(self.work / "run", run, ignore=shutil.ignore_patterns("buyer", "seller", "*.key", "witness-key.pem"))
            for key in doc["goose"]["deal"]:
                if key.startswith(("buyer-", "seller-")):
                    doc["goose"]["deal"][key] = str(run / Path(doc["goose"]["deal"][key]).name)
            mutate(doc["goose"]["deal"], run)
            pop = self.work / f"{name}-pop5"
            pop.mkdir()
            (pop / "population.json").write_text(json.dumps(doc))
            out, found = self._step_05(pop, f"{name}-f5")
            failed = {f"{s}.{c['check']}" for s, cs in json.loads((out / "checks.json").read_text()).items() for c in cs if not c["ok"]}
            return found, failed

        def tampered(deal, run):  # a step's payload altered after it was sealed
            p = run / "seller-exchange-half.json"
            h = json.loads(p.read_text())
            h["steps"][0]["payload"]["body"]["terms"]["price_minor"] += 1
            p.write_text(json.dumps(h))

        def other_signer(deal, run):  # the seller's pushed half is the buyer's
            shutil.copyfile(run / "buyer-exchange-half.json", run / "seller-exchange-half.json")

        def already_closed(deal, run):  # a half pushed after its close, not at the exchange's end
            shutil.copyfile(run / "seller-final-half.json", run / "seller-exchange-half.json")

        def other_record(deal, run):  # the buyer's record of the held half is the seller's
            shutil.copyfile(run / "seller-held-half-bundle.json", run / "buyer-held-half-bundle.json")

        for name, mutate, broken in (("tampered", tampered, {"buyer.halves_verify"}),
                                     ("othersigner", other_signer, {"buyer.halves_verify"}),
                                     ("closed", already_closed, {"buyer.pushed_at_completion"}),
                                     ("otherrecord", other_record, {"buyer.receiver_record", "buyer.holds_bytes_by_record_id"})):
            with self.subTest(name):
                found, failed = planted(name, mutate)
                self.assertEqual(found["result"], "FAIL", found["detail"])
                self.assertLessEqual(broken, failed)

    def test_step_05_judge_fails_a_fork_another_deal_a_close_citing_another_checkpoint_and_a_payload_that_is_not_the_half(self):
        self._step_04_ready()
        import base64
        import copy
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("step05_deal_tests", str(ROOT / "steps" / "05-deal"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        out, found = self._step_05(self.pop, "findings-unit")
        self.assertEqual(found["result"], "PASS", found["detail"])
        deal = json.loads((self.pop / "population.json").read_text())["goose"]["deal"]
        halves = {f"{w}-{k}": json.loads(Path(deal[f"{w}-{k}"]).read_text()) for w in ("buyer", "seller")
                  for k in ("exchange-half", "final-half")}
        verdicts = {n: json.loads((out / "verify" / f"{n}.json").read_text()) for n in halves}
        sys.path.insert(0, str(ROOT / "steps"))
        import goose_half
        keys = {n: goose_half.key_of(self.work / "keys" / n / "node-key.pub.pem") for n in ("buyer", "seller")}
        bundle = json.loads(Path(deal["buyer-held-half-bundle"]).read_text())

        def failed(mutate_verdicts=None, mutate_bundle=None):
            v, b = copy.deepcopy(verdicts), copy.deepcopy(bundle)
            (mutate_verdicts or (lambda v: None))(v)
            (mutate_bundle or (lambda b: None))(b)
            return {c["check"] for c in mod.goose_clauses("buyer", "seller", halves, v, b, True, keys) if not c["ok"]}

        self.assertEqual(failed(), set())

        def other_deal(v):
            v["seller-final-half"]["deal_id"] = "deal-another"
        self.assertIn("one_deal", failed(other_deal))

        def forked(v):  # the buyer's final half does not start with the steps it pushed
            v["buyer-final-half"]["ids"] = ["00" * 32] + v["buyer-final-half"]["ids"][1:]
        self.assertIn("final_extends_pushed", failed(forked))

        def wrong_checkpoint(v):
            v["buyer-final-half"]["counterparty_checkpoint_cites"] = ["11" * 32]
        self.assertEqual(failed(wrong_checkpoint) >= {"close_cites_counterparty_checkpoint"}, True)

        def garbage_payload(b):  # the held bytes replaced
            ext = b["extensions"]["evidencebook/payloads"]
            for k in ext:
                ext[k] = base64.urlsafe_b64encode(b"not the half").decode()
        self.assertIn("holds_bytes_by_record_id", failed(mutate_bundle=garbage_payload))

        def no_payload(b):
            b["extensions"]["evidencebook/payloads"] = {}
        self.assertIn("holds_bytes_by_record_id", failed(mutate_bundle=no_payload))

        def other_request(b):  # the record cites nothing
            for d in b["disclosures"].values():
                if d["agent_input"]["record_type"] == "goose_half_received":
                    d["agent_input"]["links"] = []
        self.assertIn("cites_request_and_checkpoint", failed(mutate_bundle=other_request))

    def test_steps_04_and_05_are_not_checked_when_the_run_exported_nothing_for_them(self):
        self._step_04_ready()
        doc = json.loads((self.pop / "population.json").read_text())
        doc["goose"].pop("admit")
        doc["goose"].pop("deal")
        pop = self.work / "noexport-pop"
        pop.mkdir()
        (pop / "population.json").write_text(json.dumps(doc))
        self.assertEqual(self._step_04(pop, "noexport-f4")[1]["result"], "NOT_CHECKED")
        self.assertEqual(self._step_05(pop, "noexport-f5")[1]["result"], "NOT_CHECKED")

    def test_steps_04_and_05_fail_with_their_checks_on_inputs_that_are_not_json(self):
        self._step_04_ready()
        import shutil
        doc = json.loads((self.pop / "population.json").read_text())
        run = self.work / "truncated-run"
        shutil.copytree(self.work / "run", run, ignore=shutil.ignore_patterns("buyer", "seller", "*.key", "witness-key.pem"))
        for name in ("buyer-half", "deal-refused", "seller-exchange-half", "buyer-held-half-bundle"):
            (run / f"{name}.json").write_text('{"truncated": ')
        doc["goose"]["admit"].update({"half": str(run / "buyer-half.json"), "refused": str(run / "deal-refused.json")})
        doc["goose"]["deal"].update({n: str(run / f"{n}.json") for n in ("seller-exchange-half", "buyer-held-half-bundle")})
        pop = self.work / "truncated-pop"
        pop.mkdir()
        (pop / "population.json").write_text(json.dumps(doc))
        for out, found in (self._step_04(pop, "truncated-f4"), self._step_05(pop, "truncated-f5")):
            self.assertEqual(found["result"], "FAIL", found["detail"])
            self.assertTrue((out / "checks.json").is_file())

    def _step_12(self, pop, name):
        out = self.work / name / "12-join"
        r = subprocess.run([str(ROOT / "steps/12-join"), "--population", str(pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, json.loads((out / "finding.json").read_text())

    def test_step_12_passes_on_the_joined_nodes_commitments_and_a_strangers_history_card_probe(self):
        self._step_04_ready()
        out, found = self._step_12(self.pop, "findings")
        self.assertEqual(found["result"], "PASS", found["detail"])
        self.assertIn("probe backend: goose", found["detail"])
        self.assertEqual(sorted(found["pinned"]), ["capsulectl", "capsulectl-book", "goose-g1"])
        paths = {x["path"] for x in found["proof"]}
        self.assertTrue({"buyer/asks/seller/history_card/answer.json", "seller/asks/buyer/history_card/outcome.bundle.json",
                         "buyer/advertised/commitment.json"} <= paths)

    def test_step_12_fails_what_a_planted_asks_session_breaks(self):
        self._step_04_ready()
        import shutil

        def planted(name, mutate):
            doc = json.loads((self.pop / "population.json").read_text())
            run = self.work / f"{name}-asks12"
            shutil.copytree(self.work / "asks" / "run" if False else self.work / "asks", run,
                            ignore=shutil.ignore_patterns("run"))
            doc["goose"]["asks"]["run"] = str(run)
            mutate(run)
            pop = self.work / f"{name}-pop12"
            pop.mkdir()
            (pop / "population.json").write_text(json.dumps(doc))
            return self._step_12(pop, f"{name}-f12")[1]

        def other_key(run):  # the node joined as another key than its commitment is for
            n = json.loads((run / "nodes.json").read_text())
            n["buyer"]["key_id"] = "00" * 32
            (run / "nodes.json").write_text(json.dumps(n))

        def no_probe(run):
            shutil.rmtree(run / "seller" / "asks" / "buyer" / "history_card")

        def altered_reply(run):
            p = run / "seller" / "asks" / "buyer" / "history_card" / "answer.json"
            p.write_bytes(p.read_bytes() + b" ")

        def forged_commitment(run):  # a binding added after the node signed
            p = run / "buyer" / "advertised" / "commitment.json"
            c = json.loads(p.read_text())
            c["controller_binding"] = {"digest": "11" * 32}
            p.write_text(json.dumps(c))

        def no_join_record(run):
            (run / "buyer" / "joined" / "join-record.bundle.json").unlink()

        def other_join_record(run):  # the seller's join record presented as the buyer's
            shutil.copyfile(run / "seller" / "joined" / "join-record.bundle.json",
                            run / "buyer" / "joined" / "join-record.bundle.json")

        def one_node(run):  # a session that joined only one of the population's nodes
            n = json.loads((run / "nodes.json").read_text())
            del n["seller"]
            (run / "nodes.json").write_text(json.dumps(n))

        for name, mutate, text in (("otherkey", other_key, "joined as"), ("noprobe", no_probe, "no history_card/1 probe"),
                                   ("reply", altered_reply, "history_card"), ("forged", forged_commitment, "commitment"),
                                   ("nojoin", no_join_record, "no join record was exported"),
                                   ("otherjoin", other_join_record, "not the node's goose_joined record"),
                                   ("onenode", one_node, "the population lists")):
            with self.subTest(name):
                found = planted(name, mutate)
                self.assertEqual(found["result"], "FAIL", found["detail"])
                self.assertIn(text, found["detail"])

    def test_step_12_judge_refuses_a_binding_and_a_probe_that_is_not_the_history_card_probe(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("step12_join_tests", str(ROOT / "steps" / "12-join"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        self.assertEqual(mod.goose_binding_errors("buyer", {"key_id": "x"}), [])
        self.assertIn("names a controller binding", mod.goose_binding_errors("buyer", {"controller_binding": {}})[0])
        s07 = mod.load_step07()
        adv = {"subjects": ["checkpoints"], "derivations": ["history_card/1"]}
        probe = dict(s07.ASKS["history_card"]["request"], nonce="n", deadline="2026-01-01T00:00:00Z", profile="evidence-interop/0")
        self.assertEqual(mod.goose_probe_errors(s07, probe, adv), [])
        self.assertTrue(mod.goose_probe_errors(s07, {**probe, "derivation": "other/1"}, adv))  # another derivation
        self.assertTrue(mod.goose_probe_errors(s07, {**probe, "subject": {"exchange": "x"}}, adv))  # another subject
        self.assertTrue(mod.goose_probe_errors(s07, {**probe, "coverage": {"min_freshness": 9}}, adv))  # other coverage
        self.assertTrue(mod.goose_probe_errors(s07, probe, {**adv, "derivations": []}))  # not advertised

    def test_step_12_is_not_checked_on_a_checkout_other_than_the_pin(self):
        self._step_04_ready()
        doc = json.loads((self.pop / "population.json").read_text())
        doc["goose"]["rev"] = "0" * 40
        pop = self.work / "otherrev-pop12"
        pop.mkdir()
        (pop / "population.json").write_text(json.dumps(doc))
        found = self._step_12(pop, "otherrev-f12")[1]
        self.assertEqual(found["result"], "NOT_CHECKED", found["detail"])
        self.assertEqual(found["not_checked"][0]["what"], "the Goose nodes' join and a stranger's probe")

    def test_step_12_is_not_checked_when_the_run_wrote_no_asks_session(self):
        doc = json.loads((self.pop / "population.json").read_text())
        doc["goose"].pop("asks")
        pop = self.work / "noasks-pop12"
        pop.mkdir()
        (pop / "population.json").write_text(json.dumps(doc))
        self.assertEqual(self._step_12(pop, "noasks-f12")[1]["result"], "NOT_CHECKED")

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
