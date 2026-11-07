#!/usr/bin/env python3
"""The Goose lab driver's Python half: G1's Goose nodes on the loop lab host, with keys that persist (loop#349).

Usage (in the Goose venv, which has `mcp`, `websockets` and `cryptography`; lab/goose.sh runs it):

  goose_live.py init       --homes DIR --keys DIR --ids FILE   join each node not in FILE (a new key on first use), write its public key
  goose_live.py advertise  --homes DIR --out DIR      each node's own signed commitment and policy (loop_advertise)
  goose_live.py session    --homes DIR --keys DIR --ids FILE --published DIR --work DIR
                           seal a model call, ask through the published commitments, write the population

The two nodes are G1's loop_server.py processes (evidencebook python/examples/goose at the goose-g1 pin), spoken to over
MCP stdio as Goose speaks to them, each with its own GOOSE_HOME under --homes. Unlike the rehearsal's, those homes
persist: a node's producer key is its identity, so it is the key nodes/<node_id>.json lists and the key the node signs
its commitment with. The private keys never leave the homes (mode 0600, written by G1); this driver reads none and
prints only public ids.

`session` is one run: goose-2 seals a model call (step 01), then the two nodes ask each other through the mailbox the
commitments in --published name (step 07, scripts/goose_ask_session.py), each answering by polling its own mailbox. The
relay is the lab's, in this process (the rehearsal's carrier): the commitments name RELAY, which GOOSE_RELAY_MAP points
at it, so only a node on this host can reach them. The population it writes (DIR/population/population.json,
"population": "live") names the nodes by their ids and the files each step reads.
"""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from goose_ask_session import Goose  # noqa: E402
from goose_rehearsal import PUBLIC  # noqa: E402
NAMES = ("goose-1", "goose-2")  # goose-1 sells, goose-2 buys
# The relay the commitments name: step 03 requires a wss:// URL; no node outside the lab host can reach this one.
RELAY = "wss://relay.loop-lab.example"
ANSWERS_WITHIN = "PT1M"  # the shortest window an absence may claim; the nodes answer only while a run polls them
POLL_INTERVAL = 30
STEPS = ["01", "03", "07"]  # the steps a session gives a Goose input; `session --pay` adds 06 (one real payment, loop#349)
PRICE_MSAT, CURRENCY, DECIMALS = 1000, "BTC", 11  # the paid deal's price in msat (BTC at assetScale 11, as the lab's band)
SRC = Path(os.environ["LOOP_GOOSE_SRC"]) / "python" / "examples" / "goose" if "LOOP_GOOSE_SRC" in os.environ else None


def servers(homes, relay_map=None):
    """(name -> StdioServerParameters): one loop_server.py per node, on its own persistent home."""
    from mcp import StdioServerParameters
    out = {}
    for name in NAMES:
        env = {**os.environ, "GOOSE_HOME": str(Path(homes) / name), "GOOSE_OPERATOR": name}
        if relay_map:
            env["GOOSE_RELAY_MAP"] = relay_map
        out[name] = StdioServerParameters(command=sys.executable, args=[str(SRC / "loop_server.py")], env=env)
    return out


async def with_nodes(homes, work, relay_map=None):
    """Open both nodes and run `work({name: Goose})`."""
    from contextlib import AsyncExitStack
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    async with AsyncExitStack() as stack:
        nodes = {}
        for name, params in servers(homes, relay_map).items():
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            nodes[name] = Goose(session, name)
        return await work(nodes)


def checkpoint_key_id(published, node_id):
    """A node's checkpoint key id, read from the commitment it published (history_cards[0].checkpoint_key, base64url of
    the Ed25519 SPKI): no join, which would seal one more record in a lab node."""
    import base64
    doc = json.loads((Path(published) / f"{node_id}.json").read_text())
    raw = doc["history_cards"][0]["checkpoint_key"]
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))[-32:].hex()


