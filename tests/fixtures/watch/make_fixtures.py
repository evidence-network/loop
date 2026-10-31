#!/usr/bin/env python3
"""Write tests/fixtures/watch/: the watch page's inputs, synthetic nodes only.

Run once: python3 -B tests/fixtures/watch/make_fixtures.py

The live days are tests/fixtures/stats/daily/ (made by make_fixtures.py
there): node 4 joins on 2026-10-30, node 3 leaves on 2026-11-01, node 2
refuses, then is absent. This script writes the rest:

  nodes/          a commitment-list entry for nodes 1, 2 and 4 (node 3 has left:
                  nodes/left/), operators are placeholder handles;
  population/     live and rehearsal name -> node id maps, as population/live is;
  rehearsal/      three TEST slots on 2026-11-05: nodes 1 and 2, node 2 absent
                  in the second slot, the third slot missed;
  findings/       through steps/finding.py's write(), in each step's own shape:
    2026-11-02-live/05-deal       the live shape: node-four holds a half from
                                  node-one, and one from a sender not listed;
    2026-11-05-rehearsal/05-deal  the rehearsal driver's shape: alice and bob,
                                  the loop's TEST population, not nodes of the
                                  view, so not drawn;
    2026-11-05-rehearsal/06-settle  node 1 pays node 2 (by key id), final;
    2026-11-05-rehearsal/07-ask   node-one asks node-two: history_card answered,
                                  exchange refused, exchange_late absent.
  Steps 06 and 07 have no live finding, and 06 and 07 none on 2026-11-02:
  "not measured yet";
  ondemand/       one on-demand run, 2026-11-01T140509Z, in the scrubbed format:
                  nodes 1, 2 and 4 (node 3 left that day), node 2 refusing;
  commitments/    the nodes' commitments as a join commits them: node-one an
                  evidence-commitment/0 with its one history_card (mesh-llm);
                  node-two an evidence-commitment/1 (fixture-only) with two
                  history_cards, two streams: mesh-llm with a witness receipt,
                  and a personal-agents one cleared and started again on
                  2026-10-20, no history yet; node-four one personal-agents card;
  bindings/       one controller binding nodes 1 and 4: bound/ (verified by
                  the tool pins.lock here pins), revoked/, unpinned/
                  (verified by a tool the lock does not pin);
  Node 1's key is the stats fixtures' reporter key: the reporter runs on node 1.
"""
import base64
import hashlib
import json
import shutil
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "steps"))
sys.path.insert(0, str(HERE.parent / "stats"))
import finding  # noqa: E402
from make_fixtures import KEY, at, day, node, q, size  # noqa: E402

CONSENT = json.loads((ROOT / "nodes" / "16a607c33006dd4243cb2347c4bdbaa5129a590b5257eea62ac5047abcf87864.json")
                     .read_text(encoding="utf-8"))["consent"]
NAMES = {1: "node-one", 2: "node-two", 3: "node-three", 4: "node-four"}


def nid(n):
    return str(n) * 64


def kid(n):
    return KEY if n == 1 else f"{n}a" * 32


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def nodes():
    for n in (1, 2, 4):
        dump(HERE / "nodes" / f"{nid(n)}.json", {
            "node_id": nid(n), "key_id": kid(n), "operator": f"operator-{NAMES[n].split('-')[1]}",
            "consent": CONSENT, "mesh": "lab", "ask_route": "mesh",
            # The fixture's nodes opt in to public pair edges, so the live view draws them (loop#77).
            "disclosure": {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"],
                           "pair_edges": "public"},
            "host_build": f"mesh-llm build {n}", "plugin_version": f"capsules v0.1.{n}"})
    dump(HERE / "nodes" / "left" / f"{nid(3)}.json", {"node_id": nid(3), "left": "2026-11-01"})


def population():
    for pop, ns in (("live", (1, 2, 3, 4)), ("rehearsal", (1, 2))):
        dump(HERE / "population" / pop / "population.json",
             {"population": pop, "nodes": [{"name": NAMES[n], "node_id": nid(n)} for n in ns], "rail": None})


