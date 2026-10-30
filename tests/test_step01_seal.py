"""steps/01-seal: a capsule capsule-emit sealed verifies Class 1 in Go, and a planted failure turns it FAIL.

The real tools come from steps/01-seal.setup.sh (network, run once). Without them
the tests that need them SKIP and say so; nothing here passes on a missing tool.
Fixtures are in tests/fixtures/step01/, made by its make_fixtures.py.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402

STEP = ROOT / "steps" / "01-seal"
FIXTURES = ROOT / "tests" / "fixtures" / "step01"
POPULATION = FIXTURES / "population" / "rehearsal"
SEALED = FIXTURES / "sealed-capsule.json"
TOOLS = Path(os.environ.get("LOOP_TOOLS") or Path.home() / ".cache" / "evidence-network-loop") / "01-seal"
PYTHON, VERIFIER = TOOLS / "venv" / "bin" / "python", TOOLS / "bin" / "cross_language"
SETUP = "run steps/01-seal.setup.sh (needs network) to install it"


def need(*tools):
    for t in tools:
        if not t.is_file():
            msg = f"SKIPPED LOUDLY: step 01's tool {t} is not installed; {SETUP}"
            if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":  # CI, after the setups: a missing tool is an error
                raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {msg}")
            print(msg, file=sys.stderr)
            raise unittest.SkipTest(msg)


def go_capsule_id(capsule):
    """The capsule_id the Go verifier computes for a capsule (its id and envelope fields removed)."""
    body = {k: v for k, v in capsule.items() if k not in ("capsule_id", "signature", "key_id")}
    out = subprocess.run([str(VERIFIER), "seal"], input=json.dumps(body).encode(), capture_output=True, check=True)
    return json.loads(out.stdout)["capsule_id"]


class Step01SealTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.findings = Path(self.tmp.name) / "findings"
        self.out = self.findings / "01-seal"

    def tearDown(self):
        self.tmp.cleanup()

    def run_step(self, *args, env=None):
        subprocess.run([sys.executable, "-B", str(STEP), "--population", str(POPULATION), "--out", str(self.out),
                        *args], check=True, env=env)
        self.assertEqual(finding.check(self.findings), [])
        return json.loads((self.out / "finding.json").read_text())

    def run_sealed(self, data, *args):
        planted = Path(self.tmp.name) / "planted.json"
        planted.write_bytes(data)
        return self.run_step("--sealed", str(planted), *args)

    def checks(self):
        return json.loads((self.out / "checks.json").read_text())["checks"]

    def test_the_rehearsal_population_seals_and_verifies_in_go(self):
        need(PYTHON, VERIFIER)
        r = self.run_step()
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertEqual(sorted(r["pinned"]), ["aac-go", "capsule-emit"])
        capsule = json.loads((self.out / "capsule.json").read_text())
        self.assertEqual(capsule["effect"]["effect_attestation"], "runtime_claimed")
        self.assertEqual(go_capsule_id(capsule), capsule["capsule_id"])
        self.assertTrue(all(c["ok"] for c in self.checks().values()))
        # The envelope signature is verified in Go (cross_language signature): nothing is outside the assertion.
        self.assertEqual(r["not_checked"], [])
        self.assertEqual(self.checks()["signature"]["state"], "ok")
        self.assertIn("producer signature authored under", r["detail"])
        self.assertEqual(finding.gaps(r["detail"]), [])

    def test_the_fixture_capsule_verifies(self):
        need(VERIFIER)
        r = self.run_sealed(SEALED.read_bytes())
        self.assertEqual(r["result"], "PASS", r["detail"])

    def test_one_flipped_byte_fails(self):
        need(VERIFIER)
        data = bytearray(SEALED.read_bytes())
        at = data.index(b'"operator": "rehearsal"') + len(b'"operator": "')
        data[at] ^= 0x01
        r = self.run_sealed(bytes(data))
        self.assertEqual(r["result"], "FAIL")
        self.assertFalse(self.checks()["class1"]["ok"])
        self.assertFalse(self.checks()["capsule_id"]["ok"])

    def test_a_capsule_id_that_does_not_recompute_fails(self):
        need(VERIFIER)
        capsule = json.loads(SEALED.read_text())
        capsule["capsule_id"] = "0" * 64
        r = self.run_sealed(json.dumps(capsule).encode())
        self.assertEqual(r["result"], "FAIL")
        self.assertFalse(self.checks()["capsule_id"]["ok"])

    def test_an_in_process_record_claiming_a_stronger_class_fails(self):
        need(VERIFIER)
        capsule = json.loads(SEALED.read_text())
        capsule["effect"]["effect_attestation"] = "gate_executed"
        capsule["capsule_id"] = go_capsule_id(capsule)  # Class 1 and the id still hold; the class does not
        r = self.run_sealed(json.dumps(capsule).encode())
        self.assertEqual(r["result"], "FAIL")
        self.assertTrue(self.checks()["class1"]["ok"])
        self.assertFalse(self.checks()["effect_class"]["ok"])

    def test_an_effect_without_effect_attestation_fails(self):
        need(VERIFIER)
        capsule = json.loads(SEALED.read_text())
        del capsule["effect"]["effect_attestation"]
        capsule["capsule_id"] = go_capsule_id(capsule)
        r = self.run_sealed(json.dumps(capsule).encode())
        self.assertEqual(r["result"], "FAIL")
        self.assertFalse(self.checks()["class1"]["ok"])  # the Go verifier's check 5
        self.assertFalse(self.checks()["effect_class"]["ok"])

    def test_a_record_without_model_attestation_fails(self):
        need(VERIFIER)
        capsule = json.loads(SEALED.read_text())
        del capsule["model_attestation"]
        capsule["capsule_id"] = go_capsule_id(capsule)
        r = self.run_sealed(json.dumps(capsule).encode())
        self.assertEqual(r["result"], "FAIL")
        self.assertFalse(self.checks()["model"]["ok"])

    def test_a_capsule_under_another_nodes_key_fails(self):
        need(VERIFIER)
        r = self.run_sealed(SEALED.read_bytes(), "--node", "bob")
        self.assertEqual(r["result"], "FAIL")
        self.assertFalse(self.checks()["key"]["ok"])

    def test_an_invalid_signature_fails(self):
        need(VERIFIER)
        capsule = json.loads(SEALED.read_text())
        sig = capsule["signature"]
        capsule["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]  # the id excludes the signature
        r = self.run_sealed(json.dumps(capsule).encode())
        self.assertEqual(r["result"], "FAIL")
        self.assertTrue(self.checks()["class1"]["ok"])
        self.assertFalse(self.checks()["signature"]["ok"])

    def test_an_unsigned_record_is_not_checked_not_pass(self):
        need(VERIFIER)
        capsule = json.loads(SEALED.read_text())
        del capsule["signature"], capsule["key_id"]  # the id excludes both
        r = self.run_sealed(json.dumps(capsule).encode())
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertEqual(self.checks()["signature"]["state"], "unclaimed")
        self.assertEqual([n["what"] for n in r["not_checked"]],
                         ["the producer envelope signature, verified in a second language"])

    def test_a_live_node_is_held_to_its_published_key(self):
        need(VERIFIER)
        loader = importlib.machinery.SourceFileLoader("step01_seal", str(STEP))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("step01_seal", loader))
        loader.exec_module(mod)
        key_id = json.loads(SEALED.read_text())["key_id"]
        data = SEALED.read_bytes()
        nodes = Path(self.tmp.name) / "nodes"
        nodes.mkdir()
        cases = {  # listing -> (ok, state)
            None: (True, "unlisted"),
            key_id: (True, "ok"),
            "f" * 64: (False, "bad"),
        }
        for listing, (ok, state) in cases.items():
            (nodes / "n1.json").unlink(missing_ok=True)
            if listing:
                (nodes / "n1.json").write_text(json.dumps({"key_id": listing}))
            got = mod.signature_check(data, VERIFIER, key_id, mod.listed_key(nodes, "n1"), listing_expected=True)
            self.assertEqual((got["ok"], got["state"]), (ok, state), got)

    def test_missing_tools_read_not_checked_never_pass(self):
        env = dict(os.environ, LOOP_TOOLS=str(Path(self.tmp.name) / "no-tools"))
        r = self.run_step(env=env)
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertEqual(sorted(n["what"] for n in r["not_checked"]),
                         ["aac-go", "capsule-emit"])


if __name__ == "__main__":
    unittest.main()
