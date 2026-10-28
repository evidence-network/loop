"""steps/finding.py: a finding is refused unless it says what it checked and on which bytes."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "steps"))
import finding  # noqa: E402


class FindingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.step = self.out / "01-seal"
        self.step.mkdir()
        (self.step / "capsule.json").write_text("{}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, **kw):
        args = dict(result="PASS", population="rehearsal", asserts="the capsule verifies in a second language",
                    pinned=["capsulectl"], proof=["capsule.json"], detail="", started=finding.now())
        args.update(kw)
        return finding.write(self.step, **args)

    def test_a_good_finding_is_written_and_checks_clean(self):
        r = self.write()
        self.assertEqual(r["pinned"]["capsulectl"]["ref"], finding.pins()["capsulectl"][1])
        self.assertEqual(finding.check(self.out), [])

    def test_a_pass_without_proof_is_refused(self):
        with self.assertRaises(ValueError):
            self.write(proof=[])

    def test_a_not_checked_without_saying_why_is_refused(self):
        with self.assertRaises(ValueError):
            self.write(result="NOT_CHECKED")
        self.write(result="NOT_CHECKED", not_checked=[{"what": "receipt", "why": "no verifier", "issue": "D-14"}])

    def test_an_unpinned_component_is_refused(self):
        with self.assertRaises(KeyError):
            self.write(pinned=["not-in-the-lock"])
        self.write()
        r = json.loads((self.step / "finding.json").read_text())
        r["pinned"]["capsulectl"]["ref"] = "main"
        (self.step / "finding.json").write_text(json.dumps(r))
        self.assertTrue(any("versions.lock" in e for e in finding.check(self.out)))

    def test_proof_bytes_that_changed_after_the_finding_are_refused(self):
        self.write()
        (self.step / "capsule.json").write_text('{"altered": true}\n')
        self.assertTrue(any("sha256" in e for e in finding.check(self.out)))

    def test_a_step_with_no_finding_is_refused(self):
        (self.out / "02-book").mkdir()
        self.write()
        self.assertTrue(any("02-book: no finding.json" in e for e in finding.check(self.out)))


if __name__ == "__main__":
    unittest.main()
