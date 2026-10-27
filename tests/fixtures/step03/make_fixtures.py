#!/usr/bin/env python3
"""Make step 03's test fixtures once: a rehearsal population and one node's advertise inputs.

Usage: python3 -B tests/fixtures/step03/make_fixtures.py

Writes, under tests/fixtures/step03/:
- population/rehearsal/population.json, in the agreed shape (alice, bob, mock rail);
- population/rehearsal/keys/<name>/node-key.pem and node-key.pub.pem: Ed25519 TEST
  keys (PKCS#8 and SPKI), made here with openssl, committed on purpose, never used live;
- population/rehearsal/advertise/alice/: commitment.json (the settings the step
  takes), history_card.json (a history_card/1 answer in the capsules plugin's flat
  evidence-interop/0 shape, see tests/reporter/fixtures/README.md; its sig is a
  stand-in) and checkpoint.json (a stand-in checkpoint). bob has none, so the
  step's own stand-ins are exercised too.
- population/live/population.json: the live shape as listed today (no keys, node
  ids not yet known), and population/live-published/population.json: one live
  node whose node id is alice's test key id, for a test carrier that holds a
  commitment signed by that key (the test plays the node). Live populations
  hold no private key: the harness never signs for a live node.
Existing keys are kept; rerunning rewrites the other files identically.
"""
import base64
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
POP = HERE / "population" / "rehearsal"
NAMES = ("alice", "bob")


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def key(name):
    d = POP / "keys" / name
    d.mkdir(parents=True, exist_ok=True)
    priv, pub = d / "node-key.pem", d / "node-key.pub.pem"
    if not priv.exists():
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(priv)], check=True)
    subprocess.run(["openssl", "pkey", "-in", str(priv), "-pubout", "-out", str(pub)], check=True)
    body = "".join(l for l in pub.read_text().splitlines() if not l.startswith("-----"))
    return base64.b64decode(body)[12:].hex()


def main():
    dump(POP / "population.json", {
        "population": "rehearsal",
        "nodes": [{"name": n, "key_dir": f"population/rehearsal/keys/{n}"} for n in NAMES],
        "rail": "mock",
    })
    key_id = {n: key(n) for n in NAMES}
    adv = POP / "advertise" / "alice"
    dump(adv / "commitment.json", {
        "ask": {"bindings": ["capsules-evidence-request/iroh", "a2a/https"]},
        "answers": {"subjects": ["record"], "derivations": ["checkpoints", "history_card/1"]},
        "predicate_classes": ["performed:counterparty_half", "settled:mock"],
        "human_gated": True,
        "answers_within": "PT24H",
        "policy": {"records": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]},
    })
    root = hashlib.sha256(b"alice rehearsal checkpoint").hexdigest()
    artifact = json.dumps({"halves": []}, separators=(",", ":")).encode()
    dump(adv / "history_card.json", {
        "profile": "evidence-interop/0",
        "subject": {"book": key_id["alice"]},
        "derivation": "history_card/1",
        "anchor": {"digest": root, "freshness": "as-of"},
        "artifact_digest": hashlib.sha256(artifact).hexdigest(),
        "responder": key_id["alice"],
        "issued_at": "2026-10-27T00:00:00Z",
        "sig": "stand-in",
        "artifact": base64.urlsafe_b64encode(artifact).rstrip(b"=").decode(),
    })
    dump(HERE / "population" / "live" / "population.json", {
        "population": "live",
        "nodes": [{"name": "lab-1", "node_id": None, "host": "lab"}, {"name": "lab-2", "node_id": None, "host": "lab"}],
        "rail": None,
    })
    dump(HERE / "population" / "live-published" / "population.json", {
        "population": "live",
        "nodes": [{"name": "lab-1", "node_id": key_id["alice"], "host": "lab"}],
        "rail": None,
    })
    dump(adv / "checkpoint.json", {"profile": "rehearsal-stand-in", "root": root, "signature": "stand-in"})


if __name__ == "__main__":
    main()
