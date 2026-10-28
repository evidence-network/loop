"""The stats page as the network over time: its acceptance checks.

  (a) the page built from the committed fixtures is byte for byte the committed
      expected page, and two builds are identical;
  (b) the negative fixture (a missed day, a node whose count fell) draws
      hatched gaps and no zero for either;
  (c) every <details> table reconciles with the values drawn in its SVG;
  (d) every SVG has a <title> and a <desc>;
  (e) no <script>, no external URL, no percentage;
  (f) rehearsal and live are never in one chart; FAIL, MALFORMED and
      NOT_CHECKED stay apart.
Contrast and label sizes are tests/test_stats_contrast.py.

The expected page is rebuilt with:
  python3 -B site/build.py tests/fixtures/stats/daily --findings tests/fixtures/stats/findings \
      --lock versions.lock --out /tmp/site && cp /tmp/site/stats/index.html tests/fixtures/stats/expected/index.html

Run: python3 -B -m unittest discover -s tests -p test_stats_redesign.py -v
"""
import contextlib
import datetime as dt
import io
import json
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "site"))
import build  # noqa: E402
import stats_model  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "stats"
DATA, FINDINGS, NEGATIVE = FIX / "daily", FIX / "findings", FIX / "negative" / "daily"
EXPECTED = FIX / "expected" / "index.html"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class El:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, attrs, parent, [], ""

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def all_text(self):
        return self.text + "".join(c.all_text() for c in self.children)

    def ancestors(self):
        p = self.parent
        while p is not None:
            yield p
            p = p.parent


class Tree(HTMLParser):
    """A plain element tree: enough to read data attributes and the text of table cells."""

    def __init__(self, page):
        super().__init__()
        self.root = self.cur = El("#root", {}, None)
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        el = El(tag, dict(attrs), self.cur)
        self.cur.children.append(el)
        if tag not in VOID:
            self.cur = el

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(El(tag, dict(attrs), self.cur))

    def handle_endtag(self, tag):
        el = self.cur
        while el is not self.root and el.tag != tag:
            el = el.parent
        if el is not self.root:
            self.cur = el.parent

    def handle_data(self, data):
        self.cur.text += data


def run_build(data, findings, out):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
        code = build.main(["build.py", str(data), "--findings", str(findings), "--lock",
                           str(ROOT / "versions.lock"), "--out", str(out)])
    assert code == 0, err.getvalue()
    return (Path(out) / "stats" / "index.html").read_text(encoding="utf-8")


def marks_and_cells(page):
    """Per SVG that draws values: (svg, its marks {key: [(kind, value)]}, its table cells {key: [(kind, value)]}).
    The table is every <details> in the nearest container around the SVG that holds one."""
    out = []
    root = Tree(page).root
    for svg in (el for el in root.walk() if el.tag == "svg"):
        marks = [el for el in svg.walk() if "data-k" in el.attrs]
        if not marks:
            continue
        box = next(a for a in svg.ancestors() if any(d.tag == "details" for d in a.walk()))
        cells = [el for d in box.walk() if d.tag == "details" for el in d.walk() if "data-k" in el.attrs]
        m, c = {}, {}
        for el in marks:
            kind = ("gap", el.attrs["data-gap"]) if "data-gap" in el.attrs else ("v", el.attrs["data-v"])
            m.setdefault(el.attrs["data-k"], []).append(kind)
        for el in cells:
            kind = ("gap", el.attrs["data-gap"]) if "data-gap" in el.attrs else ("v", el.all_text().strip())
            c.setdefault(el.attrs["data-k"], []).append(kind)
        out.append((svg, marks, m, c))
    return out


def same(a, b):
    if a[0] != b[0]:
        return False
    if a[0] == "gap":
        return a[1] == b[1]
    try:
        return abs(float(a[1]) - float(b[1])) < 1e-9
    except ValueError:
        return a[1] == b[1]


