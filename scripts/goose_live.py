#!/usr/bin/env python3
"""The Goose lab driver's Python half: the Goose nodes on the loop lab host, with keys that persist (loop#349).

Usage (in the Goose venv, which has `mcp`, `websockets` and `cryptography`; lab/goose.sh runs it):

  goose_live.py init       --homes DIR --keys DIR --ids FILE   join each node (once per key: a joined node gets its own
                           join record back), write its public key
  goose_live.py advertise  --homes DIR --out DIR      each node's own signed commitment and policy (loop_advertise)
  goose_live.py session    --homes DIR --keys DIR --ids FILE --published DIR --work DIR
                           seal a model call, ask through the published commitments, write the population

The two nodes are evidencebook's python/examples/goose loop_server.py processes at the goose-g1 pin, each a
capsulectl-deal profile (x-deal-v1), spoken to over MCP stdio as Goose speaks to them, each with its own GOOSE_HOME under
--homes. Unlike the rehearsal's, those homes persist: a node's signing key is its identity, so it is the key
nodes/<node_id>.json lists and the key the node signs its commitment with. The private keys never leave the homes (mode
0600, written by capsulectl-deal); this driver reads none and prints only public ids.

Cutover from the old Python node (evidencebook#624): a home with no capsulectl-deal profile (no <home>/deal/signing.seed)
gets one on first use, created from the old node's own key files, GOOSE_SIGNING_KEY_FILE=<home>/producer.key and
GOOSE_CHECKPOINT_KEY_FILE=<home>/checkpoint.key, so both key ids stay and nodes/ needs no new entry. The old shelf.db stays
in the home as a read-only archive; the new profile's History starts at the cutover, so `init` joins the node again and
rewrites its ids.json entry with the new join record. Every profile is made with GOOSE_NO_WITNESS=1: no public witness.

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
from goose_rehearsal import node_env  # noqa: E402
NAMES = ("goose-1", "goose-2")  # goose-1 sells, goose-2 buys
# The relay the commitments name: step 03 requires a wss:// URL; no node outside the lab host can reach this one.
RELAY = "wss://relay.loop-lab.example"
# The node's asker refuses a window under 90 seconds (scripts/goose_ask_session.py ANSWERS_WITHIN); the nodes answer only
# while a run polls them.
ANSWERS_WITHIN = "PT2M"
POLL_INTERVAL = 30
STEPS = ["01", "03", "07"]  # the steps a session gives a Goose input; `session --pay` adds 06 (one real payment, loop#349)
# The paid deal's price: USD cents. x-deal-v1 takes a 3-letter currency only (so not msat), and the node's pinned
# admission pack (capsule-engine's ladder test pack) gives a deal in BTC no GO; 50 cents is a GO under it. The rail is
# lightning: the payment the lab wallets make is what the legs reference, by its payment hash.
PRICE_MINOR, CURRENCY = 50, "USD"
SRC = Path(os.environ["LOOP_GOOSE_SRC"]) / "python" / "examples" / "goose" if "LOOP_GOOSE_SRC" in os.environ else None
# The tools steps/goose.setup.sh installed: LOOP_GOOSE_SRC is <tools>/goose/src.
TOOLS = Path(os.environ["LOOP_GOOSE_SRC"]).parent if "LOOP_GOOSE_SRC" in os.environ else None
# A node's public key, SPKI PEM, from its profile's signing seed (hex); run in the Goose venv.
PUBLIC = ("import sys; from pathlib import Path; "
          "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as K; "
          "from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat; "
          "k = K.from_private_bytes(bytes.fromhex(Path(sys.argv[1]).read_text().strip())); "
          "sys.stdout.write(k.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode())")


def has_profile(home):
    """Whether the node's home holds its capsulectl-deal profile (capsulectl deal init made it)."""
    return (Path(home) / "deal" / "signing.seed").is_file()


def carried_keys(home):
    """The old node's key files a new profile is made from, so its key ids stay: {env: path} for each that exists, and
    only while the home has no profile yet (the node reads them on its first `deal init` only)."""
    if has_profile(home):
        return {}
    return {env: str(Path(home) / f) for env, f in (("GOOSE_SIGNING_KEY_FILE", "producer.key"),
                                                    ("GOOSE_CHECKPOINT_KEY_FILE", "checkpoint.key"))
            if (Path(home) / f).is_file()}


