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

    # A NOT_CHECKED finding may not word itself as checked.

    NC = [{"what": "receipt", "why": "no verifier", "issue": "D-14"}]

    def test_a_not_checked_that_claims_a_check_is_refused(self):
        for field, text in (("detail", "the receipt was checked"), ("detail", "Witnessed by two nodes"),
                            ("asserts", "the capsule is verified in a second language"),
                            ("detail", "not checked: the key; the bytes were verified")):
            with self.subTest(field=field, text=text), self.assertRaises(ValueError) as e:
                self.write(result="NOT_CHECKED", not_checked=self.NC, **{field: text})
            self.assertIn(f"a NOT_CHECKED {field} may not claim", str(e.exception))

    def test_a_not_checked_may_say_what_was_not_checked(self):
        for text in ("the receipt was not checked", "the key is not verified", "an unverified receipt",
                     "the witness part is unchecked", "the signature could not be verified",
                     "the witness key was never verified", "NOT_CHECKED on D-14", "it has not been checked",
                     "the receipt was not witnessed"):
            with self.subTest(text=text):
                self.write(result="NOT_CHECKED", not_checked=self.NC, detail=text, asserts=f"x; {text}")
                self.assertEqual(finding.check(self.out), [])

    def test_a_pass_or_fail_may_say_checked(self):
        self.write(detail="checked and verified by the Go verifier")
        self.write(result="FAIL", detail="the signature was checked and did not hold")

    # A PASS covers its assertion fully.

    def test_a_pass_with_not_checked_entries_is_refused(self):
        with self.assertRaises(ValueError) as e:
            self.write(not_checked=self.NC)
        self.assertIn("a PASS covers its assertion fully", str(e.exception))
        self.write(detail="Outside this step's assertion: the receipt is not checked.")
        self.write(result="FAIL", not_checked=self.NC)

    # Proof paths stay inside the step directory.

    def test_absolute_or_escaping_proof_paths_are_refused(self):
        self.write()
        r = json.loads((self.step / "finding.json").read_text())
        good = r["proof"][0]
        for path in (str(self.step / "capsule.json"), "/capsule.json", "../01-seal/capsule.json",
                     "sub/../capsule.json"):
            with self.subTest(path=path):
                r["proof"] = [{**good, "path": path}]
                (self.step / "finding.json").write_text(json.dumps(r))
                errs = finding.check(self.out)
                self.assertTrue(any("must be a relative path inside the step directory" in e for e in errs), errs)
        with self.assertRaises(ValueError):
            self.write(proof=[str(self.step / "capsule.json")])


if __name__ == "__main__":
    unittest.main()
