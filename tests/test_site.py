"""Tests for the report page (site/), built from reports the real reporter wrote.

The reports come from scripts/daily_report.py run against the reporter fixtures
(tests/reporter/fixtures) through test_reporter's fake console, so the page is
tested on the layout the reporter actually writes.

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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "site"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402
import check_reports  # noqa: E402
import daily_report  # noqa: E402
import reports_model  # noqa: E402
from test_reporter import REPORTER, FakeConsole, entry, key_id, node_id  # noqa: E402

DAY, MISSED, LATER = "2026-10-28", "2026-10-29", "2026-10-30"
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


REPORTS = None  # built once, in setUpModule
_TMP = None


def setUpModule():
    global REPORTS, _TMP
    _TMP = tempfile.TemporaryDirectory()
    d = Path(_TMP.name)
    REPORTS = d / "reports" / "daily"
    nodes = d / "nodes"
    nodes.mkdir()
    reporter = d / "reporter.json"
    reporter.write_text(json.dumps(REPORTER))
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
    try:
        for date in (DAY, LATER):  # the LATER run marks the day between MISSED
            argv = ["daily_report.py", "run", "--date", date, "--console", console.url, "--nodes", str(nodes),
                    "--out", str(REPORTS), "--reporter", str(reporter), "--timeout", "0.5"]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                assert daily_report.main(argv) == 0
    finally:
        console.close()
    assert check_reports.check(REPORTS)[0] == []


def tearDownModule():
    _TMP.cleanup()


class SiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.reports = REPORTS
        cls.out = Path(cls.tmp.name) / "_site"
        with contextlib.redirect_stdout(io.StringIO()):
            assert build.main(["build.py", str(cls.reports), "--out", str(cls.out)]) == 0
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
        links = re.findall(r'href="(reports/daily/[^"]+)"', self.html)
        self.assertGreater(len(links), 0)
        for href in links:
            self.assertTrue((self.out / href).is_file(), href)
        for path in self.reports.rglob("*"):
            if path.is_file():
                self.assertEqual((self.out / "reports" / "daily" / path.relative_to(self.reports)).read_bytes(),
                                 path.read_bytes())
        n1 = f"reports/daily/{DAY}/{node_id(1)}"
        for name in ("checkpoints.request.json", "checkpoints.response.json", "checkpoints.verification"):
            self.assertIn(f'href="{n1}/{name}"', self.html)
        self.assertIn(f'href="reports/daily/{DAY}/{node_id(5)}/checkpoints.absence.txt"', self.html)

    def test_a_verified_row_shows_checkpoints_anchor_epoch_attempts_and_its_line(self):
        day = self.section(DAY)
        row = day[day.index(f'<code title="{node_id(1)}"'):]
        row = row[:row.index("</tr>")]
        self.assertIn("<code>checkpoints=3</code>", row)
        self.assertIn(f'<code title="{"a" * 64}">', row)
        self.assertIn("epoch</dt>", row)
        self.assertIn("asked once: during the run", row)
        line = (self.reports / DAY / node_id(1) / "checkpoints.verification").read_text().strip()
        self.assertIn(f'<code class="line">{line}</code>', row)

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
                self.assertEqual(build.main(["build.py", f"{d}/daily", "--out", f"{d}/_site"]), 0)
            self.assertIn("No report yet.", (Path(d) / "_site" / "index.html").read_text())


class BrokenReportTest(unittest.TestCase):
    """A report the page cannot read fails the build, and nothing is written."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = Path(self.tmp.name) / "daily"
        shutil.copytree(REPORTS, self.reports)
        self.node = self.reports / DAY / node_id(1)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self):
        out = Path(self.tmp.name) / "_site"
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = build.main(["build.py", str(self.reports), "--out", str(out)])
        return code, err.getvalue(), out

    def refused(self, needle):
        code, err, out = self.build()
        self.assertEqual(code, 1, err)
        self.assertIn(needle, err)
        self.assertIn("the page was not built", err)
        self.assertFalse(out.exists())

    def test_unknown_outcome(self):
        (self.node / "checkpoints.verification").write_text("great checkpoints=3\n")
        self.refused("unknown outcome 'great'")

    def test_unreadable_detail(self):
        (self.node / "history_card.verification").write_text('refusal reason="unterminated\n')
        self.refused("cannot read")

    def test_verified_without_a_checkpoint_count(self):
        (self.node / "checkpoints.verification").write_text(f"verified anchor={'a' * 64}\n")
        self.refused("checkpoints=<n>")

    def test_verified_line_over_a_refusal(self):
        (self.node / "checkpoints.response.json").write_bytes(
            (ROOT / "tests" / "reporter" / "fixtures" / "refusal.json").read_bytes())
        self.refused("verification state is 'refusal'")

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
        self.refused("NODE is missing")

    def test_missed_day_with_files(self):
        (self.reports / MISSED / "extra").write_text("x")
        self.refused("a MISSED day holds only MISSED")

    def test_not_a_day(self):
        (self.reports / "latest").mkdir()
        self.refused("latest: not a <YYYY-MM-DD> directory")


if __name__ == "__main__":
    unittest.main()
