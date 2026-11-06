"""scripts/lab_circulate.py (loop#384): lab-1 pays lab-3 back its charge once, through the guarded payment, only when lab-3's
request settled (200), and a refusal is a report, not an error. Wallets here are in-memory stand-ins:
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
            self.rows = self.rows + [{"payment_hash": h, "inbound": False, "status": "succeeded", "amount_msat": amt,
                                      "fee_msat": 0, "preimage": PREIMAGE.hex()}]
            self.peer.rows = self.peer.rows + [{"payment_hash": h, "inbound": True, "status": "succeeded",
                                                "amount_msat": amt - 100, "fee_msat": 100}]
            self.peer.spendable += amt - 100
            self.spendable -= amt
            return {}
        raise AssertionError(command)

    def transaction(self, h, inbound):
        return next((t for t in self.rows if t["payment_hash"] == h and bool(t["inbound"]) == inbound), None)


def wallets(lab1=11000):
    a, b = FakeWallet("lab-1", lab1), FakeWallet("lab-3", 0)
    a.peer, b.peer = b, a
    return a, b


OK = "HTTP/1.1 200 OK"


class Circulate(unittest.TestCase):
    def setUp(self):
        self.log = Path(tempfile.mkdtemp()) / "payments.jsonl"

    def test_pays_the_charge_back_once_when_lab3_was_answered(self):
        a, b = wallets()
        r = lab_circulate.circulate(a, b, self.log, OK)
        self.assertEqual((r["action"], r["amount_msat"], r["payee_received_msat"]), ("sent", 1000, 900))
        self.assertEqual([(e["direction"], e["amount_msat"]) for e in gl.read_log(self.log)], [("lab-1>lab-3", 1000)])
        self.assertNotIn("payment_hash", r)

    def test_only_a_status_line_with_code_200_pays(self):
        for status in ("HTTP/1.1 500 upstream returned 200 unexpectedly", "HTTP/1.1 2000 OK", "200", "HTTP/1.1 402 200"):
            a, b = wallets()
            self.assertEqual(lab_circulate.circulate(a, b, self.log, status)["action"], "skipped", status)
        for status in ("HTTP/1.1 200 OK", "HTTP/2 200", "HTTP/1.1 200", " HTTP/1.1 200 OK\r"):
            a, b = wallets()
            self.assertEqual(lab_circulate.circulate(a, b, self.log, status)["action"], "sent", status)

    def test_pays_nothing_and_records_it_when_the_charge_did_not_settle(self):
        for status in ("HTTP/1.1 402 Payment Required", "", None):
            a, b = wallets()
            r = lab_circulate.circulate(a, b, self.log, status or "")
            self.assertEqual(r["action"], "skipped")
            self.assertEqual(gl.read_log(self.log), [])
            self.assertEqual(b.rows, [])

    def test_refusal_is_reported_inside_the_daily_cap(self):
        a, b = wallets()
        used = [{"day": gl.utc_day(), "direction": "lab-1>lab-3", "payment_hash": "0" * 64, "amount_msat": 1500, "status": "succeeded"}] * 6
        gl.rewrite_log(self.log, used)  # 9,000 msat: the direction's day is used up
        r = lab_circulate.circulate(a, b, self.log, OK)
        self.assertEqual(r["action"], "refused")
        self.assertIn("9000", r["reason"])

    def test_eight_manual_runs_and_the_daily_one_fit_the_cap(self):
        a, b = wallets(lab1=100000)
        for _ in range(9):
            self.assertEqual(lab_circulate.circulate(a, b, self.log, OK)["action"], "sent")
        self.assertEqual(lab_circulate.circulate(a, b, self.log, OK)["action"], "refused")

    def test_lab1_too_poor_is_a_refusal(self):
        a, b = wallets(lab1=1000)
        self.assertEqual(lab_circulate.circulate(a, b, self.log, OK)["action"], "refused")

    def test_an_error_after_send_is_unknown_not_refused(self):
        a, b = wallets()

        def transaction(h, inbound):  # the poll after `send` (Wallet.transaction -> call) answers with an HTTP error
            raise gl.Refused("HTTP 500")
        a.transaction = transaction
        self.assertEqual(lab_circulate.circulate(a, b, self.log, OK)["action"], "unknown")
        self.assertEqual(len(gl.read_log(self.log)), 1)  # the reservation stays

    def test_an_error_after_send_across_utc_midnight_is_still_unknown(self):
        a, b = wallets()
        days = iter(["2026-11-07", "2026-11-08"] + ["2026-11-08"] * 20)  # the day moves on while pay() runs

        def transaction(h, inbound):
            raise gl.Refused("HTTP 500")
        a.transaction = transaction
        orig = gl.utc_day
        gl.utc_day = lambda now=None: next(days)
        try:
            self.assertEqual(lab_circulate.circulate(a, b, self.log, OK)["action"], "unknown")
        finally:
            gl.utc_day = orig

    def test_an_unknown_outcome_is_not_reported_as_refused(self):
        a, b = wallets()
        b.call = lambda c, timeout=30, **k: (_ for _ in ()).throw(gl.Unknown("no answer"))
        self.assertEqual(lab_circulate.circulate(a, b, self.log, OK)["action"], "unknown")


if __name__ == "__main__":
    unittest.main()
