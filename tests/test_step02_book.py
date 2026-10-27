"""steps/02-book: the record's place in the book is checked by evidencebook on the proof bytes.

The step's verdicts come from the driver steps/02-book.setup.sh installs (a
Rust build of the pinned evidencebook crate). Without it these tests SKIP,
saying so: they never pass without the verifier.
"""
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEP = ROOT / "steps" / "02-book"
POPULATION = ROOT / "tests" / "fixtures" / "step02" / "rehearsal"

sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("step02_book", str(STEP))
_spec = importlib.util.spec_from_loader("step02_book", _loader)
step = importlib.util.module_from_spec(_spec)
_loader.exec_module(step)

BIN = step.driver()
NO_DRIVER = ("SKIPPED LOUDLY: step 02's driver is not installed, so evidencebook's verifier cannot run; "
             "run steps/02-book.setup.sh (needs cargo and crates.io)")
if BIN is None:
    print(f"\ntest_step02_book: {NO_DRIVER}", file=sys.stderr)


@unittest.skipIf(BIN is None, NO_DRIVER)
class Step02BookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.findings = Path(self.tmp.name) / "findings"
        self.out = self.findings / "02-book"
        self.population, self.key, self.pub = step.node(POPULATION, "alice")
        self.book_id = f"{self.population}-alice"

    def tearDown(self):
        self.tmp.cleanup()

    def run_step(self):
        r = subprocess.run([sys.executable, "-B", str(STEP), "--population", str(POPULATION),
                            "--out", str(self.out)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((self.out / "finding.json").read_text())

    def checks(self):
        v = json.loads((self.out / "verify.json").read_text())
        return {c["check"]: c["ok"] for c in v["checks"]}

    def booked(self):
        """The proof files of Alice's book, made the way the step makes them."""
        started = finding.now()
        self.out.mkdir(parents=True)
        step.book(BIN, self.key, self.book_id, self.out, started)
        return started

    def decide(self, started):
        return step.decide(BIN, self.out, population=self.population, book_id=self.book_id,
                           pub=self.pub, started=started)

    def assert_clean(self):
        self.assertEqual(finding.check(self.findings), [])

    # (a) the rehearsal population
    def test_rehearsal_is_not_checked_for_the_witness_and_lists_what_held(self):
        r = self.run_step()
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertEqual(len(r["not_checked"]), 1)
        self.assertIn("D-14", r["not_checked"][0]["issue"])
        self.assertIn("witness", r["not_checked"][0]["what"])
        self.assertTrue(all(self.checks().values()), self.checks())
        for c in ("inclusion", "checkpoint_signature", "checkpoint_signer", "record_seal"):
            self.assertIn(c, r["detail"])
        self.assertIn("position 1", r["detail"])
        self.assertNotIn("witnessed", json.dumps(r).lower())
        self.assertEqual(set(r["pinned"]), {"evidencebook", "checkpointed-local-log", "evidence-request"})
        self.assertIn("checkpoint.json", [p["path"] for p in r["proof"]])
        self.assert_clean()  # (c)

    def test_a_second_run_into_the_same_directory_is_clean(self):
        self.run_step()
        self.assertEqual(self.run_step()["result"], "NOT_CHECKED")
        self.assert_clean()

    # (b) planted failures
    def test_a_record_altered_after_append_and_resealed_fails_inclusion(self):
        started = self.booked()
        rec = self.out / "record.json"
        r = json.loads(rec.read_text())
        r["header"]["statement"]["note"] = "rewritten after append"
        rec.write_text(json.dumps(r))
        # Its own producer re-seals it, so only the book can tell.
        subprocess.run([str(BIN), "reseal", "--key", str(self.key), "--record", str(rec)], check=True)
        f = self.decide(started)
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.checks(), {**{c: True for c in self.checks()}, "inclusion": False})
        self.assertIn("inclusion", f["detail"])
        self.assert_clean()

    def test_a_record_altered_after_append_fails(self):
        started = self.booked()
        rec = self.out / "record.json"
        r = json.loads(rec.read_text())
        r["header"]["committed_at"] = "2020-01-01T00:00:00Z"
        rec.write_text(json.dumps(r))
        self.assertEqual(self.decide(started)["result"], "FAIL")
        self.assertFalse(self.checks()["inclusion"])
        self.assert_clean()

    def test_a_checkpoint_from_a_different_book_fails(self):
        started = self.booked()
        # Another book with the same id and signer but other records: only the root differs.
        other = Path(self.tmp.name) / "other"
        step.book(BIN, self.key, self.book_id, other, started, note="another book's record")
        for p in ("checkpoint.json", "checkpoint.cose"):
            (self.out / p).write_bytes((other / p).read_bytes())
        f = self.decide(started)
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.checks(), {**{c: True for c in self.checks()}, "inclusion": False})
        self.assert_clean()

    def test_bobs_checkpoint_fails(self):
        started = self.booked()
        _, bob_key, _ = step.node(POPULATION, "bob")
        other = Path(self.tmp.name) / "bob"
        step.book(BIN, bob_key, "rehearsal-bob", other, started)
        (self.out / "checkpoint.json").write_bytes((other / "checkpoint.json").read_bytes())
        self.assertEqual(self.decide(started)["result"], "FAIL")
        c = self.checks()
        self.assertFalse(c["checkpoint_signer"])
        self.assertFalse(c["checkpoint_book"])
        self.assertFalse(c["inclusion"])
        self.assert_clean()

    def test_a_wrong_position_fails(self):
        started = self.booked()
        p = self.out / "placement.json"
        r = json.loads(p.read_text())
        r["position"] = 0
        p.write_text(json.dumps(r))
        self.assertEqual(self.decide(started)["result"], "FAIL")
        self.assertFalse(self.checks()["inclusion"])


class Step02WithoutDriverTest(unittest.TestCase):
    def test_no_driver_is_a_broken_step_not_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-B", str(STEP), "--population", str(POPULATION),
                                "--out", str(Path(tmp) / "02-book")], capture_output=True, text=True,
                               env={"PATH": "/usr/bin:/bin", "LOOP_STEP02_BOOK_BIN": str(Path(tmp) / "none")})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("02-book.setup.sh", r.stderr)
            self.assertFalse((Path(tmp) / "02-book" / "finding.json").exists())


if __name__ == "__main__":
    unittest.main()