class Page:
    """The fixture page and the negative page, each built once."""

    tmp = None

    @classmethod
    def pages(cls):
        if cls.tmp is None:
            cls.tmp = tempfile.TemporaryDirectory()
            d = Path(cls.tmp.name)
            cls.fixture = run_build(DATA, FINDINGS, d / "a")
            cls.again = run_build(DATA, FINDINGS, d / "b")
            cls.negative = run_build(NEGATIVE, FINDINGS, d / "n")
        return cls.fixture, cls.negative


class ExpectedPageTest(unittest.TestCase):
    """(a) The page is a function of its inputs, and CI holds it to the committed bytes."""

    def test_the_build_is_the_committed_expected_page_byte_for_byte(self):
        page, _ = Page.pages()
        self.assertEqual(page.encode("utf-8"), EXPECTED.read_bytes(),
                         "the built page differs from tests/fixtures/stats/expected/index.html; rebuild it as the "
                         "module docstring says if the change is meant")

    def test_two_builds_are_identical(self):
        Page.pages()
        self.assertEqual(Page.fixture, Page.again)

    def test_the_findings_fixture_is_what_make_findings_writes(self):
        sys.path.insert(0, str(FIX))
        import make_findings
        with tempfile.TemporaryDirectory() as d, mock.patch.object(make_findings, "OUT", Path(d) / "f"):
            make_findings.main()
            made = {p.relative_to(Path(d) / "f").as_posix(): p.read_bytes() for p in (Path(d) / "f").rglob("*")
                    if p.is_file()}
        have = {p.relative_to(FINDINGS).as_posix(): p.read_bytes() for p in FINDINGS.rglob("*") if p.is_file()}
        self.assertEqual(made, have)

    def test_the_negative_fixture_is_what_make_negative_writes(self):
        sys.path.insert(0, str(FIX))
        import make_negative
        made = {f"{d['date']}.json": json.dumps(d, sort_keys=True, indent=2) + "\n" for d in make_negative.days()}
        self.assertEqual(made, {p.name: p.read_text() for p in NEGATIVE.glob("*.json")})


class NegativeFixtureTest(unittest.TestCase):
    """(b) A missed day and a node whose count fell: hatched gaps, and no zero drawn for either."""

    def test_the_missed_day_and_the_fall_are_hatched_gaps_in_the_records_chart(self):
        _, page = Page.pages()
        sealed = next(s for s, _, _, _ in marks_and_cells(page) if s.attrs["id"] == "c-sealed")
        gaps = {el.attrs["data-k"]: el for el in sealed.walk() if "data-gap" in el.attrs}
        self.assertEqual(gaps["2026-11-05"].attrs["data-gap"], "the reporter missed this day")
        self.assertEqual(gaps["2026-11-06"].attrs["data-gap"], "a node's count fell")
        for date in ("2026-11-05", "2026-11-06"):
            self.assertEqual(gaps[date].attrs["fill"], "url(#c-sealed-h)")  # hatched
        self.assertTrue(any(el.tag == "pattern" and el.attrs["id"] == "c-sealed-h" for el in sealed.walk()))
        self.assertIn("node <code title=\"" + "5" * 64, page)  # the fall is named under the chart

    def test_no_value_is_drawn_for_the_missed_day_in_any_chart(self):
        _, page = Page.pages()
        for svg, marks, _, _ in marks_and_cells(page):
            for el in marks:
                if el.attrs["data-k"].startswith("2026-11-05") or "|2026-11-05" in el.attrs["data-k"]:
                    if el.attrs["data-k"] == "2026-11-05|listed":
                        continue  # the commitment list that day: known, and not a value of the run
                    if svg.attrs["id"] == "c-missed":
                        self.assertEqual(el.attrs["data-v"], "missed")
                        continue
                    self.assertIn("data-gap", el.attrs, (svg.attrs["id"], el.attrs))

    def test_no_zero_for_the_fall_and_no_negative(self):
        _, page = Page.pages()
        sealed = next(s for s, _, _, _ in marks_and_cells(page) if s.attrs["id"] == "c-sealed")
        values = {el.attrs["data-k"]: el.attrs["data-v"] for el in sealed.walk() if "data-v" in el.attrs}
        self.assertEqual(values, {"2026-11-04|55555555": "9", "2026-11-04|66666666": "1"})
        self.assertFalse([el for el in sealed.walk() if "zeromark" in el.attrs.get("class", "")])
        self.assertNotIn('data-v="-', page)

    def test_the_missed_day_is_a_gap_in_every_day_chart(self):
        _, page = Page.pages()
        for key in ("c-answering", "c-sealed", "c-book", "c-asks"):
            svg = next(s for s, _, _, _ in marks_and_cells(page) if s.attrs["id"] == key)
            gap = [el for el in svg.walk() if el.attrs.get("data-k") == "2026-11-05" and "data-gap" in el.attrs]
            self.assertEqual(len(gap), 1, key)
            self.assertEqual(gap[0].attrs["fill"], f"url(#{key}-h)", key)


