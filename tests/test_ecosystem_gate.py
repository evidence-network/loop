"""The ecosystem gate: a step that was PASS may not go red on a pin bump (loop#149)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "steps"))
import ecosystem_gate as gate  # noqa: E402
import finding  # noqa: E402

LOCK = ROOT / "versions.lock"


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._root = gate.ROOT
        gate.ROOT = self.dir
        self.baseline = self.dir / "baseline" / "live.json"

    def tearDown(self):
        gate.ROOT = self._root
        self.tmp.cleanup()

    def run_dir(self, name, results, population="live"):
        """A findings dir with one step per {NN: result}."""
        out = self.dir / "findings" / name
        for nn, result in results.items():
            d = out / f"{nn}-step"
            d.mkdir(parents=True)
            kw = {}
            if result == "PASS":
                (d / "p.txt").write_text("proof\n")
                kw["proof"] = ["p.txt"]
            else:
                kw["proof"] = []
            if result == "NOT_CHECKED":
                kw["not_checked"] = [{"what": "w", "why": "y", "issue": "o/r#1"}]
            finding.write(d, result=result, population=population, asserts="a fact holds",
                          pinned=["capsulectl"], detail="d", started=finding.now(), **kw)
        return out

    def update(self, name, results, eco="mesh", population="live"):
        return gate.cmd_update(eco, self.run_dir(name, results, population), self.baseline)

    def test_update_writes_the_baseline_and_verify_accepts_it(self):
        self.assertEqual(self.update("a", {"01": "PASS", "02": "NOT_CHECKED"}), [])
        doc = json.loads(self.baseline.read_text())
        self.assertEqual({k: v["result"] for k, v in doc["ecosystems"]["mesh"]["steps"].items()},
                         {"01": "PASS", "02": "NOT_CHECKED"})
        self.assertTrue(doc["not_checked_is_regression"])
        self.assertEqual(gate.cmd_verify(self.baseline, LOCK), [])

    def test_planted_pass_to_fail_is_refused(self):
        self.update("a", {"01": "PASS", "02": "PASS"})
        errs = self.update("b", {"01": "PASS", "02": "FAIL"})
        self.assertEqual(errs, ["mesh step 02: PASS -> FAIL"])
        self.assertIn("a", json.loads(self.baseline.read_text())["ecosystems"]["mesh"]["findings"])

    def test_pass_to_not_checked_is_refused_with_the_switch_on_and_allowed_off(self):
        self.update("a", {"01": "PASS"})
        self.assertEqual(self.update("b", {"01": "NOT_CHECKED"}), ["mesh step 01: PASS -> NOT_CHECKED"])
        doc = json.loads(self.baseline.read_text())
        doc["not_checked_is_regression"] = False
        gate.write(self.baseline, doc)
        self.assertEqual(self.update("c", {"01": "NOT_CHECKED"}), [])

    def test_missing_step_is_a_regression(self):
        self.update("a", {"01": "PASS", "02": "PASS"})
        self.assertEqual(self.update("b", {"01": "PASS"}), ["mesh step 02: PASS -> missing"])

    def test_progress_and_unrelated_steps_pass(self):
        self.update("a", {"01": "PASS", "02": "FAIL", "03": "NOT_CHECKED"})
        self.assertEqual(self.update("b", {"01": "PASS", "02": "PASS", "03": "PASS", "04": "FAIL"}), [])

    def test_check_compares_base_and_pull_request_baselines(self):
        self.update("a", {"01": "PASS", "02": "PASS"})
        old = self.dir / "old.json"
        old.write_text(self.baseline.read_text())
        new = json.loads(old.read_text())
        new["ecosystems"]["mesh"]["steps"]["02"]["result"] = "FAIL"
        new["not_checked_is_regression"] = False  # a pull request cannot loosen its own gate
        newp = self.dir / "new.json"
        newp.write_text(json.dumps(new))
        self.assertEqual(gate.cmd_check(old, newp), ["mesh step 02: PASS -> FAIL"])
        new["ecosystems"]["mesh"]["steps"]["02"]["result"] = "NOT_CHECKED"
        newp.write_text(json.dumps(new))
        self.assertEqual(gate.cmd_check(old, newp), ["mesh step 02: PASS -> NOT_CHECKED"])
        del new["ecosystems"]["mesh"]
        newp.write_text(json.dumps(new))
        self.assertEqual(gate.cmd_check(old, newp), ["mesh: ecosystem dropped from the baseline"])

    def test_verify_refuses_a_hand_edited_baseline(self):
        self.update("a", {"01": "FAIL"})
        doc = json.loads(self.baseline.read_text())
        doc["ecosystems"]["mesh"]["steps"]["01"]["result"] = "PASS"
        gate.write(self.baseline, doc)
        self.assertEqual(gate.cmd_verify(self.baseline, LOCK),
                         ["mesh: steps differ from the findings in findings/a"])

    def test_verify_refuses_a_baseline_from_other_pins_when_the_lock_changed(self):
        self.update("a", {"01": "PASS"})
        other = self.dir / "versions.lock"
        other.write_text(LOCK.read_text().replace("capsulectl action-state-group/evidencebook v0.8.1",
                                                  "capsulectl action-state-group/evidencebook v0.8.2"))
        self.assertEqual(len(gate.cmd_verify(self.baseline, other)), 1)

    def test_verify_refuses_a_findings_record_changed_after_the_fact(self):
        self.update("a", {"01": "PASS"})
        f = self.dir / "findings" / "a" / "01-step" / "finding.json"
        r = json.loads(f.read_text())
        r["proof"] = []
        f.write_text(json.dumps(r, indent=2, sort_keys=True) + "\n")
        doc = json.loads(self.baseline.read_text())
        doc["ecosystems"]["mesh"]["steps"]["01"]["finding_sha256"] = finding.sha256(f)
        gate.write(self.baseline, doc)
        errs = gate.cmd_verify(self.baseline, LOCK)
        self.assertEqual(len(errs), 1)
        self.assertIn("a PASS needs the proof bytes", errs[0])

    def test_two_directories_for_one_step_are_refused(self):
        out = self.run_dir("a", {"01": "FAIL"})
        other = out / "01-other"
        other.mkdir()
        finding.write(other, result="PASS", population="live", asserts="a fact holds", pinned=[],
                      proof=["p.txt"] if (other / "p.txt").write_text("x") else [], detail="d", started=finding.now())
        errs = gate.cmd_update("mesh", out, self.baseline)
        self.assertEqual(errs, ["01-step: step 01 has more than one directory"])

    def test_check_refuses_new_pins_with_the_old_findings(self):
        self.update("a", {"01": "PASS"})
        old = self.dir / "old.json"
        old.write_text(self.baseline.read_text())
        new = json.loads(old.read_text())
        new["ecosystems"]["mesh"]["pin_set_sha256"] = "0" * 64
        newp = self.dir / "new.json"
        newp.write_text(json.dumps(new))
        self.assertEqual(len(gate.cmd_check(old, newp)), 1)
        new["ecosystems"]["mesh"]["findings"] = "findings/b"
        newp.write_text(json.dumps(new))
        self.assertEqual(gate.cmd_check(old, newp), [])

    def test_check_allows_pass_to_not_checked_when_the_base_switch_is_off(self):
        self.update("a", {"01": "PASS"})
        doc = json.loads(self.baseline.read_text())
        doc["not_checked_is_regression"] = False
        old = self.dir / "old.json"
        old.write_text(json.dumps(doc))
        doc["ecosystems"]["mesh"]["steps"]["01"]["result"] = "NOT_CHECKED"
        newp = self.dir / "new.json"
        newp.write_text(json.dumps(doc))
        self.assertEqual(gate.cmd_check(old, newp), [])

    def test_exit_codes(self):
        self.update("a", {"01": "PASS"})
        self.assertEqual(gate.main(["g", "verify", "--baseline", str(self.baseline), "--lock", str(LOCK)]), 0)
        self.assertEqual(gate.main(["g", "verify", "--baseline", str(self.dir / "nope.json"), "--lock", str(LOCK)]), 1)

    def test_pin_set_digest_ignores_comments(self):
        other = self.dir / "versions.lock"
        other.write_text("# a new comment\n" + LOCK.read_text())
        self.assertEqual(gate.pin_set_sha256(other), gate.pin_set_sha256(LOCK))

    def test_update_refuses_rehearsal_and_malformed_findings(self):
        self.assertTrue(self.update("r", {"01": "PASS"}, population="rehearsal"))
        out = self.run_dir("m", {"01": "PASS"})
        (out / "01-step" / "p.txt").write_text("changed\n")
        self.assertTrue(gate.cmd_update("mesh", out, self.baseline))
        self.assertFalse(self.baseline.exists())

    def test_committed_mesh_baseline_matches_its_findings(self):
        gate.ROOT = self._root
        self.assertEqual(gate.cmd_verify(gate.BASELINE, LOCK), [])


if __name__ == "__main__":
    unittest.main()
