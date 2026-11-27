#!/usr/bin/env python3
"""One scripted Goose deal on the mock rail: the Goose node's own scripted run, with the rail paying before the buyer
settles (loop#330, evidencebook#624).

Usage (in the Goose venv): goose_session.py OUT RAIL_DIR

The same x-deal-v1 sequence as evidencebook's python/examples/goose/scripted_run.py at the goose-g1 pin (two
loop_server.py processes, a buyer and a seller, each a capsulectl-deal profile, spoken to over MCP stdio as Goose speaks
to them), with what the loop's steps read added around it:

  12  each node's join (loop_join, its commitment written beside it) and the join record's bundle, header disclosed
  04  the buyer's GO on the seller and its NO on a stranger (loop_admit); the deal's context, which cites the GO, as a
      disclosed bundle (loop_deal_bundle); the NO's bundle at the end of the session; the node's refusal to open a deal
      with the refused peer (deal-refused.json)
  01  a model call the buyer sealed (loop_seal)
  02  that record's bundle under the buyer's book checkpoint (loop_book writes the checkpoint statement, this driver posts
      it to the rehearsal's TEST witness and passes the receipt to loop_book_bundle)
  05  the deal's files in the order they are exchanged (offer, agreements, payment legs, delivery, statements) and each
      side's report bundle (loop_deal_report)
  06  the mock rail (population/rehearsal/rail/mock_rail.py) issues the settlement object for the deal before the buyer
      pays; its settlement_id is the payment reference the buyer's payer leg carries and the seller's payee leg states
      it received (the node pays on lightning only, so the deal's rail is "lightning" and the reference is this run's)
  03  each node's signed commitment (loop_advertise), naming the mailbox step 07 asks through

Nothing moves; the rail key and the witness key are TEST keys made for the run. GOOSE_NO_WITNESS=1: the profiles name no
public witness.
"""
import asyncio
import json
import os
import sys
import threading
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "population" / "rehearsal" / "rail"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lab"))
import make_rail  # noqa: E402
import mock_rail  # noqa: E402
from goose_ask_session import ADVERTISED_RELAY, ANSWERS_WITHIN, POLL_INTERVAL  # noqa: E402  the mailbox step 07 asks through
import witness as lab_witness  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding, NoEncryption, PrivateFormat, PublicFormat)
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SRC = Path(os.environ["LOOP_GOOSE_SRC"]) / "python" / "examples" / "goose"
# The deal's price is in USD cents. The node's admission pack (capsule-engine's ladder pack) gives GO only under 1 USD.
CURRENCY, DECIMALS, PRICE_MINOR, FEE = "USD", 2, 50, 1
RAIL = "lightning"  # the node pays on lightning only; in the rehearsal the reference is the mock rail's settlement_id
# A peer the buyer refuses: a 64-hex key no node holds.
STRANGER = "ab" * 32
# A report rests on the cadence tick that covers the deal. The node's default cadence is a tick every 5 minutes give or
# take 1 (capsulectl's), so the rehearsal gives each node's profile a 1-minute cadence with no jitter (CADENCE, written
# after loop_join), as evidencebook-skills#428 does for tau2-live: `deal agree` stamps at once and sets the next tick
# from the profile's cadence, so step 05's report waits about a minute, not five (loop#495).
CADENCE = "cadence:\n    interval: 1m\n    jitter: 0s\n"
REPORT_WAIT = 240


class Goose:
    """One node's MCP session, as Goose holds it."""

    def __init__(self, session, name):
        self.session, self.name = session, name

    async def call(self, tool, **arguments):
        out = await self.raw(tool, **arguments)
        if not out.get("ok"):
            raise SystemExit(f"{self.name} {tool}: {out.get('error')}")
        print(f"{self.name:6} {tool:22} ok", flush=True)
        return out

    async def raw(self, tool, **arguments):
        """The tool's JSON reply, a refusal included."""
        result = await self.session.call_tool(tool, arguments)
        return json.loads("".join(c.text for c in result.content if getattr(c, "type", "") == "text"))


def set_cadence(home, profile):
    """Give the node's capsulectl-deal profile (<home>/.config/capsule/profiles/<profile>.yaml, the one its Ctl runs
    under) the rehearsal's CADENCE. A profile that already has it is left as it is; one with another cadence refuses."""
    path = Path(home) / ".config" / "capsule" / "profiles" / f"{profile}.yaml"
    if not path.is_file():
        raise SystemExit(f"goose_session: no profile {path} after loop_join")
    text = path.read_text()
    if CADENCE in text:
        return
    if any(line.startswith("cadence:") for line in text.splitlines()):
        raise SystemExit(f"goose_session: {path} already sets another cadence")
    path.write_text(text + ("" if text.endswith("\n") else "\n") + CADENCE)


