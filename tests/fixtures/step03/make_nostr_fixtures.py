#!/usr/bin/env python3
"""Make step 03's Nostr carrier fixtures: a population whose node alice is read back from recorded relay responses.

Usage: python3 -B tests/fixtures/step03/make_nostr_fixtures.py     (needs openssl)

Writes, under tests/fixtures/step03/:
- population/nostr/population.json: alice (step 12's alice TEST key, the node commitment1/nostr/ is signed by) and bob
  (step 03's rehearsal bob, who advertises nothing on Nostr).
- population/nostr/advertise/alice/signed/{commitment,policy}.json: commitment1/nostr/'s evidence-commitment/1
  with its Nostr route, its policy moved to policies/<node_id>.json and signed again at a fixed issued_at, so the
  bytes never change between runs.
- population/nostr/advertise/alice/nostr.json: the good recording, both relays returning the node's announce event
  (AAC evidence-commitment-carriers v0 §5.3, kind 34700) signed by the mailbox key (BIP-340's test key, secret 3).
- nostr/cases/<case>.json: one recording per negative or odd case; tests/test_nostr_carrier.py names each.
Every key is a TEST key and every event is signed with fixed aux randomness. No relay was contacted: these are
recordings of what a relay would return, built here, until a real producer publishes (capsules-plugin#212).
"""
import base64
import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lab"))
import check_commitment as cc  # noqa: E402
import nostr_sign as ns  # noqa: E402

POP = HERE / "population" / "nostr"
CASES = HERE / "nostr" / "cases"
ALICE_KEY = ROOT / "tests" / "fixtures" / "step12" / "population" / "keys" / "alice" / "node-key.pem"
SOURCE = ROOT / "tests" / "fixtures" / "commitment1" / "nostr"
RELAYS = ("wss://relay.example", "wss://relay2.example")
MAILBOX_SECRET = 3  # BIP-340 test vector 0: f9308a01...36f9, the mailbox key commitment1/nostr/ names
OTHER_SECRET = 5
READ_AT = "2026-11-05T12:00:00Z"
NEWER, OLDER, FUTURE = "2026-11-05T00:00:00Z", "2026-11-04T00:00:00Z", "2026-12-31T00:00:00Z"


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def commitment(issued_at, node_id, change=None, **members):  # members replace top-level fields, but not node_id

    doc = json.loads((SOURCE / "commitment.json").read_text())
    doc["lcap"]["policy_uri"] = f"policies/{node_id}.json"
    doc["issued_at"] = issued_at
    doc.update(members)
    if change:
        change(doc)
    signed = cc.sign(doc, ALICE_KEY)
    return (json.dumps(signed, indent=2, sort_keys=True) + "\n").encode()


def event(octets, secret=MAILBOX_SECRET, node_id=None, created_at=1793836800, tags=None, content=None):
    pub = ns.pubkey(secret.to_bytes(32, "big")).hex()
    ev = {"pubkey": pub, "created_at": created_at, "kind": 34700,
          "tags": tags or [["d", node_id], ["t", "evidence-commitment"], ["x", hashlib.sha256(octets).hexdigest()]],
          "content": b64url(octets) if content is None else content}
    digest = ns.event_id(ev)
    sig = ns.schnorr_sign(digest, secret.to_bytes(32, "big"), hashlib.sha256(b"loop#305 fixture " + digest).digest())
    return {"id": digest.hex(), **ev, "sig": sig.hex()}


def recording(per_relay):
    return {"read_at": READ_AT, "relays": {r: per_relay.get(r, []) for r in RELAYS}}


