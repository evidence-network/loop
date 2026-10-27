"""Tests for the loop status page (status/status_page.py), built from loop findings.

Findings are written through steps/finding.py's write(), as a step writes them,
into findings/<date>-<population>/<NN>-<name>/. One test runs the real loop.sh.

Run: python3 -B -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "status"))
sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402
import status_page  # noqa: E402

# A step that passes: one proof file, written through steps/finding.py.
FAKE_STEP = """#!/usr/bin/env python3
import argparse, json, sys
sys.path.insert(0, {steps!r})
import finding
a = argparse.ArgumentParser()
a.add_argument("--population"); a.add_argument("--out")
args = a.parse_args()
started = finding.now()
population = json.load(open(args.population + "/population.json"))["population"]
open(args.out + "/proof.txt", "w").write("proof bytes\\n")
finding.write(args.out, result="PASS", population=population, asserts="a fake fact holds",
              pinned=[], proof=["proof.txt"], detail="", started=started)
"""
CLAIMS = re.compile(r"\b(checked|verified|witnessed)\b", re.I)
NAMES = {1: "seal", 2: "book", 3: "advertise", 12: "join"}


class Cells(HTMLParser):
    """Per step row: its population cells' text, split into the page's own words and quoted finding text."""

    def __init__(self):
        super().__init__()
        self.rows, self.row, self.pop, self.quote = {}, None, None, 0
        self.asserts = {}
        self.td = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr" and a.get("id", "").startswith("step-"):
            self.row = int(a["id"][5:])
            self.rows[self.row] = {}
            self.td = 0
        elif tag == "td" and self.row is not None:
            self.td += 1
            self.pop = a.get("data-population")
            if self.pop:
                self.rows[self.row][self.pop] = {"own": "", "quoted": "", "class": a.get("class", "")}
        elif tag == "q" and "said" in a.get("class", ""):
            self.quote += 1

    def handle_endtag(self, tag):
        if tag == "q" and self.quote:
            self.quote -= 1
        elif tag == "td":
            self.pop = None
        elif tag == "tr":
            self.row = None

    def handle_data(self, data):
        if self.row is None:
            return
        if self.pop:
            self.rows[self.row][self.pop]["quoted" if self.quote else "own"] += data
        elif self.td == 1:
            self.asserts[self.row] = self.asserts.get(self.row, "") + data


def parse(page):
    p = Cells()
    p.feed(page)
    return p


class StatusPageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.findings = self.dir / "findings"
        self.out = self.dir / "_site" / "status"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, n, result, population="rehearsal", date="2026-10-27", proof=("proof.bin",), detail="",
              not_checked=(), started="2026-10-27T02:00:00Z", name=None):
        d = self.findings / f"{date}-{population}" / f"{n:02d}-{name or NAMES.get(n, 'step')}"
        d.mkdir(parents=True, exist_ok=True)
        for p in proof:
            (d / p).write_bytes(b"proof bytes for step %d\n" % n)
        with mock.patch.object(finding, "now", return_value="2026-10-27T02:%02d:00Z" % n):
            finding.write(d, result=result, population=population, asserts=f"step {n} holds",
                          pinned=["capsulectl"], proof=list(proof), detail=detail, not_checked=not_checked,
                          started=started)
        return d

    def run_json(self, started, population="rehearsal", date="2026-10-27", run_population=None):
        d = self.findings / f"{date}-{population}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "run.json").write_text(json.dumps({"stage": "1", "population": run_population or population,
                                                "started": started}))

    def build(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = status_page.main(["status_page.py", str(self.findings), "--lock", str(ROOT / "versions.lock"),
                                     "--out", str(self.out)])
        self.err = err.getvalue()
        return code

    def page(self):
        self.assertEqual(self.build(), 0, self.err)
        self.html = (self.out / "index.html").read_text()
        return parse(self.html)

    def cell(self, n, population="rehearsal"):
        return self.page().rows[n][population]

    # The rows

    def test_twelve_rows_each_with_a_rehearsal_and_a_live_cell(self):
        self.findings.mkdir()
        rows = self.page().rows
        self.assertEqual(sorted(rows), list(range(1, 13)))
        for n, cells in rows.items():
            self.assertEqual(sorted(cells), ["live", "rehearsal"], n)

    def test_a_pass_with_its_proof_is_green_and_links_proof_and_finding(self):
        self.write(1, "PASS")
        c = self.cell(1)
        self.assertIn("s-green", c["class"])
        self.assertIn("PASS", c["own"])
        for name in ("proof.bin", "finding.json"):
            path = f"findings/2026-10-27-rehearsal/01-seal/{name}"
            self.assertIn(f'href="{path}"', self.html)
            self.assertTrue((self.out / path).is_file(), path)
        self.assertIn("<code>capsulectl</code> <code>v0.1.0-rc23</code>", self.html)

    def test_a_pass_whose_proof_bytes_changed_is_not_green(self):
        d = self.write(1, "PASS")
        (d / "proof.bin").write_bytes(b"other bytes\n")
        c = self.cell(1)
        self.assertIn("s-red", c["class"])
        self.assertIn("MALFORMED", c["own"])
        self.assertIn("does not match its sha256", c["quoted"])

    def test_a_pass_whose_proof_file_is_gone_is_not_green(self):
        d = self.write(1, "PASS")
        (d / "proof.bin").unlink()
        c = self.cell(1)
        self.assertIn("s-red", c["class"])
        self.assertIn("is not a file in the step directory", c["quoted"])

    def test_a_pass_with_no_proof_is_not_green(self):
        d = self.write(1, "FAIL", proof=())
        record = json.loads((d / "finding.json").read_text())
        record["result"] = "PASS"
        (d / "finding.json").write_text(json.dumps(record))
        c = self.cell(1)
        self.assertNotIn("s-green", c["class"])
        self.assertIn("a PASS needs the proof bytes", c["quoted"])

    def test_a_finding_from_the_other_population_is_not_green(self):
        d = self.write(1, "PASS", population="live")
        target = self.findings / "2026-10-27-rehearsal" / d.name
        target.parent.mkdir(parents=True)
        d.rename(target)
        c = self.cell(1)
        self.assertIn("s-red", c["class"])
        self.assertIn("population is live, this run is rehearsal", c["quoted"])

    def test_fail_is_red_with_its_detail(self):
        self.write(2, "FAIL", detail="the checkpoint signature does not hold")
        c = self.cell(2)
        self.assertIn("s-red", c["class"])
        self.assertIn("FAIL", c["own"])
        self.assertIn("the checkpoint signature does not hold", c["quoted"])

    def test_not_checked_shows_each_what_why_issue(self):
        nc = [{"what": "the witness receipt", "why": "no witness key is pinned", "issue": "https://example.invalid/i/1"},
              {"what": "the second language", "why": "no release yet", "issue": "Mesh-LLM/capsules-plugin#49"}]
        self.write(2, "NOT_CHECKED", not_checked=nc)
        c = self.cell(2)
        self.assertIn("s-NOT_CHECKED", c["class"])
        self.assertIn("NOT_CHECKED", c["own"])
        for item in nc:
            self.assertIn(item["what"], c["quoted"])
            self.assertIn(item["why"], c["quoted"])
        self.assertIn('href="https://example.invalid/i/1"', self.html)
        self.assertIn("<code>Mesh-LLM/capsules-plugin#49</code>", self.html)

    def test_a_step_with_no_finding_is_not_checked_with_its_owning_issue(self):
        self.write(1, "PASS")
        page = self.page()
        c = page.rows[7]["rehearsal"]
        self.assertIn("s-NOT_CHECKED", c["class"])
        self.assertIn("NOT_CHECKED", c["own"])
        self.assertIn("No finding for this step in 2026-10-27-rehearsal", c["own"])
        row = re.search(r'<tr class="row" id="step-07".*?</tr>', self.html).group(0)
        self.assertIn('href="https://github.com/Mesh-LLM/capsules-plugin/issues/49"', row)
        row = re.search(r'<tr class="row" id="step-08".*?</tr>', self.html).group(0)
        self.assertIn('href="https://github.com/action-state-group/evidencebook-skills/issues/53"', row)

    def test_no_run_of_a_population_is_not_checked(self):
        self.write(1, "PASS")
        c = self.cell(1, "live")
        self.assertIn("s-NOT_CHECKED", c["class"])
        self.assertIn("No live run yet.", c["own"])

    def test_the_newest_run_of_each_population_is_read(self):
        self.write(1, "PASS", date="2026-10-26")
        self.write(1, "FAIL", date="2026-10-27")
        self.write(1, "FAIL", population="live", date="2026-10-25")
        self.write(1, "PASS", population="live", date="2026-10-26")
        page = self.page()
        self.assertIn("s-red", page.rows[1]["rehearsal"]["class"])
        self.assertIn("s-green", page.rows[1]["live"]["class"])
        self.assertIn('href="findings/2026-10-27-rehearsal/"', self.html)
        self.assertIn('href="findings/2026-10-26-live/"', self.html)
        self.assertFalse((self.out / "findings" / "2026-10-26-rehearsal").exists())

    def test_rehearsal_and_live_stay_apart(self):
        self.write(1, "PASS")
        self.write(1, "FAIL", population="live")
        page = self.page()
        self.assertIn("s-green", page.rows[1]["rehearsal"]["class"])
        self.assertIn("s-red", page.rows[1]["live"]["class"])
        self.assertIn("2026-10-27-rehearsal", page.rows[1]["rehearsal"]["own"])
        self.assertIn("2026-10-27-live", page.rows[1]["live"]["own"])
        self.assertNotIn("total", self.html.lower())

    # The words

    def test_no_cell_claims_checked_verified_or_witnessed_unless_pass(self):
        nc = [{"what": "receipt", "why": "the witness key was never verified", "issue": "x#1"}]
        self.write(1, "PASS")
        self.write(2, "NOT_CHECKED", not_checked=nc, detail="nothing was checked")
        self.write(3, "FAIL", detail="the commitment was not verified")
        d = self.write(12, "PASS")
        (d / "proof.bin").write_bytes(b"changed\n")  # a PASS claim the check refuses
        self.write(1, "NOT_CHECKED", population="live", not_checked=nc)
        page = self.page()
        self.assertTrue(CLAIMS.search(page.rows[1]["rehearsal"]["own"]))  # a PASS cell may say it
        for n, cells in page.rows.items():
            self.assertFalse(CLAIMS.search(page.asserts[n]), (n, page.asserts[n]))
            for p, c in cells.items():
                if "s-green" not in c["class"]:
                    self.assertFalse(CLAIMS.search(c["own"]), (n, p, c["own"]))
        # What a finding says is quoted as the finding's, never as the page's claim.
        self.assertIn("the commitment was not verified", page.rows[3]["rehearsal"]["quoted"])
        self.assertIn('<q class="said">the commitment was not verified</q>', self.html)

    # Held rows

    def test_rows_4_5_6_9_are_held_for_the_flip(self):
        self.findings.mkdir()
        self.page()
        self.assertEqual(status_page.HELD_FOR_FLIP, {4, 5, 6, 9})
        held = set(int(n) for n in re.findall(r'<tr class="row" id="step-(\d\d)" data-held>', self.html))
        self.assertEqual(held, {4, 5, 6, 9})
        self.assertEqual(self.html.count(status_page.HELD_NOTE), 4)

    def test_the_flip_drops_the_held_rows_with_one_constant(self):
        self.write(1, "PASS")
        with mock.patch.object(status_page, "DROP_HELD", True):
            rows = self.page().rows
        self.assertEqual(sorted(rows), [1, 2, 3, 7, 8, 10, 11, 12])
        self.assertNotIn("data-held", self.html)

    # The badge

    def test_the_badge_shows_the_worst_row_of_each_population(self):
        self.write(1, "PASS")
        self.write(2, "FAIL")
        self.page()
        badge = (self.out / "badge.svg").read_text()
        self.assertIn('aria-label="loop: rehearsal red, live NOT_CHECKED"', badge)
        self.assertEqual(re.findall(r">([^<>]+)</text>", badge), ["loop", "rehearsal red", "live NOT_CHECKED"])
        for p in ("rehearsal", "live"):
            rs = status_page.rows({q: status_page.read_run(q, d) for q, d in
                                   status_page.latest_runs(self.findings).items()})
            self.assertEqual(status_page.worst(rs, p), {"rehearsal": "red", "live": "NOT_CHECKED"}[p])

    def test_the_badge_is_never_green_while_a_row_is_not(self):
        for n in NAMES:
            self.write(n, "PASS")
        self.page()
        self.assertIn("rehearsal NOT_CHECKED", (self.out / "badge.svg").read_text())  # steps 4-11 have no finding

    def test_the_report_page_embeds_the_badge(self):
        self.assertIn('<a href="status/"><img src="status/badge.svg" alt="Loop status"></a>',
                      (ROOT / "site" / "template.html").read_text())

    # The five numbers

    def number(self, key, population="rehearsal"):
        row = re.search(rf'<tr id="n-{key}">(.*?)</tr>', self.html).group(1)
        return re.search(rf'<td class="(\w+)" data-population="{population}">([^<]*)</td>', row).groups()

    def test_minutes_run_from_run_json_to_the_marker_steps_finished(self):
        self.run_json("2026-10-27T01:59:30Z")
        self.write(1, "PASS", started="2026-10-27T02:00:30Z")  # finished 02:01:00
        self.write(2, "PASS", started="2026-10-27T02:01:00Z")
        self.write(12, "PASS", started="2026-10-27T02:05:00Z")  # finished 02:12:00
        self.run_json("2026-10-27T02:00:00Z", population="live")
        self.write(1, "PASS", population="live", started="2026-10-27T02:00:10Z")
        self.page()
        # From run.json, not from the earliest step start (that would be 0.5).
        self.assertEqual(self.number("first-sealed"), ("measured", "1.5 minutes"))
        self.assertEqual(self.number("first-answered"), ("measured", "12.5 minutes"))
        self.assertEqual(self.number("first-sealed", "live"), ("measured", "1.0 minutes"))
        self.assertEqual(self.number("first-answered", "live"), ("placeholder", "not measured yet (M1)"))
        self.assertNotIn("upper bound", self.html)

    def test_no_valid_run_json_is_not_measured(self):
        self.write(1, "PASS")
        self.page()
        self.assertEqual(self.number("first-sealed"), ("placeholder", "not measured yet (M1)"))  # no run.json
        self.run_json("2026-10-27T01:00:00Z", run_population="live")
        self.page()
        self.assertEqual(self.number("first-sealed"), ("placeholder", "not measured yet (M1)"))  # another population
        self.run_json("yesterday")
        self.page()
        self.assertEqual(self.number("first-sealed"), ("placeholder", "not measured yet (M1)"))  # not a UTC time
        self.run_json("2026-10-27T03:00:00Z")
        self.page()
        self.assertEqual(self.number("first-sealed"), ("placeholder", "not measured yet (M1)"))  # after the step

    def test_the_countersigned_report_is_a_placeholder_until_m3(self):
        self.run_json("2026-10-27T02:00:00Z")
        for n in NAMES:
            self.write(n, "PASS")
        self.page()
        self.assertEqual(self.number("first-countersigned"), ("placeholder", "not measured yet (M3)"))

    def test_a_number_whose_step_did_not_pass_is_not_measured_yet(self):
        self.run_json("2026-10-27T02:00:00Z")
        self.write(1, "FAIL")
        self.write(12, "NOT_CHECKED", not_checked=[{"what": "a", "why": "b", "issue": "c"}])
        self.page()
        self.assertEqual(self.number("first-sealed"), ("placeholder", "not measured yet (M1)"))
        self.assertEqual(self.number("first-answered"), ("placeholder", "not measured yet (M1)"))
        self.assertEqual(self.number("first-sealed", "live"), ("placeholder", "not measured yet (M1)"))

    def test_the_numbers_due_later_are_placeholders_never_zero(self):
        self.write(1, "PASS")
        self.page()
        self.assertEqual(self.number("first-countersigned"), ("placeholder", "not measured yet (M3)"))
        self.assertEqual(self.number("limited"), ("placeholder", "not measured yet (M3)"))
        self.assertEqual(self.number("filtered"), ("placeholder", "not measured yet (M2)"))
        numbers = self.html[self.html.index('id="numbers"'):self.html.index('id="pins"')]
        self.assertNotRegex(numbers, r">\s*0(\.0)?( |<)")

    def test_each_number_has_a_unit_and_a_source(self):
        for nb in status_page.NUMBERS:
            self.assertTrue(nb.unit and nb.source, nb.key)
        self.assertEqual([nb.key for nb in status_page.NUMBERS],
                         ["first-sealed", "first-answered", "first-countersigned", "limited", "filtered"])

    def test_raw_volume_is_never_shown_without_the_filtered_figure(self):
        self.assertEqual(status_page.volume(120, None), "not measured yet (M2)")
        self.assertEqual(status_page.volume(None, 40), "not measured yet (M2)")
        self.assertEqual(status_page.volume(120, 40), "40 filtered of 120 raw records")

    # Build safety and pins

    def test_a_green_cell_whose_proof_is_not_in_the_output_fails_the_build(self):
        self.write(1, "PASS")
        real = status_page.check_rows
        runs = {p: status_page.read_run(p, d) for p, d in status_page.latest_runs(self.findings).items()}
        rs = status_page.rows(runs)
        with self.assertRaises(status_page.StatusError) as err:
            real(rs, self.dir / "empty")
        self.assertIn("is not in the page's files", str(err.exception))
        with self.assertRaises(ValueError):
            status_page.Cell("rehearsal", status_page.GREEN, "PASS", "", proofs=[])
        with self.assertRaises(ValueError):
            status_page.Cell("rehearsal", status_page.GREEN, "FAIL", "", proofs=[("p", "p")])

    def test_a_failed_build_writes_nothing(self):
        self.write(1, "PASS")
        with mock.patch.object(status_page, "check_rows", side_effect=status_page.StatusError(["planted"])):
            self.assertEqual(self.build(), 1)
        self.assertIn("planted", self.err)
        self.assertFalse(self.out.exists())

    def test_pins_come_from_versions_lock(self):
        self.findings.mkdir()
        self.page()
        lock = status_page.read_lock(ROOT / "versions.lock")
        self.assertIn(f"sha256={lock.sha256}", self.html)
        self.assertEqual((self.out / "versions.lock").read_bytes(), (ROOT / "versions.lock").read_bytes())
        status_page.build(self.findings, self.dir / "no.lock", self.out)
        self.assertIn("<code>NOT_CHECKED</code>: versions.lock not present.", (self.out / "index.html").read_text())

    def test_no_findings_at_all_builds_a_not_checked_page(self):
        self.assertEqual(self.build(), 0, self.err)
        self.assertIn("rehearsal NOT_CHECKED", (self.out / "badge.svg").read_text())

    # The real loop.sh

    def test_built_from_a_real_loop_run(self):
        """loop.sh with no step executables writes a FAIL per stage-1 step; the page shows those rows red."""
        steps = self.dir / "no-steps"
        steps.mkdir()
        env = dict(os.environ, LOOP_STEPS_DIR=str(steps))
        r = subprocess.run([str(ROOT / "loop.sh"), "--stage", "1", "--out",
                            str(self.findings / "2026-10-27-rehearsal")], capture_output=True, text=True, env=env,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        page = self.page()
        for n in (1, 2, 3, 12):
            self.assertIn("s-red", page.rows[n]["rehearsal"]["class"], n)
            self.assertIn("no step executable", page.rows[n]["rehearsal"]["quoted"])
        self.assertIn("s-NOT_CHECKED", page.rows[7]["rehearsal"]["class"])
        self.assertEqual(self.number("first-sealed"), ("placeholder", "not measured yet (M1)"))

    def test_loop_sh_writes_run_json_first_and_the_page_measures_from_it(self):
        """A real loop.sh run with a passing step 01: run.json comes before the step, and the check allows it."""
        steps = self.dir / "fake-steps"
        steps.mkdir()
        step = steps / "01-seal"
        step.write_text(FAKE_STEP.format(steps=str(ROOT / "steps")))
        step.chmod(0o755)
        run = self.findings / "2026-10-27-rehearsal"
        env = dict(os.environ, LOOP_STEPS_DIR=str(steps))
        r = subprocess.run([str(ROOT / "loop.sh"), "--stage", "1", "--out", str(run)], capture_output=True,
                           text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("01 seal PASS", r.stdout)
        meta = json.loads((run / "run.json").read_text())
        self.assertEqual(sorted(meta), ["population", "stage", "started"])
        self.assertEqual((meta["stage"], meta["population"]), ("1", "rehearsal"))
        seal = json.loads((run / "01-seal" / "finding.json").read_text())
        self.assertLessEqual(meta["started"], seal["started"])  # written before the first step
        self.assertEqual(finding.check(run), [])  # the check allows run.json
        page = self.page()
        self.assertIn("s-green", page.rows[1]["rehearsal"]["class"])
        state, text = self.number("first-sealed")
        self.assertEqual(state, "measured")
        expected = (status_page.stamp(seal["finished"]) - status_page.stamp(meta["started"])).total_seconds() / 60
        self.assertEqual(text, f"{expected:.1f} minutes")


if __name__ == "__main__":
    unittest.main()
