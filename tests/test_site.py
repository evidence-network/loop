"""Tests for the report page (site/), built from reports the real reporter wrote.

The reports come from scripts/daily_report.py run against the reporter fixtures
(tests/reporter/fixtures) through test_reporter's fake console, so the page is
tested on the layout the reporter actually writes: one directory per attempt,
a retry within the run, an ask-again after it, and a node that left.

Run: python3 -B -m unittest discover -s tests -v
"""
import contextlib
import datetime as dt
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "site"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402
import check_nodes  # noqa: E402
import check_reports  # noqa: E402
import daily_report  # noqa: E402
import report_layout as layout  # noqa: E402
import reports_model  # noqa: E402
from test_offline_retry import Clock  # noqa: E402
from test_reporter import REPORTER, FakeConsole, entry, key_id, node_id  # noqa: E402

DAY, MISSED, LATER, LAST = "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31"
COUNT = re.compile(r"^(\d+) of (\d+) listed nodes?$")


class Text(HTMLParser):
    """The page's text, split by context: inside <code>, inside <time>, in a count span, or plain."""

    def __init__(self):
        super().__init__()
        self.stack, self.chunks, self.tags = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        self.stack.append((tag, dict(attrs).get("class", "")))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        if data.strip():
            self.chunks.append((list(self.stack), data))

    def context(self, stack):
        for tag, cls in reversed(stack):
            if tag == "span" and cls == "count":
                return "count"
            if tag in ("code", "time", "style", "title"):
                return tag
        return "prose"


REPORTS = LEFT = None  # built once, in setUpModule
_TMP = None


