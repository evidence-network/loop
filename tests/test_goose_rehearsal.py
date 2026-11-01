"""The Goose rehearsal driver (scripts/goose_rehearsal.py, loop#330): G1's two nodes, the mock rail paying their deal, and step 06
judging it unchanged. Skipped unless steps/goose.setup.sh has installed the Goose tools.

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

    def test_the_rail_object_is_the_deals_and_step_06_passes_unchanged(self):
        deal = json.loads((self.work / "run" / "deal.json").read_text())
        exchange = json.loads((self.pop / "step06" / "exchange.json").read_text())
        self.assertEqual((exchange["exchange_id"], exchange["payer"], exchange["payee"]),
                         (deal["deal_id"], deal["buyer"], deal["seller"]))
        self.assertEqual(exchange["settlement_ref"]["settlement_id"], deal["settlement_id"])
        out = self.work / "findings" / "06-settle"
        r = subprocess.run([str(ROOT / "steps/06-settle"), "--population", str(self.pop), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads((out / "finding.json").read_text())["result"], "PASS")


if __name__ == "__main__":
    unittest.main()
