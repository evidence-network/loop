#!/usr/bin/env python3
"""Make the evidence-commitment/1 fixtures, once. Needs openssl and the pinned capsulectl (steps/12-join.setup.sh).

Usage: python3 -B tests/fixtures/commitment1/make_fixtures.py [two-books] [cleared] [bound]   (default: all)

  two-books/   what join.sh wrote for a node serving two books: its own
               (alice-book) and a second one made here first (alice-ledger),
               both signed by the node key. commitment.json staples two
               history_card/1 answers, one per stream.
  cleared/     a node whose mesh-llm card is a fresh stream (alice-book-2:
               a checkpoint, no record), as after a clear: commitment.json
               staples that card only, and disclosure-policy.json.
  bound/       a Mesh subject (alice, mesh/) and a Muse subject (bob, muse/:
               a fixture-only commitment, community muse), each with its own
               /1 commitment carrying controller_binding.digest, the
               binding digest (AAC Controller Binding v0 §4) of one
               controller-binding/0 record over both node keys, kept as
               binding.json; bindings/<digest>.json, a `verified`
               binding-verdict/0 of both by binding-verifier at a fake pin
               that only the fixture's versions.lock gives. They group.
  revoked/     the same pair, with a `revoked` verdict. They do not group.

The nodes are alice and bob of step 12's rehearsal population: TEST keys
(tests/fixtures/step12/population/keys/), published on purpose, never used
by a live node. The fake verifier pin exists only in these fixtures.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
POPULATION = ROOT / "tests/fixtures/step12/population/keys"
KEYS = POPULATION / "alice"
NAME = "alice"
SECOND = "alice-ledger"
FRESH = "alice-book-2"
sys.path.insert(0, str(ROOT / "scripts"))
import check_commitment as cc  # noqa: E402

# The binding verifier at a fake pin, given only in these fixtures' versions.lock.
FAKE_VERIFIER = ("binding-verifier", "evidence-network/binding-verifier-fixture", "f1" * 20)
CHECKED_AT = "2026-10-28T00:00:00Z"


def capsulectl():
    if os.environ.get("CAPSULECTL"):
        return os.environ["CAPSULECTL"]
    return str(Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache/evidence-network-loop")) / "bin/capsulectl")


def seed_of(pem, path):
    """The 32-byte Ed25519 seed of a PKCS#8 key, as the hex seed file capsulectl reads (as join.sh does)."""
    der = subprocess.run(["openssl", "pkey", "-in", str(pem), "-outform", "DER"], check=True,
                         capture_output=True).stdout
    path.write_text(der[-32:].hex() + "\n")
    path.chmod(0o600)


def public_of(pem):
    der = subprocess.run(["openssl", "pkey", "-in", str(pem), "-pubout", "-outform", "DER"], check=True,
                         capture_output=True).stdout
    return der[-32:].hex()


def second_book(home, name, record=True):
    """A book profile `name` under home's config, signed by the node key, with a checkpoint over one record
    (or, with record=False, over none: a fresh stream)."""
    env = dict(os.environ, XDG_CONFIG_HOME=str(home / "config"),
               CAPSULECTL_PLUGIN_ROOTS=os.environ.get("CAPSULECTL_PLUGIN_ROOTS", str(Path(capsulectl()).parent)))

    def cap(*args):
        return subprocess.run([capsulectl(), *args], check=True, env=env, capture_output=True, text=True).stdout

    d = home / name
    d.mkdir(parents=True)
    seed_of(KEYS / "node-key.pem", d / "node.seed")
    cap("key", "generate", "--output", str(d / "checkpoint.seed"))
    checkpoint_pub = json.loads(cap("key", "show-public", str(d / "checkpoint.seed")))["public_key"]
    cap("profile", "create", "--name", name, "--type", "jsonl", "--jsonl-path", str(d / "book"), "--log-id", name,
        "--operator", NAME, "--trusted-key", public_of(KEYS / "node-key.pem"),
        "--signing-key-file", str(d / "node.seed"), "--checkpoint-signing-key-file", str(d / "checkpoint.seed"),
        "--checkpoint-trusted-key", checkpoint_pub)
    cap("store", "init", "--profile", name)
    if not record:
        cap("cll", "checkpoint", "create", "--profile", name)
        return
    (d / "request.json").write_text(json.dumps({
        "spec_version": "capsule-seal-request/v1",
        "capsule": {"ActionID": f"{name}-first", "ActionType": "fyi", "Operator": NAME,
                    "Developer": "evidence-network-fixture", "Timestamp": "2026-10-28T00:00:00Z"},
        "payload": {"opened": "2026-10-28T00:00:00Z"}}))
    cap("seal", "--profile", name, "--request", str(d / "request.json"), "--output", str(d / "first.json"))
    cap("cll", "append", "--profile", name, "--capsule", str(d / "first.json"))
    cap("cll", "checkpoint", "create", "--profile", name)


