#!/usr/bin/env python3
"""The Goose lab's one real Lightning payment, lab wallet to lab wallet, inside the lab payment guard (loop#349).

Not run by the loop's steps (steps/06-settle only reads what this wrote): lab/goose.sh pay runs it on the lab host,
through goose_live.py. The Goose nodes (G1) drive no rail and hold no wallet, so the payer's lab wallet (lab-1 by
default) pays an invoice the payee's (lab-2) raised, both through their node's local wallet API (`/api/wallet`, the
API `mesh-llm wallet` uses), and the Goose buyer's `loop_settle` takes the invoice's payment_hash as the reference it
observed.

The guard (Ethan, 2026-11-01; LAB-BRINGUP.md): at most MAX_PAYMENT_MSAT per payment and MAX_DIRECTION_MSAT per
direction per UTC day. It is enforced here against this host's payment log (every attempt reserves its amount before
`send`, so a crash never lets a later run pass the cap) and against the payer wallet's own `spent_today_msat`. The
log, $LAB_DIR/goose/lightning/payments.jsonl, holds payment hashes and amounts only.

A preimage is hashed here and dropped: only the outcome ("verified") is returned, never the preimage, and no error text
carries an invoice or a preimage.
"""
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

MAX_PAYMENT_MSAT = 3000
MAX_DIRECTION_MSAT = 9000
AMOUNT_MSAT = 1000  # the smallest amount that works on the lab wallets (loop#300)
MAX_FEE_MSAT = 1000
HEX64 = re.compile(r"[0-9a-f]{64}")
SECRET = re.compile(r"ln[a-z0-9]{20,}|[0-9a-f]{64}", re.I)  # an invoice or a 32-byte hex string
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # the wallet API is local: never through a proxy


class Refused(Exception):
    """The guard or a preflight check refused; nothing was sent."""


class Unknown(Exception):
    """The outcome of a payment that may have gone out is not known; its reservation stays."""


def clean(text):
    return SECRET.sub("<redacted>", str(text))[:300]


