#!/usr/bin/env python3
"""Keep lab-3's wallet able to pay its daily 1,000 msat charge, by returning part of what lab-1 earned (loop#384).

lab-2 and lab-3 each pay lab-1 1,000 msat a day for the one chat completion lab.sh exchange asks, and only lab-1 sells,
so lab-3 only spends. At the end of a daily run, if lab-3's ledger holds less than the next charge (ledger_msat; its `balance` reads 0 whatever it holds), lab-1 sends it TOPUP_MSAT
through goose_lightning.pay, the guarded lab-to-lab payment (at most 3,000 msat per payment and 9,000 msat per
direction per UTC day, counted in the same log as the Goose payments). Value circulates between our own lab wallets;
nothing comes from outside the lab. A refusal or an unknown outcome is reported, never raised: the run's exit code
and findings do not depend on it. Prints one JSON line without a payment hash, invoice or preimage.

  lab_circulate.py   (LAB_DIR required; LIVE_LAB3_STATUS: the HTTP status line lab-3's request got, optional; LAB_PORT_BASE, default 9800: lab-N's wallet API is on its console, BASE+10+N)
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import goose_lightning as gl  # noqa: E402

CHARGE_MSAT = 1000  # what lab-3 pays lab-1 each day (the minimum payment, LAB-BRINGUP.md)
TX_LIMIT = 1000
TOPUP_MSAT = 1500   # the receive-side fee took 100 of 2,000 on 2026-11-06; this leaves room for it and a day's charge


def ledger_msat(wallet):
    """What the wallet's own transaction list says it holds: settled receipts minus settled payments and their fees.
    Not `balance`: lab-3's `balance` reads 0 after it received 3,800 msat and paid 1,000 (2026-11-06), so it cannot say
    when to top up. A list that reaches the limit may be cut short, so it is not summed."""
    rows = wallet.call("transactions", limit=TX_LIMIT)
    if not isinstance(rows, list):
        raise gl.Unknown(f"{wallet.name}'s transactions are unreadable")
    if len(rows) >= TX_LIMIT:  # the list may be cut short: a missed receipt or payment would skew the sum either way
        raise gl.Unknown(f"{wallet.name}'s transaction list reached {TX_LIMIT} rows; its ledger cannot be summed")
    return sum((t.get("amount_msat") or 0) if t.get("inbound") else -((t.get("amount_msat") or 0) + (t.get("fee_msat") or 0))
               for t in rows if t.get("status") == "succeeded")


def sent_or_pending(log):
    """The log's payments today in the circulation direction that are not known to have failed."""
    return sum(1 for e in gl.read_log(log) if e["direction"] == "lab-1>lab-3" and e["day"] == gl.utc_day() and e["status"] != "failed")


def circulate(payer, payee, log, asked_status=""):
    """payer (lab-1) returns TOPUP_MSAT to payee (lab-3) when payee's ledger holds less than CHARGE_MSAT, or its last
    request to lab-1 was not answered 200 (the ledger may overstate what it can send). Returns the report: action none
    (nothing to do), sent (confirmed on both wallets), refused (the guard or a check refused before anything was
    sent) or unknown (the payment may have gone out; its reservation stays in the log)."""
    try:
        before = ledger_msat(payee)
        failed_ask = bool(asked_status) and " 200" not in asked_status
        if before >= CHARGE_MSAT and not failed_ask:
            return {"action": "none", "payee": payee.name, "ledger_msat": before, "asked_status": asked_status or None}
        reserved = sent_or_pending(log)
        out = gl.pay(payer, payee, log, amount_msat=TOPUP_MSAT)
    except gl.Refused as e:
        # `pay` also raises Refused for an error after `send` (its transaction poll): that left a reservation.
        action = "unknown" if sent_or_pending(log) > reserved else "refused"
        return {"action": action, "payer": payer.name, "payee": payee.name, "reason": gl.clean(e)}
    except (gl.Unknown, ValueError) as e:  # ValueError: a 200 with a body that is not JSON, after `send` was issued
        return {"action": "unknown", "payer": payer.name, "payee": payee.name, "reason": gl.clean(e)}
    try:
        after = ledger_msat(payee)
    except (gl.Refused, gl.Unknown, ValueError):
        after = None  # the payment is confirmed; only the ledger read failed
    return {"action": "sent", "payer": payer.name, "payee": payee.name, "amount_msat": TOPUP_MSAT,
            "payee_received_msat": out["payee"]["amount_msat"], "ledger_before_msat": before,
            "ledger_after_msat": after}


def main():
    lab = os.environ.get("LAB_DIR")
    if not lab:
        print(json.dumps({"action": "none", "reason": "LAB_DIR is not set"}))
        return 0
    base = int(os.environ.get("LAB_PORT_BASE", "9800"))
    payer, payee = gl.Wallet(base + 11, "lab-1"), gl.Wallet(base + 13, "lab-3")
    status = os.environ.get("LIVE_LAB3_STATUS", "")
    print(json.dumps(circulate(payer, payee, Path(lab) / "goose" / "lightning" / "payments.jsonl", status), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
