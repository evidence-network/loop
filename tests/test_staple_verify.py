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
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import staple_stub  # noqa: E402
import staple_verify  # noqa: E402

# Copies of the Goose rehearsal's buyer and seller commitments (real evidence-commitment/1), so a later data commit cannot move them.
REHEARSAL = ROOT / "tests/fixtures/staple"
STUB = staple_stub.path()
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

    def test_each_card_is_judged_under_its_own_entrys_key_only(self):
        doc = commitment()
        other = dict(doc["history_cards"][0], stream="goose/other",
                     checkpoint_key=doc["history_cards"][0]["checkpoint_key"][:-3] + "AAA")
        nokey = {k: v for k, v in doc["history_cards"][0].items() if k != "checkpoint_key"}
        doc["history_cards"] = sorted(doc["history_cards"] + [other, dict(nokey, stream="goose/keyless")],
                                      key=lambda c: c["stream"])
        self.path.write_text(json.dumps(doc))
        got = staple_verify.verify(self.path, doc["key_id"], STUB)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(calls), 3)  # keyless, goose/node's key, goose/other's key
        self.assertEqual(sorted(c.count("--checkpoint-key") for c in calls), [0, 1, 1])
        self.assertEqual(got["report"]["capsule_cli_runs"], 3)
        # With the stub holding a card to its own entry's key, only the keyed cards verify; the keyless one does not.
        os.environ["STUB_KEYED"] = "1"
        self.addCleanup(os.environ.pop, "STUB_KEYED", None)
        keyed = staple_verify.verify(self.path, doc["key_id"], STUB)
        self.assertEqual(keyed["state"], "NOT_CHECKED")
        self.assertEqual(keyed["not_checked"], ["history_card/1 of goose/keyless: checkpoint_key NOT_CHECKED "
                                                "(no checkpoint_key on its entry, or no key given)"])
        self.assertEqual(sorted(s["stream"] for s in got["report"]["staples"]),
                         ["goose/keyless", "goose/node", "goose/other"])

    def test_a_failure_survives_a_later_group_that_gives_no_report(self):
        doc = commitment()
        other = dict(doc["history_cards"][0], stream="goose/other",
                     checkpoint_key=doc["history_cards"][0]["checkpoint_key"][:-3] + "AAA")
        doc["history_cards"] = sorted(doc["history_cards"] + [other], key=lambda c: c["stream"])
        self.path.write_text(json.dumps(doc))
        flaky = self.tmp / "flaky"  # fails the first run it is given (keys sort goose/node's before goose/other's)
        flaky.write_text(f"#!{sys.executable}\nimport os, sys\nn = os.path.exists('{self.tmp}/ran')\n"
                         f"open('{self.tmp}/ran', 'w')\nif n:\n    print('not json')\n    sys.exit(1)\n"
                         f"os.environ['STUB_MODE'] = 'fail'\nos.execv('{STUB}', ['{STUB}'] + sys.argv[1:])\n")
        flaky.chmod(0o755)
        got = staple_verify.verify(self.path, doc["key_id"], flaky)
        self.assertEqual(got["state"], "FAIL")
        self.assertTrue(got["errors"])
        self.assertTrue(any("gave no report" in m for m in got["not_checked"]), got["not_checked"])

    def test_a_legacy_commitment_is_not_checked_and_marked_legacy(self):
        doc = commitment()
        doc["profile"] = "evidence-commitment/0"
        self.path.write_text(json.dumps(doc))
        got = staple_verify.verify(self.path, doc["key_id"], STUB)
        self.assertEqual((got["state"], got["legacy"]), ("NOT_CHECKED", True))
        with unittest.mock.patch.dict(os.environ, {"CAPSULE_CLI": str(self.tmp / "absent")}):  # not installed: still legacy
            self.assertTrue(staple_verify.verify(self.path, doc["key_id"])["legacy"])
        self.assertFalse(self.log.exists())

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
        with unittest.mock.patch.dict(os.environ, {"CAPSULE_CLI": str(self.tmp / "absent")}):
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
