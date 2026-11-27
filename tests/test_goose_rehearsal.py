"""The Goose rehearsal driver (scripts/goose_rehearsal.py, loop#330): the Goose node's two capsulectl-deal profiles running one
x-deal-v1 deal with the mock rail paying it, step 01 judging the capsule the buyer sealed with loop_seal, step 03 the commitment
each node signed with loop_advertise, step 05 each side's report with capsulectl-deal verify --bundle, and step 06 the two payment
legs against the rail (01, 02, 03, 04, 05, 06, 07 and 12 pass). Skipped unless steps/goose.setup.sh has installed the Goose tools; with LOOP_REQUIRE_TOOLS=1 (CI) a missing tool fails instead of
skipping. The step 01, 04 and 06 tests also need steps/01-seal.setup.sh's verifier, the step 04 tests capsulectl, and the step 02
tests the step 02 driver, capsulectl and openssl 3 (steps/02-book.setup.sh, steps/12-join.setup.sh).

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
sys.path.insert(0, str(ROOT / "tests"))
import staple_stub  # noqa: E402

# The pinned capsule-cli is a stub for this module; tests/test_staple_verify.py runs the real one.
setUpModule, tearDownModule = staple_stub.module_hooks()
TOOLS = Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache" / "evidence-network-loop")) / "goose"
REQUIRE_TOOLS = os.environ.get("LOOP_REQUIRE_TOOLS") == "1"  # CI, after the setups: a missing tool is an error, never a skip


def seed_pem(seed, pem):
    """An Ed25519 private key PEM (PKCS#8) from a profile's hex seed file, for openssl and the step 02 driver."""
    import base64
    der = bytes.fromhex("302e020100300506032b657004220420") + bytes.fromhex(Path(seed).read_text().strip())
    Path(pem).write_text("-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PRIVATE KEY-----\n")
    Path(pem).chmod(0o600)
    return Path(pem)


def needs(path, msg):
    """Skip unless `path` exists; with LOOP_REQUIRE_TOOLS=1 the test runs and fails on the missing tool instead."""
    return (lambda f: f) if REQUIRE_TOOLS else unittest.skipUnless(Path(path).is_file(), msg)


@needs(TOOLS / "venv" / "bin" / "python", "run steps/goose.setup.sh to install the Goose tools")
class GooseRehearsalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if REQUIRE_TOOLS and not (TOOLS / "venv" / "bin" / "python").is_file():
            raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: the Goose tools are not installed in {TOOLS}; run steps/goose.setup.sh")
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
            # published byte for byte as loop_advertise wrote it, under the node's own key, stapling the profile's
            # history_card/2 for its cadence (u539)
            self.assertEqual(n["inputs"], "signed by the node")
            signed = doc["goose"]["commitments"][n["name"]]["commitment"]
            self.assertEqual((out / f"{n['name']}.commitment.json").read_bytes(), Path(signed).read_bytes())
            self.assertEqual([(c["derivation"], c["community"]) for c in n["history_cards"]], [("history_card/2", "goose")])
            self.assertRegex(n["history_cards"][0]["stream"], r"^deal-cadence/[0-9a-f]+$")
        self.assertIn("The stapled history_card/2 of buyer, seller verifies", found["detail"])

    @needs(TOOLS.parent / "01-seal" / "bin" / "cross_language", "run steps/01-seal.setup.sh")
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
        self.assertNotIn("capsule-emit", installed)  # the node's capsulectl-deal sealed it, not a capsule-emit
        self.assertNotIn("capsule-emit", found["pinned"])
        self.assertTrue({"node-key.pub.pem", "goose-run.log"} <= {x["path"] for x in found["proof"]})

    @needs(TOOLS.parent / "01-seal" / "bin" / "cross_language", "run steps/01-seal.setup.sh")
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
            if REQUIRE_TOOLS:
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
                "checkpoint": str(run / "book-checkpoint-buyer.cose"), "witnesses": str(run / "witnesses.json")}
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
                    [str(driver), "lab-receipt", "--key", str(pem), "--statement", str(run / "book-checkpoint-buyer.cose"),
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
                                     ("sellercheckpointkey", signed_by(seed_pem(self.work / "run" / "seller" / "deal" / "checkpoint.seed",
                                                                               self.work / "seller-checkpoint.pem")), {"witness_not_node"}),
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

    def _step_04(self, pop, name):
        out = self.work / name / "04-admit"
        r = subprocess.run([str(ROOT / "steps/04-admit"), "--population", str(pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, json.loads((out / "finding.json").read_text())

    def _step_04_ready(self):
        if not (TOOLS.parent / "bin" / "capsulectl").is_file():
            if REQUIRE_TOOLS:
                self.fail("capsulectl is not installed")
            self.skipTest("run steps/12-join.setup.sh")

    def test_step_04_passes_on_the_admissions_and_the_deal_context_the_goose_buyer_sealed(self):
        self._step_04_ready()
        out, found = self._step_04(self.pop, "findings")
        self.assertEqual(found["result"], "PASS", found["detail"])
        checks = json.loads((out / "checks.json").read_text())
        self.assertEqual({n: [c["check"] for c in cs] for n, cs in checks.items()}, {
            "go": ["bundle_verifies", "decision_sealed", "one_outcome", "goose_shape", "peer_named", "deal_sealed",
                   "sealed_before_deal", "deal_cites_decision", "deal_commits_offer"],
            "no": ["bundle_verifies", "decision_sealed", "one_outcome", "goose_shape", "no_deal_after_no", "gate_refuses"]})
        self.assertEqual(json.loads((out / "counts.json").read_text())["by_outcome"], {"GO": 1, "NO": 1})
        # the bytes judged are the ones loop_deal_bundle wrote, and the offer the buyer signed
        self.assertEqual((out / "scenarios" / "go" / "bundle.json").read_bytes(),
                         (self.work / "run" / "admission-go-bundle.json").read_bytes())
        self.assertEqual((out / "offer.json").read_bytes(), (self.work / "run" / "offer.json").read_bytes())
        offer = json.loads((out / "offer.verify.json").read_text())
        self.assertTrue(offer["ok"], offer)
        self.assertEqual(offer["digest"], json.loads((self.work / "run" / "deal.json").read_text())["offer"])
        self.assertIn("aac-go", found["pinned"])

    def test_step_04_fails_each_check_a_planted_population_breaks(self):
        self._step_04_ready()
        import shutil

        def planted(name, mutate):
            doc = json.loads((self.pop / "population.json").read_text())
            run = self.work / f"{name}-run4"
            shutil.copytree(self.work / "run", run, ignore=shutil.ignore_patterns("buyer", "seller", "*.key", "witness-key.pem"))
            doc["goose"]["admit"].update(go=str(run / "admission-go-bundle.json"), no=str(run / "admission-no-disclosed.json"),
                                         offer=str(run / "offer.json"), refused=str(run / "deal-refused.json"))
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

        def refusal_missing(doc, run):  # the gate was never asked: the kept answer is a success
            (run / "deal-refused.json").write_text(json.dumps({"ok": True, "peer": "ab" * 32}))

        def offer_forged(doc, run):  # the offer altered after the buyer signed it: its digest is not the context's
            o = json.loads((run / "offer.json").read_text())
            o["agent_input"]["body"]["terms"]["item"] = "something else"
            (run / "offer.json").write_text(json.dumps(o))

        def offer_resigned(doc, run):  # the offer's digest recomputes, but the signed record does not commit it
            o = json.loads((run / "offer.json").read_text())
            o["agent_input"]["body"]["terms"]["item"] = "something else"
            o["record"]["model_attestation"]["compute_attestation"]["agent_input_digest"] = "44" * 32
            (run / "offer.json").write_text(json.dumps(o))

        def other_offer(doc, run):  # another file of the deal presented as the offer
            doc["goose"]["admit"]["offer"] = str(run / "seller-agreement.json")

        for name, mutate, broken in (("swapped", swapped, {"go.decision_sealed", "no.decision_sealed"}),
                                     ("norefusal", refusal_missing, {"no.gate_refuses"}),
                                     ("forgedoffer", offer_forged, {"go.deal_commits_offer"}),
                                     ("resignedoffer", offer_resigned, {"go.deal_commits_offer"}),
                                     ("otheroffer", other_offer, {"go.deal_commits_offer"}),
                                     ("otherbuyer", other_buyer, {"go.decision_sealed", "no.decision_sealed"}),
                                     ("otherpeer", other_peer, {"go.peer_named"})):
            with self.subTest(name):
                found, failed = planted(name, mutate)
                self.assertEqual(found["result"], "FAIL", found["detail"])
                self.assertLessEqual(broken, failed)

    def test_step_04_judge_fails_a_no_that_a_record_cites_a_deal_sealed_before_its_decision_and_another_offer(self):
        self._step_04_ready()
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("step04_admit_tests", str(ROOT / "steps" / "04-admit"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        admit = json.loads((self.pop / "population.json").read_text())["goose"]["admit"]
        keys = {n: mod.book_keys.pub_hex(self.work / "keys" / n / "node-key.pem") for n in ("buyer", "seller")}
        deal = json.loads((self.work / "run" / "deal.json").read_text())
        offer_ok = (True, "the offer holds", {"digest": deal["offer"], "deal_id": deal["deal_id"], "to": keys["seller"]})

        def judged(name, path, mutate, offer_check=offer_ok):
            b = json.loads(Path(path).read_text())
            mutate(b)
            verdict = {"verdict": "VALID", "disclosures": [{"capsule_id": c, "status": "disclosure_match"} for c in b["disclosures"]]}
            refusal = json.loads(Path(admit["refused"]).read_text())
            checks, _ = mod.goose_judge(name[:2], b, verdict, keys["buyer"], keys["seller"], offer_check, refusal,
                                        "GO" if name[:2] == "go" else "NO")
            return {c["check"] for c in checks if not c["ok"]}

        self.assertEqual(judged("go-ok", admit["go"], lambda b: None), set())

        def cited(b):  # a later record's capsule cites the NO
            last = next(r for r in b["records"] if r["capsule_id"] != b["root"])
            last["references"] = [{"digest": b["root"], "digest_alg": "SHA-256", "type": "agent-action-capsule"}]
        self.assertEqual(judged("no-cited", admit["no"], cited), {"no_deal_after_no"})

        def first_cut(b):  # the checkpoint's last leaf is not in the bundle's range
            b["completeness_certificate"]["range_proof"]["to_index"] -= 1
        self.assertEqual(judged("no-short", admit["no"], first_cut), {"no_deal_after_no"})

        def reordered(b):  # the deal's context was committed before the admission
            heads = {c: d["agent_input"] for c, d in b["disclosures"].items()}
            dec = next(c for c, h in heads.items() if h["record_type"] == "goose_admitted")
            heads[dec]["seq"], heads[b["root"]]["seq"] = heads[b["root"]]["seq"], heads[dec]["seq"]
        self.assertEqual(judged("go-late", admit["go"], reordered), {"sealed_before_deal"})

        def other_digest(b):  # the context names another offer than the one the buyer signed
            b["disclosures"][b["root"]]["agent_input"]["statement"]["offer"] = "55" * 32
        self.assertEqual(judged("go-offer", admit["go"], other_digest), {"deal_commits_offer"})
        bad = (False, "the offer's signature does not verify", offer_ok[2])
        self.assertEqual(judged("go-unsigned", admit["go"], lambda b: None, bad), {"deal_commits_offer"})

    def _step_05(self, pop, name):
        out = self.work / name / "05-deal"
        r = subprocess.run([str(ROOT / "steps/05-deal"), "--population", str(pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, json.loads((out / "finding.json").read_text())

    CLAUSES_05 = ["report_verifies", "signed_by_listed_key", "checkpoint_key_is_commitments", "one_deal_one_offer",
                  "agreement_names_counterparty", "holds_counterparty_records"]

    def test_step_05_passes_on_the_deal_the_goose_nodes_ran_and_their_reports(self):
        out, found = self._step_05(self.pop, "findings")
        self.assertEqual(found["result"], "PASS", found["detail"])
        checks = json.loads((out / "checks.json").read_text())
        for side in ("buyer", "seller"):
            self.assertEqual([c["check"] for c in checks["checks"][side]], self.CLAUSES_05)
            # the node's own verifier's verdict is kept
            self.assertEqual(json.loads((out / "verify" / f"{side}-report.json").read_text())["verdict"], "VALID")
        self.assertEqual(checks["checkpoint_keys"], json.loads((self.pop / "population.json").read_text())["goose"]
                         ["verify"]["checkpoint_keys"])
        # the files judged are the bytes the nodes wrote
        self.assertEqual((out / "deal" / "seller-report.json").read_bytes(), (self.work / "run" / "seller-report.json").read_bytes())
        self.assertIn("not by a second implementation", found["detail"])
        self.assertEqual(sorted(found["pinned"]), ["capsule-engine", "goose-g1"])

    def test_step_05_fails_each_clause_a_planted_deal_breaks(self):
        import shutil

        def planted(name, mutate):
            doc = json.loads((self.pop / "population.json").read_text())
            run = self.work / f"{name}-run5"
            shutil.copytree(self.work / "run", run, ignore=shutil.ignore_patterns("buyer", "seller", "*.key", "witness-key.pem"))
            doc["goose"]["deal"] = {k: str(run / Path(v).name) for k, v in doc["goose"]["deal"].items()}
            mutate(doc, run)
            pop = self.work / f"{name}-pop5"
            pop.mkdir()
            (pop / "population.json").write_text(json.dumps(doc))
            out, found = self._step_05(pop, f"{name}-f5")
            failed = {f"{s}.{c['check']}" for s, cs in json.loads((out / "checks.json").read_text())["checks"].items()
                      for c in cs if not c["ok"]}
            return found, failed

        def tampered(doc, run):  # a record of the buyer's report altered after it was sealed
            b = json.loads((run / "buyer-report.json").read_text())
            b["records"][0]["operator"] = "someone-else"
            (run / "buyer-report.json").write_text(json.dumps(b))

        def other_signer(doc, run):  # the seller's report presented as the buyer's
            shutil.copyfile(run / "seller-report.json", run / "buyer-report.json")

        def other_offer(doc, run):  # the offer file is not the one the agreements commit
            o = json.loads((run / "offer.json").read_text())
            o["agent_input"]["body"]["terms"]["item"] = "something else"
            (run / "offer.json").write_text(json.dumps(o))

        def other_commitment(doc, run):  # the buyer's commitment names the seller's checkpoint key
            c = doc["goose"]["commitments"]
            c["buyer"] = c["seller"]

        def other_deal(doc, run):  # the run names another deal
            doc["goose"]["deal_id"] = "deal-0000000000000000"

        for name, mutate, broken in (("tampered", tampered, {"buyer.report_verifies"}),
                                     ("othersigner", other_signer, {"buyer.signed_by_listed_key",
                                                                    "buyer.checkpoint_key_is_commitments"}),
                                     ("otheroffer", other_offer, {"buyer.one_deal_one_offer", "seller.one_deal_one_offer"}),
                                     ("othercommitment", other_commitment, {"buyer.checkpoint_key_is_commitments"}),
                                     ("otherdeal", other_deal, {"buyer.one_deal_one_offer", "seller.one_deal_one_offer"})):
            with self.subTest(name):
                found, failed = planted(name, mutate)
                self.assertEqual(found["result"], "FAIL", found["detail"])
                self.assertLessEqual(broken, failed)

    def test_step_05_judge_fails_another_counterparty_a_record_not_held_and_an_unread_report(self):
        import copy
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("step05_deal_tests", str(ROOT / "steps" / "05-deal"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        out, found = self._step_05(self.pop, "findings-unit")
        self.assertEqual(found["result"], "PASS", found["detail"])
        doc = json.loads((self.pop / "population.json").read_text())
        reports = {s: json.loads(Path(doc["goose"]["deal"][f"{s}-report"]).read_text()) for s in ("buyer", "seller")}
        verdicts = {s: json.loads((out / "verify" / f"{s}-report.json").read_text()) for s in ("buyer", "seller")}
        keys = {n: mod._spki_hex(self.work / "keys" / n / "node-key.pub.pem") for n in ("buyer", "seller")}
        cp_keys = doc["goose"]["verify"]["checkpoint_keys"]
        deal = json.loads((self.work / "run" / "deal.json").read_text())

        def failed(mutate_reports=None, mutate_verdicts=None):
            r, v = copy.deepcopy(reports), copy.deepcopy(verdicts)
            (mutate_reports or (lambda r: None))(r)
            (mutate_verdicts or (lambda v: None))(v)
            return {c["check"] for c in mod.goose_clauses("buyer", "seller", r, v, keys, cp_keys, deal["offer"],
                                                         deal["deal_id"]) if not c["ok"]}

        self.assertEqual(failed(), set())

        def other_counterparty(r):
            r["buyer"]["extensions"]["x-deal-v1"]["book"]["agreement"]["payload"]["body"]["counterparty_key"] = "66" * 32
        self.assertEqual(failed(other_counterparty), {"agreement_names_counterparty"})

        def not_held(r):  # the buyer rests on fewer of the seller's records than the seller's own report names
            r["buyer"]["extensions"]["x-deal-report/v0"]["agent_input"]["rests_on"]["counterparty"]["counted"] -= 1
        self.assertEqual(failed(not_held), {"holds_counterparty_records"})

        def no_extension_reading(v):  # VALID, but the node's verifier did not read the deal (no x-deal-v1 pass)
            v["buyer"]["extensions"] = [x for x in v["buyer"]["extensions"] if x.get("kind") != "x-deal-v1"]
        self.assertEqual(failed(mutate_verdicts=no_extension_reading), {"report_verifies"})

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
        for name in ("offer", "deal-refused", "buyer-report"):
            (run / f"{name}.json").write_text('{"truncated": ')
        doc["goose"]["admit"].update({"offer": str(run / "offer.json"), "refused": str(run / "deal-refused.json")})
        doc["goose"]["deal"].update({n: str(run / f"{n}.json") for n in ("offer", "buyer-report")})
        pop = self.work / "truncated-pop"
        pop.mkdir()
        (pop / "population.json").write_text(json.dumps(doc))
        for out, found in (self._step_04(pop, "truncated-f4"), self._step_05(pop, "truncated-f5")):
            self.assertEqual(found["result"], "FAIL", found["detail"])
            self.assertTrue((out / "checks.json").is_file())

    def _step_12(self, pop, name, env=None):
        out = self.work / name / "12-join"
        r = subprocess.run([str(ROOT / "steps/12-join"), "--population", str(pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, env={**os.environ, **(env or {})})
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, json.loads((out / "finding.json").read_text())

    def test_step_12_passes_on_the_joined_nodes_commitments_and_a_strangers_history_card_probe(self):
        self._step_04_ready()
        out, found = self._step_12(self.pop, "findings")
        self.assertEqual(found["result"], "PASS", found["detail"])
        self.assertIn("probe backend: goose", found["detail"])
        self.assertEqual(sorted(found["pinned"]), ["capsule-cli", "capsulectl", "capsulectl-book", "goose-g1"])
        paths = {x["path"] for x in found["proof"]}
        self.assertTrue({"buyer/asks/seller/history_card/answer.json", "seller/asks/buyer/history_card/outcome.bundle.json",
                         "buyer/advertised/commitment.json"} <= paths)

    def test_step_12_fails_what_a_planted_asks_session_breaks(self):
        self._step_04_ready()
        import shutil

        def planted(name, mutate):
            doc = json.loads((self.pop / "population.json").read_text())
            run = self.work / f"{name}-asks12"
            shutil.copytree(self.work / "asks", run, ignore=shutil.ignore_patterns("run"))
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

        for name, mutate, text in (("otherkey", other_key, "joined as"), ("noprobe", no_probe, "no history_card/2 probe"),
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
        adv = {"subjects": ["checkpoints"], "derivations": ["history_card/2"]}
        probe = dict(s07.ASKS["history_card"]["request"], derivation="history_card/2", nonce="n",
                     deadline="2026-01-01T00:00:00Z", profile="evidence-interop/0")
        self.assertEqual(mod.goose_probe_errors(s07, probe, adv), [])
        self.assertTrue(mod.goose_probe_errors(s07, {**probe, "derivation": "other/1"}, adv))  # another derivation
        self.assertTrue(mod.goose_probe_errors(s07, {**probe, "subject": {"exchange": "x"}}, adv))  # another subject
        self.assertTrue(mod.goose_probe_errors(s07, {**probe, "coverage": {"min_freshness": 9}}, adv))  # other coverage
        self.assertTrue(mod.goose_probe_errors(s07, probe, {**adv, "derivations": []}))  # not advertised
        self.assertTrue(mod.goose_probe_errors(s07, {**probe, "derivation": "history_card/1"}, adv))  # the /1 probe (u539)

    def test_step_12_holds_the_goose_staple_to_history_card_2_on_the_cadence_under_the_commitments_checkpoint_key(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("step12_join_card_tests", str(ROOT / "steps" / "12-join"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        adv_dir = self.work / "asks" / "buyer" / "advertised"
        doc = json.loads((adv_dir / "commitment.json").read_text())
        adv = mod.advertised(adv_dir)
        self.assertEqual(mod.goose_card_errors(doc, adv), [])
        self.assertEqual(adv["checkpoint_key"], json.loads((self.pop / "population.json").read_text())["goose"]
                         ["verify"]["checkpoint_keys"]["buyer"])
        other_key = {**adv, "card_keys": {s: "77" * 32 for s in adv["card_keys"]}}
        self.assertIn("not the commitment's", mod.goose_card_errors(doc, other_key)[0])
        v1 = json.loads(json.dumps(doc))
        v1["history_cards"][0]["derivation"] = "history_card/1"
        self.assertIn("staples no history_card/2", mod.goose_card_errors(v1, adv)[0])
        book = json.loads(json.dumps(doc))
        book["history_cards"][0]["stream"] = "goose/node"
        self.assertIn("not the profile's cadence",
                      mod.goose_card_errors(book, {**adv, "card_keys": {"goose/node": adv["checkpoint_key"]}})[0])

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

    def test_step_12_is_not_checked_without_capsulectl(self):
        found = self._step_12(self.pop, "nocap-f12", {"CAPSULECTL": str(self.work / "no-such-capsulectl")})[1]
        self.assertEqual(found["result"], "NOT_CHECKED", found["detail"])
        self.assertIn("is not installed", found["detail"])

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
        # the askers are the deal's parties (loop#399): the same homes and keys that steps 01-06 read, and the request
        # was read as sealed before it was sent
        self.assertIn("the askers are the deal's parties", found["detail"])
        deal_id = json.loads((self.work / "run" / "deal.json").read_text())["deal_id"]
        self.assertIn(f"({deal_id[:21]})", found["detail"])  # the whole `deal-` + 16 hex id, not 11 hex of it
        for who in ("buyer", "seller"):
            self.assertTrue((self.work / "run" / who / "deal" / "signing.seed").is_file())  # the deal's own profiles
            self.assertFalse((self.work / "asks" / "run").exists())
        self.assertTrue(json.loads((out / "first-answered.json").read_text())["sealed"])

    def test_step_07_is_not_checked_when_the_population_names_no_deal_to_compare_the_askers_with(self):
        import shutil
        work = Path(tempfile.mkdtemp(dir=self.work))
        doc = json.loads((self.pop / "population.json").read_text())
        shutil.copytree(self.work / "asks", work / "asks", ignore=shutil.ignore_patterns("run"))
        doc["goose"]["run"] = str(work / "no-deal")
        doc["goose"]["asks"] = {"run": str(work / "asks")}
        (work / "population").mkdir()
        (work / "population" / "population.json").write_text(json.dumps(doc))
        out = work / "findings" / "07-ask"
        r = subprocess.run([str(ROOT / "steps/07-ask"), "--population", str(work / "population"), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        found = json.loads((out / "finding.json").read_text())
        self.assertEqual(found["result"], "NOT_CHECKED", found["detail"])
        self.assertEqual(found["not_checked"][0]["what"], "that the askers are the deal's parties")

    def test_step_07_fails_askers_that_are_not_the_deals_parties(self):
        def other_nodes(asks):
            nodes = json.loads((asks / "nodes.json").read_text())
            nodes["buyer"]["key_id"] = "0" * 64
            (asks / "nodes.json").write_text(json.dumps(nodes))
        found = self._judged(other_nodes)
        self.assertEqual(found["result"], "FAIL")
        self.assertIn("the askers are not the deal's parties", found["detail"])

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

    def test_step_06_passes_on_the_exchange_read_from_the_two_legs_and_each_nodes_signed_count(self):
        deal = json.loads((self.work / "run" / "deal.json").read_text())
        found, checks, out = self.judge06(self.pop)
        self.assertEqual(found["result"], "PASS", found["detail"])
        self.assertTrue(all(c["ok"] for c in checks.values()), checks)
        self.assertEqual(sorted(checks), ["adapter", "band", "exchange_match", "finality", "ledger", "ledger_count:buyer",
                                          "ledger_count:seller", "legs", "legs_signed", "rail_fee", "reference"])
        # no exchange file is the driver's: the step derived the exchange from the nodes' signed legs
        self.assertFalse((self.pop / "step06" / "exchange.json").exists())
        exchange = json.loads((out / "exchange.json").read_text())
        self.assertEqual((exchange["exchange_id"], exchange["payer"], exchange["payee"]),
                         (deal["deal_id"], deal["buyer"], deal["seller"]))
        self.assertEqual(exchange["settlement_ref"]["settlement_id"], deal["settlement_id"])
        # each node's own signed count of its settlements is inside the assertion (evidencebook#920)
        self.assertIn("disclosed settlement count", found["asserts"])
        self.assertNotIn("D1", found["detail"])
        self.assertLessEqual({"disclosure-buyer.json", "disclosure-seller.json"}, {x["path"] for x in found["proof"]})

    def _resign_disclosure(self, d, name, edit):
        """Re-sign `name`'s disclosure after `edit(doc)`, with the node's own key (the rehearsal's TEST profile)."""
        import base64
        sys.path.insert(0, str(ROOT / "scripts"))
        import check_commitment as cc
        seed = bytes.fromhex((self.work / "run" / name / "deal" / "signing.seed").read_text().strip())
        pem = Path(tempfile.mkdtemp(dir=self.work)) / "key.pem"
        der = bytes.fromhex("302e020100300506032b657004220420") + seed  # Ed25519 PKCS#8
        pem.write_text("-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PRIVATE KEY-----\n")
        doc = json.loads((d / f"disclosure-{name}.json").read_text())
        edit(doc)
        (d / f"disclosure-{name}.json").write_text(json.dumps(cc.sign(doc, pem)))

    def test_step_06_fails_a_count_that_is_missing_wrong_or_labelled_off_the_ledger(self):
        def resigned(edit):
            return lambda d: self._resign_disclosure(d, "buyer", edit)
        cases = {
            "missing": lambda d: (d / "disclosure-buyer.json").unlink(),
            "unsigned change": lambda d: (d / "disclosure-buyer.json").write_text(json.dumps(
                {**json.loads((d / "disclosure-buyer.json").read_text()), "settlements": 2})),
            "count": resigned(lambda doc: doc.update(settlements=2)),
            "rail mock": resigned(lambda doc: doc.update(rail="mock")),
            "stale label": resigned(lambda doc: doc.update(through_sequence=0)),
            "future label": resigned(lambda doc: doc.update(through_sequence=2)),
            "boolean label": resigned(lambda doc: doc.update(through_sequence=True)),
            "boolean count": resigned(lambda doc: doc.update(settlements=True)),
        }
        # the re-signing itself keeps a correct disclosure passing
        found, checks, _ = self.judge06(self.tampered_population(resigned(lambda doc: None)))
        self.assertEqual(found["result"], "PASS", found["detail"])
        for what, edit in cases.items():
            with self.subTest(what):
                found, checks, _ = self.judge06(self.tampered_population(edit))
                self.assertEqual(found["result"], "FAIL")
                self.assertFalse(checks["ledger_count:buyer"]["ok"], checks["ledger_count:buyer"])
                self.assertTrue(checks["ledger_count:seller"]["ok"])

    def _legs(self, d, edit):
        for name in ("buyer-leg.json", "seller-leg.json"):
            leg = json.loads((d / name).read_text())
            edit(name.split("-")[0], leg["record"]["record"]["settlement"], leg)
            (d / name).write_text(json.dumps(leg))

    def test_step_06_fails_legs_that_do_not_carry_the_rails_settlement_or_do_not_agree(self):
        def other_reference(side, st, leg):  # both legs name another payment than the rail's settlement
            st["payment_ref"]["value"] = "0" * 64
        found, checks, _ = self.judge06(self.tampered_population(lambda d: self._legs(d, other_reference)))
        self.assertEqual(found["result"], "FAIL")
        self.assertFalse(checks["reference"]["ok"])

        def disagree(side, st, leg):  # the payee leg states another reference than the payer leg
            if side == "seller":
                st["payment_ref"]["value"] = "1" * 64
        found, checks, _ = self.judge06(self.tampered_population(lambda d: self._legs(d, disagree)))
        self.assertEqual(found["result"], "FAIL")
        self.assertIn("differ", checks["legs"]["detail"])

        def short(side, st, leg):  # the payee received less than the payer paid
            if side == "seller":
                st["received"]["value"] = "49"
        found, checks, _ = self.judge06(self.tampered_population(lambda d: self._legs(d, short)))
        self.assertEqual(found["result"], "FAIL")
        self.assertIn("received", checks["legs"]["detail"])

    def test_step_06_fails_legs_whose_signers_are_not_the_populations_nodes(self):
        def foreign(side, st, leg):
            leg["record"]["record"]["key_id"] = "ab" * 32
        found, checks, _ = self.judge06(self.tampered_population(lambda d: self._legs(d, foreign)))
        self.assertEqual(found["result"], "FAIL")
        self.assertIn("two distinct nodes", checks["legs"]["detail"])

    @needs(TOOLS.parent / "01-seal" / "bin" / "cross_language", "run steps/01-seal.setup.sh")
    def test_step_06_fails_a_leg_altered_after_its_node_signed_it(self):
        def altered(side, st, leg):  # the same reference, but the signed record no longer matches its bytes
            if side == "buyer":
                st["observed_at"] = "2026-01-01T00:00:00Z"
        found, checks, _ = self.judge06(self.tampered_population(lambda d: self._legs(d, altered)))
        self.assertEqual(found["result"], "FAIL")
        self.assertFalse(checks["legs_signed"]["ok"])


@needs(TOOLS / "venv" / "bin" / "python", "run steps/goose.setup.sh to install the Goose tools")
class GooseNoDriverTest(unittest.TestCase):
    """A rehearsal of its own, without the step 02 driver: a class apart from GooseRehearsalTest, whose rehearsal it does
    not read, so the two rehearsals (about 7 minutes each) run side by side under scripts/unittest_parallel.py."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_a_run_without_a_usable_step_02_driver_still_passes_step_03_and_step_02_says_not_checked(self):
        # One whole rehearsal without a driver (a missing file). A driver that exists but is not executable takes the
        # same path in the session (goose_session.usable_driver is false either way), which
        # test_only_an_executable_file_is_a_usable_driver checks without a second 7-minute rehearsal (loop#489).
        bin_ = self.dir / "no-such-driver"
        work = self.dir / "nodriver-missing"
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

    def test_only_an_executable_file_is_a_usable_driver(self):
        # The session's own check, in the Goose tools' Python (goose_session imports mcp): a missing file and a file
        # that is not executable are not usable, an executable file is.
        plain, exe = self.dir / "plain-driver", self.dir / "exe-driver"
        plain.write_text("")
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        code = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import goose_session as g; "
                "print([g.usable_driver(Path(p)) for p in sys.argv[2:]])")
        r = subprocess.run([str(TOOLS / "venv" / "bin" / "python"), "-B", "-c", code, str(ROOT / "scripts"),
                            str(self.dir / "no-such-driver"), str(plain), str(exe)],
                           env={**os.environ, "LOOP_GOOSE_SRC": str(TOOLS / "src")}, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "[False, False, True]")


@needs(TOOLS / "venv" / "bin" / "python", "run steps/goose.setup.sh to install the Goose tools")
class GooseAskWindowTest(unittest.TestCase):
    """The pinned node's asker refuses a commitment whose answers_within is under its minimum (PT1M is), and every window
    the loop's Goose drivers advertise or ask through is at least that minimum."""

    PROBE = r"""
import sys, json
from datetime import timedelta
import mailbox_exchange as mx
least = (mx.MINIMUM_ABSENCE_WINDOW + mx.SEAL_MARGIN).total_seconds()
mx.route = lambda commitment: {"answers_within": int(sys.argv[1]), "responder": "r", "checkpoint": "c",
                               "mailbox_key": "m", "relays": []}
try:
    mx.ask(None, {}, "checkpoints")
    refused = None
except mx.Refused as e:
    refused = str(e)
except Exception as e:  # the window was accepted, and the ask went on to the node this probe does not give
    refused = None
print(json.dumps({"least": least, "refused": refused}))
"""

    def window(self, seconds):
        src = TOOLS / "src" / "python" / "examples" / "goose"
        r = subprocess.run([str(TOOLS / "venv" / "bin" / "python"), "-B", "-c", self.PROBE, str(seconds)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=src,
                           env={**os.environ, "PYTHONPATH": str(src)})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_a_pt1m_commitment_is_refused_and_the_loops_windows_are_not(self):
        import importlib.machinery
        import importlib.util
        import re
        loader = importlib.machinery.SourceFileLoader("step07_window_tests", str(ROOT / "steps" / "07-ask"))
        s07 = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(s07)
        def window_of(script):  # read from the source: the drivers import mcp, which only the Goose venv has
            return re.search(r'^ANSWERS_WITHIN = "([^"]+)"', (ROOT / "scripts" / script).read_text(), re.M).group(1)
        pt1m = self.window(60)
        self.assertIsNotNone(pt1m["refused"])
        self.assertIn("answers_within is shorter", pt1m["refused"])
        for name, text in (("goose_ask_session", window_of("goose_ask_session.py")), ("goose_live", window_of("goose_live.py")),
                           ("steps/07-ask", s07.REHEARSAL_ANSWERS_WITHIN)):
            seconds = s07.duration(text).total_seconds()
            with self.subTest(name):
                self.assertGreaterEqual(seconds, pt1m["least"])
                self.assertIsNone(self.window(int(seconds))["refused"])


if __name__ == "__main__":
    unittest.main()
