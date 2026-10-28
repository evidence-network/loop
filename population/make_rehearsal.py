#!/usr/bin/env python3
"""Make the rehearsal population: population.json, the Alice and Bob TEST keys, and the rehearsal witness.

Usage: make_rehearsal.py [--force]

The keys are Ed25519 test keys, committed and public on purpose. No live node
ever uses them. The rehearsal witness is a test witness: its key is under
keys/witness/, and witnesses.json is the witness directory a verifier trusts
it under. Run once; --force replaces the keys already there.
Needs openssl (3.x) on PATH.
"""
import base64
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REHEARSAL = HERE / "rehearsal"
NODES = ("alice", "bob")
# The rehearsal witness: a test witness, its receipts mean nothing outside the rehearsal.
WITNESS = {"name": "rehearsal-witness", "url": "https://witness.rehearsal.invalid",
           "key_dir": "population/rehearsal/keys/witness", "directory": "population/rehearsal/witnesses.json"}


def make_key(key_dir, stem="node-key"):
    key_dir.mkdir(parents=True, exist_ok=True)
    priv, pub = key_dir / f"{stem}.pem", key_dir / f"{stem}.pub.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(priv)], check=True)
    subprocess.run(["openssl", "pkey", "-in", str(priv), "-pubout", "-out", str(pub)], check=True)


def directory(pub_pem):
    """The witness directory (witnesses.json): the witness's endpoint and its raw Ed25519 key in hex."""
    der = base64.b64decode("".join(ln for ln in pub_pem.read_text().splitlines() if not ln.startswith("-----")))
    return {"witnesses": [{"name": WITNESS["name"], "endpoint": WITNESS["url"], "key_ids": [der[-32:].hex()]}]}


def main(argv):
    force = "--force" in argv[1:]
    for name in NODES:
        key_dir = REHEARSAL / "keys" / name
        if (key_dir / "node-key.pem").exists() and not force:
            print(f"{name}: key exists, kept (--force replaces it)")
            continue
        make_key(key_dir)
        print(f"{name}: test key written")
    witness_dir = REHEARSAL / "keys" / "witness"
    if (witness_dir / "witness-key.pem").exists() and not force:
        print("witness: key exists, kept (--force replaces it)")
    else:
        make_key(witness_dir, "witness-key")
        print("witness: test key written")
    (REHEARSAL / "witnesses.json").write_text(json.dumps(directory(witness_dir / "witness-key.pub.pem"), indent=2) + "\n")
    population = {
        "population": "rehearsal",
        "nodes": [{"name": n, "key_dir": f"population/rehearsal/keys/{n}"} for n in NODES],
        "rail": "mock",
        "witness": WITNESS,
    }
    (REHEARSAL / "population.json").write_text(json.dumps(population, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
