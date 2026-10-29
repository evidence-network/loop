#!/usr/bin/env python3
"""Make tests/fixtures/step09/: a live-shaped population whose lab nodes already hold an adjudication.

Usage: python3 -B tests/fixtures/step09/make_fixtures.py [CAPSULES_PLUGIN_CLONE]

- live/: population.json naming three nodes (data_dir "$LAB_DIR/nodes/lab-N/data")
  and, for each, keys/node-key.pub.pem and the ledger/ files the rehearsal's acted
  adjudication left (steps/09-dispute's act(), on the rehearsal TEST keys): lab-1 is
  the seller (alice), lab-2 the buyer (bob), lab-3 the referee. No private key is
  copied.
- keys/stranger/: a TEST key that is no node's, for the wrong-key plant. Made once
  with `openssl genpkey -algorithm ed25519`; this script leaves it as it is.
- referee-verdict.json: the verdict the pinned capsules plugin's own tests sign
  (crates/evidence-plugin/tests/fixtures/referee-verdict.json at the commit
  versions.lock pins as `capsules`), copied when a clone of the plugin is given.
  The plugin is Apache-2.0.
"""
import importlib.machinery
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
TIME = "2026-10-28T12:00:00Z"
NODES = {"alice": "lab-1", "bob": "lab-2", "referee": "lab-3"}


def load_step():
    loader = importlib.machinery.SourceFileLoader("step09_dispute", str(ROOT / "steps" / "09-dispute"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def live(step):
    pop_dir = ROOT / "population" / "rehearsal"
    doc = json.loads((pop_dir / "step09" / "dispute.json").read_text())
    dest = HERE / "live"
    shutil.rmtree(dest, ignore_errors=True)
    with tempfile.TemporaryDirectory() as tmp:
        acted = Path(tmp) / "09-dispute"
        acted.mkdir()
        step.act(pop_dir, acted, TIME)
        nodes = []
        for name, lab in NODES.items():
            data = dest / "nodes" / lab / "data"
            shutil.copytree(acted / name / "ledger", data / "ledger")
            (data / "keys").mkdir(parents=True)
            shutil.copyfile(step.key_dir_of(pop_dir, name, doc["nodes"][name]) / "node-key.pub.pem",
                            data / "keys" / "node-key.pub.pem")
            nodes.append({"name": lab, "node_id": doc["nodes"][name]["node_id"], "host": "lab",
                          "data_dir": f"$LAB_DIR/nodes/{lab}/data", "book_dir": None})
    (dest / "population.json").write_text(json.dumps({"population": "live", "nodes": nodes, "rail": None},
                                                     indent=2) + "\n")


def verdict(clone):
    ref = next(ln.split()[2] for ln in (ROOT / "versions.lock").read_text().splitlines()
               if ln.split()[:1] == ["capsules"])
    raw = subprocess.run(["git", "-C", str(clone), "show",
                          f"{ref}:crates/evidence-plugin/tests/fixtures/referee-verdict.json"],
                         check=True, capture_output=True).stdout
    (HERE / "referee-verdict.json").write_bytes(raw)


if __name__ == "__main__":
    live(load_step())
    if len(sys.argv) > 1:
        verdict(sys.argv[1])
