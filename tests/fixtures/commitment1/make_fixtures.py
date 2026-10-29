#!/usr/bin/env python3
"""Make the evidence-commitment/1 fixtures, once. Needs openssl and the pinned capsulectl (steps/12-join.setup.sh).

Usage: python3 -B tests/fixtures/commitment1/make_fixtures.py

  two-books/   what join.sh wrote for a node serving two books: its own
               (alice-book) and a second one made here first (alice-ledger),
               both signed by the node key. commitment.json staples two
               history_card/1 answers, one per stream.

The node is alice of step 12's rehearsal population: TEST keys
(tests/fixtures/step12/population/keys/alice), published on purpose, never
used by a live node.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
KEYS = ROOT / "tests/fixtures/step12/population/keys/alice"
NAME = "alice"
SECOND = "alice-ledger"


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


def second_book(home, name):
    """A book profile `name` under home's config, signed by the node key, with one record and a checkpoint."""
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
    (d / "request.json").write_text(json.dumps({
        "spec_version": "capsule-seal-request/v1",
        "capsule": {"ActionID": f"{name}-first", "ActionType": "fyi", "Operator": NAME,
                    "Developer": "evidence-network-fixture", "Timestamp": "2026-10-28T00:00:00Z"},
        "payload": {"opened": "2026-10-28T00:00:00Z"}}))
    cap("seal", "--profile", name, "--request", str(d / "request.json"), "--output", str(d / "first.json"))
    cap("cll", "append", "--profile", name, "--capsule", str(d / "first.json"))
    cap("cll", "checkpoint", "create", "--profile", name)


def join(out, home):
    subprocess.run([str(ROOT / "join.sh"), "--name", NAME, "--node-key", str(KEYS / "node-key.pem"),
                    "--controller-key", str(KEYS / "controller-key.pem"), "--out", str(out), "--home", str(home)],
                   check=True, env=dict(os.environ, CAPSULECTL=capsulectl()))


def make(out, home):
    """A two-book join into out, with its profiles under home."""
    second_book(home, SECOND)
    join(out, home)


def main():
    if not Path(capsulectl()).is_file():
        print(f"make_fixtures: no capsulectl at {capsulectl()}; run steps/12-join.setup.sh", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        out = HERE / "two-books"
        shutil.rmtree(out, ignore_errors=True)
        make(out, Path(tmp) / "home")
    print("make_fixtures: wrote two-books/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
