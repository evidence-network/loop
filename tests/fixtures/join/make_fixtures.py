#!/usr/bin/env python3
"""Make the join/leave test fixtures once, from the rehearsal TEST keys.

Usage: python3 -B tests/fixtures/join/make_fixtures.py [OUT_DIR]   (default: this folder)

Signs with population/rehearsal/keys/{alice,bob}: TEST keys, committed on purpose,
never used live. A test node's node_id is its key_id, as on the rehearsal
population. Writes:
- good-join.json: alice joins under today's operator statement;
- good-leave.json: alice leaves, an hour later;
- forged-join.json: claims alice's key_id and key, signed by bob;
- forged-join-own-key.json: claims alice's key_id, carries and is signed by bob's key;
- stale-consent-join.json: alice joins under the first operator statement;
- handle-join.json: alice's join with an extra `handle` field, signed with it.
Ed25519 signatures are deterministic, so rerunning rewrites the files identically.
"""
import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_commitment  # noqa: E402
import check_join  # noqa: E402

KEYS = ROOT / "population" / "rehearsal" / "keys"
JOIN_AT = "2026-10-27T20:00:00Z"
LEAVE_AT = "2026-10-27T21:00:00Z"


def key(name):
    """(private key path, spki base64, key_id) of a rehearsal TEST key."""
    der = check_commitment.spki_from_pem((KEYS / name / "node-key.pub.pem").read_text())
    return KEYS / name / "node-key.pem", base64.b64encode(der).decode("ascii"), check_commitment.key_id_of(der)


def record(kind, owner, at, consent=check_join.CONSENT_SHA256):
    _, spki, key_id = key(owner)
    return {"kind": kind, "node_id": key_id, "key_id": key_id, "consent_sha256": consent, "at": at, "spki": spki}


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def fixtures():
    alice, bob = key("alice")[0], key("bob")[0]
    _, bob_spki, _ = key("bob")
    older = check_join.consent_sha256(next(iter(check_join.OLDER_CONSENTS.values())))
    own_key = record("join/1", "alice", JOIN_AT)
    own_key["spki"] = bob_spki
    return {
        "good-join.json": check_commitment.sign(record("join/1", "alice", JOIN_AT), alice),
        "good-leave.json": check_commitment.sign(record("leave/1", "alice", LEAVE_AT), alice),
        "forged-join.json": check_commitment.sign(record("join/1", "alice", JOIN_AT), bob),
        "forged-join-own-key.json": check_commitment.sign(own_key, bob),
        "stale-consent-join.json": check_commitment.sign(record("join/1", "alice", JOIN_AT, older), alice),
        "handle-join.json": check_commitment.sign({**record("join/1", "alice", JOIN_AT), "handle": "alice-test"},
                                                  alice),
    }


def main(argv):
    out = Path(argv[1]) if len(argv) > 1 else HERE
    out.mkdir(parents=True, exist_ok=True)
    for name, doc in fixtures().items():
        dump(out / name, doc)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