def join(out, home, name=NAME):
    keys = POPULATION / name
    subprocess.run([str(ROOT / "join.sh"), "--name", name, "--node-key", str(keys / "node-key.pem"),
                    "--controller-key", str(keys / "controller-key.pem"), "--out", str(out), "--home", str(home)],
                   check=True, env=dict(os.environ, CAPSULECTL=capsulectl()), stderr=subprocess.DEVNULL)


def make(out, home):
    """A two-book join into out, with its profiles under home."""
    second_book(home, SECOND)
    join(out, home)


def write_json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def commitment_only(joined, out, change, name=NAME):
    """joined's commitment, changed and signed again by the node's TEST key, with its policy, into out."""
    doc = json.loads((joined / "commitment.json").read_text())
    change(doc)
    write_json(out / "commitment.json", cc.sign(doc, POPULATION / name / "node-key.pem"))
    shutil.copy(joined / "disclosure-policy.json", out / "disclosure-policy.json")


def make_cleared(out, tmp):
    """A node that cleared: its mesh-llm card is a fresh stream, with a checkpoint and no record."""
    home, joined = tmp / "cleared-home", tmp / "cleared-out"
    second_book(home, FRESH, record=False)
    join(joined, home)
    commitment_only(joined, out, lambda d: d.__setitem__(
        "history_cards", [c for c in d["history_cards"] if c["stream"] == FRESH]))


def raw_sign(pem, data, tmp):
    """Ed25519 signature (hex) by the key at pem over data."""
    (tmp / "msg").write_bytes(data)
    subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(pem), "-rawin", "-in", str(tmp / "msg"), "-out",
                    str(tmp / "sig")], check=True, capture_output=True)
    return (tmp / "sig").read_bytes().hex()


def binding_record(tmp):
    """A controller-binding/0 record over alice's and bob's node keys, signed by both, and its binding digest:
    the sha256 of the JCS form without sigs (AAC Controller Binding v0 §4)."""
    keys = [(public_of(POPULATION / n / "node-key.pem"), POPULATION / n / "node-key.pem") for n in ("alice", "bob")]
    record = {"binding_version": "controller-binding/0", "claim": "same_controller_asserted",
              "method": "dual_signature/0", "keys": [k for k, _ in keys], "issued_at": CHECKED_AT,
              "not_before": CHECKED_AT, "not_after": "2027-10-28T00:00:00Z", "nonce": "f1" * 16,
              "revocation": "revocation_statement/0"}
    signing_input = cc.canonical(record)
    record["sigs"] = [{"key_id": k, "sig": raw_sign(pem, signing_input, tmp)} for k, pem in keys]
    return record, hashlib.sha256(signing_input).hexdigest()


def make_bound(bound, revoked, tmp):
    """A Mesh subject and a Muse subject under one controller: verified (bound/) and revoked (revoked/)."""
    join(tmp / "mesh-out", tmp / "mesh-home", "alice")
    join(tmp / "muse-out", tmp / "muse-home", "bob")

    record, digest = binding_record(tmp)

    def mesh(d):
        d["controller_binding"] = {"digest": digest}

    def muse(d):
        mesh(d)
        for c in d["history_cards"]:
            c["community"] = "muse"
    lock = ("# versions.lock for these fixtures only: it pins a fake binding verifier.\n"
            + " ".join(FAKE_VERIFIER) + "\n")
    for d, status in ((bound, "verified"), (revoked, "revoked")):
        commitment_only(tmp / "mesh-out", d / "mesh", mesh, "alice")
        commitment_only(tmp / "muse-out", d / "muse", muse, "bob")
        subjects = sorted(json.loads((d / x / "commitment.json").read_text())["node_id"] for x in ("mesh", "muse"))
        write_json(d / "binding.json", record)
        write_json(d / "bindings" / f"{digest}.json", {
            "profile": "binding-verdict/0", "controller_key_id": digest, "subjects": subjects,
            "status": status, "verifier": f"{FAKE_VERIFIER[0]}@{FAKE_VERIFIER[2]}", "checked_at": CHECKED_AT,
            "cites": None})
        (d / "versions.lock").write_text(lock)


def main():
    if not Path(capsulectl()).is_file():
        print(f"make_fixtures: no capsulectl at {capsulectl()}; run steps/12-join.setup.sh", file=sys.stderr)
        return 2
    only = sys.argv[1:] or ["two-books", "cleared", "bound"]  # e.g. `bound` remakes bound/ and revoked/ only
    for d in only + (["revoked"] if "bound" in only else []):
        shutil.rmtree(HERE / d, ignore_errors=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if "two-books" in only:
            make(HERE / "two-books", tmp / "home")
        if "cleared" in only:
            make_cleared(HERE / "cleared", tmp)
        if "bound" in only:
            make_bound(HERE / "bound", HERE / "revoked", tmp)
    print(f"make_fixtures: wrote {', '.join(only)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