def rehearsal():
    both = "discloses: checkpoints, history_card/1"
    d = "2026-11-05"
    ok = [q("verified", (at(d, 6), "verified"), digest="1a" * 32), q("verified", (at(d, 6), "verified"), digest="1b" * 32)]
    slots = [
        dict(day(f"{d}T0600Z", [node(1, both, ok), node(2, both, [q("verified", (at(d, 6), "verified"), digest="2a" * 32),
                                                                 q("refusal", (at(d, 6), "refusal"), digest="2b" * 32)])], 2)),
        dict(day(f"{d}T0610Z", [node(1, both, ok), node(2, both, [q("absence", (at(d, 6), "timeout")),
                                                                 q("absence", (at(d, 6), "unreachable"))], absent=1)], 2)),
        dict(day(f"{d}T0620Z", [], 2, missed="the reporter did not run")),
    ]
    for s in slots:
        dump(HERE / "rehearsal" / f"{s['date']}.json", s)


def write(step_dir, result, population, files, not_checked=()):
    for rel, obj in files.items():
        p = step_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with mock.patch.object(finding, "now", return_value="2026-11-05T06:30:00Z"):
        finding.write(step_dir, result=result, population=population, asserts="the fixture's step holds",
                      pinned=[], proof=sorted(files), detail="a fixture for the watch page", not_checked=list(not_checked),
                      started="2026-11-05T06:00:00Z")


NC = [{"what": "a part of the fixture", "why": "a fixture", "issue": "evidence-network/loop#1"}]


def findings():
    f = HERE / "findings"
    held = lambda n, sender: {"held": f"held-{n}", "capsule_id": f"{n}" * 64,  # noqa: E731
                              "provenance": {"ok": True, "detail": f"received from {sender} via mesh at 2026-11-02T06:00:00Z"}}
    write(f / "2026-11-02-live" / "05-deal", "NOT_CHECKED", "live",
          {"node-four/checks.json": {"node": "node-four", "held": [held(1, "node-one"), held(2, "someone-else")]}}, NC)
    dump(f / "2026-11-02-live" / "run.json", {"population": "live", "stage": "2", "started": "2026-11-02T06:00:00Z"})
    r = f / "2026-11-05-rehearsal"
    write(r / "05-deal", "PASS", "rehearsal", {
        "deal.json": {"deal_id": "rehearsal-deal-fixture"},
        "alice/push.json": {"pushed": True}, "bob/push.json": {"pushed": True},
        "alice/held/received-provenance.jsonl": json.dumps({"received_from": "bob-transport"}) + "\n",
        "bob/held/received-provenance.jsonl": json.dumps({"received_from": "alice-transport"}) + "\n"})
    write(r / "06-settle", "PASS", "rehearsal", {
        # An amount and currency, as the step's exchange carries them: the export must never show them.
        "exchange.json": {"exchange_id": "rehearsal-ex-fixture", "payer": kid(1), "payee": kid(2),
                          "amount_atomic": 4200, "currency": "USDC", "decimals": 6,
                          "settlement_ref": {"rail": "mock", "settlement_id": "c" * 64}},
        "checks.json": {"checks": {k: {"ok": True, "detail": "fixture"} for k in
                                   ("ledger", "reference", "adapter", "finality", "exchange_match")}
                       | {"band": {"ok": True, "detail": "PASS: band b2"}}}})
    asks = "node-one/asks/node-two"
    write(r / "07-ask", "NOT_CHECKED", "rehearsal", {
        f"{asks}/history_card/outcome.json": {"outcome": "artifact", "record_id": "d" * 64},
        f"{asks}/exchange/outcome.json": {"outcome": "refusal", "reason": "not_authorized", "record_id": "e" * 64},
        f"{asks}/exchange_late/outcome.json": {"outcome": "recorded_absence", "record_id": "f" * 64}}, NC)
    dump(r / "run.json", {"population": "rehearsal", "stage": "2", "started": "2026-11-05T06:00:00Z"})