class ReconcileTest(unittest.TestCase):
    """(c) Each <details> table holds exactly the values its SVG draws, and the marks are drawn to scale."""

    def check(self, page):
        found = marks_and_cells(page)
        self.assertGreaterEqual(len(found), 10)
        for svg, marks, m, c in found:
            key = svg.attrs["id"]
            if key == "band":
                # A square per node per question: the table counts them per date, question and state.
                drawn = {}
                for k, kinds in m.items():
                    for kind in kinds:
                        if kind[0] == "v":
                            drawn[f"{k}|{kind[1]}"] = drawn.get(f"{k}|{kind[1]}", 0) + 1
                        else:
                            drawn[k] = kind
                for k, kinds in c.items():
                    (kind,) = kinds
                    if kind[0] == "gap":
                        self.assertEqual(drawn.pop(k), kind, k)
                    else:
                        self.assertEqual(drawn.pop(k, 0), int(kind[1]), k)
                self.assertEqual(drawn, {}, "squares drawn with no row in the table")
                continue
            self.assertEqual(sorted(m), sorted(c), key)
            for k in m:
                self.assertEqual(len(m[k]), 1, (key, k))
                self.assertEqual(len(c[k]), 1, (key, k))
                self.assertTrue(same(m[k][0], c[k][0]), (key, k, m[k], c[k]))
            if "data-unit" in svg.attrs:
                unit, base = float(svg.attrs["data-unit"]), float(svg.attrs["data-base"])
                for el in marks:
                    if "data-v" not in el.attrs:
                        continue
                    try:
                        v = float(el.attrs["data-v"])
                    except ValueError:
                        continue  # a state or an event, not a quantity
                    if el.tag == "rect":
                        self.assertAlmostEqual(float(el.attrs["height"]), v * unit, delta=0.02, msg=(key, el.attrs))
                    elif el.tag == "circle":
                        self.assertAlmostEqual(float(el.attrs["cy"]), base - v * unit, delta=0.02, msg=(key, el.attrs))
                    elif el.tag == "line":
                        self.assertAlmostEqual(float(el.attrs["y1"]), base - v * unit, delta=0.02, msg=(key, el.attrs))

    def test_the_fixture_page(self):
        self.check(Page.pages()[0])

    def test_the_negative_page(self):
        self.check(Page.pages()[1])

    def test_every_chart_has_its_numbers_in_a_details_table(self):
        page, _ = Page.pages()
        root = Tree(page).root
        for fig in (el for el in root.walk() if el.tag == "figure" and "chart" in el.attrs.get("class", "")):
            self.assertTrue(any(el.tag == "details" for el in fig.walk()), fig.attrs)
            self.assertTrue(any(el.tag == "table" for el in fig.walk()), fig.attrs)