def profile_ctl(homes, name):
    """The node's own capsulectl-deal profile, as the node runs it (python/examples/goose/ctl.py: profile `name`, the
    operator servers() gives the node, under its GOOSE_HOME), with the plugin steps/goose.setup.sh built."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    from ctl import Ctl
    return Ctl(Path(homes) / name, name, node_env(TOOLS)["GOOSE_CAPSULECTL_DEAL"])


def servers(homes, relay_map=None):
    """(name -> StdioServerParameters): one loop_server.py per node, on its own persistent home."""
    from mcp import StdioServerParameters
    out = {}
    for name in NAMES:
        home = Path(homes) / name
        env = {**os.environ, "GOOSE_HOME": str(home), "GOOSE_OPERATOR": name, **node_env(TOOLS), **carried_keys(home)}
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
    the Ed25519 SPKI): no join, which would seal one more record in a lab node. scripts/live_cross_session.py reads it."""
    import base64
    doc = json.loads((Path(published) / f"{node_id}.json").read_text())
    raw = doc["history_cards"][0]["checkpoint_key"]
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))[-32:].hex()


async def pay_deal(nodes, ids, step06, pay_one, settled, ctl):
    """One x-deal-v1 deal goose-2 buys from goose-1 over a real Lightning payment: the node's own scripted sequence (as
    goose_session.py makes it), except the rail. `pay_one(before_send)` (goose_lightning.pay on the lab wallets) pays
    once both sides hold each other's agreement and the buyer's check has passed and returns the record step 06 reads; the buyer's payer leg and the seller's payee
    leg carry its payment_hash as their payment reference. The final reviews pause, and are answered "proceed": the lab
    operator who runs `lab/goose.sh pay` asked for this payment. `ctl(name, *args)` runs one capsulectl-deal verb on that
    node's own profile: the node's loop_settle seals `--sent` and the settled leg in one call, so the driver runs `--sent`
    itself before the wallet sends (below). Writes DIR (step06): buyer-leg.json, seller-leg.json and lightning.json."""
    buyer, seller = nodes["goose-2"], nodes["goose-1"]
    b, s = ids["goose-2"], ids["goose-1"]
    step06.mkdir(parents=True, exist_ok=True)
    tmp = step06 / "tmp"
    tmp.mkdir(exist_ok=True)
    await buyer.call("loop_admit", peer_key_id=s["key_id"], decision="GO", reason="known inference seller")
    await seller.call("loop_admit", peer_key_id=b["key_id"], decision="GO", reason="paying customer")
    opened = await buyer.call("loop_deal_open", peer_key_id=s["key_id"], item="inference: 1k tokens",
                              price_minor=PRICE_MINOR, currency=CURRENCY, rail="lightning",
                              asked="buy 1k tokens of inference from the seller", out_path=str(tmp / "offer.json"))
    deal_id = opened["deal_id"]
    await seller.call("loop_deal_respond", request_file=str(tmp / "offer.json"),
                      asked="sell 1k tokens of inference at the listed price", out_path=str(tmp / "seller-agreement.json"))
    await buyer.call("loop_deal_reconcile", peer_file=str(tmp / "seller-agreement.json"))
    await buyer.call("loop_deal_agree", deal_id=deal_id, out_path=str(tmp / "buyer-agreement.json"))
    await seller.call("loop_deal_reconcile", peer_file=str(tmp / "buyer-agreement.json"))
    leg, loop, approved = str(step06 / "buyer-leg.json"), asyncio.get_running_loop(), {}

    async def review(ref):
        # Everything that can refuse this payment runs before the money moves, as capsulectl-deal's settle contract
        # says (internal/deal/deal_v1_settle.go: "Before you hand it to the rail, run deal settle --sent --check"):
        # the buyer's check (a deny stops here, nothing sent; a pause is answered "proceed", the operator asked for
        # the payment), then `--sent`, which re-checks the gate, the approval and the limits and counts the payment in
        # flight. The node's loop_settle afterwards finds it in flight and seals the settled leg (goose_node.py pay).
        paused = await buyer.call("loop_settle", deal_id=deal_id, payment_ref=ref, out_path=leg)
        if not paused.get("paused"):
            # The check proceeded without a pause: the node sealed --sent and the settled leg in that call.
            return
        if paused.get("verdict") == "deny":
            raise RuntimeError(f"the buyer's check denied the payment (check {paused['check_id']}); nothing was sent")
        await buyer.call("loop_deal_approve", deal_id=deal_id, check_id=paused["check_id"], choice="proceed", said="yes")
        await loop.run_in_executor(None, lambda: ctl(buyer.name, "settle", "--deal", deal_id, "--sent", "--check",
                                                     paused["check_id"], "--payment-ref-type", "ln.payment_hash"))
        approved["check_id"] = paused["check_id"]

    def before_send(ref):  # runs in pay_one's thread; the nodes live on this event loop
        asyncio.run_coroutine_threadsafe(review(ref), loop).result()
    from goose_lightning import Refused as WalletRefused
    try:
        record = await loop.run_in_executor(None, pay_one, before_send)  # the one real payment
    except WalletRefused:
        # After --sent, a refusal from the wallet side means nothing settled (the wallet's own row says failed, or the
        # day's cap stopped it before send): the payment is recorded failed, which ends its check. An outcome that is
        # not known (goose_lightning.Unknown) leaves it in flight, as the contract says.
        if approved:
            ctl(buyer.name, "settle", "--deal", deal_id, "--check", approved["check_id"], "--status", "failed",
                "--out", str(tmp / "buyer-failed-leg.json"))
        raise
    ref = record["payment_hash"]
    if approved:
        await buyer.call("loop_settle", deal_id=deal_id, payment_ref=ref, out_path=leg, check_id=approved["check_id"])
    await seller.call("loop_deal_reconcile", peer_file=leg)
    await seller.call("loop_deal_received", deal_id=deal_id, amount_minor=PRICE_MINOR, payment_ref=ref,
                      out_path=str(step06 / "seller-leg.json"))
    await buyer.call("loop_deal_reconcile", peer_file=str(step06 / "seller-leg.json"))
    delivery = str(tmp / "seller-delivery.json")
    paused = await seller.call("loop_deal_deliver", deal_id=deal_id, out_path=delivery)
    if paused.get("paused"):
        await seller.call("loop_deal_approve", deal_id=deal_id, check_id=paused["check_id"], choice="proceed", said="yes")
        await seller.call("loop_deal_deliver", deal_id=deal_id, out_path=delivery, check_id=paused["check_id"])
    await buyer.call("loop_deal_reconcile", peer_file=delivery)
    for me in (buyer, seller):
        await me.call("loop_deal_close", deal_id=deal_id, status="received", out_path=str(tmp / f"{me.name}-statement.json"))
    for me, peer in ((buyer, seller), (seller, buyer)):
        await peer.call("loop_deal_reconcile", peer_file=str(tmp / f"{me.name}-statement.json"))
    record["payments_recorded"] = settled(ref, deal_id)  # both books hold the deal now
    shutil.rmtree(tmp)
    (step06 / "lightning.json").write_text(json.dumps({**record, "deal_id": deal_id}, indent=2, sort_keys=True) + "\n")
    return deal_id