def ondemand():
    d, run = "2026-11-01", "2026-11-01T140509Z"
    both = "discloses: checkpoints, history_card/1"
    t = f"{d}T14:05:09Z"
    dump(HERE / "ondemand" / f"{run}.json", dict(day(run, [
        node(1, both, [q("verified", (t, "verified"), size=size(57, t), digest="5a" * 32),
                       q("verified", (t, "verified"), size=size(57, t), digest="5b" * 32)]),
        node(2, both, [q("refusal", (t, "refusal"), digest="6a" * 32), q("refusal", (t, "refusal"), digest="6b" * 32)]),
        node(4, both, [q("verified", (t, "verified"), size=size(10, t), digest="7a" * 32),
                       q("verified", (t, "verified"), size=size(10, t), digest="7b" * 32)]),
    ], 3)))


def card(stream, receipts=(), history=True, started="2026-10-01"):
    """A history card's answer bytes, as the commitment staples them: only what the page reads is real."""
    hist = [{"checkpoint": "00", "consistency_from_previous": None} | ({"receipts": list(receipts)} if receipts else {})]
    art = json.dumps({"derivation": "history_card/1", "evidence_stream": stream, "history": hist if history else [],
                      "stream_started": started}, sort_keys=True)
    return json.dumps({"artifact": base64.urlsafe_b64encode(art.encode()).decode().rstrip("="),
                       "derivation": "history_card/1", "issued_at": f"{started}T06:00:00Z",
                       "profile": "evidence-interop/0"}, sort_keys=True).encode()


def staple(stream, community, b):
    return {"stream": stream, "community": community, "derivation": "history_card/1",
            "sha256": hashlib.sha256(b).hexdigest(), "bytes": base64.b64encode(b).decode()}


def commitments():
    root = HERE / "commitments" / "2026-10-28" / "join"
    one = card("node-one-book")
    dump(root / "node-one" / "commitment.json", {
        "profile": "evidence-commitment/0", "node_id": kid(1), "key_id": kid(1), "ask": {"endpoint": "book:node-one-book"},
        "history_card": {"derivation": "history_card/1", "sha256": hashlib.sha256(one).hexdigest(),
                         "bytes": base64.b64encode(one).decode()}})
    # node-two: two streams, one witnessed; its personal-agents stream was cleared and started again, no history yet.
    dump(root / "node-two" / "commitment.json", {
        "profile": "evidence-commitment/1", "node_id": kid(2), "key_id": kid(2), "ask": {"endpoint": "book:node-two-book-a"},
        "history_cards": [staple("node-two-book-a", "mesh-llm", card("node-two-book-a", [{"witness": "witness-one", "sig": "00"}])),
                          staple("node-two-book-b", "muse", card("node-two-book-b", history=False, started="2026-10-20"))]})
    dump(root / "node-four" / "commitment.json", {
        "profile": "evidence-commitment/1", "node_id": kid(4), "key_id": kid(4), "ask": {"endpoint": "book:node-four-agent"},
        "history_cards": [staple("node-four-agent", "muse", card("node-four-agent"))]})


CONTROLLER = "c" * 64
PIN = "0123456789abcdef0123456789abcdef01234567"


def bindings():
    """One controller binds nodes 1 (mesh-llm) and 4 (personal agents): verified with a pinned tool, revoked,
    and verified by a tool the lock does not pin."""
    for name, status, verifier in (("bound", "verified", f"binding-verifier@{PIN}"), ("revoked", "revoked", None),
                                   ("unpinned", "verified", "binding-verifier@" + "f" * 40)):
        dump(HERE / "bindings" / name / f"{CONTROLLER}.json", {
            "profile": "binding-verdict/0", "controller_key_id": CONTROLLER, "subjects": [kid(1), kid(4)],
            "status": status, "verifier": verifier, "checked_at": "2026-10-30T06:00:00Z",
            "cites": "evidence-network/loop#1"})
    (HERE / "pins.lock").write_text(f"# a fixture lock: the binding verifier's pin\nbinding-verifier evidence-network/loop {PIN}\n",
                                        encoding="utf-8")


def main():
    for d in ("nodes", "population", "rehearsal", "findings", "ondemand", "commitments", "bindings"):
        shutil.rmtree(HERE / d, ignore_errors=True)
    nodes()
    population()
    rehearsal()
    findings()
    ondemand()
    commitments()
    bindings()


if __name__ == "__main__":
    main()
