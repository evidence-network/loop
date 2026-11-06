"""scripts/lab_circulate.py (loop#384): lab-1 returns part of its earnings to lab-3 only when lab-3 cannot pay its
next charge, through the guarded payment, and a refusal is a report, not an error. Wallets here are in-memory stand-ins:
nothing is paid and no network is reached. Run: python3 -B -m unittest discover -s tests -v"""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import goose_lightning as gl  # noqa: E402
import lab_circulate  # noqa: E402


PREIMAGE = bytes(range(32))


class FakeWallet:
    """Raises invoices whose hash matches a fixed preimage, and moves value to its peer on `send` (receive fee 100)."""

    def __init__(self, name, spendable):
        self.name, self.spendable, self.peer, self.rows, self.invoices = name, spendable, None, [], {}

    def call(self, command, timeout=30, **a):
        if command == "balance":
            return {"spendable_msat": self.spendable}
        if command == "policy":
            return {"spent_today_msat": 0}
        if command == "transactions":
            return self.rows
        if command == "fund":
            h = hashlib.sha256(PREIMAGE).hexdigest()
            self.invoices[h] = a["amount_msat"]
            return {"payment_hash": h, "bolt11": "lnbc" + "x" * 30, "amount_msat": a["amount_msat"]}
        if command == "send":
            h, amt = next(iter(self.peer.invoices.items()))
            self.rows = [{"payment_hash": h, "inbound": False, "status": "succeeded", "amount_msat": amt, "fee_msat": 0,
                          "preimage": PREIMAGE.hex()}]
            self.peer.rows = [{"payment_hash": h, "inbound": True, "status": "succeeded", "amount_msat": amt - 100, "fee_msat": 100}]
            self.peer.spendable += amt - 100
            self.spendable -= amt
            return {}
        raise AssertionError(command)

    def transaction(self, h, inbound):
        return next((t for t in self.rows if t["payment_hash"] == h and bool(t["inbound"]) == inbound), None)


def wallets(lab1=11000, lab3=900):
    a, b = FakeWallet("lab-1", lab1), FakeWallet("lab-3", lab3)
    a.peer, b.peer = b, a
    return a, b


class Circulate(unittest.TestCase):
    def setUp(self):
        self.log = Path(tempfile.mkdtemp()) / "payments.jsonl"

    def test_sends_when_lab3_is_under_a_charge(self):
        a, b = wallets(lab3=900)
        r = lab_circulate.circulate(a, b, self.log)
        self.assertEqual((r["action"], r["amount_msat"], r["payee_received_msat"]), ("sent", 1500, 1400))
        self.assertGreaterEqual(b.spendable, lab_circulate.CHARGE_MSAT)
        self.assertEqual([e["direction"] for e in gl.read_log(self.log)], ["lab-1>lab-3"])  # counted against the guard
        self.assertNotIn("payment_hash", r)

    def test_does_nothing_when_lab3_can_pay(self):
        a, b = wallets(lab3=1000)
        self.assertEqual(lab_circulate.circulate(a, b, self.log)["action"], "none")
        self.assertEqual(gl.read_log(self.log), [])

    def test_refusal_is_reported_inside_the_daily_cap(self):
        a, b = wallets()
        used = [{"day": gl.utc_day(), "direction": "lab-1>lab-3", "payment_hash": "0" * 64, "amount_msat": 1500,
                 "status": "succeeded"}] * 6  # 6 x 1,500 = 9,000 msat: the direction's day is used up
        gl.rewrite_log(self.log, used)
        r = lab_circulate.circulate(a, b, self.log)
        self.assertEqual(r["action"], "refused")
        self.assertIn("9000", r["reason"])

    def test_unknown_outcome_is_not_reported_as_refused(self):
        a, b = wallets()
        orig = b.call
        b.call = lambda c, timeout=30, **k: (_ for _ in ()).throw(gl.Unknown("no answer")) if c == "fund" else orig(c, timeout, **k)
        self.assertEqual(lab_circulate.circulate(a, b, self.log)["action"], "unknown")

    def test_a_confirmed_payment_survives_a_failed_balance_read_after_it(self):
        a, b = wallets()
        orig, reads = b.call, []

        def call(c, timeout=30, **k):
            if c == "balance":
                reads.append(1)
                if len(reads) > 1:
                    raise gl.Unknown("no answer")
            return orig(c, timeout, **k)
        b.call = call
        r = lab_circulate.circulate(a, b, self.log)
        self.assertEqual((r["action"], r["spendable_after_msat"]), ("sent", None))

    def test_lab1_too_poor_is_a_refusal(self):
        a, b = wallets(lab1=1000)
        self.assertEqual(lab_circulate.circulate(a, b, self.log)["action"], "refused")


if __name__ == "__main__":
    unittest.main()
