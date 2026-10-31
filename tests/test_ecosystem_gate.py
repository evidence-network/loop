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

    def run_dir(self, name, results, population="live", detail="d", under="findings"):
        """A findings dir with one step per {NN: result}."""
        out = self.dir / under / name
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
                          pinned=["capsulectl"], detail=detail, started=finding.now(), **kw)
        return out

    def update(self, name, results, eco="mesh", population="live"):
        return gate.cmd_update(eco, self.run_dir(name, results, population), self.baseline)

    def test_update_writes_the_baseline_and_verify_accepts_it(self):
        self.assertEqual(self.update("a", {"01": "PASS", "02": "NOT_CHECKED"}), [])
        doc = json.loads(self.baseline.read_text())
        self.assertEqual({k: v["result"] for k, v in doc["ecosystems"]["mesh"]["steps"].items()},
                         {"01": "PASS", "02": "NOT_CHECKED"})
        self.assertTrue(doc["not_checked_is_regression"])
        self.assertEqual(gate.cmd_verify(self.baseline), [])

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
        self.assertEqual(self.update("c", {"01": "NOT_CHECKED"}), [])  # compare() itself still honours the switch

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
        self.assertEqual(gate.cmd_verify(self.baseline),
                         ["mesh: steps differ from the findings in findings/a"])

    def test_verify_refuses_a_findings_record_changed_after_the_fact(self):
        self.update("a", {"01": "PASS"})
        f = self.dir / "findings" / "a" / "01-step" / "finding.json"
        r = json.loads(f.read_text())
        r["proof"] = []
        f.write_text(json.dumps(r, indent=2, sort_keys=True) + "\n")
        doc = json.loads(self.baseline.read_text())
        doc["ecosystems"]["mesh"]["steps"]["01"]["finding_sha256"] = finding.sha256(f)
        gate.write(self.baseline, doc)
        errs = gate.cmd_verify(self.baseline)
        self.assertEqual(len(errs), 1)
        self.assertIn("a PASS needs the proof bytes", errs[0])

    def test_verify_refuses_a_run_made_under_other_pins(self):
        self.update("a", {"01": "PASS"})
        f = self.dir / "findings" / "a" / "01-step" / "finding.json"
        r = json.loads(f.read_text())
        r["pinned"]["capsulectl"]["ref"] = "v0.0.1"
        f.write_text(json.dumps(r, indent=2, sort_keys=True) + "\n")
        doc = json.loads(self.baseline.read_text())
        doc["ecosystems"]["mesh"]["steps"]["01"]["finding_sha256"] = finding.sha256(f)
        gate.write(self.baseline, doc)
        self.assertEqual(gate.cmd_verify(self.baseline), ["mesh 01-step: pinned capsulectl is not the versions.lock entry"])

    def test_verify_refuses_an_aliased_findings_path(self):
        self.update("a", {"01": "PASS"})
        doc = json.loads(self.baseline.read_text())
        doc["ecosystems"]["mesh"]["findings"] = "findings/./a"
        gate.write(self.baseline, doc)
        self.assertEqual(gate.cmd_verify(self.baseline),
                         ["mesh: findings is 'findings/./a', write it as 'findings/a'"])

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
        new["ecosystems"]["mesh"]["pins"]["capsulectl"] = "x y"
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

    def test_check_refuses_turning_the_switch_off(self):
        self.update("a", {"01": "PASS"})
        old = self.dir / "old.json"
        old.write_text(self.baseline.read_text())
        new = json.loads(old.read_text())
        new["not_checked_is_regression"] = False
        newp = self.dir / "new.json"
        newp.write_text(json.dumps(new))
        self.assertEqual(len(gate.cmd_check(old, newp)), 1)

    def test_verify_requires_the_switch_on(self):
        self.update("a", {"01": "PASS"})
        doc = json.loads(self.baseline.read_text())
        doc["not_checked_is_regression"] = False
        gate.write(self.baseline, doc)
        self.assertEqual(len(gate.cmd_verify(self.baseline)), 1)

    def test_update_reads_a_missing_ecosystem_in_run_json_as_mesh(self):
        out = self.run_dir("a", {"01": "PASS"})
        (out / "run.json").write_text(json.dumps({"stage": "1", "population": "live", "started": "t"}))
        self.assertEqual(gate.cmd_update("mesh", out, self.baseline), [])
        self.assertEqual(len(gate.cmd_update("muse", out, self.baseline)), 1)
        (out / "run.json").write_text(json.dumps({"ecosystem": "muse", "stage": "1", "population": "live",
                                                  "started": "t"}))
        self.assertEqual(len(gate.cmd_update("mesh", out, self.baseline)), 1)
        self.assertEqual(gate.cmd_update("muse", out, self.baseline), [])

    def test_exit_codes(self):
        self.update("a", {"01": "PASS"})
        self.assertEqual(gate.main(["g", "verify", "--baseline", str(self.baseline)]), 0)
        self.assertEqual(gate.main(["g", "verify", "--baseline", str(self.dir / "nope.json")]), 1)

    def test_a_pin_added_since_is_not_a_bump_but_a_moved_or_removed_one_is(self):
        recorded = {"a": "r/a v1", "b": "r/b v1"}
        self.assertEqual(gate.moved_pins(recorded, {**recorded, "c": "r/c v1"}), [])
        self.assertEqual(gate.moved_pins(recorded, {**recorded, "b": "r/b v2"}), ["b"])
        self.assertEqual(gate.moved_pins(recorded, {"a": "r/a v1"}), ["b"])

    def test_verify_refuses_a_baseline_recording_other_pins(self):
        self.update("a", {"01": "PASS"})
        doc = json.loads(self.baseline.read_text())
        doc["ecosystems"]["mesh"]["pins"]["capsulectl"] = "x y"
        gate.write(self.baseline, doc)
        self.assertEqual(len(gate.cmd_verify(self.baseline)), 1)

    def test_pin_comments_are_not_pins(self):
        other = self.dir / "versions.lock"
        other.write_text("# a new comment\n" + LOCK.read_text())
        self.assertEqual(gate.lock_pins(other), gate.lock_pins(LOCK))

    def test_update_refuses_rehearsal_and_malformed_findings(self):
        self.assertTrue(self.update("r", {"01": "PASS"}, population="rehearsal"))
        out = self.run_dir("m", {"01": "PASS"})
        (out / "01-step" / "p.txt").write_text("changed\n")
        self.assertTrue(gate.cmd_update("mesh", out, self.baseline))
        self.assertFalse(self.baseline.exists())


    # --- An open-month preview of steps 10 and 11 is never a baseline result (evidence-network/loop#242) ---
    PREVIEW = finding.PREVIEW + ". run r, window 2026-10-01T00:00:00Z to 2026-11-01T00:00:00Z (the open month)"

    def preview_dir(self, name="p", results=None):
        """What steps 10 and 11 write under --preview-open-month: live PASS findings with the marker, in a preview/ dir."""
        return self.run_dir(name, results or {"10": "PASS", "11": "PASS"}, detail=self.PREVIEW, under="preview")

    def test_update_refuses_a_preview_though_it_is_live_and_all_pass(self):
        out = self.preview_dir()
        errs = gate.cmd_update("mesh", out, self.baseline)
        self.assertEqual(len(errs), 2, errs)
        self.assertTrue(all("an open-month preview, not a baseline result" in e for e in errs), errs)
        self.assertFalse(self.baseline.exists())

    def test_update_refuses_one_preview_step_among_baseline_steps(self):
        self.update("a", {"01": "PASS", "10": "NOT_CHECKED"})
        before = self.baseline.read_text()
        out = self.run_dir("b", {"01": "PASS", "10": "PASS"})
        f = json.loads((out / "10-step" / "finding.json").read_text())
        f["detail"] = self.PREVIEW
        gate.write(out / "10-step" / "finding.json", f)
        errs = gate.cmd_update("mesh", out, self.baseline)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("10-step: an open-month preview", errs[0])
        self.assertEqual(self.baseline.read_text(), before)

    def test_update_through_the_command_line_exits_1_on_a_preview(self):
        out = self.preview_dir()
        self.assertEqual(gate.main(["g", "update", "--ecosystem", "mesh", "--findings", str(out),
                                    "--baseline", str(self.baseline)]), 1)
        self.assertFalse(self.baseline.exists())

    def test_verify_refuses_a_baseline_whose_findings_became_a_preview(self):
        self.update("a", {"10": "PASS"})
        doc = json.loads(self.baseline.read_text())
        out = self.dir / doc["ecosystems"]["mesh"]["findings"]
        f = json.loads((out / "10-step" / "finding.json").read_text())
        f["detail"] = self.PREVIEW
        gate.write(out / "10-step" / "finding.json", f)
        self.assertTrue(any("an open-month preview" in e for e in gate.cmd_verify(self.baseline)))

    def test_the_gate_never_discovers_a_preview_directory(self):
        # The gate reads the findings directory it is handed and the ones the baseline names; nothing else under the
        # root, a preview/ dir included, is listed or globbed, so even a malformed one beside them changes nothing.
        self.update("a", {"01": "PASS"})
        self.preview_dir("p")
        junk = self.dir / "preview" / "junk"
        junk.mkdir(parents=True)
        (junk / "not-a-step").write_text("x\n")
        self.assertEqual(gate.cmd_verify(self.baseline), [])
        doc = json.loads(self.baseline.read_text())
        self.assertEqual(doc["ecosystems"]["mesh"]["findings"], "findings/a")
        old = self.dir / "old.json"
        old.write_text(self.baseline.read_text())
        self.assertEqual(gate.cmd_check(old, self.baseline), [])
        self.assertEqual(self.update("b", {"01": "PASS"}), [])
        self.assertEqual(json.loads(self.baseline.read_text())["ecosystems"]["mesh"]["findings"], "findings/b")


if __name__ == "__main__":
    unittest.main()
