"""The Goose rehearsal driver (scripts/goose_rehearsal.py, loop#330): G1's two nodes, the mock rail paying their deal, step 01
judging the capsule the buyer sealed with loop_seal and step 06 judging the rail, both unchanged (01 passes; 06's rail checks
hold and the row stays NOT_CHECKED until G1's pay record is read). Skipped unless steps/goose.setup.sh has installed the
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

    def test_population_names_the_two_goose_nodes_and_the_steps_with_an_input(self):
        doc = json.loads((self.pop / "population.json").read_text())
        self.assertEqual([n["name"] for n in doc["nodes"]], ["buyer", "seller"])
        self.assertEqual(doc["rail"], "mock")
        self.assertEqual(doc["goose"]["steps"], ["01", "06"])

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

    def test_the_rail_object_is_the_deals_and_step_06_holds_its_rail_checks_but_stays_not_checked(self):
        deal = json.loads((self.work / "run" / "deal.json").read_text())
        exchange = json.loads((self.pop / "step06" / "exchange.json").read_text())
        self.assertEqual((exchange["exchange_id"], exchange["payer"], exchange["payee"]),
                         (deal["deal_id"], deal["buyer"], deal["seller"]))
        self.assertEqual(exchange["settlement_ref"]["settlement_id"], deal["settlement_id"])
        out = self.work / "findings" / "06-settle"
        r = subprocess.run([str(ROOT / "steps/06-settle"), "--population", str(self.pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        found = json.loads((out / "finding.json").read_text())
        self.assertEqual(found["result"], "NOT_CHECKED")  # G1's pay record is not read yet (capsule-emit#408)
        checks = json.loads((out / "checks.json").read_text())["checks"]
        self.assertTrue(all(c["ok"] for c in checks.values()), checks)


if __name__ == "__main__":
    unittest.main()