def public_key(home):
    """A node's public key (SPKI PEM), from its profile's signing seed."""
    pub = subprocess.run([sys.executable, "-c", PUBLIC, str(Path(home) / "deal" / "signing.seed")],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if pub.returncode != 0:
        raise SystemExit(f"cannot read a node's signing key: {pub.stderr.strip()}")
    return pub.stdout


def cmd_init(a):
    """Join every node (a home with no capsulectl-deal profile gets one made from its old key files, so its key ids
    stay), write every node's public key, and print the ids: {name: {node_id, key_id, record_id}}. loop_join seals one
    join record per key (`once="key_id"`), so a node already joined gets its own join record back: --ids is always the
    profile's, never a stale pre-cutover entry left by an init that stopped before lab/goose.sh rewrote the file."""
    ids = json.loads(Path(a.ids).read_text()) if Path(a.ids).is_file() else {}

    async def work(nodes):
        for name in NAMES:
            j = await nodes[name].call("loop_join")
            # the node's id is its signing key's id
            ids[name] = {"node_id": j["key_id"], "key_id": j["key_id"], "record_id": j["record_id"]}
    asyncio.run(with_nodes(a.homes, work))
    for name in NAMES:
        kd = Path(a.keys) / name
        kd.mkdir(parents=True, exist_ok=True)
        (kd / "node-key.pub.pem").write_text(public_key(Path(a.homes) / name))
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
        asyncio.run(asks.run(run, relay, a.homes, a.published, joined, names=NAMES, relay_name=RELAY,
                           operator="{}"))  # the operator servers() gives each node
    finally:
        relay.close()
    src = Path(os.environ["LOOP_GOOSE_SRC"])
    rev = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], stdin=subprocess.DEVNULL, capture_output=True,
                         text=True).stdout.strip()
    if not rev:
        raise SystemExit("cannot read the Goose checkout revision")
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
            node_ids = json.loads(Path(a.ids).read_text())

            async def deal(nodes):
                return await pay_deal(nodes, node_ids, pop / "step06", lambda before_send: gl.pay(payer, payee, log, before_send=before_send),
                                      lambda h, d: gl.mark_settled(log, h, d, f"{payer.name}>{payee.name}"),
                                      lambda name, *args: profile_ctl(a.homes, name).run(*args))
            asyncio.run(with_nodes(a.homes, deal))
            steps.append("06")
            lightning = {"payer": payer.name, "payee": payee.name, "file": "step06/lightning.json"}
    nodes = [{"name": n, "node_id": ids[n]["key_id"], "key_dir": str(Path(a.keys) / n)} for n in NAMES]
    doc = {"population": "live", "nodes": nodes, "pinned": ["goose-g1", "capsule-engine"],
           "goose": {"run": str(work), "log": str(work / "session.log"), "rev": rev,
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
    # Every node process this driver starts, the ask session's included, runs on the installed plugin and engine.
    os.environ.update(node_env(TOOLS))
    {"init": cmd_init, "advertise": cmd_advertise, "session": cmd_session}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
