#!/usr/bin/env python3
"""Make step 12's test fixtures, once. Needs openssl and the pinned capsulectl (steps/12-join.setup.sh).

Usage: python3 -B tests/fixtures/step12/make_fixtures.py

  population/            a rehearsal population of two nodes, alice and bob,
                         with TEST keys made here (published on purpose, never
                         used by a live node)
  joined/alice/          what join.sh wrote for alice: binding, book, policy,
                         commitment, and the stranger's probe with its book
  wrong-key/checkpoints.answer.json
                         bob's book answering alice's stranger checkpoints
                         request: a probe answer signed by the wrong key

Keys already present are kept, so re-running changes only the joined output.
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
NODES = ("alice", "bob")


def capsulectl():
    if os.environ.get("CAPSULECTL"):
        return os.environ["CAPSULECTL"]
    return str(Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache/evidence-network-loop")) / "bin/capsulectl")


def keys():
    for name in NODES:
        d = HERE / "population/keys" / name
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "node-key.pem").is_file():
            subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(d / "node-key.pem")], check=True)
        subprocess.run(["openssl", "pkey", "-in", str(d / "node-key.pem"), "-pubout", "-out",
                        str(d / "node-key.pub.pem")], check=True)
        if not (d / "controller-key.pem").is_file():
            subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(d / "controller-key.pem")],
                           check=True)
    rel = HERE.relative_to(ROOT) / "population/keys"
    pop = {"population": "rehearsal", "nodes": [{"name": n, "key_dir": f"{rel}/{n}"} for n in NODES], "rail": "mock"}
    (HERE / "population/population.json").write_text(json.dumps(pop, indent=2) + "\n")
    (HERE / "population/keys/README.md").write_text(
        "TEST KEYS. Made by make_fixtures.py for step 12's tests, published on purpose, never used by a live node.\n")


def join(name, out, home):
    d = HERE / "population/keys" / name
    subprocess.run([str(ROOT / "join.sh"), "--name", name, "--node-key", str(d / "node-key.pem"),
                    "--controller-key", str(d / "controller-key.pem"), "--out", str(out), "--home", str(home)],
                   check=True, env=dict(os.environ, CAPSULECTL=capsulectl()))


def main():
    if not Path(capsulectl()).is_file():
        print(f"make_fixtures: no capsulectl at {capsulectl()}; run steps/12-join.setup.sh", file=sys.stderr)
        return 2
    keys()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        joined = HERE / "joined/alice"
        shutil.rmtree(joined, ignore_errors=True)
        join("alice", joined, tmp / "alice-home")
        join("bob", tmp / "bob-out", tmp / "bob-home")
        # bob's book answers the request alice's stranger sent: the wrong key.
        wrong = HERE / "wrong-key"
        wrong.mkdir(exist_ok=True)
        (wrong / "checkpoints.answer.json").unlink(missing_ok=True)
        env = dict(os.environ, XDG_CONFIG_HOME=str(tmp / "bob-home/config"),
                   CAPSULECTL_PLUGIN_ROOTS=str(Path(capsulectl()).parent))
        subprocess.run([capsulectl(), "book", "respond", "--profile", "bob",
                        "--policy", str(tmp / "bob-out/policy.json"),
                        "--request", str(joined / "probe/checkpoints.request.json"),
                        "--output", str(wrong / "checkpoints.answer.json")],
                       check=True, env=env, stdout=subprocess.DEVNULL)
    print("make_fixtures: wrote population/, joined/alice/ and wrong-key/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