def main():
    source = json.loads((SOURCE / "commitment.json").read_text())
    node_id = source["node_id"]
    newer, older = commitment(NEWER, node_id), commitment(OLDER, node_id)
    other = commitment(NEWER, node_id, answers_within="PT2H")  # same issued_at, different octets
    future = commitment(FUTURE, node_id)
    both = lambda ev: {r: [ev] for r in RELAYS}  # noqa: E731
    good = event(newer, node_id=node_id)

    dump(POP / "population.json", {"population": "rehearsal", "rail": "mock", "nodes": [
        {"name": "alice", "key_dir": "../step12/population/keys/alice"},
        {"name": "bob", "key_dir": "population/rehearsal/keys/bob"}]})
    # step12's alice key dir holds node-key.pem and node-key.pub.pem, the key_dir shape the step reads.
    adv = POP / "advertise" / "alice"
    (adv / "signed").mkdir(parents=True, exist_ok=True)
    (adv / "signed" / "commitment.json").write_bytes(newer)
    (adv / "signed" / "policy.json").write_bytes((SOURCE / "disclosure-policy.json").read_bytes())
    dump(adv / "nostr.json", recording(both(good)))

    forged_sig = dict(good, sig=good["sig"][:-2] + ("00" if good["sig"][-2:] != "00" else "01"))
    forged_id = dict(good, id="0" * 64)
    wrong_x = event(newer, node_id=node_id, tags=[["d", node_id], ["t", "evidence-commitment"], ["x", "ab" * 32]])
    edited = copy.deepcopy(json.loads(newer))
    edited["answers_within"] = "PT2H"  # a member changed, sig kept: the Ed25519 signature no longer fits
    edited_octets = (json.dumps(edited, indent=2, sort_keys=True) + "\n").encode()
    other_pub = ns.pubkey(OTHER_SECRET.to_bytes(32, "big")).hex()
    rotated = commitment("2026-11-05T06:00:00Z", node_id)
    rotated_doc = json.loads(rotated)
    rotated_doc["ask"]["nostr"]["mailbox_key"] = other_pub  # the node moved to a new mailbox key
    rotated = (json.dumps(cc.sign(rotated_doc, ALICE_KEY), indent=2, sort_keys=True) + "\n").encode()
    empty = event(newer, node_id=node_id, content="", tags=[["d", node_id], ["t", "evidence-commitment"],
                                                           ["x", hashlib.sha256(newer).hexdigest()],
                                                           ["r", "https://node.example/commitment.json"]])
    new_policy = lambda d: d["lcap"].__setitem__("policy_digest", "ab" * 32)  # noqa: E731
    newer_policy = commitment("2026-11-05T06:00:00Z", node_id, new_policy)
    older_policy = commitment(OLDER, node_id, new_policy)
    other_node = commitment(NEWER, node_id, lambda d: d.__setitem__("node_id", "cd" * 32))
    surrogate = dict(good, tags=good["tags"] + [["foo", "\ud800"]])
    bad_tag = event(newer, node_id=node_id, tags=[["d", 123], ["t", "evidence-commitment"], ["x", {}]])
    bad_content = event(newer, node_id=node_id, content="A")  # validly signed, not base64url octets
    cases = {
        "newer-policy-changed": recording({RELAYS[0]: [event(newer_policy, node_id=node_id)], RELAYS[1]: [good]}),
        "older-policy-changed": recording({RELAYS[0]: [event(older_policy, node_id=node_id)], RELAYS[1]: [good]}),
        "relayed-newer": recording({RELAYS[0]: [event(rotated, secret=OTHER_SECRET, node_id=node_id)],
                                    RELAYS[1]: [good]}),
        "relayed-conflict": recording({RELAYS[0]: [event(other, secret=OTHER_SECRET, node_id=node_id)],
                                       RELAYS[1]: [good]}),
        "other-node-inside": recording({RELAYS[0]: [event(other_node, node_id=node_id)], RELAYS[1]: [good]}),
        "surrogate-tag": recording({RELAYS[0]: [surrogate], RELAYS[1]: [good]}),
        "not-a-commitment": recording(both(event(b"not json", node_id=node_id))),
        "older-and-over-size": recording({RELAYS[0]: [event(older, node_id=node_id)],
                                          RELAYS[1]: [event(newer, node_id=node_id, content="", tags=[
                                              ["d", node_id], ["t", "evidence-commitment"],
                                              ["x", hashlib.sha256(newer).hexdigest()],
                                              ["r", "https://node.example/commitment.json"]])]}),
        "only-future-dated": recording(both(event(future, node_id=node_id))),
        "good": recording(both(good)),
        "forged-signature": recording(both(forged_sig)),
        "forged-id": recording(both(forged_id)),
        "digest-mismatch": recording(both(wrong_x)),
        "edited-commitment": recording(both(event(edited_octets, node_id=node_id))),
        "relayed-only": recording(both(event(newer, secret=OTHER_SECRET, node_id=node_id))),
        "older-beside-newer": recording({RELAYS[0]: [event(older, node_id=node_id)], RELAYS[1]: [good]}),
        "only-older": recording(both(event(older, node_id=node_id))),
        "relays-conflict": recording({RELAYS[0]: [good], RELAYS[1]: [event(other, node_id=node_id)]}),
        "forged-beside-good": recording({RELAYS[0]: [forged_sig, event(newer, secret=OTHER_SECRET, node_id=node_id)],
                                         RELAYS[1]: [good]}),
        "future-dated-beside-good": recording({RELAYS[0]: [event(future, node_id=node_id)], RELAYS[1]: [good]}),
        "wrong-d": recording(both(event(newer, node_id="cd" * 32))),
        "over-size": recording(both(event(newer, node_id=node_id, content="",
                                          tags=[["d", node_id], ["t", "evidence-commitment"],
                                                ["x", hashlib.sha256(newer).hexdigest()],
                                                ["r", "https://node.example/commitment.json"]]))),
        "no-events": recording({}),
        "key-rotated": recording({r: [event(rotated, secret=OTHER_SECRET, node_id=node_id), good] if r == RELAYS[0]
                                  else [good] for r in RELAYS}),
        "key-rotated-only": recording(both(event(rotated, secret=OTHER_SECRET, node_id=node_id))),
        "malformed-beside-good": recording({RELAYS[0]: [bad_tag, bad_content], RELAYS[1]: [good]}),
        "conflict-with-over-size": dict(recording({RELAYS[0]: [good], RELAYS[1]: [event(other, node_id=node_id)]}),
                                         relays={**recording({RELAYS[0]: [good], RELAYS[1]: [event(other, node_id=node_id)]})["relays"],
                                                 "wss://relay3.example": [empty]}),
    }
    for name, rec in cases.items():
        dump(CASES / f"{name}.json", rec)


if __name__ == "__main__":
    main()
