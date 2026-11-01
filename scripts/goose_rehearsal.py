#!/usr/bin/env python3
"""Run the Goose rehearsal's two nodes and write the population the loop's steps judge them under (loop#330).

Usage: goose_rehearsal.py --work DIR [--tools DIR]

Runs evidencebook's G1 scripted run (python/examples/goose/scripted_run.py at the goose-g1 pin: two
loop_server.py processes, a buyer and a seller, spoken to over MCP stdio as Goose speaks to them) into
DIR/run, then writes DIR/population/population.json in the mesh rehearsal's shape: each node's `key_dir`
holds `node-key.pem` and `node-key.pub.pem`, taken from the node's own producer key, so a step checks
a record under the key the node signed it with. A `goose` member names what the run exported and which
steps read it (`steps`); loop.sh runs a step for Goose only when it is listed and writes NOT_CHECKED for
the others. The keys are generated for the run and are test keys; DIR stays outside the findings.

Exit 0 once population.json is written; exit 1 when the run did not complete or the tools are missing.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

TOOLS = Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache" / "evidence-network-loop")) / "goose"
# The steps that have a Goose input (steps/<NN>-*); each reads population.json's `goose` member.
STEPS = ["01"]
# Run in the Goose venv (the loop's own steps are standard-library only): a node's producer key, PKCS#8 PEM, in;
# its public half, SPKI PEM, out.
PUBLIC = ("import sys; from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, "
          "load_pem_private_key as k; sys.stdout.write(k(open(sys.argv[1],'rb').read(), None).public_key()"
          ".public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode())")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--tools", type=Path, default=TOOLS)
    a = ap.parse_args(argv)
    python, src = a.tools / "venv" / "bin" / "python", a.tools / "src"
    if not python.is_file() or not (src / "python" / "examples" / "goose" / "scripted_run.py").is_file():
        print(f"goose_rehearsal: no Goose tools in {a.tools} (run steps/goose.setup.sh)", file=sys.stderr)
        return 1
    work = a.work.resolve()
    run = work / "run"
    r = subprocess.run([str(python), "-B", str(src / "python" / "examples" / "goose" / "scripted_run.py"), str(run)],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    (work / "scripted_run.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        print(f"goose_rehearsal: the scripted run failed (log: {work / 'scripted_run.log'})", file=sys.stderr)
        return 1
    nodes = []
    for name in ("buyer", "seller"):
        key = run / name / "producer.key"
        kd = work / "keys" / name
        kd.mkdir(parents=True, exist_ok=True)
        pub = subprocess.run([str(python), "-c", PUBLIC, str(key)], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if pub.returncode != 0:
            print(f"goose_rehearsal: cannot read {name}'s producer key: {pub.stderr.strip()}", file=sys.stderr)
            return 1
        (kd / "node-key.pem").write_bytes(key.read_bytes())
        os.chmod(kd / "node-key.pem", 0o600)
        (kd / "node-key.pub.pem").write_text(pub.stdout)
        nodes.append({"name": name, "key_dir": str(kd)})
    # Step 01 checks a capsule a node sealed: the first step of the buyer's own half of the deal.
    half = json.loads((run / "buyer-half.json").read_text())
    capsule = work / "sealed-buyer.json"
    capsule.write_text(json.dumps(half["steps"][0]["capsule"], sort_keys=True))
    pop = work / "population"
    pop.mkdir(parents=True, exist_ok=True)
    doc = {"population": "rehearsal", "nodes": nodes, "rail": "mock",
           "goose": {"run": str(run), "steps": STEPS, "sealed": {"node": "buyer", "capsule": str(capsule)}}}
    (pop / "population.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
