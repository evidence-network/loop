#!/usr/bin/env python3
"""Run the Goose rehearsal's two nodes and write the population the loop's steps judge them under (loop#330).

Usage: goose_rehearsal.py --work DIR [--tools DIR]

Runs scripts/goose_session.py, G1's scripted run (python/examples/goose at the goose-g1 pin: two
loop_server.py processes, a buyer and a seller, spoken to over MCP stdio as Goose speaks to them) with the
mesh rehearsal's mock rail paying the deal, into DIR/run and DIR/population/rail, then writes DIR/population/population.json in the mesh rehearsal's shape: each node's `key_dir`
holds `node-key.pem` and `node-key.pub.pem`, taken from the node's own producer key, so a step checks
a record under the key the node signed it with. A `goose` member names what the run exported and which
steps read it (`steps`); loop.sh runs a step for Goose only when it is listed and writes NOT_CHECKED for
the others. The keys are generated for the run and are test keys; DIR stays outside the findings.

Exit 0 once population.json is written; exit 3 when the Goose tools are not installed (steps/goose.setup.sh); exit 1
when they are and the run did not complete (the tail of the run's log is printed). `--steps` prints the steps that
have a Goose input and exits.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "population" / "rehearsal" / "rail"))
TOOLS = Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache" / "evidence-network-loop")) / "goose"
# The steps that have a Goose input (steps/<NN>-*); each reads population.json's `goose` member.
STEPS = ["01", "06"]
NO_TOOLS = 3  # exit status: the Goose tools are not installed, so nothing was run
# Run in the Goose venv (the loop's own steps are standard-library only): a node's producer key, PKCS#8 PEM, in;
# its public half, SPKI PEM, out.
PUBLIC = ("import sys; from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, "
          "load_pem_private_key as k; sys.stdout.write(k(open(sys.argv[1],'rb').read(), None).public_key()"
          ".public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode())")


def step06_inputs(pop, work, nodes, deal):
    """Step 06's rehearsal inputs for the deal: the exchange (the deal's terms and the rail's settlement by reference),
    the controllers (stand-ins, as in the mesh rehearsal) and each node's signed settlement disclosure."""
    import check_commitment as cc
    import make_rail

    ledger = [json.loads(ln) for ln in (pop / "rail" / "ledger.jsonl").read_text().splitlines()]
    line = ledger[-1]
    if line["exchange_id"] != deal["deal_id"] or line["settlement_id"] != deal["settlement_id"]:
        raise SystemExit("goose_rehearsal: the rail's settlement is not the deal's")
    inputs = pop / "step06"
    make_rail.dump(inputs / "exchange.json", {
        "exchange_id": line["exchange_id"], "payer": line["payer"], "payee": line["payee"],
        "currency": line["currency"], "amount_atomic": line["amount_atomic"], "decimals": line["decimals"],
        "settlement_ref": {"rail": "mock", "settlement_id": line["settlement_id"]}})
    make_rail.dump(inputs / "controllers.json", {"stand_in": True, "controllers": {
        deal["buyer"]: "goose-controller-buyer", deal["seller"]: "goose-controller-seller"}})
    for n in nodes:
        key_id = deal["buyer"] if n["name"] == "buyer" else deal["seller"]
        settled = sum(1 for o in ledger if key_id in (o["payer"], o["payee"]))
        make_rail.dump(inputs / f"disclosure-{n['name']}.json", cc.sign(
            {"type": "settlement-disclosure/v1", "node": n["name"], "key_id": key_id, "rail": "mock",
             "through_sequence": len(ledger), "settlements": settled}, Path(n["key_dir"]) / "node-key.pem"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", type=Path)
    ap.add_argument("--steps", action="store_true", help="print the steps that have a Goose input")
    ap.add_argument("--tools", type=Path, default=TOOLS)
    a = ap.parse_args(argv)
    if a.steps:
        print(" ".join(STEPS))
        return 0
    if a.work is None:
        ap.error("--work is required")
    python, src = a.tools / "venv" / "bin" / "python", a.tools / "src"
    if not python.is_file() or not (src / "python" / "examples" / "goose" / "scripted_run.py").is_file():
        print(f"goose_rehearsal: no Goose tools in {a.tools} (run steps/goose.setup.sh)", file=sys.stderr)
        return NO_TOOLS
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    run = work / "run"
    r = subprocess.run([str(python), "-B", str(HERE / "goose_session.py"), str(run), str(work / "population" / "rail")],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       env={**os.environ, "LOOP_GOOSE_SRC": str(src), "PYTHONDONTWRITEBYTECODE": "1"})
    (work / "scripted_run.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        print("goose_rehearsal: the scripted run failed; the end of its log:\n" + "\n".join(
            (r.stdout + r.stderr).splitlines()[-30:]), file=sys.stderr)
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
    # Step 01 checks the capsule the buyer sealed for a model call (loop_seal).
    capsule = run / "sealed-buyer.json"
    rev = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], stdin=subprocess.DEVNULL, capture_output=True,
                         text=True).stdout.strip()  # the Goose checkout the run used
    pop = work / "population"
    pop.mkdir(parents=True, exist_ok=True)
    step06_inputs(pop, work, nodes, json.loads((run / "deal.json").read_text()))
    doc = {"population": "rehearsal", "nodes": nodes, "rail": "mock",
           "pinned": ["goose-g1"], "goose": {"run": str(run), "log": str(work / "scripted_run.log"), "rev": rev, "steps": STEPS, "sealed": {"node": "buyer", "capsule": str(capsule)}}}
    (pop / "population.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
