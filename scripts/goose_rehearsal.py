#!/usr/bin/env python3
"""Run the Goose rehearsal's two nodes and write the population the loop's steps judge them under (loop#330).

Usage: goose_rehearsal.py --work DIR [--tools DIR]

Runs scripts/goose_session.py, the Goose node's scripted x-deal-v1 run (python/examples/goose at the goose-g1 pin: two
loop_server.py processes, a buyer and a seller, each a capsulectl-deal profile, spoken to over MCP stdio as Goose speaks
to them) with the mesh rehearsal's mock rail paying the deal, into DIR/run and DIR/population/rail, then writes
DIR/population/population.json in the mesh rehearsal's shape: each node's `key_dir` holds `node-key.pem` and
`node-key.pub.pem`, made from the node's own signing seed (<home>/deal/signing.seed), so a step checks a record under the
key the node signed it with. A `goose` member names what the run exported and which steps read it (`steps`):

  01 `sealed`       the capsule the buyer sealed for a model call (loop_seal)
  02 `book`         that record's Evidence Bundle in the buyer's book, under the checkpoint the test witness received
  03 `commitments`  each node's own signed commitment and policy (loop_advertise)
  04 `admit`        the buyer's GO (the deal's context bundle, which cites it), its NO, the offer, and the node's refusal
                    to open a deal with the refused peer
  05 `deal`         the deal's x-deal-v1 files (offer, agreements, payment legs, delivery, statements) and each side's
                    report bundle, judged with `capsulectl-deal verify --bundle` (`verify.capsulectl_deal`)
  06 population/step06  the two payment legs, read against the mock rail's settlement object
  07, 12 `asks`     the asks the two deal nodes made of each other through the mailboxes their commitments advertise
                    (scripts/goose_ask_session.py: each node polls its own mailbox and answers, never serves); 12 also
                    judges each node's join and the stranger's history_card/2 probe

loop.sh runs a step for Goose only when it is listed and writes NOT_CHECKED for the others. The keys are generated for
the run and are test keys; DIR stays outside the findings.

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
STEPS = ["01", "02", "03", "04", "05", "06", "07", "12"]
NO_TOOLS = 3  # exit status: the Goose tools are not installed, so nothing was run
# Run in the Goose venv (the loop's own steps are standard-library only): a node's signing seed (hex, the profile's
# deal/signing.seed) in; its private key as PKCS#8 PEM and its public half as SPKI PEM out, to the two paths given.
KEYS = ("import sys; from pathlib import Path; "
        "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as K; "
        "from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption; "
        "k = K.from_private_bytes(bytes.fromhex(Path(sys.argv[1]).read_text().strip())); "
        "Path(sys.argv[2]).write_bytes(k.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())); "
        "Path(sys.argv[3]).write_bytes(k.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))")
# The deal's files, in the order they are exchanged, and each side's report bundle (step 05).
DEAL_FILES = ("offer", "seller-agreement", "buyer-agreement", "buyer-leg", "seller-leg", "seller-delivery",
              "buyer-statement", "seller-statement", "buyer-report", "seller-report")


def node_env(tools):
    """What a Goose node process needs from the tools steps/goose.setup.sh installed: the capsulectl-deal plugin and the
    capsule-engine admission command, and no public witness."""
    return {"GOOSE_CAPSULECTL_DEAL": str(tools / "bin" / "capsulectl-deal"),
            "GOOSE_CAPSULE_ENGINE": str(tools / "bin" / "capsule-engine"), "GOOSE_NO_WITNESS": "1"}


def node_keys(python, run, work, name):
    """The node's key_dir (node-key.pem, node-key.pub.pem) made from its profile's signing seed, or None."""
    kd = work / "keys" / name
    kd.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(python), "-c", KEYS, str(run / name / "deal" / "signing.seed"), str(kd / "node-key.pem"),
                        str(kd / "node-key.pub.pem")], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"goose_rehearsal: cannot read {name}'s signing seed: {r.stderr.strip()}", file=sys.stderr)
        return None
    os.chmod(kd / "node-key.pem", 0o600)
    return kd


def step06_inputs(pop, work, deal):
    """Step 06's rehearsal inputs for the deal: the two payment legs the nodes sealed (the buyer's payer leg and the
    seller's payee leg, each carrying the payment reference it observed), each node's signed settlement disclosure
    (loop_settlement_disclosure) and the controllers (stand-ins, as in the mesh rehearsal). The step derives the
    exchange from the legs and the rail's object; the driver writes none."""
    import make_rail

    run = work / "run"
    inputs = pop / "step06"
    inputs.mkdir(parents=True, exist_ok=True)
    for name in ("buyer-leg.json", "seller-leg.json", "disclosure-buyer.json", "disclosure-seller.json"):
        src = name
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
    deal_bin = a.tools / "bin" / "capsulectl-deal"
    if not python.is_file() or not (src / "python" / "examples" / "goose" / "loop_server.py").is_file() \
            or not deal_bin.is_file() or not (a.tools / "bin" / "capsule-engine").is_file():
        print(f"goose_rehearsal: no Goose tools in {a.tools} (run steps/goose.setup.sh)", file=sys.stderr)
        return NO_TOOLS
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    run = work / "run"
    env = {**os.environ, "LOOP_GOOSE_SRC": str(src), "PYTHONDONTWRITEBYTECODE": "1", **node_env(a.tools)}
    r = subprocess.run([str(python), "-B", str(HERE / "goose_session.py"), str(run), str(work / "population" / "rail")],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
    (work / "scripted_run.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        print("goose_rehearsal: the scripted run failed; the end of its log:\n" + "\n".join(
            (r.stdout + r.stderr).splitlines()[-30:]), file=sys.stderr)
        return 1
    # Step 07: the deal's two nodes (the same homes and keys) ask each other through their advertised mailboxes.
    asks = work / "asks"
    r = subprocess.run([str(python), "-B", str(HERE / "goose_ask_session.py"), str(asks), str(run)], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, env=env)
    (work / "ask_session.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        print("goose_rehearsal: the ask session failed; the end of its log:\n" + "\n".join(
            (r.stdout + r.stderr).splitlines()[-30:]), file=sys.stderr)
        return 1
    nodes = []
    for name in ("buyer", "seller"):
        kd = node_keys(python, run, work, name)
        if kd is None:
            return 1
        nodes.append({"name": name, "key_dir": str(kd)})
    capsule = run / "sealed-buyer.json"
    rev = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], stdin=subprocess.DEVNULL, capture_output=True,
                         text=True).stdout.strip()  # the Goose checkout the run used
    if not rev:
        print("goose_rehearsal: cannot read the Goose checkout revision", file=sys.stderr)
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
    checkpoint_keys = {n: (run / f"{n}.checkpoint-key").read_text().strip() for n in ("buyer", "seller")}
    # Without the step 02 driver (steps/02-book.setup.sh) the run has no test witness, so no book bundle: step 02 then
    # reports NOT_CHECKED, naming the driver.
    bundle = run / "book-bundle-buyer.json"
    book = ({"node": "buyer", "bundle": str(bundle), "book_id": "goose/node", "capsule": str(capsule),
             "checkpoint": str(run / "book-checkpoint-buyer.cose"), "witnesses": str(run / "witnesses.json"),
             "checkpoint_key": checkpoint_keys["buyer"], "checkpoint_keys": checkpoint_keys}
            if bundle.is_file() else None)

    # Steps 04 and 05 read what the session exported; without it (it always does) their members are left out and they
    # read NOT_CHECKED.
    def present(*files):
        return all((run / f).is_file() for f in files)

    admit = ({"node": "buyer", "peer": "seller", "go": str(run / "admission-go-bundle.json"),
              "no": str(run / "admission-no-disclosed.json"), "offer": str(run / "offer.json"),
              "refused": str(run / "deal-refused.json")}
             if present("admission-go-bundle.json", "admission-no-disclosed.json", "offer.json", "deal-refused.json")
             else None)
    deal_inputs = ({n: str(run / f"{n}.json") for n in DEAL_FILES} if present(*(f"{n}.json" for n in DEAL_FILES))
                   else None)
    doc = {"population": "rehearsal", "nodes": nodes, "rail": "mock", "pinned": ["goose-g1", "capsule-engine"],
           "goose": {"run": str(run), "log": str(work / "scripted_run.log"), "rev": rev, "steps": STEPS,
                     "sealed": {"node": "buyer", "capsule": str(capsule)}, **({"book": book} if book else {}),
                     **({"admit": admit} if admit else {}), **({"deal": deal_inputs} if deal_inputs else {}),
                     "deal_id": deal["deal_id"],
                     "verify": {"capsulectl_deal": str(deal_bin), "checkpoint_keys": checkpoint_keys},
                     "commitments": commitments,
                     "asks": {"run": str(asks), "log": str(work / "ask_session.log")}}}
    (pop / "population.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
