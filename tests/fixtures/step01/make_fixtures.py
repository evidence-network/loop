#!/usr/bin/env python3
"""Make step 01's test fixtures once; the tests read them and never regenerate.

Run with the Python that steps/01-seal.setup.sh installed (it has `cryptography`):
    ~/.cache/evidence-network-loop/01-seal/venv/bin/python -B tests/fixtures/step01/make_fixtures.py   (from the repo root)

Writes, beside this file:
  population/rehearsal/population.json   the agreed population shape, Alice and Bob
  population/rehearsal/keys/<node>/      TEST Ed25519 keys (PKCS#8 + SPKI PEM), never used live
  sealed-capsule.json                    one capsule steps/01-seal sealed with Alice's test key
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization as s
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
POP = HERE / "population" / "rehearsal"


def main():
    nodes = []
    for name in ("alice", "bob"):
        d = POP / "keys" / name
        d.mkdir(parents=True, exist_ok=True)
        k = Ed25519PrivateKey.generate()
        (d / "node-key.pem").write_bytes(k.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
        (d / "node-key.pub.pem").write_bytes(
            k.public_key().public_bytes(s.Encoding.PEM, s.PublicFormat.SubjectPublicKeyInfo))
        nodes.append({"name": name, "key_dir": f"population/rehearsal/keys/{name}"})
    (POP / "population.json").write_text(
        json.dumps({"population": "rehearsal", "nodes": nodes, "rail": "mock"}, indent=2) + "\n")
    (POP / "keys" / "README.md").write_text(
        "TEST KEYS for step 01's tests only. Published on purpose; never used by a live node.\n")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "01-seal"
        subprocess.run([sys.executable, "-B", str(ROOT / "steps" / "01-seal"), "--population", str(POP),
                        "--out", str(out)], check=True)
        r = json.loads((out / "finding.json").read_text())
        if r["result"] != "PASS":
            sys.exit(f"the fixture seal did not PASS: {r['detail']}")
        shutil.copyfile(out / "capsule.json", HERE / "sealed-capsule.json")


if __name__ == "__main__":
    main()