async def pay_deal(nodes, ids, step06, pay_one, settled):
    """One deal goose-2 buys from goose-1 over a real Lightning payment: G1's calls as goose_session.py makes them, except
    the rail. `pay_one()` (goose_lightning.pay on the lab wallets) pays once both sides hold the agreed terms and returns
    the record step 06 reads; the buyer's loop_settle commits its payment_hash and loop_pay_opening opens it. Writes
    DIR (step06): the halves, the opening, each node's signed settlement disclosure and lightning.json."""
    buyer, seller = nodes["goose-2"], nodes["goose-1"]
    b, s = ids["goose-2"], ids["goose-1"]  # each: key_id, checkpoint_key_id
    step06.mkdir(parents=True, exist_ok=True)
    await buyer.call("loop_admit", peer_key_id=s["key_id"], checkpoint_key_id=s["checkpoint_key_id"],
                     decision="GO", reason="known inference seller")
    await seller.call("loop_admit", peer_key_id=b["key_id"], checkpoint_key_id=b["checkpoint_key_id"],
                      decision="GO", reason="paying customer")
    opened = await buyer.call("loop_deal_open", peer_key_id=s["key_id"], item="inference: 1k tokens",
                              price_minor=PRICE_MSAT, currency=CURRENCY, rail="lightning",
                              asked="buy 1k tokens of inference from the seller")
    deal_id, tmp = opened["deal_id"], step06 / "tmp"
    tmp.mkdir(exist_ok=True)
    await buyer.call("loop_deal_push", deal_id=deal_id, out_path=str(tmp / "buyer-half.json"))
    await seller.call("loop_deal_respond", request_file=str(tmp / "buyer-half.json"),
                      asked="sell 1k tokens of inference at the listed price")
    await seller.call("loop_deal_push", deal_id=deal_id, out_path=str(tmp / "seller-half.json"))
    await buyer.call("loop_deal_reconcile", peer_file=str(tmp / "seller-half.json"))
    record = await asyncio.get_running_loop().run_in_executor(None, pay_one)  # the one real payment
    await buyer.call("loop_settle", deal_id=deal_id, payment_ref=record["payment_hash"])
    for me in (buyer, seller):
        await me.call("loop_deal_end_exchange", deal_id=deal_id, out_path=str(tmp / f"{me.name}-exchange-half.json"))
    for me, peer in ((buyer, seller), (seller, buyer)):
        await peer.call("loop_deal_reconcile", peer_file=str(tmp / f"{me.name}-exchange-half.json"))
    await buyer.call("loop_deal_close", deal_id=deal_id, status="received")
    await seller.call("loop_deal_close", deal_id=deal_id, status="received")
    await buyer.call("loop_pay_opening", deal_id=deal_id, out_path=str(step06 / "buyer-pay-opening.json"))
    record["payments_recorded"] = settled(record["payment_hash"], deal_id)  # both books hold the deal now
    for node in (buyer, seller):  # the rail has no ledger sequence: 0
        await node.call("loop_settlement_disclosure", rail="lightning", through_sequence=0,
                        out_path=str(step06 / f"disclosure-{node.name}.json"))
    for me, peer in ((buyer, seller), (seller, buyer)):
        path = str(tmp / f"{me.name}-final-half.json")
        await me.call("loop_deal_push", deal_id=deal_id, out_path=path)
        await peer.call("loop_deal_reconcile", peer_file=path)
    shutil.copyfile(tmp / f"{buyer.name}-final-half.json", step06 / "buyer-half.json")
    shutil.copyfile(tmp / f"{seller.name}-final-half.json", step06 / "seller-half.json")
    shutil.rmtree(tmp)
    (step06 / "lightning.json").write_text(json.dumps({**record, "deal_id": deal_id}, indent=2, sort_keys=True) + "\n")
    return deal_id


def public_key(producer_key):
    pub = subprocess.run([sys.executable, "-c", PUBLIC, str(producer_key)], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True)
    if pub.returncode != 0:
        raise SystemExit(f"cannot read a node's producer key: {pub.stderr.strip()}")
    return pub.stdout


def cmd_init(a):
    """Join each node that has no identity in --ids yet (a node joined once keeps its first join record), write every
    node's public key, and print the ids: {name: {node_id, key_id, record_id}}."""
    ids = json.loads(Path(a.ids).read_text()) if Path(a.ids).is_file() else {}

    async def work(nodes):
        for name, g in nodes.items():
            if "record_id" not in ids.get(name, {}):
                j = await g.call("loop_join")
                ids[name] = {"node_id": j["key_id"], "key_id": j["key_id"], "record_id": j["record_id"]}  # G1's node id is its signing key's id
    asyncio.run(with_nodes(a.homes, work))
    for name in NAMES:
        kd = Path(a.keys) / name
        kd.mkdir(parents=True, exist_ok=True)
        (kd / "node-key.pub.pem").write_text(public_key(Path(a.homes) / name / "producer.key"))
    print(json.dumps(ids, indent=2, sort_keys=True))


def cmd_advertise(a):
    async def work(nodes):
        for g in nodes.values():
            await g.call("loop_advertise", out_dir=str(a.out), relays=RELAY, answers_within=ANSWERS_WITHIN,
                         poll_interval=POLL_INTERVAL)
    asyncio.run(with_nodes(a.homes, work))


