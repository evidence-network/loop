#!/usr/bin/env python3
"""Run the Goose rehearsal's two nodes and write the population the loop's steps judge them under (loop#330).

Usage: goose_rehearsal.py --work DIR [--tools DIR]

Runs scripts/goose_session.py, G1's scripted run (python/examples/goose at the goose-g1 pin: two
loop_server.py processes, a buyer and a seller, spoken to over MCP stdio as Goose speaks to them) with the
mesh rehearsal's mock rail paying the deal, into DIR/run and DIR/population/rail, then writes DIR/population/population.json in the mesh rehearsal's shape: each node's `key_dir`
holds `node-key.pem` and `node-key.pub.pem`, taken from the node's own producer key, so a step checks
a record under the key the node signed it with. A `goose` member names what the run exported and which
steps read it (`steps`): step 01 the capsule the buyer sealed (`sealed`), step 02 the Evidence Bundle of that record in the buyer's book with
the checkpoint over it and the test witness's receipt (`book`, loop_book_bundle), step 05 the halves the nodes pushed at the end of the exchange and their final halves, and each receiver's record of the half it holds (`deal`; G1's verify_half judges the halves), step 04 the buyer's admissions (`admit`: the GO and the NO as Evidence Bundles, loop_book_bundle, and the deal's first half), step 03 each node's own signed commitment and
policy (`commitments`, loop_advertise), step 07 the asks the two nodes made of each other through the mailboxes their
commitments advertise (`asks`, scripts/goose_ask_session.py: each node polls its own mailbox and answers, never serves); loop.sh runs a step for Goose only when it is listed and writes NOT_CHECKED for
the others. The keys are generated for the run and are test keys; DIR stays outside the findings.

Exit 0 once population.json is written; exit 3 when the Goose tools are not installed (steps/goose.setup.sh); exit 1
when they are and the run did not complete (the tail of the run's log is printed). `--steps` prints the steps that
have a Goose input and exits.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "population" / "rehearsal" / "rail"))
TOOLS = Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache" / "evidence-network-loop")) / "goose"
# The steps that have a Goose input (steps/<NN>-*); each reads population.json's `goose` member.
STEPS = ["01", "02", "03", "04", "05", "06", "07"]
NO_TOOLS = 3  # exit status: the Goose tools are not installed, so nothing was run
# Run in the Goose venv (the loop's own steps are standard-library only): a node's producer key, PKCS#8 PEM, in;
# its public half, SPKI PEM, out.
PUBLIC = ("import sys; from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, "
          "load_pem_private_key as k; sys.stdout.write(k(open(sys.argv[1],'rb').read(), None).public_key()"
          ".public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode())")


def step06_inputs(pop, work, deal):
    """Step 06's rehearsal inputs for the deal: what G1 itself wrote (the nodes' final halves, the buyer's opening of its
    pay record's reference commitment, each node's signed settlement disclosure) and the controllers (stand-ins, as in
    the mesh rehearsal). The step derives the exchange from G1's records; the driver writes none."""
    import make_rail

    run = work / "run"
    inputs = pop / "step06"
    inputs.mkdir(parents=True, exist_ok=True)
    for src, name in (("buyer-final-half.json", "buyer-half.json"), ("seller-final-half.json", "seller-half.json"),
                      ("buyer-pay-opening.json", "buyer-pay-opening.json"),
                      ("buyer-settlement-disclosure.json", "disclosure-buyer.json"),
                      ("seller-settlement-disclosure.json", "disclosure-seller.json")):
        shutil.copyfile(run / src, inputs / name)
    make_rail.dump(inputs / "controllers.json", {"stand_in": True, "controllers": {
        deal["buyer"]: "goose-controller-buyer", deal["seller"]: "goose-controller-seller"}})


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
    # Step 07: the two nodes ask each other through their advertised mailboxes (a second pair of nodes, its own homes).
    asks = work / "asks"
    r = subprocess.run([str(python), "-B", str(HERE / "goose_ask_session.py"), str(asks)], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True,
                       env={**os.environ, "LOOP_GOOSE_SRC": str(src), "PYTHONDONTWRITEBYTECODE": "1"})
    (work / "ask_session.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        print("goose_rehearsal: the ask session failed; the end of its log:\n" + "\n".join(
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
    emit = subprocess.run([str(python), "-B", "-I", "-c", "import importlib.metadata as m; print(m.version('capsule-emit'))"],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout.strip()  # what sealed the capsule
    if not rev or not emit:
        print(f"goose_rehearsal: cannot read the Goose checkout revision ({rev!r}) or the capsule-emit version ({emit!r})", file=sys.stderr)
        return 1
    pop = work / "population"
    pop.mkdir(parents=True, exist_ok=True)
    deal = json.loads((run / "deal.json").read_text())
    step06_inputs(pop, work, deal)
    # Step 03 publishes each node's commitment as the node signed it (loop_advertise), never one the harness signs.
    commitments = {}
    for n, key_id in zip(nodes, (deal["buyer"], deal["seller"])):
        c, pol = run / "commitments" / f"{key_id}.json", run / "commitments" / "policies" / f"{key_id}.json"
        if not (c.is_file() and pol.is_file()):
            print(f"goose_rehearsal: the run wrote no commitment for {n['name']} ({c})", file=sys.stderr)
            return 1
        commitments[n["name"]] = {"commitment": str(c), "policy": str(pol)}
    # Without the step 02 driver (steps/02-book.setup.sh) the run has no test witness, so no book bundle: step 02 then
    # reports NOT_CHECKED, naming the driver.
    bundle = run / "book-bundle-buyer.json"
    book = ({"node": "buyer", "bundle": str(bundle), "book_id": "goose/node", "capsule": str(capsule),
             "witnesses": str(run / "witnesses.json"), "checkpoint_key": (run / "buyer.checkpoint-key").read_text().strip(),
             "checkpoint_keys": {n: (run / f"{n}.checkpoint-key").read_text().strip() for n in ("buyer", "seller")}}
            if bundle.is_file() else None)
    # Step 04 and step 05 read what the session exported; without it (it always does) their members are left out and they read NOT_CHECKED.
    def present(*files):
        return all((run / f).is_file() for f in files)

    admit = ({"node": "buyer", "peer": "seller", "go": str(run / "admission-go-disclosed.json"),
              "no": str(run / "admission-no-disclosed.json"), "half": str(run / "buyer-half.json"),
              "refused": str(run / "deal-refused.json")}
             if present("admission-go-disclosed.json", "admission-no-disclosed.json", "buyer-half.json", "deal-refused.json") else None)
    names = [f"{who}-{kind}" for who in ("buyer", "seller") for kind in ("exchange-half", "final-half", "held-half-bundle")]
    deal_inputs = ({n: str(run / f"{n}.json") for n in names} if present(*(f"{n}.json" for n in names)) else None)
    doc = {"population": "rehearsal", "nodes": nodes, "rail": "mock",
           "pinned": ["goose-g1"], "goose": {"run": str(run), "log": str(work / "scripted_run.log"), "rev": rev, "capsule_emit": emit, "steps": STEPS, "sealed": {"node": "buyer", "capsule": str(capsule)}, **({"book": book} if book else {}), **({"admit": admit} if admit else {}),
                                             **({"deal": deal_inputs} if deal_inputs else {}),
                                             "verify": {"python": str(python), "src": str(src),
                                                        "checkpoint_keys": {n: (run / f"{n}.checkpoint-key").read_text().strip()
                                                                            for n in ("buyer", "seller")}},
                                             "commitments": commitments,
                                             "asks": {"run": str(asks), "log": str(work / "ask_session.log")}}}
    (pop / "population.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
