#!/usr/bin/env python3
"""Make the rehearsal population: population.json and the Alice and Bob TEST keys.

Usage: make_rehearsal.py [--force]

The keys are Ed25519 test keys, committed and public on purpose. No live node
ever uses them. Run once; --force replaces the keys already there.
Needs openssl (3.x) on PATH.
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REHEARSAL = HERE / "rehearsal"
NODES = ("alice", "bob")


def make_key(key_dir):
    key_dir.mkdir(parents=True, exist_ok=True)
    priv, pub = key_dir / "node-key.pem", key_dir / "node-key.pub.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(priv)], check=True)
    subprocess.run(["openssl", "pkey", "-in", str(priv), "-pubout", "-out", str(pub)], check=True)


def main(argv):
    force = "--force" in argv[1:]
    for name in NODES:
        key_dir = REHEARSAL / "keys" / name
        if (key_dir / "node-key.pem").exists() and not force:
            print(f"{name}: key exists, kept (--force replaces it)")
            continue
        make_key(key_dir)
        print(f"{name}: test key written")
    population = {
        "population": "rehearsal",
        "nodes": [{"name": n, "key_dir": f"population/rehearsal/keys/{n}"} for n in NODES],
        "rail": "mock",
    }
    (REHEARSAL / "population.json").write_text(json.dumps(population, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
