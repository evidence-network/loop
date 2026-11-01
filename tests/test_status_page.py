"""Tests for the loop status page (status/status_page.py), built from loop findings.

Findings are written through steps/finding.py's write(), as a step writes them,
into findings/<date>-<population>/<NN>-<name>/. One test runs the real loop.sh.

Run: python3 -B -m unittest discover -s tests -v
"""
import contextlib
import html
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
            if not (d / p).exists():  # a proof the test wrote itself is kept
                (d / p).write_bytes(b"proof bytes for step %d\n" % n)
        with mock.patch.object(finding, "now", return_value="2026-10-27T02:%02d:00Z" % n):
            finding.write(d, result=result, population=population, asserts=f"step {n} holds",
                          pinned=["capsulectl"], proof=list(proof), detail=detail, not_checked=not_checked,
                          started=started)
        return d

    def answered(self, at, result="PASS", population="rehearsal", sealed=None, **kw):
        """Step 07's finding, its first-answered.json saying the first ask was answered at `at`."""
        d = self.findings / f"2026-10-27-{population}" / "07-ask"
        d.mkdir(parents=True, exist_ok=True)
        (d / "first-answered.json").write_text(json.dumps({"answered_at": at, "ask": "stevenmih-1 checkpoints",
                                                              **({} if sealed is None else {"sealed": sealed})}))
        return self.write(7, result, population=population, proof=("first-answered.json",), name="ask", **kw)

    def run_json(self, started, population="rehearsal", date="2026-10-27", run_population=None):
        d = self.findings / f"{date}-{population}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "run.json").write_text(json.dumps({"stage": "1", "population": run_population or population,
                                                "started": started}))

    def test_ecosystem_flag_reads_that_ecosystems_findings_and_builds_beside_the_mesh_page(self):
        self.findings = self.dir / "findings" / "muse"
        self.write(1, "NOT_CHECKED", proof=(), not_checked=[{"what": "w", "why": "y", "issue": "o/r#1"}])
        cwd, err = os.getcwd(), io.StringIO()
        os.chdir(self.dir)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = status_page.main(["status_page.py", "--ecosystem", "muse", "--lock", str(ROOT / "versions.lock")])
                bad = status_page.main(["status_page.py", "--ecosystem", "other"])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(bad, 2)
        page = (self.dir / "_site" / "status-consumer-agent" / "index.html").read_text()
        self.assertIn("<title>Loop status (consumer-agent)</title>", page)
        self.assertIn("<h1>Loop status (consumer-agent)</h1>", page)
        badge = (self.dir / "_site" / "status-consumer-agent" / "badge.svg").read_text()
        self.assertIn("loop (consumer-agent):", badge)
        self.assertIn(">loop (consumer-agent)</text>", badge)
        self.assertFalse((self.dir / "_site" / "status").exists())

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
        self.assertIn(f"<code>capsulectl</code> <code>{finding.pins()['capsulectl'][1]}</code>", self.html)

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
        self.assertIn('href="https://github.com/evidence-network/loop/issues/188"', row)

    def test_steps_08_10_and_11_are_owned_by_a_loop_issue(self):
        self.write(1, "PASS")
        self.page()
        for n, issue in ((8, 188), (10, 189), (11, 189)):
            row = re.search(rf'<tr class="row" id="step-{n:02d}".*?</tr>', self.html).group(0)
            self.assertIn(f'owner: <a href="https://github.com/evidence-network/loop/issues/{issue}">loop #{issue}</a>', row)
            self.assertNotIn("evidencebook-skills", row)

    # A PASS finding's gaps, in the convention the steps write: one sentence in
    # detail, "Outside this step's assertion: ...", ending with owner/repo#N.
    GAPS = {1: ("the producer envelope signature is not verified in a second language",
                "action-state-group/agent-action-capsule#457"),
            3: ("alice: the stapled history_card/1 signature is not verified",
                "action-state-group/agent-action-capsule#430"),
            12: ("the controller binding of the joined node is not established",
                 "action-state-group/agent-action-capsule#457")}

    def gap_detail(self, n):
        text, issue = self.GAPS[n]
        return f"alice: PASS via the carrier. Outside this step's assertion: {text}: {issue}."

    def test_a_pass_row_shows_each_gap_with_its_issue_linked(self):
        for n in self.GAPS:
            self.write(n, "PASS", detail=self.gap_detail(n))
        page = self.page()
        for n, (text, issue) in self.GAPS.items():
            c = page.rows[n]["rehearsal"]
            self.assertIn("s-green", c["class"])
            row = re.search(rf'<tr class="row" id="step-{n:02d}".*?</tr>', self.html).group(0)
            repo, num = issue.split("#")
            gap = re.search(r'<p class="gap">(.*?)</p>', row).group(1)
            self.assertIn(f'<q class="said">{status_page.e(text)}</q>', gap)
            self.assertIn(f'<a href="https://github.com/{repo}/issues/{num}">{issue}</a>', gap)
            self.assertIn("gap, outside the assertion", c["own"])

    def test_a_pass_with_no_gap_sentence_has_no_gap_line(self):
        self.write(1, "PASS", detail="alice: PASS via the carrier.")
        self.page()
        self.assertNotIn('<p class="gap">', self.html)

    def test_gaps_parse_the_convention(self):
        g = status_page.gaps("x. Outside this step's assertion: a; b, step 02's c: o/r#7. y.")
        self.assertEqual(g, [status_page.Gap("a; b, step 02's c", "o/r#7", "https://github.com/o/r/issues/7")])
        g = status_page.gaps("Outside this step's assertion: a: o/r#1. Outside this step's assertion: b: o/s#2.")
        self.assertEqual([(x.text, x.issue) for x in g], [("a", "o/r#1"), ("b", "o/s#2")])
        self.assertEqual(status_page.gaps("Outside this step's assertion: no issue named."), [])
        self.assertEqual(status_page.gaps(""), [])

    def test_gaps_show_only_on_pass(self):
        self.write(1, "FAIL", detail=self.gap_detail(1))
        self.page()
        self.assertNotIn('<p class="gap">', self.html)

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

    def test_numbered_runs_are_picked_by_date_then_number_not_by_name(self):
        for name in ("2026-10-30-live", "2026-10-31-live", "2026-10-31-live-2", "2026-10-31-live-9",
                     "2026-10-31-live-17", "2026-10-31-rehearsal-3"):
            (self.findings / name).mkdir(parents=True)
        latest = status_page.latest_runs(self.findings)
        self.assertEqual(latest["live"].name, "2026-10-31-live-17")
        self.assertEqual(latest["rehearsal"].name, "2026-10-31-rehearsal-3")
        (self.findings / "2026-11-01-live").mkdir()
        self.assertEqual(status_page.latest_runs(self.findings)["live"].name, "2026-11-01-live")

    def test_each_ecosystem_has_its_own_column_and_runs_never_cross(self):
        self.write(1, "PASS", population="live", date="2026-10-31")
        (self.findings / "2026-10-31-live").rename(self.findings / "2026-10-31-live-17")
        muse = self.findings / "muse"
        self.findings, mesh = muse, self.findings
        self.write(1, "FAIL", population="live", date="2026-10-29")
        self.findings = mesh
        self.page()
        self.assertEqual(self.html.count('data-ecosystem="'), 6)  # mesh, muse and goose, rehearsal and live tables
        live = self.html[self.html.index('data-population="live"'):self.html.index('id="steps"')]
        self.assertIn("2026-10-31-live-17", live)
        self.assertIn("findings/consumer-agent/2026-10-29-live/", live)
        self.assertNotIn("2026-10-29-live/", self.html.replace("findings/consumer-agent/2026-10-29-live/", ""))
        self.assertTrue((self.out / "findings" / "consumer-agent" / "2026-10-29-live" / "01-seal" / "finding.json").is_file())
        self.assertTrue((self.out / "badge-consumer-agent.svg").is_file())
        self.assertNotIn("muse", self.html.lower())  # the public page never names the product
        self.assertIn("2026-10-29-live/01-seal/finding.json", live)  # the red muse cell links its finding

    def test_an_ecosystem_with_no_run_keeps_its_column_citing_its_owner(self):
        self.write(1, "PASS")
        self.page()
        muse = self.html[self.html.index('data-ecosystem="consumer-agent"'):self.html.index('id="steps"')]
        self.assertIn("no live run yet", muse)
        self.assertIn("https://github.com/evidence-network/loop/issues/154", muse)
        self.assertEqual(muse.count("owner: <a href=\"https://github.com/evidence-network/loop/issues/154"), 2 + 24)

    def test_goose_has_its_own_column_citing_loop_315_and_never_takes_a_mesh_run(self):
        self.write(1, "PASS")
        goose = self.findings / "goose"
        self.findings, mesh = goose, self.findings
        self.write(1, "NOT_CHECKED", proof=(), not_checked=[{"what": "w", "why": "y", "issue": "evidence-network/loop#315"}])
        self.findings = mesh
        self.page()
        col = self.html[self.html.index('data-ecosystem="goose"'):self.html.index('id="steps"')]
        self.assertIn("findings/goose/", col)
        self.assertIn("https://github.com/evidence-network/loop/issues/315", col)
        self.assertTrue((self.out / "badge-goose.svg").is_file())
        self.assertNotIn("findings/goose/", self.html[:self.html.index('data-ecosystem="goose"')])

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
        self.write(2, "NOT_CHECKED", not_checked=nc, detail="the receipt was not checked")
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

    def test_the_badge_counts_each_population_and_takes_the_worst_rows_colour(self):
        self.write(1, "PASS")
        self.write(2, "FAIL")
        self.page()
        badge = (self.out / "badge.svg").read_text()
        label = "rehearsal 1 PASS · 1 FAIL · 10 not checked, live 0 PASS · 0 FAIL · 12 not checked"
        self.assertIn(f'aria-label="loop: {label}"', badge)
        self.assertEqual(re.findall(r">([^<>]+)</text>", badge),
                         ["loop", "rehearsal 1 PASS · 1 FAIL · 10 not checked", "live 0 PASS · 0 FAIL · 12 not checked"])
        self.assertEqual(re.findall(r'<rect [^>]*fill="(#\w+)"', badge)[1:],
                         [status_page.BADGE_COLOURS["red"], status_page.BADGE_COLOURS["NOT_CHECKED"]])
        self.assertIn(f'<img src="badge.svg" alt="loop: {label}">', self.html)
        for p in ("rehearsal", "live"):
            rs = status_page.rows({q: status_page.read_run(q, d) for q, d in
                                   status_page.latest_runs(self.findings).items()})
            self.assertEqual(status_page.worst(rs, p), {"rehearsal": "red", "live": "NOT_CHECKED"}[p])

    def test_four_pass_and_eight_not_checked_reads_as_counts_and_is_never_green(self):
        for n in NAMES:  # 01, 02, 03 and 12 PASS; steps 4-11 have no finding
            self.write(n, "PASS")
        self.page()
        badge = (self.out / "badge.svg").read_text()
        self.assertIn(">rehearsal 4 PASS · 0 FAIL · 8 not checked</text>", badge)
        self.assertIn('aria-label="loop: rehearsal 4 PASS · 0 FAIL · 8 not checked, live 0 PASS · 0 FAIL · 12 not '
                      'checked"', badge)
        rehearsal = re.findall(r'<rect [^>]*fill="(#\w+)"', badge)[1]
        self.assertEqual(rehearsal, status_page.BADGE_COLOURS["NOT_CHECKED"])
        self.assertNotEqual(rehearsal, status_page.BADGE_COLOURS["green"])
        self.assertNotIn("NOT_CHECKED", badge)  # the counts replace the worst state's name
        # One row per population, so it fits a 390 px phone (358 px between the gutters) without scaling.
        self.assertLessEqual(int(re.search(r'<svg [^>]*width="(\d+)"', badge).group(1)), 358)

    def test_the_report_page_embeds_the_badge(self):
        self.assertIn('<a href="status/"><img src="status/badge.svg" alt="loop: $badge_alt"></a>',
                      (ROOT / "site" / "template.html").read_text())

    # The five numbers

    def number(self, key, population="rehearsal"):
        row = re.search(rf'<tr id="n-{key}">(.*?)</tr>', self.html).group(1)
        cls, text = re.search(rf'<td class="(\w+)" data-population="{population}">([^<]*)</td>', row).groups()
        return cls, html.unescape(text)

    def test_minutes_run_from_run_json_to_the_marker_steps_finished(self):
        self.run_json("2026-10-27T01:59:30Z")
        self.write(1, "PASS", started="2026-10-27T02:00:30Z")  # finished 02:01:00
        self.write(2, "PASS", started="2026-10-27T02:01:00Z")
        self.write(12, "PASS", started="2026-10-27T02:05:00Z")  # finished 02:12:00, no longer the marker
        self.run_json("2026-10-27T02:00:00Z", population="live")
        self.write(1, "PASS", population="live", started="2026-10-27T02:00:10Z")
        self.page()
        # From run.json, not from the earliest step start (that would be 0.5).
        self.assertEqual(self.number("first-sealed"), ("measured", "1.5 minutes"))
        self.assertEqual(self.number("first-answered"), ("placeholder", "not measured yet (M2)"))  # no step 07
        self.assertEqual(self.number("first-sealed", "live"), ("measured", "1.0 minutes"))
        self.assertEqual(self.number("first-answered", "live"), ("placeholder", "not measured yet (M2)"))
        self.assertNotIn("upper bound", self.html)

    def test_first_answered_is_step_07s_first_answer_not_its_finished(self):
        self.run_json("2026-10-27T01:59:30Z")
        self.answered("2026-10-27T02:04:00.250000Z", started="2026-10-27T02:03:00Z")  # finished 02:07:00
        self.run_json("2026-10-27T02:00:00Z", population="live")
        # A live finding is NOT_CHECKED while the plugin does not seal its requests; its answers still count.
        self.answered("2026-10-27T02:00:42Z", result="NOT_CHECKED", population="live",
                      not_checked=[{"what": "x", "why": "y", "issue": "o/r#1"}])
        self.page()
        self.assertEqual(self.number("first-answered"), ("measured", "4.5 minutes (first answer: stevenmih-1 checkpoints)"))
        self.assertEqual(self.number("first-answered", "live"), ("measured", "42 s (first answer: stevenmih-1 checkpoints)"))

    def test_a_live_number_names_the_ask_and_says_it_was_not_sealed(self):
        self.run_json("2026-10-27T02:00:00Z", population="live")
        self.answered("2026-10-27T02:00:42Z", result="NOT_CHECKED", population="live", sealed=False,
                      not_checked=[{"what": "x", "why": "y", "issue": "o/r#1"}])
        self.run_json("2026-10-27T01:59:30Z")
        self.answered("2026-10-27T02:04:00Z", sealed=True, started="2026-10-27T02:03:00Z")
        self.page()
        self.assertEqual(self.number("first-answered", "live"),
                         ("measured", "42 s (first answer: stevenmih-1 checkpoints; the request was not sealed before it was sent)"))
        self.assertEqual(self.number("first-answered"), ("measured", "4.5 minutes (first answer: stevenmih-1 checkpoints)"))

    def test_first_answered_is_not_measured_without_an_answer_or_on_a_fail(self):
        self.run_json("2026-10-27T02:00:00Z")
        self.answered(None)  # every ask absent: nothing answered
        self.run_json("2026-10-27T02:00:00Z", population="live")
        self.answered("2026-10-27T02:00:42Z", result="FAIL", population="live")
        self.page()
        self.assertEqual(self.number("first-answered"), ("placeholder", "not measured yet (M2)"))
        self.assertEqual(self.number("first-answered", "live"), ("placeholder", "not measured yet (M2)"))

    def test_first_answered_reads_only_the_file_the_finding_covers(self):
        self.run_json("2026-10-27T02:00:00Z")
        d = self.answered("2026-10-27T02:01:00Z")
        (d / "first-answered.json").write_text(json.dumps({"answered_at": "2026-10-27T02:00:01Z"}))
        self.page()  # the finding's sha256 no longer matches: a malformed finding is no measure
        self.assertEqual(self.number("first-answered"), ("placeholder", "not measured yet (M2)"))

    def test_under_a_minute_reads_in_seconds(self):
        self.run_json("2026-10-27T02:00:18Z")
        self.write(1, "PASS", started="2026-10-27T02:00:20Z")  # finished 02:01:00
        self.page()
        self.assertEqual(self.number("first-sealed"), ("measured", "42 s"))
        self.assertEqual(status_page.duration(0), "<1 s")
        self.assertEqual(status_page.duration(0.99 / 60), "<1 s")  # never "0 s", nor rounded up to "1 s"
        self.assertEqual(status_page.duration(1 / 60), "1 s")
        self.assertEqual(status_page.duration(59.4 / 60), "59 s")
        self.assertEqual(status_page.duration(59.6 / 60), "1.0 minutes")
        self.assertEqual(status_page.duration(1.5), "1.5 minutes")

    def test_a_pass_with_no_run_json_says_the_run_did_not_record_its_start(self):
        self.write(1, "PASS")
        self.page()
        self.assertEqual(self.number("first-sealed"),
                         ("placeholder", "not measured: this run did not record its start time (run.json)"))
        self.assertNotIn("not measured yet (M1)</td>", re.search(r'<tr id="n-first-sealed">.*?</tr>',
                                                                 self.html).group(0).split('data-population="live"')[0])

    def test_minutes_measured_no_run_json_and_no_pass_are_three_cases(self):
        self.write(1, "PASS")
        self.write(12, "NOT_CHECKED", not_checked=[{"what": "x", "why": "y", "issue": "o/r#1"}])
        self.page()  # no run.json
        self.assertEqual(self.number("first-sealed"), ("placeholder", status_page.NO_START))
        self.assertEqual(self.number("first-answered"), ("placeholder", "not measured yet (M2)"))  # no step 07
        self.run_json("2026-10-27T02:00:00Z")
        self.page()
        self.assertEqual(self.number("first-sealed"), ("measured", "1.0 minutes"))
        self.assertEqual(self.number("first-answered"), ("placeholder", "not measured yet (M2)"))

    def test_an_unusable_run_json_says_why(self):
        self.write(1, "PASS")
        self.run_json("2026-10-27T01:00:00Z", run_population="live")
        self.page()
        self.assertEqual(self.number("first-sealed"),  # another population's run.json
                         ("placeholder", "not measured: this run's run.json gives no start time for this population"))
        self.run_json("yesterday")
        self.page()
        self.assertEqual(self.number("first-sealed"),  # not a UTC time
                         ("placeholder", "not measured: this run's run.json gives no start time for this population"))
        self.run_json("2026-10-27T03:00:00Z")
        self.page()
        self.assertEqual(self.number("first-sealed"),
                         ("placeholder", "not measured: this run's run.json start is after the step finished"))

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
        self.assertEqual(self.number("first-answered"), ("placeholder", "not measured yet (M2)"))
        self.assertEqual(self.number("first-sealed", "live"), ("placeholder", "not measured yet (M1)"))

    def test_the_numbers_due_later_are_placeholders_never_zero(self):
        self.write(1, "PASS")
        self.page()
        self.assertEqual(self.number("first-countersigned"), ("placeholder", "not measured yet (M3)"))
        self.assertEqual(self.number("refused"), ("placeholder", "not measured yet (M3)"))
        self.assertEqual(self.number("filtered"), ("placeholder", "not measured yet (M2)"))
        numbers = self.html[self.html.index('id="numbers"'):self.html.index('id="pins"')]
        self.assertNotRegex(numbers, r">\s*0(\.0)?( |<)")

    def test_each_number_has_a_unit_and_a_source(self):
        for nb in status_page.NUMBERS:
            self.assertTrue(nb.unit and nb.source, nb.key)
        self.assertEqual([nb.key for nb in status_page.NUMBERS],
                         ["first-sealed", "first-answered", "first-countersigned", "refused", "filtered"])

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
        self.assertIn(">rehearsal 0 PASS · 0 FAIL · 12 not checked</text>", (self.out / "badge.svg").read_text())

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
        self.assertEqual(sorted(meta), ["population", "stage", "started", "steps"])
        self.assertEqual(meta["steps"], ["01", "02", "03", "12", "08"])
        self.assertEqual((meta["stage"], meta["population"]), ("1", "rehearsal"))
        seal = json.loads((run / "01-seal" / "finding.json").read_text())
        self.assertLessEqual(meta["started"], seal["started"])  # written before the first step
        self.assertEqual(finding.check(run), [])  # the check allows run.json
        page = self.page()
        self.assertIn("s-green", page.rows[1]["rehearsal"]["class"])
        state, text = self.number("first-sealed")
        self.assertEqual(state, "measured")
        expected = (status_page.stamp(seal["finished"]) - status_page.stamp(meta["started"])).total_seconds() / 60
        self.assertEqual(text, status_page.duration(expected))


if __name__ == "__main__":
    unittest.main()
