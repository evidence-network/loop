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

OBS = {"capsule_id": "obs-1", "model_attestation": {"compute_attestation": {"x-mesh-settlement-v1": {
    "preimage": "ab" * 32, "payment_hash": "cd" * 32}}}}


class NoPreimagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_preimage_member_is_found_at_any_depth_and_only_when_set(self):
        self.assertTrue(live.carries_preimage(OBS))
        self.assertTrue(live.carries_preimage([{"a": [{"preimage": "x"}]}]))
        self.assertFalse(live.carries_preimage({"preimage": None}))
        self.assertFalse(live.carries_preimage({"preimage": ""}))
        self.assertFalse(live.carries_preimage({"preimage_check": "verified", "payment_hash": "cd" * 32}))

    def test_the_copy_keeps_every_other_line_byte_for_byte_and_stubs_the_rest_at_its_line(self):
        src = self.d / "capsules.jsonl"
        lines = [b'{"capsule_id": "a", "x": 1}', json.dumps(OBS).encode(), b"not json", b'{"capsule_id":"b"}']
        src.write_bytes(b"\n".join(lines) + b"\n")
        self.assertEqual(live.copy_redacted(src, self.d / "out.jsonl"), 1)
        out = (self.d / "out.jsonl").read_bytes().split(b"\n")
        self.assertEqual(len(out), len(lines) + 1)
        self.assertEqual([out[0], out[2], out[3]], [lines[0], lines[2], lines[3]])
        stub = json.loads(out[1])
        self.assertEqual(stub["capsule_id"], "obs-1")
        self.assertEqual(stub["redacted"], live.REDACTED)
        self.assertNotIn(b"ab" * 32, (self.d / "out.jsonl").read_bytes())

    def test_the_scan_names_each_file_and_line_never_the_value(self):
        (self.d / "a.jsonl").write_text('{"ok": 1}\n' + json.dumps(OBS) + "\n")
        (self.d / "b.json").write_text(json.dumps({"deep": OBS}))
        (self.d / "c.json").write_text(json.dumps({"preimage_check": "verified"}))
        found = check_no_preimages.scan([self.d])
        self.assertEqual(found, [f"{self.d / 'a.jsonl'}:2: a preimage member", f"{self.d / 'b.json'}:1: a preimage member"])
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "check_no_preimages.py"), str(self.d)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("ab" * 32, r.stdout + r.stderr)

    def test_a_preimage_beside_its_payment_hash_is_found_under_any_name_and_in_any_file(self):
        pre = bytes(range(32))
        ph = hashlib.sha256(pre).hexdigest()
        (self.d / "ledger.jsonl").write_text(json.dumps({"payment_hash": ph}) + "\n")
        (self.d / "notes.md").write_text("ok\nproof " + pre.hex().upper() + " end\n")
        (self.d / "c.json").write_text(json.dumps({"secret": pre.hex()}))
        found = check_no_preimages.scan([self.d])
        self.assertEqual(sorted(found), sorted([f"{self.d / 'c.json'}:1: a value whose sha256 is in the trees",
                                                f"{self.d / 'notes.md'}:2: a value whose sha256 is in the trees"]))
        # A hash alone, or a 64-hex value whose sha256 is nowhere in the trees, is not a hit.
        (self.d / "notes.md").unlink()
        (self.d / "c.json").write_text(json.dumps({"key_id": "ef" * 32, "payment_hash": ph}))
        self.assertEqual(check_no_preimages.scan([self.d]), [])

    def test_the_published_trees_hold_no_preimage(self):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "check_no_preimages.py")],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
