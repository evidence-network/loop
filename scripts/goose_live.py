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
STEPS = ["01", "03", "07"]  # the steps this driver gives a Goose input (06 waits on the live rail, loop#349)
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
        asyncio.run(asks.run(run, relay, names=NAMES, homes=a.homes, published=a.published, relay_name=RELAY,
                             joined=joined))
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
    nodes = [{"name": n, "node_id": ids[n]["key_id"], "key_dir": str(Path(a.keys) / n)} for n in NAMES]
    doc = {"population": "live", "nodes": nodes, "pinned": ["goose-g1"],
           "goose": {"run": str(work), "log": str(work / "session.log"), "rev": rev, "capsule_emit": emit,
                     "steps": STEPS, "sealed": {"node": "goose-2", "capsule": str(sealed)},
                     "asks": {"run": str(run), "log": str(work / "session.log")}}}
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
    a = ap.parse_args(argv)
    if SRC is None:
        raise SystemExit("set LOOP_GOOSE_SRC to the Goose checkout (steps/goose.setup.sh installs it)")
    {"init": cmd_init, "advertise": cmd_advertise, "session": cmd_session}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
