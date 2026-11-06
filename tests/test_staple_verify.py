"""scripts/staple_verify.py: the pinned capsule-cli's `verify --history` on a commitment's stapled history_card/1.

The mapping from capsulectl's per-property report to PASS / FAIL / NOT_CHECKED is tested with a stub
(tests/staple_stub.py). The real binary, when steps/capsule-cli.setup.sh has installed it (LOOP_REQUIRE_TOOLS=1
makes a missing one an error), checks the Goose rehearsal's real commitments and refuses a planted one.
Run: python3 -B -m unittest tests.test_staple_verify -v
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import staple_stub  # noqa: E402
import staple_verify  # noqa: E402

REHEARSAL = ROOT / "findings/goose/2026-11-06-rehearsal/03-advertise"
STUB = staple_stub.use()
REAL = Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache/evidence-network-loop")) / "capsule-cli/bin/capsulectl"


def commitment(name="buyer"):
    return json.loads((REHEARSAL / f"{name}.commitment.json").read_text())


class MappingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = self.tmp / "commitment.json"
        self.path.write_text(json.dumps(commitment()))
        self.log = self.tmp / "argv.log"
        env = {"STUB_LOG": str(self.log)}
        for k, v in env.items():
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)
        self.addCleanup(os.environ.pop, "STUB_MODE", None)

    def test_every_signature_property_passing_is_pass(self):
        got = staple_verify.verify(self.path, commitment()["key_id"], STUB)
        self.assertEqual((got["state"], got["errors"], got["not_checked"]), ("PASS", [], []))
        self.assertEqual(got["outside"], ["caller_invariance", "request_binding"])

    def test_the_keys_come_from_the_loop_not_the_staple(self):
        staple_verify.verify(self.path, "ab" * 32, STUB)
        argv = json.loads(self.log.read_text().splitlines()[0])
        self.assertEqual(argv[argv.index("--counterparty-key") + 1], "ab" * 32)
        self.assertEqual(argv[argv.index("--checkpoint-key") + 1],
                         "e47a24402b214b9c4eb7819a25f3abc7aa19f8a7911ee884fe94f3f3510c4b31")

    def test_a_failing_property_is_a_failure(self):
        os.environ["STUB_MODE"] = "fail"
        got = staple_verify.verify(self.path, commitment()["key_id"], STUB)
        self.assertEqual(got["state"], "FAIL")
        self.assertIn("history_card/1 of goose/node: commitment_signature FAIL: the commitment's key_id is not "
                      "the counterparty key you hold", got["errors"])

    def test_a_property_that_cannot_run_is_not_checked(self):
        os.environ["STUB_MODE"] = "nokey"
        got = staple_verify.verify(self.path, commitment()["key_id"], STUB)
        self.assertEqual(got["state"], "NOT_CHECKED")
        self.assertEqual(got["errors"], [])
        self.assertEqual(got["not_checked"], ["history_card/1 of goose/node: checkpoint_key NOT_CHECKED "
                                              "(no checkpoint_key on its entry, or no key given)"])

    def test_no_binary_is_not_checked_and_names_the_setup(self):
        os.environ["CAPSULE_CLI"], real = str(self.tmp / "absent"), os.environ["CAPSULE_CLI"]
        self.addCleanup(os.environ.__setitem__, "CAPSULE_CLI", real)
        got = staple_verify.verify(self.path, commitment()["key_id"])
        self.assertEqual(got["state"], "NOT_CHECKED")
        self.assertIn("steps/capsule-cli.setup.sh", got["not_checked"][0])
        self.assertIn(staple_verify.pinned_version(), got["not_checked"][0])

    def test_an_unreadable_commitment_is_not_checked(self):
        self.path.write_text("not json")
        self.assertEqual(staple_verify.verify(self.path, "ab" * 32, STUB)["state"], "NOT_CHECKED")

    def test_versions_lock_pins_a_capsule_cli_release_tag(self):
        self.assertRegex(staple_verify.pinned_version(), r"^v\d+\.\d+\.\d+(-rc\d+)?$")


@unittest.skipUnless(REAL.is_file() or os.environ.get("LOOP_REQUIRE_TOOLS"),
                     f"capsule-cli not installed at {REAL}: run steps/capsule-cli.setup.sh (CI does)")
class RealBinaryTest(unittest.TestCase):
    def test_the_installed_binary_is_the_pinned_release(self):
        built = (REAL.parent / "capsule-cli.built-from").read_text().split()
        self.assertEqual(built[1], staple_verify.pinned_version())

    def test_the_goose_rehearsals_real_commitments_verify(self):
        for name in ("buyer", "seller"):
            got = staple_verify.verify(REHEARSAL / f"{name}.commitment.json", commitment(name)["key_id"], REAL)
            self.assertEqual((got["state"], got["errors"], got["not_checked"]), ("PASS", [], []), name)

    def test_another_nodes_key_is_a_failure(self):
        got = staple_verify.verify(REHEARSAL / "buyer.commitment.json", commitment("seller")["key_id"], REAL)
        self.assertEqual(got["state"], "FAIL")

    def test_a_card_altered_after_signing_is_a_failure(self):
        doc = commitment()
        card = json.loads(base64.b64decode(doc["history_cards"][0]["bytes"]))
        card["anchor"]["digest"] = card["anchor"]["digest"][:-1] + ("0" if card["anchor"]["digest"][-1] != "0" else "1")
        doc["history_cards"][0]["bytes"] = base64.b64encode(json.dumps(card).encode()).decode()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "commitment.json"
            path.write_text(json.dumps(doc))
            got = staple_verify.verify(path, doc["key_id"], REAL)
        self.assertEqual(got["state"], "FAIL")


if __name__ == "__main__":
    unittest.main()