def setUpModule():
    global REPORTS, LEFT, _TMP
    _TMP = tempfile.TemporaryDirectory()
    d = Path(_TMP.name)
    REPORTS = d / "reports" / "daily"
    nodes = d / "nodes"
    nodes.mkdir()
    LEFT = nodes / "left"
    reporter = d / "reporter.json"
    reporter.write_text(json.dumps({**REPORTER, "retry_after_s": [3600]}))  # one more attempt, an hour in
    both = lambda what: {"checkpoints": what, "history_card": what}  # noqa: E731
    # One node per outcome class; listed out of id order so the page has to sort.
    behaviour = {
        node_id(6): both("not_evidence"),
        node_id(1): both("verified"),
        node_id(2): both("refusal"),
        node_id(3): both("no_announced_key"),
        node_id(4): {"checkpoints": "verified", "history_card": "absence"},
        node_id(5): both("timeout"),
        node_id(8): both("no_checkpoint_yet"),
    }
    announced = {n: key_id(int(n[0], 16)) for n in behaviour if n != node_id(3)}
    for n in behaviour:
        (nodes / f"{n}.json").write_text(json.dumps(entry(n, key_id(int(n[0], 16)))))
    console = FakeConsole(behaviour, announced, delay=0.6)

    def main(verb, date, clock, *extra):
        argv = ["daily_report.py", verb, *extra, "--date", date, "--console", console.url, "--nodes", str(nodes),
                "--out", str(REPORTS), "--reporter", str(reporter), "--timeout", "0.5"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert daily_report.main(argv, now=clock.now, sleep=clock.sleep) == 0

    def morning(date, hour=6):
        return Clock(dt.datetime.fromisoformat(date).replace(hour=hour, tzinfo=dt.timezone.utc))

    try:
        # DAY: node 4's history_card gets no answer at first and one at the retry an hour in.
        clock = morning(DAY)
        clock.at.append((clock.t + dt.timedelta(seconds=3600),
                         lambda: behaviour[node_id(4)].update(history_card="verified")))
        main("run", DAY, clock)
        # Node 8 leaves before LATER; the LATER run marks the day between MISSED.
        (nodes / f"{node_id(8)}.json").unlink()
        LEFT.mkdir()
        (LEFT / f"{node_id(8)}.json").write_text(json.dumps({"node_id": node_id(8), "left": LATER}))
        behaviour[node_id(4)]["history_card"] = "absence"
        main("run", LATER, morning(LATER))
        main("run", LAST, morning(LAST))
        # LAST: node 4 answers its history_card when asked again in the afternoon.
        behaviour[node_id(4)]["history_card"] = "verified"
        main("ask", LAST, morning(LAST, 15), "--node", node_id(4))
    finally:
        console.close()
    assert check_reports.check(REPORTS)[0] == []
    assert check_nodes.check_left(LEFT, set()) == []


def tearDownModule():
    _TMP.cleanup()


class SiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.reports = REPORTS
        cls.out = Path(cls.tmp.name) / "_site"
        with contextlib.redirect_stdout(io.StringIO()):
            assert build.main(["build.py", str(cls.reports), "--out", str(cls.out), "--left", str(LEFT)]) == 0
        cls.html = (cls.out / "index.html").read_text(encoding="utf-8")
        cls.text = Text()
        cls.text.feed(cls.html)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def section(self, date):
        start = self.html.index(f'id="d{date}"')
        end = self.html.find('<section class="day', start)
        return self.html[start:end if end > 0 else None]

    def row(self, date, n):
        day = self.section(date)
        row = day[day.index(f'<th scope="row"><code title="{node_id(n)}"'):]  # not another node's key cell
        return row[:row.index("</tr>")]

    def cell(self, date, n, stem):
        """One question's cell in a node's row: checkpoints first, then history_card."""
        cells = re.findall(r'<td class="answer o-[a-z_]+">.*?</td>', self.row(date, n), re.S)
        return cells[list(reports_model.QUESTIONS).index(stem)]

    def attempt(self, date, n, i=0):
        return layout.attempts(self.reports / date / node_id(n))[i]

    def test_every_outcome_class_and_the_missed_day_are_shown(self):
        day = self.section(DAY)
        for o in reports_model.OUTCOMES:
            self.assertIn(f'<td class="answer o-{o}">', day, o)
        missed = self.section(MISSED)
        self.assertIn("Missed:", missed)
        self.assertIn("the reporter did not run", missed)
        self.assertIn('href="reports/daily/2026-10-29/MISSED"', missed)

    def test_one_row_per_listed_node_ordered_by_node_id(self):
        day = self.section(DAY)
        rows = re.findall(r'<th scope="row"><code title="([0-9a-f]{64})"', day)
        self.assertEqual(rows, sorted(rows))
        self.assertEqual(len(rows), 7)
        self.assertIn("Nodes are listed by node id. The order means nothing.", day)

    def test_every_number_has_a_denominator(self):
        """A number in the page's text is a count "N of M listed nodes", or a quoted value (code) or a time."""
        counts = 0
        for stack, data in self.text.chunks:
            ctx = self.text.context(stack)
            if ctx in ("code", "time", "style", "title"):
                continue
            if ctx == "count":
                m = COUNT.match(data.strip())
                self.assertTrue(m, data)
                n, of = map(int, m.groups())
                self.assertLessEqual(n, of, data)
                counts += 1
                continue
            self.assertIsNone(re.search(r"\d", data), f"a number with no denominator: {data.strip()!r}")
        self.assertGreater(counts, 0)
        self.assertIn(build.count(1, 7), self.section(DAY))  # node 6 alone is not_evidence
        self.assertIn(f"{build.count(6, 7)} answered", self.section(DAY))  # node 5 timed out on both

    def test_counts_of_one_day_use_that_days_listed_nodes(self):
        day = Text()
        day.feed(self.section(DAY))
        for _, data in day.chunks:
            m = COUNT.match(data.strip())
            if m:
                self.assertEqual(m.group(2), "7", data)

    def test_the_word_verified_is_only_the_outcome_class(self):
        for stack, data in self.text.chunks:
            if self.text.context(stack) == "style":
                continue
            if re.search(r"verif", data, re.I):
                ctx = [(t, c) for t, c in stack if t == "code"]
                self.assertTrue(ctx, f"'verif...' outside code: {data.strip()!r}")
                if data.strip() == "verified":
                    self.assertTrue(ctx[-1][1].startswith("outcome"), data)
                # else: quoted record text (a file name, a result line, a reason the plugin gave)

    def test_no_score_rank_or_table_of_nodes_by_result(self):
        headers = re.findall(r'<th scope="col">(.*?)</th>', self.html)
        self.assertEqual(len(set(headers)), 5)
        for word in ("score", "rank", "league", "leaderboard", "percent", "%", "total"):
            for tag, attrs in self.text.tags:
                self.assertNotIn(word, " ".join(map(str, attrs.values())).lower(), (tag, attrs))
        for stack, data in self.text.chunks:
            if self.text.context(stack) == "prose":
                low = data.lower()
                for word in ("rank", "league", "leaderboard", "%", "total"):
                    self.assertTrue(word not in low or "no ranking" in low, data)
                if "score" in low:
                    self.assertTrue("no score" in low or "into a number" in low, data)

    def test_static_and_no_bracketed_internal_ids(self):
        self.assertNotIn("<script", self.html.lower())
        self.assertIsNone(re.search(r"\son[a-z]+=", self.html))
        # The public leak-lint rule: [ + three or more hyphenated lowercase segments + ].
        self.assertIsNone(re.search(r"\[[a-z0-9]+(?:-[a-z0-9]+){2,}\]", self.html))

    def test_every_link_opens_the_raw_bytes(self):
        links = re.findall(r'href="((?:reports/daily|nodes/left)/[^"]+)"', self.html)
        self.assertGreater(len(links), 0)
        for href in links:
            self.assertTrue((self.out / href).is_file(), href)
        for path in self.reports.rglob("*"):
            if path.is_file():
                self.assertEqual((self.out / "reports" / "daily" / path.relative_to(self.reports)).read_bytes(),
                                 path.read_bytes())
        n1 = self.attempt(DAY, 1).relative_to(self.reports.parent.parent).as_posix()
        for name in ("checkpoints.request.json", "checkpoints.response.json", "checkpoints.verification"):
            self.assertIn(f'href="{n1}/{name}"', self.html)
        for i in (0, 1):  # node 5 timed out at both attempts; each has its own why
            n5 = self.attempt(DAY, 5, i).relative_to(self.reports.parent.parent).as_posix()
            self.assertIn(f'href="{n5}/checkpoints.absence.txt"', self.html)

    def test_a_verified_row_shows_checkpoints_anchor_epoch_attempts_and_its_line(self):
        row = self.row(DAY, 1)
        self.assertIn("<code>checkpoints=3</code>", row)
        self.assertIn(f'<code title="{"a" * 64}">', row)
        self.assertIn("epoch</dt>", row)
        self.assertIn('asked once<ul class="attempts"><li><time datetime="2026-10-28T06:00:00Z">', row)
        self.assertIn("<code>by=run pass=1</code>", row)
        line = (self.reports / DAY / node_id(1) / "checkpoints.verification").read_text().strip()
        self.assertIn(f'<code class="line">{line}</code>', row)

    def test_attempts_per_question_come_from_attempts(self):
        """Node 4's history_card: unreachable at the run's first pass, an answer at the retry an hour in."""
        cell = self.cell(DAY, 4, "history_card")
        self.assertIn('<td class="answer o-verified">', cell)
        tries = re.findall(r'<li><time datetime="([^"]+)">.*?<code class="outcome o-([a-z_]+)">.*?<code>(by=[^<]+)</code>',
                           cell)
        self.assertEqual(tries, [("2026-10-28T06:00:00Z", "unreachable", "by=run pass=1"),
                                 ("2026-10-28T07:00:00Z", "verified", "by=run pass=2")])
        self.assertIn("asked twice", cell)
        self.assertIn("asked once", self.cell(DAY, 4, "checkpoints"))  # answered at once: never asked again
        # Nothing came back for node 5: the day's absence over both attempts.
        cell = self.cell(DAY, 5, "checkpoints")
        self.assertIn("asked twice", cell)
        self.assertIn("attempts=2026-10-28T06:00:00Z,2026-10-28T07:00:00Z", cell)

    def test_a_re_ask_answer_is_shown_from_the_attempts(self):
        settled = (self.reports / LAST / node_id(4) / "history_card.verification").read_text()
        self.assertTrue(settled.startswith("absence "), settled)  # the run's settled files stay as they were
        cell = self.cell(LAST, 4, "history_card")
        self.assertIn('<td class="answer o-verified">', cell)
        self.assertIn("<code>by=ask</code>", cell)
        self.assertIn("Answered when asked again after the run.", cell)
        again = self.attempt(LAST, 4, -1).relative_to(self.reports.parent.parent).as_posix()
        self.assertIn(f'href="{again}/history_card.response.json"', cell)

    def test_absent_days_running_is_a_fact_about_the_node(self):
        fact = lambda date, n: re.findall(r'<code class="fact">([^<]+)</code>', self.row(date, n))  # noqa: E731
        self.assertEqual(fact(LAST, 5), ["absent 2 days running"])
        self.assertEqual(fact(LATER, 5), ["absent today"])  # the MISSED day before it ends the run of days
        self.assertEqual(fact(DAY, 5), ["absent today"])
        self.assertEqual(fact(LAST, 4), [])  # it answered checkpoints
        self.assertEqual(fact(LAST, 1), [])

    def test_a_node_that_left_is_shown_left_on_its_date(self):
        self.assertIn(f'<code title="{node_id(8)}">', self.html[self.html.index('id="left"'):self.html.index('id="d')])
        self.assertIn(f'left on <time datetime="{LATER}">{LATER}</time>', self.html)
        self.assertIn(f'href="nodes/left/{node_id(8)}.json"', self.html)
        self.assertNotIn(f'<th scope="row"><code title="{node_id(8)}"', self.section(LATER))
        self.assertIn(f'<th scope="row"><code title="{node_id(8)}"', self.section(DAY))  # its days already reported stay

    def test_the_offline_check_is_not_faked(self):
        """No command re-checks a saved answer yet (see the report); the page must say so and show none."""
        start = self.html.index('id="offline"')
        section = self.html[start:self.html.index("<h2", start)]
        self.assertIn("Not yet available.", section)
        self.assertNotIn("<pre", section)

    def test_no_reports_builds_an_empty_page(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "daily").mkdir()
            (Path(d) / "daily" / ".gitkeep").write_text("")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(build.main(["build.py", f"{d}/daily", "--out", f"{d}/_site", "--left", f"{d}/left"]), 0)
            page = (Path(d) / "_site" / "index.html").read_text()
            self.assertIn("No report yet.", page)
            self.assertNotIn('id="left"', page)


class BrokenReportTest(unittest.TestCase):
    """A report the page cannot read fails the build, and nothing is written."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = Path(self.tmp.name) / "daily"
        shutil.copytree(REPORTS, self.reports)
        self.left = Path(self.tmp.name) / "left"
        shutil.copytree(LEFT, self.left)
        self.node = self.reports / DAY / node_id(1)
        self.attempt = layout.attempts(self.node)[0]  # where the page reads node 1's answers

    def tearDown(self):
        self.tmp.cleanup()

    def build(self):
        out = Path(self.tmp.name) / "_site"
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = build.main(["build.py", str(self.reports), "--out", str(out), "--left", str(self.left)])
        return code, err.getvalue(), out

    def refused(self, needle):
        code, err, out = self.build()
        self.assertEqual(code, 1, err)
        self.assertIn(needle, err)
        self.assertIn("the page was not built", err)
        self.assertFalse(out.exists())

    def test_unknown_outcome(self):
        (self.attempt / "checkpoints.verification").write_text("great checkpoints=3\n")
        self.refused("checkpoints.verification is not one line starting with one of")  # check_reports' refusal

    def test_unreadable_detail(self):
        (self.attempt / "history_card.verification").write_text('refusal reason="unterminated\n')
        self.refused("cannot read")

    def test_verified_without_a_checkpoint_count(self):
        (self.attempt / "checkpoints.verification").write_text(f"verified anchor={'a' * 64}\n")
        self.refused("checkpoints=<n>")

    def test_verified_line_over_a_refusal(self):
        refusal = (ROOT / "tests" / "reporter" / "fixtures" / "refusal.json").read_bytes()
        (self.attempt / "checkpoints.response.json").write_bytes(refusal)
        (self.node / "checkpoints.response.json").write_bytes(refusal)  # still the bytes of an attempt
        self.refused("verification state is 'refusal'")

    def test_stray_file_in_an_attempt(self):
        (self.attempt / "rank.txt").write_text("1\n")
        self.refused("unexpected file(s) rank.txt")

    def test_bad_leave_mark(self):
        (self.left / f"{node_id(8)}.json").write_text(json.dumps({"node_id": node_id(8), "left": "soon"}))
        self.refused("left must be a YYYY-MM-DD date")

    def test_bad_run_json(self):
        (self.reports / DAY / "RUN.json").write_text("{")
        self.refused("RUN.json unreadable")

    def test_stray_file(self):
        (self.node / "score.json").write_text("{}")
        self.refused("unexpected file(s) score.json")

    def test_missing_answer(self):
        (self.node / "checkpoints.response.json").unlink()
        self.refused("needs exactly one of")

    def test_unknown_node_line(self):
        (self.node / "NODE").write_text("excellent\n")
        self.refused("NODE must say")

    def test_missed_day_with_files(self):
        (self.reports / MISSED / "extra").write_text("x")
        self.refused("a MISSED day holds nothing else")

    def test_not_a_day(self):
        (self.reports / "latest").mkdir()
        self.refused("latest: not a <YYYY-MM-DD> directory")


if __name__ == "__main__":
    unittest.main()
