"""The Goose lab's one real Lightning payment (scripts/goose_lightning.py, loop#349) against fake lab wallets: what the
guard refuses, what a payment returns, and, where the Goose tools are installed, a session with --pay judged by step 06.

Run: python3 -m unittest tests.test_goose_pay -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import goose_lightning as gl  # noqa: E402
from fake_wallet import FakeLab  # noqa: E402

TOOLS = Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache" / "evidence-network-loop"))
PY = TOOLS / "goose" / "venv" / "bin" / "python"


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "lightning" / "payments.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def wallets(self, lab):
        return gl.Wallet(lab.ports["lab-1"], "lab-1"), gl.Wallet(lab.ports["lab-2"], "lab-2")

    def test_a_payment_returns_the_wallets_numbers_and_never_the_preimage(self):
        with FakeLab() as lab:
            rec = gl.pay(*self.wallets(lab), self.log, wait=5)
            pre = lab.invoices[rec["payment_hash"]][0]
        self.assertEqual(rec["payer"], {"status": "succeeded", "amount_msat": 1000, "fee_msat": 0, "preimage_check": "verified"})
        self.assertEqual(rec["payee"], {"status": "succeeded", "amount_msat": 995, "fee_msat": 5})
        self.assertNotIn("payments_recorded", rec)  # the count comes after the Goose deal closes
        self.assertEqual(gl.mark_settled(self.log, rec["payment_hash"], "deal-x", "lab-1>lab-2"), 1)
        self.assertEqual(gl.read_log(self.log)[0]["status"], "settled")
        self.assertNotIn(pre, json.dumps(rec) + self.log.read_text())

    def test_a_payment_over_the_per_payment_cap_is_refused_and_sends_nothing(self):
        with FakeLab() as lab:
            with self.assertRaisesRegex(gl.Refused, "3000"):
                gl.pay(*self.wallets(lab), self.log, amount_msat=3001, wait=5)
            self.assertEqual(lab.sent, 0)

    def test_the_day_cap_counts_the_log_and_the_wallet(self):
        with FakeLab() as lab:
            p = self.wallets(lab)
            for _ in range(9):
                gl.pay(*p, self.log, wait=5)
            with self.assertRaisesRegex(gl.Refused, "9000"):
                gl.pay(*p, self.log, wait=5)
            self.assertEqual(lab.sent, 9)
        with FakeLab(spent_today=8500) as lab:  # the wallet's own count, with an empty log
            with self.assertRaisesRegex(gl.Refused, "spent today"):
                gl.pay(*self.wallets(lab), Path(self.tmp.name) / "other.jsonl", wait=5)

    def test_a_pending_payment_or_a_short_balance_means_the_lab_is_not_idle(self):
        with FakeLab(pending=True) as lab:
            with self.assertRaisesRegex(gl.Refused, "pending"):
                gl.pay(*self.wallets(lab), self.log, wait=5)
        with FakeLab(spendable=1500) as lab:
            with self.assertRaisesRegex(gl.Refused, "spendable"):
                gl.pay(*self.wallets(lab), self.log, wait=5)

    def test_a_failed_payment_releases_its_reservation(self):
        with FakeLab(send_result="failed") as lab:
            with self.assertRaisesRegex(gl.Refused, "failed"):
                gl.pay(*self.wallets(lab), self.log, wait=5)
        self.assertEqual([e["status"] for e in gl.read_log(self.log)], ["failed"])
        self.assertEqual(gl.reserved_today(gl.read_log(self.log), "lab-1>lab-2", gl.utc_day()), 0)

    def test_an_unknown_outcome_keeps_its_reservation(self):
        with FakeLab(send_result="pending") as lab:
            with self.assertRaises(gl.Unknown):
                gl.pay(*self.wallets(lab), self.log, wait=2)
        self.assertEqual(gl.reserved_today(gl.read_log(self.log), "lab-1>lab-2", gl.utc_day()), 1000)

    def test_an_http_error_on_send_keeps_the_reservation(self):
        for code in (500, 402):
            with FakeLab(send_http=code) as lab:
                log = Path(self.tmp.name) / f"{code}.jsonl"
                with self.assertRaises(gl.Unknown):
                    gl.pay(*self.wallets(lab), log, wait=2)
            self.assertEqual(gl.reserved_today(gl.read_log(log), "lab-1>lab-2", gl.utc_day()), 1000)

    def test_errors_never_carry_an_invoice_or_a_hash(self):
        text = gl.clean("bad lnbc10n1pabcdefghijklmnopqrstuvwxyz and " + "a" * 64)
        self.assertNotIn("lnbc", text)
        self.assertNotIn("a" * 64, text)


class DriverTest(unittest.TestCase):
    def test_dry_run_of_pay_prints_the_paid_session_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as lab:
            r = subprocess.run([str(ROOT / "lab" / "goose.sh"), "--dry-run", "pay"], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, env={**os.environ, "LAB_DIR": lab, "LOOP_TOOLS": lab})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("goose_live.py session", r.stdout)
            self.assertIn("--pay --lightning-log", r.stdout)
            self.assertEqual(sorted(p.name for p in Path(lab).iterdir()), [])


@unittest.skipUnless(PY.is_file(), "run steps/goose.setup.sh to install the Goose tools")
class PaidSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.lab = Path(cls.tmp.name)
        env = {**os.environ, "LAB_DIR": str(cls.lab), "LOOP_TOOLS": str(TOOLS)}
        r = subprocess.run([str(ROOT / "lab" / "goose.sh"), "init"], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        cls.ids = json.loads((cls.lab / "goose" / "ids.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drive(self, *args):
        r = subprocess.run([str(PY), "-B", str(ROOT / "scripts" / "goose_live.py"), *args], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, env={**os.environ, "LOOP_GOOSE_SRC": str(TOOLS / "goose" / "src")})
        self.assertEqual(r.returncode, 0, r.stderr[-2500:])

    def step06(self, pop_dir, name):
        out = self.lab / name / "06-settle"
        r = subprocess.run([str(ROOT / "steps" / "06-settle"), "--population", str(pop_dir), "--out", str(out)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[:1500])
        return json.loads((out / "finding.json").read_text()), out

    def test_a_paid_session_passes_step_06_and_a_tampered_record_does_not(self):
        lab = self.lab / "goose"
        pub = self.lab / "published"
        self.drive("advertise", "--homes", str(lab / "homes"), "--out", str(pub))
        with FakeLab() as fake:
            work = self.lab / "work"
            self.drive("session", "--homes", str(lab / "homes"), "--keys", str(lab / "keys"), "--ids", str(lab / "ids.json"),
                       "--published", str(pub), "--work", str(work), "--pay", "--payer-port", str(fake.ports["lab-1"]),
                       "--payee-port", str(fake.ports["lab-2"]), "--lightning-log", str(lab / "lightning" / "payments.jsonl"))
            self.assertEqual(fake.sent, 1)
            pre = next(iter(fake.invoices.values()))[0]
        pop_dir = work / "population"
        pop = json.loads((pop_dir / "population.json").read_text())
        self.assertEqual(pop["goose"]["steps"], ["01", "03", "07", "06"])
        found, out = self.step06(pop_dir, "ok")
        self.assertEqual(found["result"], "PASS", found["detail"])
        self.assertIn("lab wallet lab-1 to lab-2", found["detail"])
        for f in out.iterdir():  # nothing the step wrote holds the preimage
            self.assertNotIn(pre, f.read_text())
        # each tamper is a FAIL naming the check
        ln = json.loads((pop_dir / "step06" / "lightning.json").read_text())
        for what, mutate, check in (
                ("finality", lambda d: d["payer"].update(preimage_check="mismatch"), "finality"),
                ("fee", lambda d: d["payee"].update(fee_msat=0), "rail_fee"),
                ("match", lambda d: d["payee"].update(amount_msat=900), "exchange_match"),
                ("hash", lambda d: d.update(payment_hash="1" * 64), "reference"),
                ("count", lambda d: d.update(payments_recorded=2), "ledger_count")):
            d = json.loads(json.dumps(ln))
            mutate(d)
            (pop_dir / "step06" / "lightning.json").write_text(json.dumps(d))
            found, _ = self.step06(pop_dir, f"bad-{what}")
            self.assertEqual(found["result"], "FAIL", what)
            self.assertIn(check, found["detail"], what)
        (pop_dir / "step06" / "lightning.json").write_text(json.dumps(ln))

    def test_a_guard_refusal_runs_the_session_without_06_and_says_why(self):
        lab = self.lab / "goose"
        pub = self.lab / "published-refused"
        self.drive("advertise", "--homes", str(lab / "homes"), "--out", str(pub))
        with FakeLab(pending=True) as fake:
            work = self.lab / "work-refused"
            r = subprocess.run([str(PY), "-B", str(ROOT / "scripts" / "goose_live.py"), "session", "--homes", str(lab / "homes"),
                                "--keys", str(lab / "keys"), "--ids", str(lab / "ids.json"), "--published", str(pub),
                                "--work", str(work), "--pay", "--payer-port", str(fake.ports["lab-1"]),
                                "--payee-port", str(fake.ports["lab-2"]), "--lightning-log", str(lab / "lightning" / "refused.jsonl")],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               env={**os.environ, "LOOP_GOOSE_SRC": str(TOOLS / "goose" / "src")})
            self.assertEqual(r.returncode, 0, r.stderr[-2500:])
            self.assertEqual(fake.sent, 0)
        self.assertIn("goose_live: payment refused: ", r.stderr)
        pop = json.loads((work / "population" / "population.json").read_text())
        self.assertEqual(pop["goose"]["steps"], ["01", "03", "07"])
        self.assertNotIn("lightning", pop["goose"])


if __name__ == "__main__":
    unittest.main()