class SvgTest(unittest.TestCase):
    """(d) and (e)."""

    def test_every_svg_has_a_title_and_a_desc(self):
        for page in Page.pages():
            root = Tree(page).root
            for svg in (el for el in root.walk() if el.tag == "svg"):
                if svg.attrs.get("aria-hidden") == "true":
                    continue  # a legend swatch beside its words
                kids = [c.tag for c in svg.children]
                self.assertEqual(kids[:2], ["title", "desc"], svg.attrs)
                self.assertTrue(svg.children[0].all_text().strip() and svg.children[1].all_text().strip())
                self.assertEqual(svg.attrs["aria-labelledby"], f"{svg.attrs['id']}-t {svg.attrs['id']}-d")
                self.assertEqual(svg.attrs["role"], "img")

    def test_ids_are_unique(self):
        for page in Page.pages():
            ids = [el.attrs["id"] for el in Tree(page).root.walk() if "id" in el.attrs]
            self.assertEqual(len(ids), len(set(ids)))

    def test_no_script_no_external_url_no_percentage(self):
        for page in Page.pages():
            low = page.lower()
            self.assertNotIn("<script", low)
            self.assertNotIn("javascript:", low)
            for el in Tree(page).root.walk():
                for attr in ("src", "href"):
                    v = el.attrs.get(attr)
                    if v is None:
                        continue
                    if el.tag == "a" and attr == "href" and el.attrs.get("href", "").startswith("https://github.com/"):
                        continue  # a cited issue: a link, never a loaded asset
                    self.assertNotRegex(v, r"^(?:[a-z]+:|//)", (el.tag, attr, v))
            text = "".join(el.text for el in Tree(page).root.walk() if el.tag != "style")
            self.assertNotIn("%", text)
            self.assertNotIn("percent", text.lower())
            css = page[page.index("<style>"):page.index("</style>")]
            self.assertNotIn("@font-face", css)
            self.assertNotIn("@import", css)


class PopulationsTest(unittest.TestCase):
    """(f) Rehearsal and live never share a chart; FAIL, MALFORMED and NOT_CHECKED stay apart."""

    def build_with(self, keep):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        f = Path(tmp.name) / "findings"
        for run in keep:
            shutil.copytree(FINDINGS / run, f / run)
        return run_build(DATA, f, Path(tmp.name) / "out")

    def test_each_loop_chart_is_one_population(self):
        page, _ = Page.pages()
        for svg, marks, _, _ in marks_and_cells(page):
            runs = {el.attrs["data-k"].rsplit("-", 1)[-1] for el in marks if "rehearsal" in el.attrs["data-k"]
                    or el.attrs["data-k"].endswith("-live")}
            self.assertLessEqual(len(runs), 1, svg.attrs["id"])
            if runs:
                self.assertTrue(svg.attrs["id"].endswith(runs.pop()), svg.attrs["id"])

    def test_malformed_fail_and_not_checked_are_three_things(self):
        page = self.build_with(["2026-10-30-rehearsal", "2026-11-01-rehearsal"])
        fig = page[page.index('<figure class="pop" data-population="rehearsal">'):]
        fig = fig[:fig.index("</figure>")]
        self.assertIn('class="tile s-red l-MALFORMED" href="status/#step-12"', fig)
        self.assertIn('class="tile s-NOT_CHECKED l-NOT_CHECKED" href="status/#step-03"', fig)
        for label, n in (("PASS", 2), ("FAIL", 0), ("MALFORMED", 1), ("NOT_CHECKED", 9)):
            self.assertIn(f"{build.code(label)} {build.count(n, 12, 'step')}", fig)
        page = self.build_with(["2026-10-30-rehearsal"])
        self.assertIn('class="tile s-red l-FAIL" href="status/#step-03"', page)
        css = (ROOT / "site" / "template.html").read_text()
        self.assertIn("ol.strip a.l-MALFORMED { border: 2px dotted", css)  # told apart from FAIL beyond the label

    def test_the_pass_sparkline_counts_pass_only(self):
        page, _ = Page.pages()
        spark = page[page.index('<svg class="spark" id="s-pass-rehearsal"'):]
        spark = spark[:spark.index("</svg>")]
        # 10-30: 01 PASS; 11-01: 01, 02 PASS (12 MALFORMED is not a PASS); 11-02: 01, 02, 03, 12 PASS.
        for run, n in (("2026-10-30-rehearsal", 1), ("2026-11-01-rehearsal", 2), ("2026-11-02-rehearsal", 4)):
            self.assertIn(f'data-k="{run}" data-v="{n}"', spark)
        self.assertIn("no run yet", page[page.index('id="s-pass-live"'):][:600])


