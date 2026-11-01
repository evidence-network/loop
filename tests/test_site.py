"""Tests for the stats site (site/), built from the scrubbed day files and loop findings.

The day files are tests/fixtures/stats/daily/ (made by make_fixtures.py there,
in the fixed format); the findings are written through steps/finding.py's
write(), as a step writes them.

Run: python3 -B -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "site"))
sys.path.insert(0, str(ROOT / "steps"))
import build  # noqa: E402
import finding  # noqa: E402
import stats_model  # noqa: E402
import status_page  # noqa: E402

DATA = ROOT / "tests" / "fixtures" / "stats" / "daily"
COUNT = re.compile(r"^(\d+(?:\.\d+)?)(?: s)? of (?:the run's )?(\d+(?:\.\d+)?) [a-z]")
CLAIMS = re.compile(r"\b(checked|verified|witnessed)\b", re.I)
DATES = ["2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]


def nid(n):
    return str(n) * 64


class Text(HTMLParser):
    """The page's text, each chunk with the stack of (tag, class) it sits in."""

    def __init__(self):
        super().__init__()
        self.stack, self.chunks, self.tags = [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        self.stack.append((tag, a.get("class", "")))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        if data.strip():
            self.chunks.append((list(self.stack), data))

    @staticmethod
    def context(stack):
        for tag, cls in reversed(stack):
            if tag == "span" and cls == "count":
                return "count"
            if tag == "span" and cls == "value":
                return "value"  # a measured value: the sentence around it says what it is of
            if tag == "td" and cls == "num":
                return "num"  # a cell in a table of numbers; its column header names it
            if tag == "desc":
                return "desc"  # an SVG's description, read by assistive technology
            if "code" in cls.split():
                return "code"
            if tag in ("code", "time", "style", "title") or (tag in ("text", "tspan") and "code" in cls.split()):
                return "code" if tag in ("text", "tspan") else tag
        return "prose"


def parse(page):
    t = Text()
    t.feed(page)
    return t


def write_finding(findings, n, name, result, population, not_checked=(), detail=""):
    d = findings / f"2026-10-27-{population}" / f"{n:02d}-{name}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "proof.bin").write_bytes(b"proof bytes for step %d\n" % n)
    with mock.patch.object(finding, "now", return_value="2026-10-27T02:%02d:00Z" % n):
        finding.write(d, result=result, population=population, asserts=f"step {n} holds", pinned=["capsulectl"],
                      proof=["proof.bin"], detail=detail, not_checked=not_checked, started="2026-10-27T02:00:00Z")


def make_findings(findings):
    """Rehearsal: 01 PASS, 02 NOT_CHECKED, 03 FAIL. Live: 01 PASS. Each run starts at 02:00."""
    nc = [{"what": "the witness receipt", "why": "no witness yet", "issue": "https://example.org/issue/1"}]
    write_finding(findings, 1, "seal", "PASS", "rehearsal")
    write_finding(findings, 2, "book", "NOT_CHECKED", "rehearsal", nc)
    write_finding(findings, 3, "advertise", "FAIL", "rehearsal")
    write_finding(findings, 1, "seal", "PASS", "live")
    for p in ("rehearsal", "live"):
        (findings / f"2026-10-27-{p}" / "run.json").write_text(
            json.dumps({"stage": "1", "population": p, "started": "2026-10-27T02:00:00Z"}))


BRANDS = ("action state", "action-state", "actionstate", "asg", "muse", "dots", "©", "copyright")
# The two allowed exceptions; both are provenance. The status page's pin table: the
# repository each pinned component comes from. A cited issue: exactly an `owner/repo#N`
# reference, or a link to https://github.com/owner/repo/issues/N. Prose naming the org
# is not an exception.
PINS = re.compile(r'<h2 id="pins">.*?<table>.*?</table>', re.S)
ISSUE_LINK = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/\d+\b")
ISSUE_REF = re.compile(r"\b[\w.-]+/[\w.-]+#\d+\b")


def branding(out):
    """(page, brand) for each brand on any built HTML page, outside the two exceptions."""
    hits = []
    for page in sorted(Path(out).rglob("*.html")):
        text = page.read_text(encoding="utf-8")
        if page.relative_to(out).as_posix() == "stats/status/index.html":
            text = PINS.sub("", text, count=1)
        if PINS.search(text):
            hits.append((page.relative_to(out).as_posix(), "a pin table outside the status page's pins"))
        low = ISSUE_REF.sub("", ISSUE_LINK.sub("", text)).lower()
        hits += [(page.relative_to(out).as_posix(), b) for b in BRANDS if re.search(rf"\b{re.escape(b)}\b", low)]
    return hits


def run_build(data, findings, out):
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = build.main(["build.py", str(data), "--findings", str(findings), "--lock",
                           str(ROOT / "versions.lock"), "--out", str(out)])
    return code, err.getvalue()


class SiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.findings = d / "findings"
        make_findings(cls.findings)
        cls.out = d / "_site"
        code, err = run_build(DATA, cls.findings, cls.out)
        assert code == 0, err
        cls.html = (cls.out / "stats" / "index.html").read_text(encoding="utf-8")
        cls.text = parse(cls.html)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def section(self, anchor):
        """From an anchor to the next heading of a section (h2, or h3 with an id)."""
        start = self.html.index(f'id="{anchor}"')
        ends = [i for i in (self.html.find("<h2", start), self.html.find('<h3 id="', start + 1)) if i > 0]
        return self.html[start:min(ends) if ends else None]

    def figure(self, population):
        m = re.search(rf'<figure class="pop" data-population="{population}">(.*?)</figure>', self.html, re.S)
        return m.group(1)

    def chart(self, key):
        return re.search(rf'<figure class="chart[^"]*" id="f-{key}">(.*?)</figure>', self.html, re.S).group(1)

    def mark(self, node, date, stem):
        m = re.search(rf'<g class="node" data-node="{node}">(.*?)</g>', self.html, re.S)
        got = re.search(rf'<rect class="([a-z_-]+)" data-k="{node[:8]}\|{date}(?:\|{stem})?"', m.group(1))
        if got:
            return got.group(1)
        x = re.findall(r'<rect class="out" x="(\d+)"', m.group(1))
        return "out" if x else None

    # The site's shape

    def test_the_site_is_served_under_stats(self):
        self.assertTrue((self.out / "stats" / "index.html").is_file())
        self.assertTrue((self.out / "stats" / "status" / "index.html").is_file())
        self.assertTrue((self.out / "stats" / "status" / "badge.svg").is_file())
        root = (self.out / "index.html").read_text()
        self.assertIn('<a href="stats/">', root)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["index.html", "stats"])

    def test_one_page_the_old_report_page_is_gone(self):
        self.assertFalse((ROOT / "site" / "reports_model.py").exists())
        self.assertFalse((self.out / "reports").exists())
        for anchor in ("multiples", "loop", "numbers", "nodes", "about"):
            self.assertIn(f'<h2 id="{anchor}">', self.html)
        for anchor in ("matters", "what", "outcomes", "why", "join", "offline"):
            self.assertIn(f'<h3 id="{anchor}">', self.html)

    def test_static_inline_and_no_external_assets(self):
        for page in self.out.rglob("*.html"):
            text = page.read_text()
            self.assertNotIn("<script", text.lower(), page)
            self.assertIsNone(re.search(r"\son[a-z]+=", text), page)
            self.assertIsNone(re.search(r'\ssrc="(?:https?:)?//', text), page)
            self.assertNotIn("<link", text, page)
            self.assertNotIn("@import", text, page)
            # The only url() is a hatch pattern in the same SVG: url(#<id>).
            self.assertNotIn("url(", re.sub(r"url\(#[a-z0-9-]+\)", "", text.replace("url=stats/", "")), page)
        self.assertIn("<svg", self.html)
        # The public leak-lint rule: [ + three or more hyphenated lowercase segments + ].
        self.assertIsNone(re.search(r"\[[a-z0-9]+(?:-[a-z0-9]+){2,}\]", self.html))

    def test_it_says_it_is_the_evidence_networks_with_no_company_branding(self):
        self.assertIn("This site is the evidence network's.", self.html)
        self.assertIn("evidence network", (self.out / "index.html").read_text())
        pages = sorted(self.out.rglob("*.html"))
        self.assertIn(self.out / "stats" / "status" / "index.html", pages)
        self.assertEqual(len(pages), 3)  # the root redirect, the stats page and the status page
        pins = PINS.search((self.out / "stats" / "status" / "index.html").read_text())
        self.assertIn("action-state-group/", pins.group(0))  # the pin table exception is used
        self.assertEqual(branding(self.out), [])

    def test_a_cited_issue_reference_is_allowed_and_the_org_in_prose_is_not(self):
        ref = "action-state-group/agent-action-capsule#457"
        gap = f"alice: PASS. Outside this step's assertion: the envelope signature is not verified: {ref}."
        with tempfile.TemporaryDirectory() as d:
            findings = Path(d) / "findings"
            make_findings(findings)
            write_finding(findings, 1, "seal", "PASS", "rehearsal", detail=gap)
            self.assertEqual(run_build(DATA, findings, Path(d) / "a")[0], 0)
            for page in ("stats/index.html", "stats/status/index.html"):
                text = (Path(d) / "a" / page).read_text()
                self.assertIn(f'<a href="https://github.com/action-state-group/agent-action-capsule/issues/457">{ref}</a>',
                              text)
            self.assertEqual(branding(Path(d) / "a"), [])  # a gap line with an AAC issue ref passes
            for prose in ("Built by action-state-group.", "See action-state-group/agent-action-capsule for more.",
                          "An Action State project.", "https://github.com/action-state-group/agent-action-capsule"):
                write_finding(findings, 1, "seal", "PASS", "rehearsal", detail=f"{gap} {prose}")
                self.assertEqual(run_build(DATA, findings, Path(d) / "b")[0], 0)
                hits = branding(Path(d) / "b")
                self.assertTrue(hits, prose)  # the org anywhere else on a built page fails
                self.assertEqual({h[0] for h in hits}, {"stats/status/index.html"}, prose)

    def test_only_the_deploy_job_takes_the_pages_concurrency_group(self):
        wf = (ROOT / ".github" / "workflows" / "stats.yml").read_text()
        jobs = wf.index("\njobs:")
        self.assertNotIn("concurrency:", wf[:jobs])  # not workflow-wide: pull request builds never wait on it
        deploy = wf[wf.index("\n  deploy:"):]
        self.assertEqual(wf.count("concurrency:"), 1)
        self.assertRegex(deploy, r"\n    concurrency:\n      group: pages\n      cancel-in-progress: false\n")

    # The top: today in words over the pulse band, then the small multiples

    def test_the_page_leads_with_today_and_the_band_then_the_multiples_loop_numbers_nodes_and_about(self):
        order = re.findall(r'<h2 id="([a-z]+)">', self.html)
        self.assertEqual(order, ["multiples", "loop", "numbers", "nodes", "about"])
        top = self.html[:self.html.index('<h2 id="multiples">')]
        self.assertLess(top.index('<p class="today">'), top.index('<svg class="band"'))
        today = re.search(r'<p class="today">(.*?)</p>', top).group(1)
        self.assertIn(build.count(2, 3) + " answered", today)  # 11-02: nodes 1 and 4 replied
        self.assertIn(build.count(1, 6, "day") + " missed", today)
        self.assertIn('records sealed: <span class="value">0</span>, from ' + build.count(2, 3), today)
        # The prose is one collapsed section at the end.
        about = self.html[self.html.index('<h2 id="about">'):]
        self.assertTrue(about.split("</h2>", 1)[1].lstrip().startswith('<details class="about">'))

    def test_the_band_is_a_column_per_day_a_square_per_node(self):
        band = re.search(r'<svg class="band".*?</svg>', self.html, re.S).group(0)
        for date, n in (("2026-10-28", 3), ("2026-10-30", 4), ("2026-11-01", 3), ("2026-11-02", 3)):
            self.assertEqual(len(re.findall(rf'data-k="{date}\|checkpoints"', band)), n, date)
            self.assertEqual(len(re.findall(rf'data-k="{date}\|history_card"', band)), n, date)
        self.assertIn('<rect class="gap" data-k="2026-10-29" data-gap="the reporter missed this day"', band)
        self.assertIn('<rect class="nofile" data-k="2026-10-31" data-gap="no day file"', band)
        self.assertIn('<path class="tick-join"', band)  # node 4, first seen 10-30
        self.assertIn('<path class="tick-leave"', band)  # node 3, left 11-01
        self.assertNotIn("older", band)  # six days: no older band

    def test_records_sealed_bars_by_node_and_gaps_never_zero(self):
        c = self.chart("c-sealed")
        bars = re.findall(r'<rect class="bar sealed( longer)?" data-k="([\d-]+)\|([0-9a-f]{8})" data-v="(\d+)"', c)
        # Node 1: 22, 31, 41, 41 records; node 4: 2, 2. A node's first count adds nothing.
        self.assertEqual([(d, n, int(v)) for _, d, n, v in bars],
                         [("2026-10-30", "11111111", 9), ("2026-11-01", "11111111", 10),
                          ("2026-11-02", "11111111", 0), ("2026-11-02", "44444444", 0)])
        # 10-29 was missed, 10-31 has no file: the next count of node 1 covers more than one day, lighter.
        self.assertEqual([bool(b[0]) for b in bars], [True, True, False, False])
        self.assertEqual(c.count('<path class="bracket"'), 2)
        gaps = {k: build.html.unescape(v)
                for k, v in re.findall(r'<rect class="(?:gap|nofile)" data-k="([\d-]+)" data-gap="([^"]+)"', c)}
        self.assertEqual(gaps, {"2026-10-28": "no node's count could be compared with an earlier one",
                                "2026-10-29": "the reporter missed this day", "2026-10-31": "no day file"})
        self.assertIn('class="zeromark"', c)  # a measured zero is drawn as one; a gap never is

    def test_rehearsal_and_live_are_never_one_chart(self):
        self.assertIn("rehearsal nodes run only in the loop and are never added here", self.chart("c-sealed"))
        self.assertEqual(re.findall(r'<figure class="pop" data-population="(\w+)">', self.html), ["rehearsal", "live"])

    def test_no_negative_value_anywhere(self):
        self.assertIsNone(re.search(r'data-v="-|>-\d|−\d', self.html))
        for stack, data in self.text.chunks:
            self.assertIsNone(re.search(r"(^|\s)[-−]\d", data), data)

    def test_both_halves_is_a_count_below_the_fold_not_zero(self):
        s = re.search(r'<section class="number" id="n-halves">(.*?)</section>', self.html, re.S).group(1)
        self.assertIn(f"not measured yet ({build.code('M2')})", s)
        self.assertIn("A count of exchanges, out of the exchanges seen", s)
        self.assertIsNone(re.search(r">\s*0\b|\b0 of", s))
        self.assertGreater(self.html.index('id="n-halves"'), self.html.index("<svg"))

    def test_why_this_matters_is_short_plain_and_neutral(self):
        section = self.section("matters")
        words = len(re.sub(r"<[^>]+>", " ", section.split("</h3>", 1)[1]).split())
        self.assertTrue(100 <= words <= 150, words)
        self.assertIn("<h4>How it helps</h4>", section)
        low = section.lower()
        for word in ("price", "pricing", "fee", "pay", "economic", "reputation", "score", "rank", "underwrit",
                     "insur", "kyb", "customer", "premium", "registry"):
            self.assertNotIn(word, low)
        self.assertIn('href="#offline"', section)  # the offline check is not claimed before it exists

    # The loop

    TILE = re.compile(r'<a class="tile s-([A-Za-z_]+) l-([A-Z_]+)( held)?( ringed)?" href="status/#step-(\d\d)" '
                      r'data-step="(\d+)" data-label="([A-Z_]+)"( data-gaps="\d+")?')

    def test_the_loop_is_a_strip_per_population_from_the_status_rows(self):
        runs = {p: status_page.read_run(p, d) for p, d in status_page.latest_runs(self.findings).items()}
        rows = status_page.rows(runs)
        for p in status_page.POPULATIONS:
            tiles = self.TILE.findall(self.figure(p))
            self.assertEqual([int(t[5]) for t in tiles], list(range(1, 13)))
            for t, row in zip(tiles, rows):
                self.assertEqual((t[0], t[1], t[6]), (row.cells[p].state, row.cells[p].label, row.cells[p].label))
                self.assertEqual(bool(t[2]), row.held)
                self.assertEqual(t[4], f"{row.step.n:02d}")
        self.assertIn('class="tile s-green l-PASS" href="status/#step-01"', self.figure("rehearsal"))
        self.assertIn('class="tile s-NOT_CHECKED l-NOT_CHECKED" href="status/#step-02"', self.figure("rehearsal"))
        self.assertIn('class="tile s-red l-FAIL" href="status/#step-03"', self.figure("rehearsal"))
        self.assertIn('class="tile s-NOT_CHECKED l-NOT_CHECKED" href="status/#step-02"', self.figure("live"))
        # Rehearsal above live, each with its PASS-per-run sparkline.
        self.assertLess(self.html.index('data-population="rehearsal"><figcaption>'),
                        self.html.index('data-population="live"><figcaption>'))
        for p in status_page.POPULATIONS:
            self.assertIn(f'<svg class="spark" id="s-pass-{p}"', self.figure(p))

    def test_a_pass_step_with_a_gap_is_ringed(self):
        gaps = {1: ("seal", "the producer envelope signature is not verified in a second language",
                    "action-state-group/agent-action-capsule#457"),
                3: ("advertise", "alice: the stapled history_card/1 signature is not verified",
                    "action-state-group/agent-action-capsule#430"),
                12: ("join", "the controller binding of the joined node is not established",
                     "action-state-group/agent-action-capsule#457")}
        with tempfile.TemporaryDirectory() as d:
            findings = Path(d) / "findings"
            make_findings(findings)
            for n, (name, text, issue) in gaps.items():
                write_finding(findings, n, name, "PASS", "rehearsal",
                              detail=f"alice: PASS. Outside this step's assertion: {text}: {issue}.")
            self.assertEqual(run_build(DATA, findings, Path(d) / "_site")[0], 0)
            page = (Path(d) / "_site" / "stats" / "index.html").read_text()
        fig = re.search(r'<figure class="pop" data-population="rehearsal">(.*?)</figure>', page, re.S).group(1)
        for n, (_, text, issue) in gaps.items():
            self.assertIn(f'class="tile s-green l-PASS ringed" href="status/#step-{n:02d}" data-step="{n}" '
                          f'data-label="PASS" data-gaps="1"', fig)
            repo, num = issue.split("#")
            line = re.search(rf'<li><a href="status/#step-{n:02d}">.*?</li>', fig).group(0)
            self.assertIn(f"gap, outside the assertion: <q>{build.e(text)}</q>", line)
            self.assertIn(f'<a href="https://github.com/{repo}/issues/{num}">{issue}</a>', line)
        self.assertEqual(fig.count(' ringed"'), 3)
        self.assertNotIn("data-gaps", self.figure("rehearsal"))  # no gap sentence, no ring
        self.assertNotIn('class="gaps"', self.html)

    def number(self, key, population):
        s = re.search(rf'<section class="number" id="n-{key}">(.*?)</section>', self.html, re.S).group(1)
        return re.search(rf'<div class="num-cell" data-population="{population}"><p><strong>\w+</strong>: (.*?)</p>',
                         s).group(1)

    def page_number(self, page, key, population):
        s = re.search(rf'<section class="number" id="n-{key}">(.*?)</section>', page, re.S).group(1)
        return re.search(rf'<div class="num-cell" data-population="{population}">(.*?)</details></div>', s,
                         re.S).group(1)

    def test_under_a_minute_reads_in_seconds(self):
        with tempfile.TemporaryDirectory() as d:
            findings = Path(d) / "findings"
            make_findings(findings)
            (findings / "2026-10-27-rehearsal" / "run.json").write_text(
                json.dumps({"stage": "1", "population": "rehearsal", "started": "2026-10-27T02:00:18Z"}))
            self.assertEqual(run_build(DATA, findings, Path(d) / "_site")[0], 0)
            page = (Path(d) / "_site" / "stats" / "index.html").read_text()
        # Step 01 finished 02:01:00 and step 03, the run's last, 02:03:00.
        self.assertIn('<span class="count">42 s of the run\'s 2.7 minutes</span>',
                      self.page_number(page, "first-sealed", "rehearsal"))

    def test_under_one_second_reads_less_than_one_second_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            findings = Path(d) / "findings"
            make_findings(findings)
            (findings / "2026-10-27-rehearsal" / "run.json").write_text(
                json.dumps({"stage": "1", "population": "rehearsal", "started": "2026-10-27T02:01:00Z"}))
            self.assertEqual(run_build(DATA, findings, Path(d) / "_site")[0], 0)
            page = (Path(d) / "_site" / "stats" / "index.html").read_text()
        cell = self.page_number(page, "first-sealed", "rehearsal")
        self.assertIn('median <span class="value">&lt;1 s</span>', cell)  # step 01 finished at 02:01:00
        self.assertIn('<span class="count">&lt;1 s of the run\'s 2.0 minutes', cell)
        self.assertNotIn(">0 s", page)

    def test_the_header_badge_alt_counts_each_population(self):
        self.assertIn('<a href="status/"><img src="status/badge.svg" alt="loop: rehearsal 1 PASS · 1 FAIL · 10 not '
                      'checked, live 1 PASS · 0 FAIL · 11 not checked"></a>', self.html)

    def test_held_rows_follow_the_status_page(self):
        held = [int(t[5]) for t in self.TILE.findall(self.figure("live")) if t[2]]
        self.assertEqual(sorted(held), sorted(status_page.HELD_FOR_FLIP))
        loop = self.section("loop")
        self.assertIn(status_page.HELD_NOTE, loop)
        self.assertIn("Dashed: <code>04</code>, <code>05</code>, <code>06</code>, <code>09</code>.", loop)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(status_page, "DROP_HELD", True):
            self.assertEqual(run_build(DATA, self.findings, Path(d) / "_site")[0], 0)
            page = (Path(d) / "_site" / "stats" / "index.html").read_text()
        steps = re.findall(r'data-step="(\d+)"', page)
        self.assertEqual(sorted(set(map(int, steps))), [n for n in range(1, 13) if n not in status_page.HELD_FOR_FLIP])
        self.assertNotIn(" held", page.split('id="loop"')[1].split('id="numbers"')[0])
        self.assertIn(build.count(1, 8, "step"), page)

    def test_rehearsal_and_live_apart_never_summed_and_fail_malformed_not_checked_distinct(self):
        self.assertIn(f"{build.code('PASS')} {build.count(1, 12, 'step')}", self.figure("rehearsal"))
        self.assertIn(f"{build.code('FAIL')} {build.count(1, 12, 'step')}", self.figure("rehearsal"))
        self.assertIn(f"{build.code('MALFORMED')} {build.count(0, 12, 'step')}", self.figure("rehearsal"))
        self.assertIn(f"{build.code('NOT_CHECKED')} {build.count(10, 12, 'step')}", self.figure("rehearsal"))
        self.assertIn(f"{build.code('FAIL')} {build.count(0, 12, 'step')}", self.figure("live"))
        for _, data in self.text.chunks:
            m = COUNT.match(data.strip())
            if m and "step" in data:
                self.assertEqual(m.group(2), "12", data)  # never one population's steps added to the other's
        for nb in status_page.NUMBERS:
            s = re.search(rf'<section class="number" id="n-{nb.key}">(.*?)</section>', self.html, re.S).group(1)
            self.assertEqual(re.findall(r'class="num-cell" data-population="(\w+)"', s), ["rehearsal", "live"])

    def test_an_unsealed_live_first_answer_says_so_in_the_number_cell(self):
        nb = next(n for n in status_page.NUMBERS if n.key == "first-answered")
        with mock.patch.object(status_page, "number_value", return_value=("42 s", True)), \
                mock.patch.object(status_page, "minutes", return_value=0.7), \
                mock.patch.object(build, "run_minutes", return_value=1.0), \
                mock.patch.object(status_page, "first_ask", return_value=("lab-1 history_card/1", False)):
            text = build.number_text(nb, object())
        self.assertIn("first answer: lab-1 history_card/1; the request was not sealed before it was sent", text)
        with mock.patch.object(status_page, "number_value", return_value=("42 s", True)), \
                mock.patch.object(status_page, "minutes", return_value=0.7), \
                mock.patch.object(build, "run_minutes", return_value=1.0), \
                mock.patch.object(status_page, "first_ask", return_value=("lab-1 history_card/1", True)):
            text = build.number_text(nb, object())
        self.assertIn("(first answer: lab-1 history_card/1)", text)
        self.assertNotIn("not sealed", text)

    def test_the_five_numbers_with_m2_and_m3_as_placeholders(self):
        self.assertEqual([nb.key for nb in status_page.NUMBERS] + ["halves"],
                         re.findall(r'<section class="number" id="n-([a-z-]+)">', self.html))
        # Step 01 finished 02:01, run started 02:00; the rehearsal run's last finding ended 02:03, the live run's 02:01.
        self.assertEqual(self.number("first-sealed", "rehearsal"),
                         "<span class=\"count\">1.0 of the run's 3.0 minutes</span>")
        self.assertEqual(self.number("first-sealed", "live"),
                         "<span class=\"count\">1.0 of the run's 1.0 minutes</span>")
        self.assertEqual(self.number("first-answered", "rehearsal"), "not measured yet (<code>M2</code>)")
        for key, m in (("first-countersigned", "M3"), ("refused", "M3"), ("filtered", "M2")):
            for p in status_page.POPULATIONS:
                self.assertEqual(self.number(key, p), f"not measured yet (<code>{m}</code>)")
                # A dotted track with the milestone, and no value drawn.
                s = re.search(rf'<svg class="spark" id="n-{key}-{p}".*?</svg>', self.html, re.S).group(0)
                self.assertIn('<line class="track"', s)
                self.assertIn(f"not measured yet ({m})", s)
                self.assertNotIn("data-v=", s)

    # The nodes, day by day

    def test_a_heatmap_row_per_node_by_node_id(self):
        nodes = re.findall(r'<g class="node" data-node="([0-9a-f]{64})">', self.html)
        self.assertEqual(nodes, [nid(n) for n in (1, 2, 3, 4)])
        self.assertIn("the order means nothing", self.section("nodes"))
        self.assertEqual(re.findall(r'<details class="node-detail" data-node="([0-9a-f]{64})">', self.html), nodes)

    def test_each_state_missed_not_asked_and_no_file(self):
        self.assertEqual(self.mark(nid(1), "2026-10-28", "checkpoints"), "st-verified")
        self.assertEqual(self.mark(nid(2), "2026-10-28", "history_card"), "st-refusal")
        self.assertEqual(self.mark(nid(3), "2026-10-28", "checkpoints"), "st-verified")  # at the retry
        self.assertEqual(self.mark(nid(3), "2026-10-28", "history_card"), "st-absence")
        self.assertEqual(self.mark(nid(3), "2026-10-30", "checkpoints"), "st-not_evidence")
        self.assertEqual(self.mark(nid(3), "2026-10-30", "history_card"), "st-no_announced_key")
        self.assertEqual(self.mark(nid(4), "2026-11-01", "history_card"), "st-refusal")
        for n in (1, 2, 3, 4):
            self.assertEqual(self.mark(nid(n), "2026-10-29", "checkpoints"), "gap")
            self.assertEqual(self.mark(nid(n), "2026-10-31", "history_card"), "nofile")
        self.assertEqual(self.mark(nid(2), "2026-10-30", "checkpoints"), "st-absence")
        node4 = re.search(rf'<g class="node" data-node="{nid(4)}">(.*?)</g>', self.html, re.S).group(1)
        self.assertIn("not asked, not on the list this day", node4)  # 10-28: not listed yet
        nodes = self.section("nodes")
        self.assertIn(f"{build.count(4, 6, 'day')} with a settled run", nodes)
        self.assertIn(f"{build.count(1, 6, 'day')} missed", nodes)
        self.assertIn(f"{build.count(1, 6, 'day')} with no day file", nodes)

    def test_the_heatmap_is_large_enough_to_read(self):
        svg = re.search(r'<div class="scroll">(<svg class="heatmap" id="heatmap" width="(\d+)" height="\d+".*?</svg>)'
                        r"</div>", self.section("nodes"), re.S)
        self.assertIsNotNone(svg)  # drawn at its own size, in its own scroll box: the page never scrolls sideways
        marks = re.findall(r'<rect class="st-[a-z_]+" data-k="[^"]+" data-v="[^"]+" x="[\d.]+" y="[\d.]+" '
                           r'width="(\d+)" height="(\d+)"', svg.group(1))
        self.assertTrue(marks and all(int(w) >= 22 and int(h) >= 14 for w, h in marks), marks[:2])
        self.assertIn('upper: <tspan class="code">checkpoints</tspan>', svg.group(1))
        # Six days plus the labels fit a 390 px phone inside its 16 px gutters.
        self.assertLessEqual(int(svg.group(2)), 390 - 32)

    def test_left_on(self):
        node = re.search(rf'<g class="node" data-node="{nid(3)}">(.*?)</g>', self.html, re.S).group(1)
        self.assertIn('left <tspan class="code">11-01</tspan>', node)
        self.assertIn('<line class="leftmark"', node)
        table = re.search(rf'<details class="node-detail" data-node="{nid(3)}">(.*?)</details>', self.html,
                          re.S).group(1)
        self.assertIn('left on <time datetime="2026-11-01">', table)
        self.assertNotIn("left", re.search(rf'data-node="{nid(2)}">(.*?)</g>', self.html, re.S).group(1))

    def test_each_node_table_shows_the_scrubbed_fields_and_answer_sha256(self):
        n1 = re.search(rf'<details class="node-detail" data-node="{nid(1)}">(.*?)</details>', self.html, re.S).group(1)
        self.assertIn(f'<code class="sha">sha256={"1a" * 32}</code>', n1)
        self.assertIn("<code>mmr_size=41</code> at <time", n1)
        n3 = re.search(rf'<details class="node-detail" data-node="{nid(3)}">(.*?)</details>', self.html, re.S).group(1)
        self.assertIn('asked twice<ul class="attempts"><li><time datetime="2026-10-28T06:00:00Z">2026-10-28T06:00:00Z'
                      '</time> <code class="outcome o-unreachable">unreachable</code>', n3)
        n2 = re.search(rf'<details class="node-detail" data-node="{nid(2)}">(.*?)</details>', self.html, re.S).group(1)
        self.assertIn('<code class="fact">absent 2 days running</code>', n2)
        self.assertIn('<code class="fact">absent today</code>', n2)
        missed = self.chart("c-missed")
        self.assertIn("the reporter did not run: its host was down", missed)

    def test_every_link_stays_off_reports_and_raw_bytes(self):
        for page in self.out.rglob("*.html"):
            for href in re.findall(r'(?:href|src)="([^"]*)"', page.read_text()):
                self.assertIsNone(re.search(r"(^|/)reports(/|$)", href), f"{page.name}: {href}")
        data_links = re.findall(r'href="(data/daily/[^"]+)"', self.html)
        self.assertEqual(sorted(set(data_links)), sorted(f"data/daily/{p.name}" for p in DATA.glob("*.json")))
        for href in data_links:
            self.assertEqual((self.out / "stats" / href).read_bytes(), (DATA / Path(href).name).read_bytes())
        for name in (".request.json", ".response.json", ".verification", "absence.txt", "peer-key.json", "RUN.json"):
            self.assertNotIn(name, self.html)

    # The rules

    def test_every_number_has_a_denominator(self):
        """A number in the page's text is a count "N of M ...", a quoted value (code), a time, hover or SVG
        description text, a measured value the sentence around it qualifies, or a cell in a table of numbers whose
        header names it."""
        counts = 0
        for stack, data in self.text.chunks:
            ctx = self.text.context(stack)
            if ctx in ("code", "time", "style", "title", "desc"):
                continue
            if ctx == "value":
                continue
            if ctx == "num":
                self.assertTrue(any(tag == "details" for tag, _ in stack), data)
                continue
            if ctx == "count":
                m = COUNT.match(data.strip())
                self.assertTrue(m, data)
                self.assertLessEqual(float(m.group(1)), float(m.group(2)), data)
                counts += 1
                continue
            if ISSUE_REF.fullmatch(data.strip()):
                continue  # a cited issue, as its finding gives it
            self.assertIsNone(re.search(r"\d", data), f"a number with no denominator: {data.strip()!r}")
        self.assertGreater(counts, 10)

    def test_no_score_rank_or_league_table(self):
        for header in re.findall(r'<th scope="col">(.*?)</th>', self.html):
            self.assertIsNone(re.search(r"score|rank|league|total|%|percent", header.lower()), header)
        for word in ("score", "rank", "league", "leaderboard", "percent", "%", "total", "top"):
            for tag, attrs in self.text.tags:
                for k, v in attrs.items():
                    if k not in ("d", "transform", "viewbox", "x", "y", "x1", "x2", "y1", "y2"):
                        self.assertNotIn(word, f"{k}={v}".lower().replace("stop", ""), (tag, attrs))
        for stack, data in self.text.chunks:
            if self.text.context(stack) in ("prose", "count", "title", "desc"):
                low = data.lower()
                for word in ("rank", "league", "leaderboard", "%", "total", "percent"):
                    self.assertTrue(word not in low or "no ranking" in low or "not a total" in low, data)
                if "score" in low:
                    self.assertTrue("no score" in low or "into a number" in low, data)
        # The heatmap's rows and the node tables are by node id, whatever they answered.
        rows = re.findall(r'<g class="node" data-node="([0-9a-f]{64})">', self.html)
        self.assertEqual(rows, sorted(rows))

    def test_nothing_says_checked_verified_or_witnessed_unless_pass(self):
        for stack, data in self.text.chunks:
            if self.text.context(stack) in ("code", "style"):
                continue
            if CLAIMS.search(data):
                self.assertTrue(any("s-green" in cls for _, cls in stack), f"a claim outside a PASS: {data.strip()!r}")

    def test_not_checked_is_never_drawn_as_pass(self):
        for p in status_page.POPULATIONS:
            for t in self.TILE.findall(self.figure(p)):
                self.assertEqual(t[0] == "green", t[1] == "PASS")
                self.assertEqual(t[1], t[6])

    def test_the_offline_check_is_not_faked(self):
        section = self.section("offline")
        self.assertIn("Not yet available.", section)
        self.assertNotIn("<pre", section)