def issue_settlement(rail_dir, deal_id, payer, payee):
    """The rail's one settlement object for the deal: written to rail_dir, returned."""
    pem, key_id = make_rail.rail_key(rail_dir)
    make_rail.dump(rail_dir / "rail.json", {
        "rail": "mock", "declaration": mock_rail.DECLARATION, "test_key": True, "key_id": key_id,
        "public_key": "keys/rail-key.pub.pem", "ledger": "ledger.jsonl", "currency": CURRENCY,
        "decimals": DECIMALS, "fee_paying": True})
    line = mock_rail.issue(pem, sequence=1, exchange_id=deal_id, payer=payer, payee=payee, currency=CURRENCY,
                           amount_atomic=PRICE_MINOR, decimals=DECIMALS, fee_atomic=FEE, final=True,
                           issued_at="2026-11-01T00:01:00Z")
    (rail_dir / "ledger.jsonl").write_bytes(make_rail.cc.canonical(line) + b"\n")
    return line


def start_witness(out, driver):
    """The rehearsal's test witness for step 02: the lab witness's handler (lab/witness.py) on a loopback port, over plain
    HTTP, signing with a key made for this run and held apart from both nodes. Returns (server, endpoint, the directory
    file the receipt is checked under); the step's listing of the witness key is written beside it as witnesses.json."""
    key = out / "witness-key.pem"
    private = Ed25519PrivateKey.generate()
    key.write_bytes(private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    key.chmod(0o600)
    server = ThreadingHTTPServer(("127.0.0.1", 0), lab_witness.handler(str(driver), str(key), None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    key_id = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    not_before = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # receipts count from the witness's start
    endpoint = f"http://127.0.0.1:{server.server_port}"
    directory = out / "witnesses-directory.json"
    directory.write_text(json.dumps({"witnesses": [{"name": "goose-rehearsal-witness", "key_ids": [key_id],
                                                    "endpoint": endpoint}]}))
    (out / "witnesses.json").write_text(json.dumps({"witnesses": [{
        "name": "goose-rehearsal-witness", "key_id": key_id, "not_before": not_before}]}, indent=2) + "\n")
    return server, endpoint, directory


def witness_receipt(endpoint, statement):
    """The witness's receipt for one checkpoint statement, in a bundle's checkpoint.witnesses form (the node's
    loop_book_bundle takes it as the host fetched it)."""
    req = urllib.request.Request(f"{endpoint}/checkpoints", data=statement, method="POST",
                                 headers={"Content-Type": "application/cose"})
    with urllib.request.urlopen(req, timeout=60) as r:
        got = json.loads(r.read())
    return {"ts_url": endpoint, "entry_hash": got["entry_hash"], "receipt_b64": got["receipt_b64"],
            "leaf_index": got["leaf_index"], "tree_size": got["tree_size"]}


def usable_driver(driver):
    """Whether the step 02 driver can run, an executable file: the session starts the rehearsal's test witness (and so
    writes the buyer's book bundle) only then; otherwise it runs without one and step 02 reads NOT_CHECKED."""
    return driver.is_file() and os.access(driver, os.X_OK)


async def run(out, rail_dir):
    out.mkdir(parents=True, exist_ok=True)
    driver = Path(os.environ.get("LOOP_STEP02_BOOK_BIN") or Path(os.environ.get(
        "LOOP_TOOLS", Path.home() / ".cache" / "evidence-network-loop")) / "02-book" / "bin" / "loop-step02-book")
    witness, endpoint, directory = start_witness(out, driver) if usable_driver(driver) else (None, None, None)

    def params(name):
        env = {**os.environ, "GOOSE_HOME": str(out / name), "GOOSE_OPERATOR": f"goose-{name}", "GOOSE_NO_WITNESS": "1"}
        return StdioServerParameters(command=sys.executable, args=[str(SRC / "loop_server.py")], env=env)

    async with stdio_client(params("buyer")) as (br, bw), stdio_client(params("seller")) as (sr, sw):
        async with ClientSession(br, bw) as bs, ClientSession(sr, sw) as ss:
            await bs.initialize()
            await ss.initialize()
            buyer, seller = Goose(bs, "buyer"), Goose(ss, "seller")
            # 12: the join, with the node's default commitment written beside it; the join record's bundle (header disclosed)
            # is exported for step 12. The asks of step 07 are made by these same nodes (goose_ask_session.py DEAL_RUN).
            b = await buyer.call("loop_join", commitment_dir=str(out / "buyer" / "joined"))
            s = await seller.call("loop_join", commitment_dir=str(out / "seller" / "joined"))
            for name in ("buyer", "seller"):  # before the deal, so its agreement's stamp sets a 1-minute next tick
                set_cadence(out / name, f"goose-{name}")
            for me, j in ((buyer, b), (seller, s)):
                await me.call("loop_book_bundle", book_id="goose/node", record_id=j["record_id"], disclose=True,
                              out_path=str(out / me.name / "joined" / "join-record.bundle.json"))
            (out / "joined.json").write_text(json.dumps(
                {"buyer": {"key_id": b["key_id"], "record_id": b["record_id"]},
                 "seller": {"key_id": s["key_id"], "record_id": s["record_id"]}}, sort_keys=True))
            for name, key in (("buyer", b), ("seller", s)):
                (out / f"{name}.key").write_text(key["key_id"])
                (out / f"{name}.checkpoint-key").write_text(key["checkpoint_key_id"])
            await buyer.call("loop_admit", peer_key_id=s["key_id"], decision="GO", reason="known inference seller")
            await seller.call("loop_admit", peer_key_id=b["key_id"], decision="GO", reason="paying customer")
            # 04: the buyer also refuses a stranger (NO). No deal opens with it, and no record cites that NO.
            refused = await buyer.call("loop_admit", peer_key_id=STRANGER, decision="NO", reason="unknown seller")
            # 01: the buyer seals a model call, an effect-boundary record step 01 reads. Provider and model are what
            # the step tells the producer (steps/01-seal MODEL); model_attestation must carry them back.
            sealed = await buyer.call("loop_seal", provider="rehearsal", model_id="rehearsal-model",
                                      prompt="rehearsal inference", completion="rehearsal completion",
                                      out_path=str(out / "sealed-buyer.json"))
            if witness:  # 02: the sealed call's place in the buyer's book, its checkpoint and the witness's receipt
                cose = out / "book-checkpoint-buyer.cose"
                cp = await buyer.call("loop_book", book_id="goose/node", out_path=str(cose))
                receipt = out / "book-receipt-buyer.json"
                receipt.write_text(json.dumps(witness_receipt(endpoint, cose.read_bytes()), sort_keys=True))
                await buyer.call("loop_book_bundle", book_id="goose/node", record_id=sealed["record_id"],
                                 out_path=str(out / "book-bundle-buyer.json"), witness_receipt=str(receipt),
                                 witness_directory=str(directory), at=cp["checkpoint"]["id"])
            # 05: the buyer offers.
            opened = await buyer.call("loop_deal_open", peer_key_id=s["key_id"], item="inference: 1k tokens",
                                      price_minor=PRICE_MINOR, currency=CURRENCY, rail=RAIL,
                                      asked="buy 1k tokens of inference from the seller", out_path=str(out / "offer.json"))
            deal_id = opened["deal_id"]
            # 04: the buyer's record of the deal's context, which cites its GO, with both disclosed: one bundle.
            await buyer.call("loop_deal_bundle", deal_id=deal_id, out_path=str(out / "admission-go-bundle.json"))
            # 04: the gate holds, asked directly: a deal with the peer the buyer refused is itself refused, and seals nothing.
            attempt = await buyer.raw("loop_deal_open", peer_key_id=STRANGER, item="inference: 1k tokens",
                                      price_minor=PRICE_MINOR, currency=CURRENCY, rail=RAIL, asked="buy from the stranger",
                                      out_path=str(out / "offer-to-stranger.json"))
            if attempt.get("ok"):
                raise SystemExit("buyer loop_deal_open: a deal opened with the peer the buyer refused (NO)")
            (out / "deal-refused.json").write_text(json.dumps({"peer": STRANGER, **attempt}, sort_keys=True))
            # 05: the seller answers with its agreement; the buyer agrees back.
            await seller.call("loop_deal_respond", request_file=str(out / "offer.json"),
                              asked="sell 1k tokens of inference at the listed price",
                              out_path=str(out / "seller-agreement.json"))
            await buyer.call("loop_deal_reconcile", peer_file=str(out / "seller-agreement.json"))
            await buyer.call("loop_deal_agree", deal_id=deal_id, out_path=str(out / "buyer-agreement.json"))
            await seller.call("loop_deal_reconcile", peer_file=str(out / "buyer-agreement.json"))
            # 06: the rail settles; the buyer pays with the rail's reference. The final review pauses (a price the agent
            # picked, a rail with no card protection); the user's answer is sealed, as in the node's scripted run.
            settlement = issue_settlement(rail_dir, deal_id, b["key_id"], s["key_id"])
            ref = settlement["settlement_id"]
            paused = await buyer.call("loop_settle", deal_id=deal_id, payment_ref=ref, out_path=str(out / "buyer-leg.json"))
            if paused.get("paused"):
                await buyer.call("loop_deal_approve", deal_id=deal_id, check_id=paused["check_id"], choice="proceed",
                                 said="yes")
                await buyer.call("loop_settle", deal_id=deal_id, payment_ref=ref, out_path=str(out / "buyer-leg.json"),
                                 check_id=paused["check_id"])
            await seller.call("loop_deal_reconcile", peer_file=str(out / "buyer-leg.json"))
            await seller.call("loop_deal_received", deal_id=deal_id, amount_minor=PRICE_MINOR, payment_ref=ref,
                              out_path=str(out / "seller-leg.json"))
            await buyer.call("loop_deal_reconcile", peer_file=str(out / "seller-leg.json"))
            # 06: each node's signed count of its settlements on the deal's rail, through the mock ledger's latest
            # sequence (its one line), which step 06 compares with the rail's ledger (ledger_count).
            for node in (buyer, seller):
                await node.call("loop_settlement_disclosure", rail=RAIL, through_sequence=settlement["sequence"],
                                out_path=str(out / f"disclosure-{node.name}.json"))
            delivery = await seller.call("loop_deal_deliver", deal_id=deal_id, out_path=str(out / "seller-delivery.json"))
            if delivery.get("paused"):
                await seller.call("loop_deal_approve", deal_id=deal_id, check_id=delivery["check_id"], choice="proceed",
                                  said="yes")
                await seller.call("loop_deal_deliver", deal_id=deal_id, out_path=str(out / "seller-delivery.json"),
                                  check_id=delivery["check_id"])
            await buyer.call("loop_deal_reconcile", peer_file=str(out / "seller-delivery.json"))
            for me in (buyer, seller):
                await me.call("loop_deal_close", deal_id=deal_id, status="received",
                              out_path=str(out / f"{me.name}-statement.json"))
            for me, peer in ((buyer, seller), (seller, buyer)):
                await peer.call("loop_deal_reconcile", peer_file=str(out / f"{me.name}-statement.json"))
            # 05: each side's report of the deal, as an Evidence Bundle resting on the cadence tick that covers it.
            await asyncio.gather(*(node.call("loop_deal_report", deal_id=deal_id, wait_seconds=REPORT_WAIT,
                                             out_path=str(out / f"{node.name}-report.json")) for node in (buyer, seller)))
            for node in (buyer, seller):
                await node.call("loop_probe")
                await node.call("loop_standard_history", out_path=str(out / f"{node.name}-card.json"))
                await node.call("loop_history_card", out_path=str(out / f"{node.name}-history-card.json"))
                # 03: the node's own signed commitment and policy (loop_advertise), laid out for nodes/commitments/.
                # It advertises a Nostr mailbox, through which step 07 asks the node.
                await node.call("loop_advertise", out_dir=str(out / "commitments"), relays=ADVERTISED_RELAY,
                                answers_within=ANSWERS_WITHIN, poll_interval=POLL_INTERVAL)
            # 04: the NO with the book up to the end of the session, so no record sealed after it cites it.
            await buyer.call("loop_book_bundle", book_id="goose/node", record_id=refused["record_id"], disclose=True,
                             out_path=str(out / "admission-no-disclosed.json"))
    if witness:
        witness.shutdown()
    (out / "deal.json").write_text(json.dumps({"deal_id": deal_id, "offer": opened["offer"], "buyer": b["key_id"],
                                               "seller": s["key_id"], "settlement_id": ref}, sort_keys=True))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: goose_session.py OUT RAIL_DIR")
    asyncio.run(run(Path(sys.argv[1]), Path(sys.argv[2])))
