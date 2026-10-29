#!/usr/bin/env python3
"""Make step 04 Admit's rehearsal inputs: signed stand-ins for what other steps own.

Usage: make_standins.py [POPULATION_DIR]   (default: population/rehearsal)

Writes POPULATION_DIR/step04/ (see README.md there). The seller is the
population's `alice` and the buyer `bob`; carol, dave, erin, the referee and
the controllers get TEST keys under step04/keys/, made once when missing and
kept after that, so a rerun reproduces the same bytes (Ed25519 signatures are
deterministic). Every key here is a TEST key, published on purpose and never
used by a live node. Needs openssl 3.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "steps" / "04-admit.d"))
import standin  # noqa: E402

OFFERED_AT = "2026-10-28T12:00:00Z"
FRESH = "2026-10-28T06:00:00Z"
STALE = "2026-10-25T00:00:00Z"
PARTIES = ("carol", "dave", "erin", "referee", "controller-alice", "controller-dave")


def key(d, name):
    """The TEST private key PEM of a party, made once."""
    p = d / "keys" / name / "key.pem"
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(p)], check=True)
        subprocess.run(["openssl", "pkey", "-in", str(p), "-pubout", "-out", str(p.parent / "key.pub.pem")], check=True)
    return p


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def main(argv):
    pop_dir = Path(argv[1]) if len(argv) > 1 else ROOT / "population" / "rehearsal"
    pop = json.loads((pop_dir / "population.json").read_text())
    node_key = {}
    for n in pop["nodes"]:
        kd = Path(n["key_dir"])
        node_key[n["name"]] = (kd if kd.is_absolute() else ROOT / kd) / "node-key.pem"
    out = pop_dir / "step04"
    keys = {**{n: node_key[n] for n in ("alice", "bob")}, **{p: key(out, p) for p in PARTIES}}
    pub = {n: standin.pub_hex(k) for n, k in keys.items()}
    seller, buyer = pub["alice"], pub["bob"]

    write(out / "controllers.json", [
        standin.sign({"kind": "controller_declaration", "controller": pub["controller-alice"],
                      "keys": sorted([pub["alice"], pub["carol"]]), "issued_at": "2026-10-20T00:00:00Z"},
                     keys["controller-alice"]),
        standin.sign({"kind": "controller_declaration", "controller": pub["controller-dave"],
                      "keys": [pub["dave"]], "issued_at": "2026-10-20T00:00:00Z"}, keys["controller-dave"]),
    ])
    write(out / "referees.json", {"referees": [{"id": "rehearsal-referee", "key": pub["referee"]}]})

    def past(n, who):
        """A past deal of the seller's with `who`, carrying who's signed half."""
        deal_id = f"rehearsal-past-{n}-{who}"
        half = standin.sign({"kind": "deal_half", "deal_id": deal_id, "seller_key": seller,
                             "counterparty_key": pub[who], "exchange_digest": digest(deal_id)}, keys[who])
        return {"deal_id": deal_id, "counterparty_key": pub[who], "counterparty_half": half}

    def card(checkpoint_at, deals):
        return standin.sign({"derivation": "history_card/1", "subject_key": seller, "checkpoint_at": checkpoint_at,
                             "deals": deals}, keys["alice"])

    def adjudication(deal_id, buyer_name, outcome, signer="referee"):
        return standin.sign({"kind": "adjudication_issued", "referee": pub[signer], "issued_at": "2026-10-27T00:00:00Z",
                             "result": {"deal_id": deal_id, "exchange_digest": digest(deal_id), "seller_key": seller,
                                        "buyer_key": pub[buyer_name], "outcome": outcome},
                             "cites": [digest(deal_id)]}, keys[signer])

    honest = [past(1, "dave"), past(2, "erin")]
    scenarios = {
        # name: (band decade, card or None, adjudications)
        "t0-no-card": (-1, None, []),
        "go": (0, card(FRESH, honest), [adjudication(honest[0]["deal_id"], "dave", "for_seller"),
                                        adjudication(honest[1]["deal_id"], "erin", "against_seller", signer="carol")]),
        "ask": (0, card(STALE, honest), []),
        "no-half": (0, card(FRESH, []), []),
        "self-dealt": (0, card(FRESH, [past(3, "carol"), past(4, "carol")]), []),
        "adverse-with-buyer": (0, card(FRESH, honest), [adjudication("rehearsal-past-5-bob", "bob", "against_seller")]),
        "adverse-other": (0, card(FRESH, honest), [adjudication(honest[1]["deal_id"], "erin", "against_seller")]),
    }
    for name, (decade, c, adjs) in scenarios.items():
        d = out / "scenarios" / name
        write(d / "deal.json", {"deal_id": f"rehearsal-admit-{name}", "seller_key": seller, "buyer_key": buyer,
                                "band": {"currency": "USDC", "decade": decade}, "offered_at": OFFERED_AT})
        if c is None:
            (d / "history_card.json").unlink(missing_ok=True)
        else:
            write(d / "history_card.json", c)
        write(d / "adjudications.json", adjs)
    print(f"make_standins: wrote {out} ({len(scenarios)} scenarios)")


if __name__ == "__main__":
    main(sys.argv)