def band_columns(page):
    """The x of each window day's first square in the pulse band, by date, and the band's viewBox width."""
    band = page[page.index('<svg class="band"'):]
    band = band[:band.index("</svg>")]
    xs = {}
    for el in Tree(band + "</svg>").root.walk():
        k = el.attrs.get("data-k", "")
        if el.tag == "rect" and "x" in el.attrs and k:
            xs.setdefault(k.split("|")[0], (float(el.attrs["x"]), float(el.attrs["width"])))
    return xs, float(band.split('viewBox="0 0 ', 1)[1].split(" ")[0]), band


class BandWidthTest(unittest.TestCase):
    """The pulse band spans the content column: columns as wide as the days allow, capped, right-aligned."""

    def test_few_days_take_the_capped_column_with_the_spare_width_on_the_left(self):
        page, _ = Page.pages()
        xs, w, band = band_columns(page)
        self.assertEqual(w, build.BAND_W)
        self.assertNotIn(' width="', band.split(">", 1)[0])  # scaled by CSS to the column, not drawn at a fixed size
        dates = sorted(xs)
        centres = [xs[d][0] + xs[d][1] / 2 for d in dates]
        steps = {round(b - a, 2) for a, b in zip(centres, centres[1:])}
        self.assertEqual(steps, {build.BAND_COL})  # six days: the cap, not giant blocks
        self.assertAlmostEqual(centres[-1] + build.BAND_COL / 2, w - 8)  # the newest day at the right edge
        self.assertGreater(min(x for x, _ in xs.values()), w / 2)  # the spare width is on the left
        self.assertEqual(xs[dates[-1]][1], build.BAND_CELL)  # squares as large as allowed

    def test_thirty_days_spread_across_the_width(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "daily"
            data.mkdir()
            src = json.loads((DATA / "2026-11-02.json").read_text())
            for i in range(30):
                date = (dt.date(2026, 10, 1) + dt.timedelta(days=i)).isoformat()
                (data / f"{date}.json").write_text(json.dumps(dict(src, date=date), sort_keys=True, indent=2) + "\n")
            xs, w, _ = band_columns(run_build(data, FINDINGS, Path(d) / "out"))
        lo = min(x for x, _ in xs.values())
        self.assertLess(lo, 0.05 * w)  # the days fill the band
        col = (w - 4 - 8) / 30
        self.assertLess(col, build.BAND_COL)
        centres = sorted(x + cw / 2 for x, cw in xs.values())
        self.assertAlmostEqual(centres[1] - centres[0], col, places=1)


class WindowTest(unittest.TestCase):
    """Thirty days in full; older days in a thin band at the left of the pulse band only."""

    def test_older_days_fold_into_a_thin_band(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "daily"
            data.mkdir()
            src = json.loads((DATA / "2026-11-02.json").read_text())
            start = dt.date(2026, 10, 1)
            for i in range(35):
                date = (start + dt.timedelta(days=i)).isoformat()
                src["date"] = date
                (data / f"{date}.json").write_text(json.dumps(src, sort_keys=True, indent=2) + "\n")
            page = run_build(data, FINDINGS, Path(d) / "out")
        band = page[page.index('<svg class="band"'):]
        band = band[:band.index("</svg>")]
        self.assertIn(">older</text>", band)
        self.assertIn('data-k="2026-10-01|checkpoints"', band)  # the oldest day is still drawn, thin
        older = [el for el in Tree(band + "</svg>").root.walk() if el.attrs.get("data-k", "").startswith("2026-10-01|")]
        self.assertTrue(older and all(el.attrs["width"] == "5" for el in older))
        sealed = page[page.index('id="f-c-sealed"'):]
        sealed = sealed[:sealed.index("</figure>")]
        self.assertNotIn('data-k="2026-10-05', sealed)  # the small multiples draw the newest thirty days only
        self.assertIn('data-k="2026-10-06', sealed)
        self.assertEqual(len(stats_model.window(stats_model.load(DATA))[1]), 6)


if __name__ == "__main__":
    unittest.main()
