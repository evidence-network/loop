import base64
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def load():
    spec = importlib.util.spec_from_file_location("check_commitment", ROOT / "scripts" / "check_commitment.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CommitmentInputTest(unittest.TestCase):
    def test_the_input_carries_the_signed_commitment_and_the_policy_bytes_that_its_digest_names(self):
        mod = load()
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "policies").mkdir()
            policy = b'{"policy":{"x":1},"sig":{}}'
            (d / "policies" / "n.json").write_bytes(policy)
            doc = {"key_id": "aa", "lcap": {"policy_digest": hashlib.sha256(policy).hexdigest(),
                                            "policy_uri": "policies/n.json"}}
            (d / "n.json").write_text(json.dumps(doc))
            got = mod.commitment_input(d / "n.json")
            self.assertEqual(got["commitment"], doc)
            raw = base64.b64decode(got["policy"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), doc["lcap"]["policy_digest"])


if __name__ == "__main__":
    unittest.main()
