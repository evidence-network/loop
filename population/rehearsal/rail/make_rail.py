#!/usr/bin/env python3
"""Make the mock rail's files, and step 06's rehearsal inputs, for a population.

Usage: make_rail.py [--population DIR]      (default: population/rehearsal)

Writes, under DIR (README.md beside this file has the formats):
  rail/rail.json, rail/ledger.jsonl, and rail/keys/rail-key.pem and
  rail-key.pub.pem (a TEST key, made with openssl only when absent);
  step06/exchange.json, step06/controllers.json, and step06/disclosure-<node>.json
  for each node, signed with the node's key from DIR/population.json.

The output is deterministic for given keys: rerunning it rewrites the same
bytes (Ed25519 signatures are deterministic). tests/test_step06_settle.py
checks the committed files are exactly what this writes.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import mock_rail  # noqa: E402

cc = mock_rail.cc
CURRENCY, DECIMALS = "USDC", 6
ISSUED = "2026-10-28T00:0{}:00Z"
# (exchange id, payer, payee, amount_atomic, fee_atomic). The last is the exchange step 06 asserts on;
# the third is a payment from alice to itself, which the filtered volume excludes.
LEDGER = (
    ("rehearsal-ex-0001", "alice", "bob", 50000, 100),
    ("rehearsal-ex-0002", "bob", "alice", 700, 10),
    ("rehearsal-ex-0003", "alice", "alice", 9000000, 100),
    ("rehearsal-ex-0004", "alice", "bob", 4200, 10),
)
CONTROLLERS = {"alice": "rehearsal-controller-a", "bob": "rehearsal-controller-b"}


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def node_keys(population_dir):
    """{name: (private PEM path, key_id)} for the population's nodes."""
    pop = json.loads((population_dir / "population.json").read_text(encoding="utf-8"))
    out = {}
    for n in pop["nodes"]:
        key_dir = Path(n["key_dir"])
        key_dir = key_dir if key_dir.is_absolute() else ROOT / key_dir
        out[n["name"]] = (key_dir / "node-key.pem", cc.key_id_of(cc.spki_from_pem((key_dir / "node-key.pub.pem").read_text())))
    return out


def rail_key(rail_dir):
    keys = rail_dir / "keys"
    if not (keys / "rail-key.pem").is_file():
        keys.mkdir(parents=True, exist_ok=True)
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(keys / "rail-key.pem")], check=True)
        subprocess.run(["openssl", "pkey", "-in", str(keys / "rail-key.pem"), "-pubout",
                        "-out", str(keys / "rail-key.pub.pem")], check=True)
    return keys / "rail-key.pem", cc.key_id_of(cc.spki_from_pem((keys / "rail-key.pub.pem").read_text()))


def make(population_dir):
    population_dir = Path(population_dir)
    rail_dir, inputs = population_dir / "rail", population_dir / "step06"
    nodes = node_keys(population_dir)
    pem, key_id = rail_key(rail_dir)
    dump(rail_dir / "rail.json", {"rail": "mock", "declaration": mock_rail.DECLARATION, "test_key": True,
                                  "key_id": key_id, "public_key": "keys/rail-key.pub.pem", "ledger": "ledger.jsonl",
                                  "currency": CURRENCY, "decimals": DECIMALS, "fee_paying": True})
    lines = []
    for seq, (ex, payer, payee, amount, fee) in enumerate(LEDGER, 1):
        lines.append(mock_rail.issue(pem, sequence=seq, exchange_id=ex, payer=nodes[payer][1], payee=nodes[payee][1],
                                     currency=CURRENCY, amount_atomic=amount, decimals=DECIMALS, fee_atomic=fee,
                                     final=True, issued_at=ISSUED.format(seq)))
    (rail_dir / "ledger.jsonl").write_bytes(b"".join(cc.canonical(o) + b"\n" for o in lines))
    last = lines[-1]
    dump(inputs / "exchange.json", {"exchange_id": last["exchange_id"], "payer": last["payer"], "payee": last["payee"],
                                    "currency": CURRENCY, "amount_atomic": last["amount_atomic"],
                                    "decimals": DECIMALS, "settlement_ref": {"rail": "mock",
                                                                             "settlement_id": last["settlement_id"]}})
    dump(inputs / "controllers.json", {"stand_in": True, "controllers": {
        nodes[n][1]: c for n, c in CONTROLLERS.items() if n in nodes}})
    for name, (node_pem, node_id) in nodes.items():
        settled = sum(1 for o in lines if node_id in (o["payer"], o["payee"]))
        dump(inputs / f"disclosure-{name}.json", cc.sign(
            {"type": "settlement-disclosure/v1", "node": name, "key_id": node_id, "rail": "mock",
             "through_sequence": len(lines), "settlements": settled}, node_pem))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--population", default=str(ROOT / "population" / "rehearsal"))
    make(ap.parse_args(argv).population)
    return 0


if __name__ == "__main__":
    sys.exit(main())