def cmd_session(a):
    import goose_ask_session as asks
    work = Path(a.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    sealed = work / "sealed-goose-2.json"

    async def seal(nodes):
        # 01: the buyer seals a model call, an effect-boundary record step 01 reads. Provider and model are what the
        # step tells the producer (steps/01-seal MODEL); model_attestation must carry them back.
        await nodes["goose-2"].call("loop_seal", provider="rehearsal", model_id="rehearsal-model",
                                    prompt="rehearsal inference", completion="rehearsal completion",
                                    out_path=str(sealed))
    asyncio.run(with_nodes(a.homes, seal))
    run = work / "asks"
    relay = asks.Relay()
    try:
        joined = {n: {"key_id": d["key_id"], "record_id": d["record_id"]} for n, d in
                  json.loads(Path(a.ids).read_text()).items()}
        asyncio.run(asks.run(run, relay, a.homes, a.published, joined, names=NAMES, relay_name=RELAY))
    finally:
        relay.close()
    src = Path(os.environ["LOOP_GOOSE_SRC"])
    rev = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], stdin=subprocess.DEVNULL, capture_output=True,
                         text=True).stdout.strip()
    emit = subprocess.run([sys.executable, "-B", "-I", "-c", "import importlib.metadata as m; print(m.version('capsule-emit'))"],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout.strip()
    if not rev or not emit:
        raise SystemExit(f"cannot read the Goose checkout revision ({rev!r}) or the capsule-emit version ({emit!r})")
    ids = json.loads((run / "nodes.json").read_text())
    pop = work / "population"
    pop.mkdir(exist_ok=True)
    steps, lightning = list(STEPS), None
    if a.pay:  # 06: one real Lightning payment, lab wallet to lab wallet, inside the guard (scripts/goose_lightning.py)
        import goose_lightning as gl
        payer, payee = gl.Wallet(a.payer_port, a.payer_name), gl.Wallet(a.payee_port, a.payee_name)
        log = Path(a.lightning_log)
        try:
            gl.preflight(payer, payee, gl.AMOUNT_MSAT, log)  # refuse before the nodes start the deal
        except gl.Refused as e:
            # The guard refused and nothing was sent: the run goes on without 06, which loop.sh writes NOT_CHECKED
            # (loop#363: a refused day still publishes steps 01, 03 and 07). The reason is one line for the caller.
            print(f"goose_live: payment refused: {e}", file=sys.stderr)
            a.pay = False
        if a.pay:
            node_ids = {n: {**d, "checkpoint_key_id": checkpoint_key_id(a.published, d["key_id"])}
                        for n, d in json.loads(Path(a.ids).read_text()).items()}

            async def deal(nodes):
                return await pay_deal(nodes, node_ids, pop / "step06", lambda: gl.pay(payer, payee, log),
                                      lambda h, d: gl.mark_settled(log, h, d, f"{payer.name}>{payee.name}"))
            asyncio.run(with_nodes(a.homes, deal))
            steps.append("06")
            lightning = {"payer": payer.name, "payee": payee.name, "file": "step06/lightning.json"}
    nodes = [{"name": n, "node_id": ids[n]["key_id"], "key_dir": str(Path(a.keys) / n)} for n in NAMES]
    doc = {"population": "live", "nodes": nodes, "pinned": ["goose-g1"],
           "goose": {"run": str(work), "log": str(work / "session.log"), "rev": rev, "capsule_emit": emit,
                     "steps": steps, "sealed": {"node": "goose-2", "capsule": str(sealed)},
                     "asks": {"run": str(run), "log": str(work / "session.log")}}}
    if lightning:
        doc["goose"]["lightning"] = lightning
    (pop / "population.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "advertise", "session"):
        p = sub.add_parser(name)
        p.add_argument("--homes", type=Path, required=True)
        if name == "init":
            p.add_argument("--keys", type=Path, required=True)
            p.add_argument("--ids", type=Path, required=True)
        elif name == "advertise":
            p.add_argument("--out", type=Path, required=True)
        else:
            p.add_argument("--published", type=Path, required=True)
            p.add_argument("--keys", type=Path, required=True)
            p.add_argument("--ids", type=Path, required=True)
            p.add_argument("--work", type=Path, required=True)
            p.add_argument("--pay", action="store_true", help="also make the one real Lightning payment (step 06)")
            p.add_argument("--payer-port", type=int, default=9811, help="the payer lab node's console port (lab-1)")
            p.add_argument("--payee-port", type=int, default=9812, help="the payee lab node's console port (lab-2)")
            p.add_argument("--payer-name", default="lab-1")
            p.add_argument("--payee-name", default="lab-2")
            p.add_argument("--lightning-log", type=Path, help="the payment log (the guard's count); needed with --pay")
    a = ap.parse_args(argv)
    if a.cmd == "session" and a.pay and not a.lightning_log:
        ap.error("--pay needs --lightning-log")
    if SRC is None:
        raise SystemExit("set LOOP_GOOSE_SRC to the Goose checkout (steps/goose.setup.sh installs it)")
    {"init": cmd_init, "advertise": cmd_advertise, "session": cmd_session}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
