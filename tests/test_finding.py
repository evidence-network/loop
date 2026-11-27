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
        self.write(result="NOT_CHECKED", not_checked=[{"what": "receipt", "why": "no verifier", "issue": "example-org/example#1"}])

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

    NC = [{"what": "receipt", "why": "no verifier", "issue": "example-org/example#1"}]

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
                     "the witness key was never verified", "NOT_CHECKED on example-org/example#1", "it has not been checked",
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
        self.write(detail="Outside this step's assertion: the receipt is not checked: example-org/example#1.")
        self.write(result="FAIL", not_checked=self.NC)

    # A gap outside the assertion is one sentence ending with the issue, owner/repo#N.

    def test_a_gap_sentence_ends_with_its_issue(self):
        gap = finding.outside("the binding is not verified; no verifier yet", "example-org/example#12")
        self.assertEqual(gap, "Outside this step's assertion: the binding is not verified; no verifier yet: "
                              "example-org/example#12.")
        detail = f"all held. {gap} {finding.outside('the staple', 'other-org/other#3')}"
        self.write(detail=detail)
        self.assertEqual(finding.gaps(detail), [("the binding is not verified; no verifier yet",
                                                 "example-org/example#12"), ("the staple", "other-org/other#3")])
        with self.assertRaises(ValueError):
            finding.outside("the staple", "no issue yet")

    def test_a_gap_without_its_issue_is_refused(self):
        for detail in ("Outside this step's assertion: the receipt is not checked.",
                       "Outside this step's assertion: the staple (no issue yet).",
                       "Outside this step's assertion: the staple: example-org/example#1"):
            with self.subTest(detail=detail), self.assertRaises(ValueError) as e:
                self.write(detail=detail)
            self.assertIn("each gap in detail is one sentence", str(e.exception))

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


class CheckoutRefTest(unittest.TestCase):
    """finding.checkout_ref: the revision a Goose run records, compared with the goose-g1 pin (loop#501)."""

    def setUp(self):
        import subprocess
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "src"
        self.src.mkdir()

        def git(*a):
            return subprocess.run(["git", "-C", str(self.src), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                   *a], check=True, capture_output=True, text=True).stdout.strip()
        self.git = git
        git("init", "-q")
        git("commit", "-q", "--allow-empty", "-m", "one")
        self.one = git("rev-parse", "HEAD")
        git("tag", "-a", "-m", "release", "capsulectl/v0.1.1")  # annotated, as the release tag is
        git("commit", "-q", "--allow-empty", "-m", "two")
        self.two = git("rev-parse", "HEAD")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def lock(self, ref):
        path = self.tmp / "versions.lock"
        path.write_text(f"goose-g1 action-state-group/evidencebook {ref}\n")
        return path

    def test_tag_pin_at_its_tag_reads_as_the_tag(self):
        self.git("checkout", "-q", self.one)
        self.assertEqual(finding.checkout_ref("goose-g1", self.src, self.lock("capsulectl/v0.1.1")), "capsulectl/v0.1.1")

    def test_tag_pin_elsewhere_reads_as_the_commit(self):
        self.assertEqual(finding.checkout_ref("goose-g1", self.src, self.lock("capsulectl/v0.1.1")), self.two)

    def test_tag_pin_without_the_tag_reads_as_the_commit(self):
        # A checkout installed before the tag pin (a SHA fetch keeps no tags) never reads as the release.
        self.git("tag", "-d", "capsulectl/v0.1.1")
        self.git("checkout", "-q", self.one)
        self.assertEqual(finding.checkout_ref("goose-g1", self.src, self.lock("capsulectl/v0.1.1")), self.one)

    def test_sha_pin_reads_as_the_commit(self):
        self.git("checkout", "-q", self.one)
        self.assertEqual(finding.checkout_ref("goose-g1", self.src, self.lock(self.one)), self.one)

    def test_no_checkout_is_empty(self):
        self.assertEqual(finding.checkout_ref("goose-g1", self.tmp / "none", self.lock("capsulectl/v0.1.1")), "")


if __name__ == "__main__":
    unittest.main()
