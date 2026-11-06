#!/usr/bin/env python3
"""Ping-pong with lab-3 (loop#384): when lab-3's daily charge settled, lab-1 pays it the same amount back, once.

lab-2 and lab-3 each pay lab-1 1,000 msat a day for the one chat completion lab.sh exchange asks, and only lab-1 sells,
so lab-3 would only spend. At the end of a daily run, if lab-3's request to lab-1 was answered 200 (its charge settled),
lab-1 pays lab-3 CHARGE_MSAT through goose_lightning.pay, the guarded lab-to-lab payment (at most 3,000 msat per payment
and 9,000 msat per direction per UTC day, counted in the same log as the Goose payments). Otherwise nothing is paid.
Value circulates between our own lab wallets; nothing comes from outside the lab. No balance or ledger is read to decide.
One attempt per run, no retry. The outcome is reported, never raised: the run's exit code and findings do not depend on
it. Prints one JSON line without a payment hash, invoice or preimage.

  lab_circulate.py   (LAB_DIR required; LIVE_LAB3_STATUS: the HTTP status line lab-3's request got;
                      LAB_PORT_BASE, default 9800: lab-N's wallet API is on its console, BASE+10+N)

Guard fit on a day with a manual run: this direction (lab-1 to lab-3) takes CHARGE_MSAT per run, so a daily run plus
8 manual ones fit its 9,000 msat cap. pay() also checks lab-1's wallet-wide spent_today_msat against 9,000, and the
Goose payment (lab-1 to lab-2, 1,000 msat per run) spends from the same day, so with a Goose run that day one fewer
manual run fits; a refusal is recorded, never raised.

A refusal "the lab is not idle" is expected while lab-3's wallet holds an unpaid invoice of ours (the probe of
2026-11-06, a pending inbound row that pay()'s idle check counts): it is not a payment in flight and not a failed
run. It expires on 2026-11-07 at 16:17 UTC, after that day's 13:05 UTC run; the 2026-11-08 run is the first that can pay.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import goose_lightning as gl  # noqa: E402

CHARGE_MSAT = 1000  # what lab-3 pays lab-1 each day (the minimum payment, LAB-BRINGUP.md)


STATUS_200 = re.compile(r"HTTP/\S+\s+200(\s|$)")


def open_payments(log):
    """The payment hashes in the circulation direction, on any day, that are not known to have failed."""
    return {e["payment_hash"] for e in gl.read_log(log) if e["direction"] == "lab-1>lab-3" and e["status"] != "failed"}


def circulate(payer, payee, log, asked_status=""):
    """payer (lab-1) pays payee (lab-3) CHARGE_MSAT back when payee's request was answered 200. Returns the report:
    action skipped (the charge did not settle: nothing paid), sent (confirmed on both wallets), refused (the guard or a
    check refused before anything was sent) or unknown (the payment may have gone out; its reservation stays in the log)."""
    if not STATUS_200.match(asked_status.strip()):
        return {"action": "skipped", "payee": payee.name, "asked_status": asked_status or None,
                "reason": "lab-3's charge did not settle"}
    before = open_payments(log)
    try:
        out = gl.pay(payer, payee, log, amount_msat=CHARGE_MSAT)
    except gl.Refused as e:
        # `pay` also raises Refused for an error after `send` (its transaction poll): that left a reservation.
        action = "unknown" if open_payments(log) - before else "refused"
        return {"action": action, "payer": payer.name, "payee": payee.name, "reason": gl.clean(e)}
    except (gl.Unknown, ValueError) as e:  # ValueError: a 200 with a body that is not JSON, after `send` was issued
        return {"action": "unknown", "payer": payer.name, "payee": payee.name, "reason": gl.clean(e)}
    return {"action": "sent", "payer": payer.name, "payee": payee.name, "amount_msat": CHARGE_MSAT,
            "payee_received_msat": out["payee"]["amount_msat"]}


def main():
    lab = os.environ.get("LAB_DIR")
    if not lab:
        print(json.dumps({"action": "skipped", "reason": "LAB_DIR is not set"}))
        return 0
    base = int(os.environ.get("LAB_PORT_BASE", "9800"))
    payer, payee = gl.Wallet(base + 11, "lab-1"), gl.Wallet(base + 13, "lab-3")
    status = os.environ.get("LIVE_LAB3_STATUS", "")
    print(json.dumps(circulate(payer, payee, Path(lab) / "goose" / "lightning" / "payments.jsonl", status), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
