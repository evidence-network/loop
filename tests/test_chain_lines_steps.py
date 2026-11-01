"""Steps 04, 06 and 07 read a node's chain the same from the shelf (`capsules chain-lines`) as from the flat file
(loop#296): same lines in, same result out; and a shelf node whose chain cannot be read is a gap, not a crash."""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
sys.path.insert(0, str(ROOT / "status"))
import ledger_proof  # noqa: E402
import live  # noqa: E402


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / "steps" / file))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


step04, step06, step07 = load("s04", "04-admit"), load("s06", "06-settle"), load("s07", "07-ask")
LINES = [json.dumps({"capsule_id": "a", "n": 1}), json.dumps({"padding": True}), json.dumps({"capsule_id": "b"})]


class Nodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.flat, self.shelf = root / "flat", root / "shelf"
        (self.flat / "ledger").mkdir(parents=True)
        (self.shelf / "ledger").mkdir(parents=True)
        (self.flat / "ledger" / "capsules.jsonl").write_text("\n".join(LINES) + "\n")
        (self.shelf / "ledger" / live.CHAIN_STORE).write_bytes(b"sqlite")
        self.bin = root / "capsules"
        self.use("printf '%s\\n' " + " ".join(f"'{ln}'" for ln in LINES))

    def use(self, body):
        self.bin.write_text(f"#!/bin/sh\n{body}\n")
        self.bin.chmod(0o755)
        p = mock.patch.dict(os.environ, {live.CAPSULES_BIN: str(self.bin)})
        p.start()
        self.addCleanup(p.stop)

    def test_04_counts_the_same_records(self):
        self.assertEqual(step04.ledger_decisions(self.shelf), step04.ledger_decisions(self.flat))
        self.assertEqual(step04.ledger_decisions(self.shelf)[0], 3)

    def test_06_reads_the_same_settlement_records(self):
        self.assertEqual(step06.recorded(self.shelf / "ledger" / "capsules.jsonl"),
                         step06.recorded(self.flat / "ledger" / "capsules.jsonl"))

    def test_07_numbers_the_same_records(self):
        self.assertEqual(step07.ask_records(self.shelf / "ledger", numbered=True),
                         step07.ask_records(self.flat / "ledger", numbered=True))
        self.assertEqual(step07.chain_records(self.shelf / "ledger"), step07.chain_records(self.flat / "ledger"))

    def test_a_failing_chain_lines_is_a_gap_in_04_and_07(self):
        self.use("echo unreadable >&2; exit 1")
        self.assertIsNone(step04.ledger_decisions(self.shelf))
        self.assertIsNone(step07.chain_records(self.shelf / "ledger"))
        self.assertIsNone(step07.ask_records(self.shelf / "ledger"))

    def test_no_capsules_binary_leaves_a_shelf_chain_unreadable(self):
        with mock.patch.dict(os.environ, {live.CAPSULES_BIN: ""}), mock.patch("shutil.which", return_value=None):
            self.assertIsNone(live.readable({"data_dir": str(self.shelf)}, "data_dir", "ledger/capsules.jsonl"))
            self.assertIn("no readable ledger/capsules.jsonl", live.why_not({"data_dir": str(self.shelf)}, "data_dir",
                                                                           "ledger/capsules.jsonl"))
            with self.assertRaises(OSError):
                step06.recorded(self.shelf / "ledger" / "capsules.jsonl")

    def test_keep_checked_keeps_no_copy_and_says_why_when_chain_lines_fails(self):
        self.use("exit 1")
        dest = Path(self.tmp.name) / "out" / "capsules.jsonl"
        asked = ledger_proof.keep_checked(None, self.shelf / "ledger" / "capsules.jsonl", dest, "capsules.jsonl")
        self.assertFalse(dest.exists())
        self.assertIn("could not be read", asked["refusal"])
        self.assertEqual(json.loads(Path(f"{dest}{ledger_proof.KEPT_SUFFIX}").read_text())["refusal"], asked["refusal"])

    def test_the_preimage_never_reaches_the_proof_if_redaction_fails(self):
        self.use("printf '%s\\n' '{\"preimage\": \"" + "ab" * 32 + "\"}'")
        dest = Path(self.tmp.name) / "out" / "capsules.jsonl"
        with mock.patch.object(live, "redact_bytes", side_effect=MemoryError):
            with self.assertRaises(MemoryError):
                ledger_proof.keep_copy(self.shelf / "ledger" / "capsules.jsonl", dest)
        self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main()
