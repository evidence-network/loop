"""A payment preimage never reaches a published tree: steps/live.py copy_redacted and scripts/check_no_preimages.py."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_no_preimages  # noqa: E402
import live  # noqa: E402

PRE = "ab" * 32
HASH = hashlib.sha256(bytes.fromhex(PRE)).hexdigest()
OBS = {"capsule_id": "obs-1", "key_id": "k1", "model_attestation": {"compute_attestation": {"x-mesh-settlement-v1": {
    "phase": "output_settlement_observed", "source": "wallet_reported", "preimage": PRE, "payment_hash": HASH,
    "amount_msat": 1000, "exchange_id": "ex-1"}}}}


class NoPreimagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_preimage_is_found_under_any_key_naming_one_at_any_depth_and_inside_json_strings(self):
        self.assertTrue(live.carries_preimage(OBS))
        self.assertTrue(live.carries_preimage([{"a": [{"payment_preimage": "x"}]}]))
        self.assertTrue(live.carries_preimage({"preImage": "x"}))
        self.assertTrue(live.carries_preimage({"stdout": json.dumps(OBS)}))
        self.assertFalse(live.carries_preimage({"preimage": None}))
        self.assertFalse(live.carries_preimage({"preimage": live.REDACTED}))
        self.assertFalse(live.carries_preimage({"preimage_check": "verified", "payment_hash": HASH}))

    def test_the_copy_redacts_the_value_keeps_the_record_and_withholds_an_unparsable_line(self):
        src = self.d / "capsules.jsonl"
        truncated = json.dumps(OBS).encode()[:-10]  # a line copied while the node appends to it
        lines = [b'{"capsule_id": "a", "x": 1}', json.dumps(OBS).encode(), b"not json", truncated, b'{"capsule_id":"b"}']
        src.write_bytes(b"\n".join(lines) + b"\n")
        self.assertEqual(live.copy_redacted(src, self.d / "out.jsonl"), 2)
        data = (self.d / "out.jsonl").read_bytes()
        out = data.split(b"\n")
        self.assertEqual(len(out), len(lines) + 1)
        self.assertEqual([out[0], out[2], out[4]], [lines[0], lines[2], lines[4]])
        kept = json.loads(out[1])["model_attestation"]["compute_attestation"]["x-mesh-settlement-v1"]
        self.assertEqual(kept["preimage"], live.REDACTED)
        # Everything an evaluator reads of the settlement stays (step 10 runs one on 05's copied chain).
        self.assertEqual({k: kept[k] for k in ("phase", "source", "payment_hash", "amount_msat", "exchange_id")},
                         {k: OBS["model_attestation"]["compute_attestation"]["x-mesh-settlement-v1"][k]
                          for k in ("phase", "source", "payment_hash", "amount_msat", "exchange_id")})
        self.assertEqual(json.loads(out[1])["capsule_id"], "obs-1")
        self.assertEqual(json.loads(out[3])["redacted"], live.UNPARSED)
        self.assertNotIn(PRE.encode(), data)
        self.assertEqual(check_no_preimages.scan([self.d / "out.jsonl"]), [])

    def test_the_scan_fails_closed_on_every_shape_a_preimage_can_take(self):
        cases = {
            "a.jsonl": json.dumps(OBS),
            "pretty.jsonl": json.dumps(OBS, indent=2),
            "concat.json": json.dumps(OBS) + json.dumps(OBS),
            "spelled.json": json.dumps({"payment_preimage": PRE}),
            "nested.json": json.dumps({"stdout": json.dumps({"preimage": PRE})}),
            "join.log": f"settled preimage={PRE} ok",
            "keyless.txt": f'{{"payment_hash": "{HASH}"}}\nsomething {PRE}\n',
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                f = self.d / name
                f.write_text(text)
                try:
                    self.assertTrue(check_no_preimages.scan([f]), name)
                finally:
                    f.unlink()
        (self.d / "clean.json").write_text(json.dumps({"preimage_check": "verified", "payment_hash": HASH}))
        self.assertEqual(check_no_preimages.scan([self.d]), [])
        (self.d / "x.jsonl").write_text(json.dumps(OBS) + "\n")
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "check_no_preimages.py"), str(self.d)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn(PRE, r.stdout + r.stderr)

    def test_a_preimage_beside_its_payment_hash_is_found_under_any_name_and_in_any_file(self):
        pre = bytes(range(32))
        ph = hashlib.sha256(pre).hexdigest()
        (self.d / "ledger.jsonl").write_text(json.dumps({"payment_hash": ph}) + "\n")
        (self.d / "notes.md").write_text("ok\nproof " + pre.hex().upper() + " end\n")
        (self.d / "c.json").write_text(json.dumps({"secret": pre.hex()}))
        self.assertEqual(sorted(check_no_preimages.scan([self.d])),
                         sorted([f"{self.d / 'c.json'}:1", f"{self.d / 'notes.md'}:2"]))
        # A hash alone, or a 64-hex value whose sha256 is nowhere in the trees, is not a hit.
        (self.d / "notes.md").unlink()
        (self.d / "c.json").write_text(json.dumps({"key_id": "ef" * 32, "payment_hash": ph}))
        self.assertEqual(check_no_preimages.scan([self.d]), [])

    def test_the_checkout_holds_no_preimage(self):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "check_no_preimages.py")],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