class EmptyDataTest(unittest.TestCase):
    def test_no_day_yet_and_no_findings_builds(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "daily"
            data.mkdir()
            (data / ".gitkeep").write_text("")
            code, err = run_build(data, Path(d) / "findings", Path(d) / "_site")
            self.assertEqual(code, 0, err)
            page = (Path(d) / "_site" / "stats" / "index.html").read_text()
            self.assertIn("No day yet.", page)
            self.assertIn("no run yet", page)
            self.assertNotIn("<svg class=\"timeline\"", page)

    def test_a_missing_data_directory_fails(self):
        with tempfile.TemporaryDirectory() as d:
            code, err = run_build(Path(d) / "nope", Path(d) / "findings", Path(d) / "_site")
            self.assertEqual(code, 1)
            self.assertIn("no such directory", err)


class BrokenDayFileTest(unittest.TestCase):
    """A day file the site cannot read fails the build, and nothing is written."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "daily"
        shutil.copytree(DATA, self.data)
        self.out = Path(self.tmp.name) / "_site"

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, date, change):
        path = self.data / f"{date}.json"
        d = json.loads(path.read_text())
        change(d)
        path.write_text(json.dumps(d, sort_keys=True, indent=2) + "\n")

    def refused(self, needle):
        code, err = run_build(self.data, Path(self.tmp.name) / "findings", self.out)
        self.assertEqual(code, 1, err)
        self.assertIn(needle, err)
        self.assertIn("the stats site was not built", err)
        self.assertFalse(self.out.exists())

    def q(self, d, i=0, stem="checkpoints"):
        return d["nodes"][i]["questions"][stem]

    def test_the_fixtures_build(self):
        self.assertEqual(run_build(self.data, Path(self.tmp.name) / "findings", self.out)[0], 0)

    def test_not_json(self):
        (self.data / "2026-10-28.json").write_text("{")
        self.refused("2026-10-28.json: not JSON")

    def test_not_the_fixed_form(self):
        path = self.data / "2026-10-28.json"
        path.write_text(json.dumps(json.loads(path.read_text()), indent=4))
        self.refused("not in the fixed form")

    def test_an_unknown_key(self):
        self.edit("2026-10-28", lambda d: d.update(score=1))
        self.refused("unexpected score")

    def test_answer_bytes_are_refused(self):
        self.edit("2026-10-28", lambda d: self.q(d).update(response="eyJ2ZXJpZmljYXRpb24iOnt9fQ=="))
        self.refused("unexpected response")

    def test_a_missing_key(self):
        self.edit("2026-10-28", lambda d: d["nodes"][0].pop("left_on"))
        self.refused("missing left_on")

    def test_an_unknown_state(self):
        self.edit("2026-10-28", lambda d: self.q(d).update(state="great"))
        self.refused("state 'great' is not one of")

    def test_an_unknown_attempt_state(self):
        self.edit("2026-10-28", lambda d: self.q(d)["attempts"][0].update(state="slow"))
        self.refused("state 'slow' is not one of")

    def test_a_state_its_attempts_do_not_settle(self):
        self.edit("2026-10-28", lambda d: self.q(d, 2).update(state="refusal"))
        self.refused("but the attempts settle as 'verified'")

    def test_an_answer_hash_on_an_absence(self):
        self.edit("2026-10-28", lambda d: self.q(d, 2, "history_card").update(answer_sha256="0" * 64))
        self.refused("answer_sha256 must be null exactly when the state is absence")

    def test_a_bad_hash(self):
        self.edit("2026-10-28", lambda d: self.q(d).update(answer_sha256="xyz"))
        self.refused("answer_sha256 is not 64 hex or null")

    def test_a_date_that_is_not_the_file_name(self):
        self.edit("2026-10-28", lambda d: d.update(date="2026-10-27"))
        self.refused("the file name says 2026-10-28")

    def test_a_stray_file(self):
        (self.data / "notes.txt").write_text("x")
        self.refused("notes.txt: not a <YYYY-MM-DD>.json day file")

    def test_a_missed_day_with_nodes(self):
        src = json.loads((self.data / "2026-10-28.json").read_text())["nodes"]
        self.edit("2026-10-29", lambda d: d.update(nodes=src))
        self.refused("a missed day has no nodes")

    def test_nodes_out_of_order(self):
        self.edit("2026-10-28", lambda d: d["nodes"].reverse())
        self.refused("nodes are not sorted by node_id")

    def test_more_nodes_than_listed(self):
        self.edit("2026-10-28", lambda d: d.update(listed=2))
        self.refused("3 nodes but only 2 listed")

    def test_absent_days_running_that_contradicts_the_day(self):
        self.edit("2026-11-02", lambda d: d["nodes"][1].update(absent_days_running=0))
        self.refused("absent_days_running is 0 but the node answered nothing")

    def test_a_bad_attempt_time(self):
        self.edit("2026-10-28", lambda d: self.q(d)["attempts"][0].update(at="yesterday"))
        self.refused("at is not a UTC timestamp")

    def test_an_existing_site_is_left_alone(self):
        self.out.mkdir()
        (self.out / "keep").write_text("old")
        (self.data / "2026-10-28.json").write_text("{")
        code, _ = run_build(self.data, Path(self.tmp.name) / "findings", self.out)
        self.assertEqual(code, 1)
        self.assertEqual((self.out / "keep").read_text(), "old")


class SealedPerDayTest(unittest.TestCase):
    """stats_model.sealed_per_day: a node's count minus its last earlier one; a drop is a gap, never negative."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "daily"
        shutil.copytree(DATA, self.data)
        self.out = Path(self.tmp.name) / "_site"

    def tearDown(self):
        self.tmp.cleanup()

    def set_size(self, date, i, mmr):
        path = self.data / f"{date}.json"
        d = json.loads(path.read_text())
        q = d["nodes"][i]["questions"]["checkpoints"]
        q["log_size_at_checkpoint"]["mmr_size"] = mmr
        q["records_at_checkpoint"] = build.stats_model.leaves(mmr)
        path.write_text(json.dumps(d, sort_keys=True, indent=2) + "\n")

    def test_a_drop_is_a_gap_with_a_note_never_negative(self):
        self.set_size("2026-11-02", 0, 41)  # node 1: 41 records on 11-01, 22 on 11-02
        points = {p.date: p for p in stats_model.sealed_per_day(stats_model.load(self.data))}
        self.assertIsNone(points["2026-11-02"].sealed)
        self.assertEqual(points["2026-11-02"].drops, [(nid(1), "2026-11-01")])
        self.assertTrue(all(p.sealed is None or p.sealed >= 0 for p in points.values()))
        self.assertEqual(run_build(self.data, Path(self.tmp.name) / "findings", self.out)[0], 0)
        page = (self.out / "stats" / "index.html").read_text()
        sealed = re.search(r'<figure class="chart" id="f-c-sealed">.*?</figure>', page, re.S).group(0)
        self.assertIn('<rect class="gap" data-k="2026-11-02" data-gap="a node&#x27;s count fell"', sealed)
        self.assertNotIn('data-k="2026-11-02|', sealed)
        self.assertIn('<time datetime="2026-11-02">2026-11-02</time>: node <code title="' + nid(1), page)
        self.assertIn("A drop is not drawn as a negative value", page)
        self.assertIsNone(re.search(r'data-v="-', page))

    def test_a_count_counts_from_the_last_earlier_known_one(self):
        # Node 1 is not verified on 11-01: 11-02 compares with 10-30.
        path = self.data / "2026-11-01.json"
        d = json.loads(path.read_text())
        d["nodes"][0]["questions"]["checkpoints"].update(log_size_at_checkpoint=None, records_at_checkpoint=None)
        path.write_text(json.dumps(d, sort_keys=True, indent=2) + "\n")
        points = {p.date: p for p in stats_model.sealed_per_day(stats_model.load(self.data))}
        self.assertEqual((points["2026-11-02"].sealed, points["2026-11-02"].nodes), (10, 2))

    def test_records_that_are_not_the_checkpoints_leaf_count_are_refused(self):
        path = self.data / "2026-10-28.json"
        d = json.loads(path.read_text())
        d["nodes"][0]["questions"]["checkpoints"]["records_at_checkpoint"] = 41
        path.write_text(json.dumps(d, sort_keys=True, indent=2) + "\n")
        code, err = run_build(self.data, Path(self.tmp.name) / "findings", self.out)
        self.assertEqual(code, 1)
        self.assertIn("records_at_checkpoint is not the leaf count", err)


class FixturesTest(unittest.TestCase):
    def test_the_fixtures_are_what_make_fixtures_writes(self):
        sys.path.insert(0, str(DATA.parent))
        import make_fixtures
        made = {f"{d['date']}.json": json.dumps(d, sort_keys=True, indent=2) + "\n" for d in make_fixtures.days()}
        self.assertEqual(made, {p.name: p.read_text() for p in DATA.glob("*.json")})
        self.assertEqual(len(stats_model.load(DATA)), 5)


if __name__ == "__main__":
    unittest.main()
