#!/usr/bin/env python3
"""Keep lab-3's wallet able to pay its daily 1,000 msat charge, by returning part of what lab-1 earned (loop#384).

lab-2 and lab-3 each pay lab-1 1,000 msat a day for the one chat completion lab.sh exchange asks, and only lab-1 sells,
so lab-3 only spends. At the end of a daily run, if lab-3 holds less than the next charge, lab-1 sends it TOPUP_MSAT
through goose_lightning.pay, the guarded lab-to-lab payment (at most 3,000 msat per payment and 9,000 msat per
direction per UTC day, counted in the same log as the Goose payments). Value circulates between our own lab wallets;
nothing comes from outside the lab. A refusal or an unknown outcome is reported, never raised: the run's exit code
and findings do not depend on it. Prints one JSON line without a payment hash, invoice or preimage.

  lab_circulate.py   (LAB_DIR required; LAB_PORT_BASE, default 9800: lab-N's wallet API is on its console, BASE+10+N)
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import goose_lightning as gl  # noqa: E402

CHARGE_MSAT = 1000  # what lab-3 pays lab-1 each day (the minimum payment, LAB-BRINGUP.md)
TOPUP_MSAT = 1500   # the receive-side fee took 100 of 2,000 on 2026-11-06; this leaves room for it and a day's charge


def circulate(payer, payee, log):
    """payer (lab-1) returns TOPUP_MSAT to payee (lab-3) when payee holds less than CHARGE_MSAT. Returns the report."""
    try:
        before = (payee.call("balance") or {}).get("spendable_msat")
        if not isinstance(before, int):
            return {"action": "none", "reason": f"{payee.name}'s balance is unreadable"}
        if before >= CHARGE_MSAT:
            return {"action": "none", "payee": payee.name, "spendable_msat": before}
        out = gl.pay(payer, payee, log, amount_msat=TOPUP_MSAT)
        after = (payee.call("balance") or {}).get("spendable_msat")
        return {"action": "sent", "payer": payer.name, "payee": payee.name, "amount_msat": TOPUP_MSAT,
                "payee_received_msat": out["payee"]["amount_msat"], "spendable_before_msat": before,
                "spendable_after_msat": after}
    except (gl.Refused, gl.Unknown) as e:
        return {"action": "refused", "payer": payer.name, "payee": payee.name, "reason": gl.clean(e)}


def main():
    lab = os.environ.get("LAB_DIR")
    if not lab:
        print(json.dumps({"action": "none", "reason": "LAB_DIR is not set"}))
        return 0
    base = int(os.environ.get("LAB_PORT_BASE", "9800"))
    payer, payee = gl.Wallet(base + 11, "lab-1"), gl.Wallet(base + 13, "lab-3")
    print(json.dumps(circulate(payer, payee, Path(lab) / "goose" / "lightning" / "payments.jsonl"), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
