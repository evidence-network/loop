#!/usr/bin/env python3
"""One scripted Goose deal on the mock rail: G1's scripted run, with the rail paying before the buyer settles (loop#330).

Usage (in the Goose venv): goose_session.py OUT RAIL_DIR

The same calls as evidencebook's python/examples/goose/scripted_run.py (two loop_server.py processes, a buyer and a
seller, spoken to over MCP stdio as Goose speaks to them), with one difference: after the seller has answered the
buyer's half, the mock rail (population/rehearsal/rail/mock_rail.py) issues the settlement object for the deal, and
the buyer's `loop_settle` takes that object's settlement_id as the payment reference it observed. The rail is the
one the mesh rehearsal's step 06 reads; the deal's pay record commits the reference, so the deal and the rail's
object are tied by the id. Nothing moves; the rail key is a TEST key made for the run.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "population" / "rehearsal" / "rail"))
sys.path.insert(0, str(ROOT / "scripts"))
import make_rail  # noqa: E402
import mock_rail  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SRC = Path(os.environ["LOOP_GOOSE_SRC"]) / "python" / "examples" / "goose"
CURRENCY, DECIMALS, PRICE_MINOR, FEE = "USD", 2, 500, 1  # the deal's price is in USD cents


class Goose:
    """One node's MCP session, as Goose holds it."""

    def __init__(self, session, name):
        self.session, self.name = session, name

    async def call(self, tool, **arguments):
        result = await self.session.call_tool(tool, arguments)
        out = json.loads("".join(c.text for c in result.content if getattr(c, "type", "") == "text"))
        if not out.get("ok"):
            raise SystemExit(f"{self.name} {tool}: {out.get('error')}")
        print(f"{self.name:6} {tool:22} ok")
        return out


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


async def run(out, rail_dir):
    out.mkdir(parents=True, exist_ok=True)

    def params(name):
        env = {**os.environ, "GOOSE_HOME": str(out / name), "GOOSE_OPERATOR": f"goose-{name}"}
        return StdioServerParameters(command=sys.executable, args=[str(SRC / "loop_server.py")], env=env)

    async with stdio_client(params("buyer")) as (br, bw), stdio_client(params("seller")) as (sr, sw):
        async with ClientSession(br, bw) as bs, ClientSession(sr, sw) as ss:
            await bs.initialize()
            await ss.initialize()
            buyer, seller = Goose(bs, "buyer"), Goose(ss, "seller")
            b, s = await buyer.call("loop_join"), await seller.call("loop_join")
            await buyer.call("loop_admit", peer_key_id=s["key_id"], checkpoint_key_id=s["checkpoint_key_id"],
                             decision="GO", reason="known inference seller")
            await seller.call("loop_admit", peer_key_id=b["key_id"], checkpoint_key_id=b["checkpoint_key_id"],
                              decision="GO", reason="paying customer")
            # 01: the buyer seals a model call, an effect-boundary record step 01 reads. Provider and model are what
            # the step tells the producer (steps/01-seal MODEL); model_attestation must carry them back.
            await buyer.call("loop_seal", provider="rehearsal", model_id="rehearsal-model",
                             prompt="rehearsal inference", completion="rehearsal completion",
                             out_path=str(out / "sealed-buyer.json"))
            opened = await buyer.call("loop_deal_open", peer_key_id=s["key_id"], item="inference: 1k tokens",
                                      price_minor=PRICE_MINOR, currency=CURRENCY, rail="mock",
                                      asked="buy 1k tokens of inference from the seller")
            deal_id = opened["deal_id"]
            await buyer.call("loop_deal_push", deal_id=deal_id, out_path=str(out / "buyer-half.json"))
            await seller.call("loop_deal_respond", request_file=str(out / "buyer-half.json"),
                              asked="sell 1k tokens of inference at the listed price")
            await seller.call("loop_deal_push", deal_id=deal_id, out_path=str(out / "seller-half.json"))
            await buyer.call("loop_deal_reconcile", peer_file=str(out / "seller-half.json"))
            settlement = issue_settlement(rail_dir, deal_id, b["key_id"], s["key_id"])
            await buyer.call("loop_settle", deal_id=deal_id, payment_ref=settlement["settlement_id"])
            await buyer.call("loop_deal_close", deal_id=deal_id, status="received")
            await seller.call("loop_deal_close", deal_id=deal_id, status="received")
            for me, peer in ((buyer, seller), (seller, buyer)):
                path = str(out / f"{me.name}-final-half.json")
                await me.call("loop_deal_push", deal_id=deal_id, out_path=path)
                await peer.call("loop_deal_reconcile", peer_file=path)
            for node, key in ((buyer, b), (seller, s)):
                await node.call("loop_tick")
                await node.call("loop_probe")
                await node.call("loop_standard_history", out_path=str(out / f"{node.name}-card.json"))
                await node.call("loop_history_card", book_id=f"deal/{deal_id}",
                                out_path=str(out / f"{node.name}-history-card.json"))
                (out / f"{node.name}.key").write_text(key["key_id"])
    (out / "deal.json").write_text(json.dumps({"deal_id": deal_id, "buyer": b["key_id"], "seller": s["key_id"],
                                               "settlement_id": settlement["settlement_id"]}, sort_keys=True))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: goose_session.py OUT RAIL_DIR")
    asyncio.run(run(Path(sys.argv[1]), Path(sys.argv[2])))