class Wallet:
    """One lab node's local wallet API."""

    def __init__(self, port, name):
        self.port, self.name = port, name

    def call(self, command, timeout=30, **args):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/wallet", method="POST",
                                     data=json.dumps({"command": command, **args}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with OPENER.open(req, timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            detail = clean(e.read()[:300].decode("utf-8", "replace"))
            e.close()
            raise Refused(f"{self.name} wallet refused {command}: HTTP {e.code} {detail}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise Unknown(f"no answer from {self.name}'s wallet to {command} ({type(e).__name__})")

    def transaction(self, payment_hash, inbound):
        for t in self.call("transactions", limit=50) or []:
            if t.get("payment_hash") == payment_hash and bool(t.get("inbound")) == inbound:
                return t
        return None


def utc_day(now=None):
    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%d")


@contextlib.contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def read_log(path):
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def rewrite_log(path, entries):
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in entries))
    os.replace(tmp, path)


def reserved_today(entries, direction, day):
    """What the log counts against the day's cap in a direction: every attempt not known to have failed."""
    return sum(e["amount_msat"] for e in entries
               if e["direction"] == direction and e["day"] == day and e["status"] != "failed")


def preflight(payer, payee, amount_msat, log, now=None):
    """Refuse before anything is raised or sent unless the lab is idle and the guard holds. """
    direction = f"{payer.name}>{payee.name}"
    if not (isinstance(amount_msat, int) and 0 < amount_msat <= MAX_PAYMENT_MSAT):
        raise Refused(f"{amount_msat!r} msat is not within the guard's {MAX_PAYMENT_MSAT} msat per payment")
    day = utc_day(now)
    spent = reserved_today(read_log(log), direction, day)
    if spent + amount_msat > MAX_DIRECTION_MSAT:
        raise Refused(f"{spent} msat already used {direction} on {day} (UTC); {amount_msat} more passes the {MAX_DIRECTION_MSAT} cap")
    policy = payer.call("policy") or {}
    if (policy.get("spent_today_msat") or 0) + amount_msat > MAX_DIRECTION_MSAT:
        raise Refused(f"{payer.name}'s wallet reports {policy.get('spent_today_msat')} msat spent today; "
                      f"{amount_msat} more passes the {MAX_DIRECTION_MSAT} cap")
    for w in (payer, payee):  # idle: no payment in flight on either wallet
        pending = [t for t in w.call("transactions", limit=20) or [] if t.get("status") == "pending"]
        if pending:
            raise Refused(f"{w.name}'s wallet has {len(pending)} pending payment(s): the lab is not idle")
    spendable = (payer.call("balance") or {}).get("spendable_msat")
    if not isinstance(spendable, int) or spendable < amount_msat + MAX_FEE_MSAT:
        raise Refused(f"{payer.name} has {spendable!r} msat spendable, under {amount_msat + MAX_FEE_MSAT} (amount and fee ceiling)")
    return spendable


def mark_settled(log, payment_hash, deal_id, direction):
    """The Goose deal for a succeeded payment is closed on both nodes: mark its entry, and return how many payments in
    this direction have a settled deal (what each node's disclosure counts). A payment whose deal never closed stays
    `succeeded`: it counts against the cap, not here, and the next step 06 FAILs on the mismatch until it is looked at."""
    with locked(log):
        entries = read_log(log)
        for e in entries:
            if e["payment_hash"] == payment_hash:
                e["status"], e["deal_id"] = "settled", deal_id
        rewrite_log(log, entries)
    return sum(1 for e in entries if e["status"] == "settled" and e["direction"] == direction)


def pay(payer, payee, log, amount_msat=AMOUNT_MSAT, wait=120, now=None):
    """One payment payer -> payee. Returns the record step 06 reads (lightning.json), without the preimage.

    The amount is reserved in the log before `send`; a failure that the wallet reports releases it, one whose outcome
    is not known (no answer to `send`, or no terminal status within `wait`) keeps it."""
    preflight(payer, payee, amount_msat, log, now)
    direction = f"{payer.name}>{payee.name}"
    inv = payee.call("fund", amount_msat=amount_msat)
    h = inv.get("payment_hash") if isinstance(inv, dict) else None
    if not (isinstance(h, str) and HEX64.fullmatch(h) and isinstance(inv.get("bolt11"), str) and inv.get("amount_msat") == amount_msat):
        raise Refused("the payee's wallet did not return a payment_hash and an invoice for the amount; nothing was sent")
    entry = {"day": utc_day(now), "direction": direction, "payment_hash": h, "amount_msat": amount_msat, "status": "reserved",
             "at": (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    with locked(log):  # re-check under the lock: two runs never both pass the cap
        entries = read_log(log)
        if reserved_today(entries, direction, entry["day"]) + amount_msat > MAX_DIRECTION_MSAT:
            raise Refused(f"the {MAX_DIRECTION_MSAT} msat cap for {direction} on {entry['day']} was reached while this run started")
        entries.append(entry)
        rewrite_log(log, entries)

    def settle_entry(status):
        with locked(log):
            entries = read_log(log)
            for e in entries:
                if e["payment_hash"] == h:
                    e["status"] = status
            rewrite_log(log, entries)

    try:
        payer.call("send", invoice=inv["bolt11"], amount_msat=None, max_fee_msat=MAX_FEE_MSAT, timeout=wait)
    except (Refused, Unknown):
        # An error on `send` does not prove nothing went out (the wallet can fail after its pay step started, loop#300):
        # the reservation stays unless the payer's own transaction row says failed.
        pass
    deadline = time.monotonic() + wait
    out = inb = None
    while time.monotonic() < deadline:
        out, inb = payer.transaction(h, False), payee.transaction(h, True)
        if out and out.get("status") == "failed":
            settle_entry("failed")
            raise Refused(f"the payment failed in {payer.name}'s wallet ({clean(out.get('status_msg'))})")
        if out and inb and out.get("status") == "succeeded" and inb.get("status") == "succeeded":
            break
        time.sleep(1)
    else:
        raise Unknown(f"payment {h} has no terminal status on both wallets after {wait} s (payer row: "
                      f"{out.get('status') if out else 'none'}); its reservation stays")
    pre = out.get("preimage")
    check = ("verified" if isinstance(pre, str) and HEX64.fullmatch(pre) and hashlib.sha256(bytes.fromhex(pre)).hexdigest() == h
             else "mismatch" if isinstance(pre, str) else None)
    del pre
    settle_entry("succeeded")
    return {"rail": "lightning", "payment_hash": h, "payer_wallet": payer.name, "payee_wallet": payee.name,
            "payer": {"status": out["status"], "amount_msat": out["amount_msat"], "fee_msat": out["fee_msat"],
                      "preimage_check": check},
            "payee": {"status": inb["status"], "amount_msat": inb["amount_msat"], "fee_msat": inb["fee_msat"]},
            "guard": {"per_payment_msat": MAX_PAYMENT_MSAT,
                                                       "per_direction_per_day_msat": MAX_DIRECTION_MSAT}}
