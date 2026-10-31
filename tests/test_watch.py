"""The watch page (site/watch.py): the network as a graph, day by day, from committed files only.

  (b) the nodes and edges of a shown day reconcile with that day's JSON;
  (c) a node that joins on day N is not in the data before N, and is from N;
      a node that leaves is there before its left_on and marked left after;
  (d) answered, refused and absent are three outcomes with three distinct
      strokes and end markers; rehearsal and live are two data sets;
  (e) the built page and its files reference no external URL;
  (e) the built page and its files reference no external URL, and the only
      URL the script reads is its own relative data.json;
  (f) no amount or band, in the export or in the hover card's markup;
  (i) every pair of shown nodes is dotted, solid only where a committed
      finding shows the two exchanged, on that day only; no reporter node;
  (j) an on-demand run is its own labelled point, and the daily entries, rows
      and counts are the same with and without it;
  (m) the zoom, pan, rotate and reset controls and their keys; the layout is
      seeded from node ids, and nodes already placed do not move when one joins;
  (n) a node's badge counts the history cards stapled to its commitment, and
      its card lists each card's stream and witnesses; a ring per community,
      thin with "no history since" for a card with no history, new or cleared;
  bound groups: one dot only for a verified verdict whose verifier is pinned;
  (o) d3 is vendored at its pinned sha256 with its licence beside it;
  the charts' tables hold the export's numbers, "not measured yet" never 0;
  the stats page's branding and neutrality checks run over /watch/; an edge
  kind with no finding for the day says "not measured yet"; /stats/ is
  unchanged by the watch build; contrast in both schemes and on the canvas.

Fixtures: tests/fixtures/stats/daily/ (node 4 joins on 2026-10-30, node 3
leaves on 2026-11-01) and tests/fixtures/watch/ (made by make_fixtures.py
there: nodes/, population/, a rehearsal set, findings in each step's shape, an
on-demand run and the nodes' commitments).

Run: python3 -B -m unittest discover -s tests -p test_watch.py -v
"""
import contextlib
import filecmp
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "site"))
sys.path.insert(0, str(ROOT / "tests"))
import build  # noqa: E402
import stats_model  # noqa: E402
import watch  # noqa: E402
import test_site  # noqa: E402
import test_stats_contrast as contrast  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "watch"
DAILY = ROOT / "tests" / "fixtures" / "stats" / "daily"
STATS_FINDINGS = ROOT / "tests" / "fixtures" / "stats" / "findings"
EXPECTED = ROOT / "tests" / "fixtures" / "stats" / "expected" / "index.html"
# The one URL the page and its script carry: the SVG namespace, an identifier that is never fetched.
SVG_NS = "http://www.w3.org/2000/svg"
# What the vendored d3 carries: XML namespace identifiers, never fetched, and its home page in its banner comment.
D3_URLS = {SVG_NS, "http://www.w3.org/1999/xhtml", "http://www.w3.org/1999/xlink", "http://www.w3.org/2000/xmlns/",
           "http://www.w3.org/XML/1998/namespace", "https://d3js.org"}
D3 = ROOT / "site" / "vendor" / "d3-7.9.0"
D3_SHA256 = "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539"
NODE = shutil.which("node") or os.path.expanduser("~/.local/opt/node-v22.23.3/bin/node")
MONEY = re.compile(r"amount|band|msat|currency|price|fee\b|usd|usdc|sats?\b|payment", re.I)


BOUND = "bound:" + hashlib.sha256(("c" * 64).encode()).hexdigest()[:16]  # the group id: a hash, never the controller key


def nid(n):
    return str(n) * 64


def run(out, daily=DAILY, rehearsal=FIX / "rehearsal", findings=FIX / "findings", ondemand=FIX / "ondemand",
        nodes=FIX / "nodes", commitments=FIX / "commitments", bindings=FIX / "bindings" / "bound", lock=FIX / "pins.lock"):
    err = io.StringIO()
    extra = (["--ondemand", str(ondemand)] if ondemand else []) + (["--bindings", str(bindings)] if bindings else [])
    extra += ["--lock", str(lock)] if lock else []
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = watch.main(["watch.py", "--daily", str(daily), "--rehearsal", str(rehearsal), "--findings",
                           str(findings), "--nodes", str(nodes), "--population", str(FIX / "population"),
                           "--commitments", str(commitments), "--out", str(out)] + extra)
    assert code == 0, err.getvalue()
    return json.loads((Path(out) / "watch" / "data.json").read_text(encoding="utf-8"))


def keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from keys(v)


def strings(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)
    elif isinstance(obj, str):
        yield obj


def on(data, view, date):
    return next(d for d in data["views"][view]["days"] if d["date"] == date)


def daily(data):
    return [d for d in data["views"]["live"]["days"] if d["set"] == "daily"]


def asks(d):
    return [a | {"to": n["node_id"]} for n in d["nodes"] if not n["left"] for a in n.get("asked", [])]


def node_js(script, *args):
    """Run a script under node with d3 and watch-layout.js loaded; its stdout as JSON."""
    if not os.access(NODE, os.X_OK):
        raise unittest.SkipTest("SKIPPED LOUDLY: no node binary to run site/watch-layout.js")
    pre = ("const d3 = require(process.argv[1]); const layout = require(process.argv[2]);"
           "const data = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'));"
           "const seeds = {}; for (const k in data.nodes) seeds[k] = data.nodes[k].seed;")
    r = subprocess.run([NODE, "-e", pre + script, str(D3 / "d3.min.js"), str(ROOT / "site" / "watch-layout.js"),
                        *map(str, args)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class Rows(HTMLParser):
    """The fallback tables: (view, set) -> [{data-k: cell text}] with each row's date and set."""

    def __init__(self):
        super().__init__()
        self.view, self.rows, self.cell = None, {}, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "details" and "data-view" in a and "fallback" in a.get("class", ""):
            self.view = (a["data-view"], a["data-set"])
            self.rows[self.view] = []
        elif tag == "tr" and "data-date" in a and self.view:
            self.rows[self.view].append({"date": a["data-date"], "set": a["data-set"]})
        elif tag == "td" and "data-k" in a:
            self.cell = a["data-k"]
            self.rows[self.view][-1][self.cell] = ""

    def handle_endtag(self, tag):
        if tag == "td":
            self.cell = None

    def handle_data(self, data):
        if self.cell:
            self.rows[self.view][-1][self.cell] += data


class Charts(HTMLParser):
    """The charts' tables: (view, metric) -> [(date, set, label, cell text, unmeasured?)]."""

    def __init__(self):
        super().__init__()
        self.at, self.rows, self.row, self.cell = None, {}, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "figure" and "data-metric" in a:
            self.at = (a["data-view"], a["data-metric"])
            self.rows[self.at] = []
        elif tag == "tr" and "data-date" in a and self.at:
            self.row = [a["data-date"], a["data-set"], "", "", False]
            self.rows[self.at].append(self.row)
        elif tag == "th" and self.row is not None:
            self.cell = 2
        elif tag == "td" and "data-v" in a and self.row is not None:
            self.cell = 3
            self.row[4] = "unmeasured" in a.get("class", "")

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.cell = None
        if tag == "figure":
            self.at, self.row = None, None

    def handle_data(self, data):
        if self.cell is not None:
            self.row[self.cell] += data


class WatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "_site"
        cls.data = run(cls.out)
        cls.page = (cls.out / "watch" / "index.html").read_text(encoding="utf-8")
        cls.js = (cls.out / "watch" / "watch.js").read_text(encoding="utf-8")
        cls.layout_js = (cls.out / "watch" / "watch-layout.js").read_text(encoding="utf-8")
        cls.days = {d.date: d for d in stats_model.load(DAILY)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ours(self):
        """The page's own files: everything under watch/ but the vendored d3."""
        return [p for p in (self.out / "watch").iterdir() if p.is_file()]

    def test_the_page_and_its_files_are_written_under_watch(self):
        got = sorted(p.relative_to(self.out / "watch").as_posix() for p in (self.out / "watch").rglob("*") if p.is_file())
        self.assertEqual(got, ["data.json", "index.html", "vendor/d3-7.9.0/LICENSE", "vendor/d3-7.9.0/d3.min.js",
                               "watch-layout.js", "watch.js"])
        for f in ("watch.js", "watch-layout.js"):
            self.assertEqual((self.out / "watch" / f).read_bytes(), (ROOT / "site" / f).read_bytes())

    def test_the_page_carries_the_export_inline_byte_for_byte_the_same_data(self):
        blob = re.search(r'<script type="application/json" id="watch-data">(.*?)</script>', self.page, re.S).group(1)
        self.assertEqual(json.loads(blob.replace("<\\/", "</")), self.data)
        self.assertEqual(self.data["format"], "watch/2")

    def test_two_builds_are_identical(self):
        with tempfile.TemporaryDirectory() as d:
            run(Path(d))
            cmp = filecmp.dircmp(self.out / "watch", Path(d) / "watch")
            self.assertEqual(cmp.diff_files, [])
            self.assertEqual((cmp.left_only, cmp.right_only), ([], []))

    # (b) The shown day against its JSON

    def test_b_nodes_and_asks_reconcile_with_each_days_json(self):
        for entry in daily(self.data):
            day = self.days.get(entry["date"])
            drawn = [n for n in entry["nodes"] if not n["left"]]
            got = asks(entry)
            if day is None or day.missed:
                self.assertEqual((drawn, got), ([], []), entry["date"])
                continue
            on_day = [n for n in day.nodes if not (n.left_on and entry["date"] >= n.left_on)]
            self.assertEqual(sorted(n["node_id"] for n in drawn), sorted(n.node_id for n in on_day), entry["date"])
            self.assertEqual(entry["listed"], day.listed)
            want = {o: 0 for o in watch.OUTCOMES}
            for n in on_day:
                for a in n.answers.values():
                    want["refused" if a.state == "refusal" else "absent" if a.state == "absence" else "answered"] += 1
                    match = [x for x in got if x["to"] == n.node_id and x["question"] == stats_model.QUESTIONS[a.stem]]
                    self.assertEqual([m["state"] for m in match], [a.state])
            self.assertEqual({o: sum(1 for x in got if x["outcome"] == o) for o in watch.OUTCOMES}, want, entry["date"])
            for n in drawn:
                d = next(x for x in day.nodes if x.node_id == n["node_id"])
                self.assertEqual(sum(n["asks"].values()), 2)
                self.assertEqual(len(n["asked"]), 2)
                self.assertEqual(n["records_at_checkpoint"], d.answers["checkpoints"].records)

    def test_b_on_the_committed_data_too(self):
        with tempfile.TemporaryDirectory() as d:
            data = run(Path(d), daily=ROOT / "stats" / "data" / "daily", rehearsal=ROOT / "stats" / "data" / "rehearsal",
                       findings=ROOT / "findings", ondemand=None, nodes=ROOT / "nodes", commitments=ROOT / "lab" / "runs",
                       bindings=ROOT / "bindings", lock=ROOT / "versions.lock")
        # Whatever is committed (no bindings on main today), a group is only ever a verified, pinned verdict.
        self.assertTrue(all(g["verifier"] in watch.pins(ROOT / "versions.lock") for g in data["groups"]))
        for view, root, period in (("live", ROOT / "stats" / "data" / "daily", stats_model.layout.Daily),
                                   ("rehearsal", ROOT / "stats" / "data" / "rehearsal", stats_model.layout.Rehearsal)):
            files = {d.date: d for d in stats_model.load(root, period)}
            for entry in data["views"][view]["days"]:
                day = files.get(entry["date"])
                n = 0 if day is None or day.missed else len(day.nodes)
                self.assertEqual(len([x for x in entry["nodes"] if not x["left"]]), n)
                self.assertEqual(len(asks(entry)), 2 * n)
                self.assertEqual(len(entry["pairs"]), n * (n - 1) // 2)

    def test_the_text_table_holds_the_graphs_counts(self):
        p = Rows()
        p.feed(self.page)
        for view, v in self.data["views"].items():
            for which in ("days", "ondemand"):
                want = [d for d in v["days"] if (d["set"] == "ondemand") == (which == "ondemand")]
                rows = {r["date"]: r for r in p.rows.get((view, which), [])}
                self.assertEqual(sorted(rows), sorted(d["date"] for d in want), (view, which))
                for d in want:
                    r = rows[d["date"]]
                    self.assertEqual(r["set"], d["set"])
                    drawn = [n for n in d["nodes"] if not n["left"]]
                    got = asks(d)
                    if d["run"] == "settled":
                        self.assertEqual(r["nodes"], f"{len(drawn)} of {d['listed']} listed")
                    self.assertEqual(r["left"], str(len(d["nodes"]) - len(drawn)))
                    for o in watch.OUTCOMES:
                        n = sum(1 for x in got if x["outcome"] == o)
                        self.assertEqual(r[f"ask-{o}"], f"{n} of {len(got)} asks" if got else "-")
                    solid = sum(1 for x in d["pairs"] if x["line"] == "solid")
                    self.assertEqual(r["pairs"], f"{solid} solid of {len(d['pairs'])}")
                    for kind in watch.STEPS:
                        m = d["measured"][kind]
                        self.assertIn("not measured yet" if not m["measured"] else f"{m['not_drawn']} not drawn", r[kind])

    # (c) Joining and leaving

    def test_c_a_fourth_node_joins_on_its_day_and_not_before(self):
        four = nid(4)
        for d in daily(self.data):
            ids = [n["node_id"] for n in d["nodes"]]
            if d["date"] < "2026-10-30":
                self.assertNotIn(four, ids, d["date"])
                self.assertNotIn(four, [x for p in d["pairs"] for x in (p["a"], p["b"])], d["date"])
            elif d["run"] == "settled":
                self.assertIn(four, ids, d["date"])
        n = next(x for x in on(self.data, "live", "2026-10-30")["nodes"] if x["node_id"] == four)
        self.assertTrue(n["first_day"])
        self.assertEqual(n["days_listed"], 1)
        later = next(x for x in on(self.data, "live", "2026-11-02")["nodes"] if x["node_id"] == four)
        self.assertFalse(later["first_day"])
        self.assertEqual(later["days_listed"], 3)

    def test_a_leaver_is_present_before_left_on_and_marked_left_after(self):
        three = nid(3)
        for date in ("2026-10-28", "2026-10-30"):
            n = next(x for x in on(self.data, "live", date)["nodes"] if x["node_id"] == three)
            self.assertFalse(n["left"], date)
            self.assertEqual(n["left_on"], "2026-11-01")
        for date in ("2026-11-01", "2026-11-02"):
            d = on(self.data, "live", date)
            n = next(x for x in d["nodes"] if x["node_id"] == three)
            self.assertTrue(n["left"], date)
            self.assertNotIn(three, [x["to"] for x in asks(d)])  # not asked once it left
            self.assertNotIn(three, [x for p in d["pairs"] for x in (p["a"], p["b"])])
        self.assertIn('" left"', self.js)  # the script fades it: class "left"
        self.assertRegex(self.page, r"svg \.left \{ opacity: \.\d+; \}")

    def test_a_leaver_still_in_its_left_on_day_file_is_marked_left(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d) / "daily"
            shutil.copytree(DAILY, dd)
            f = dd / "2026-11-01.json"
            day = json.loads(f.read_text())
            gone = {"absent_days_running": 1, "left_on": "2026-11-01", "node_id": nid(3), "node_line": "discloses: checkpoints",
                    "questions": {k: {"answer_sha256": None, "attempts": [{"at": "2026-11-01T06:00:00Z", "state": "absence"}],
                                      "log_size_at_checkpoint": None, "records_at_checkpoint": None, "state": "absence"}
                                  for k in ("checkpoints", "history_card")}}
            day["nodes"] = sorted(day["nodes"] + [gone], key=lambda n: n["node_id"])
            day["listed"] = 4
            f.write_text(json.dumps(day, sort_keys=True, indent=2) + "\n")
            data = run(Path(d) / "out", daily=dd)
        n = next(x for x in on(data, "live", "2026-11-01")["nodes"] if x["node_id"] == nid(3))
        self.assertTrue(n["left"])
        self.assertEqual(n["days_listed"], 3)
        self.assertFalse(next(x for x in on(data, "live", "2026-10-30")["nodes"] if x["node_id"] == nid(3))["left"])

    # (d) Three distinct visuals; two data sets

    def test_d_refusal_absence_and_answer_are_three_distinct_rings(self):
        style = {o: re.search(rf"svg \.o-{o} \{{([^}}]*)\}}", self.page).group(1) for o in watch.OUTCOMES}
        dashes = {o: re.search(r"stroke-dasharray: ([\d. ]+)", s).group(1) if "dasharray" in s else "solid"
                  for o, s in style.items()}
        self.assertEqual(len(set(dashes.values())), 3, dashes)
        self.assertIn('"ask o-" + a.outcome', self.js)  # each ask ring takes its outcome's stroke
        # The pulses that travel differ by shape too: a square for a refusal, a dot or a hollow ring otherwise.
        self.assertIn('x.outcome === "refused" ? el("rect"', self.js)
        self.assertRegex(self.page, r"svg \.traveller\.o-absent \{ fill: var\(--cv-bg\)")
        self.assertEqual(watch.outcome("refusal"), "refused")
        self.assertEqual(watch.outcome("absence"), "absent")
        for st in ("verified", "no_checkpoint_yet", "no_announced_key", "not_evidence"):
            self.assertEqual(watch.outcome(st), "answered")
        seen = {a["outcome"] for v in self.data["views"].values() for d in v["days"] for a in asks(d)}
        self.assertEqual(seen, set(watch.OUTCOMES))

    def test_d_rehearsal_and_live_are_separate_data_sets_never_summed(self):
        live, reh = self.data["views"]["live"], self.data["views"]["rehearsal"]
        self.assertEqual((live["test"], reh["test"]), (False, True))
        self.assertTrue(all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["date"]) for d in daily(self.data)))
        self.assertTrue(all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{4}Z", d["date"]) for d in reh["days"]))
        self.assertEqual({d["set"] for d in live["days"]}, {"daily", "ondemand"})
        self.assertEqual({d["set"] for d in reh["days"]}, {"rehearsal"})
        self.assertTrue(all(d["file"] is None or d["file"].startswith("stats/data/daily/") for d in daily(self.data)))
        self.assertTrue(all(d["file"] is None or d["file"].startswith("stats/data/rehearsal/") for d in reh["days"]))
        self.assertEqual(sorted(self.data["views"]), ["live", "rehearsal"])
        # One view drawn at a time: the script draws data.views[state.view] only, its charts included.
        self.assertIn("return data.views[state.view];", self.js)
        self.assertNotRegex(self.js, r"views\.live[^;]*views\.rehearsal|views\.rehearsal[^;]*views\.live")
        self.assertEqual(self.page.count('<details class="fallback" data-view="rehearsal"'), 1)
        self.assertEqual(self.page.count('<div class="metrics" data-view='), 2)

    # (i) Pairs, not spokes

    def test_i_every_pair_is_dotted_and_solid_only_with_a_committed_exchange_that_day(self):
        for v in self.data["views"].values():
            for d in v["days"]:
                shown = sorted(n["node_id"] for n in d["nodes"] if not n["left"])
                self.assertEqual([(p["a"], p["b"]) for p in d["pairs"]],
                                 [(a, b) for i, a in enumerate(shown) for b in shown[i + 1:]], d["date"])
                for p in d["pairs"]:
                    held = {x["kind"] for x in d["edges"] if {x["from"], x["to"]} == {p["a"], p["b"]}
                            and x["outcome"] in ("answered", "refused")}
                    self.assertEqual((p["line"], p["kinds"]), ("solid" if held else "dotted", sorted(held)), d["date"])

        def line(view, date, a, b):
            return next(p["line"] for p in on(self.data, view, date)["pairs"] if (p["a"], p["b"]) == (nid(a), nid(b)))
        # The live deal: node 1 to node 4 on 2026-11-02, and on that day only.
        self.assertEqual(line("live", "2026-11-02", 1, 4), "solid")
        self.assertEqual(line("live", "2026-11-02", 1, 2), "dotted")
        self.assertEqual(line("live", "2026-11-02", 2, 4), "dotted")
        self.assertEqual(line("live", "2026-11-01", 1, 4), "dotted")
        self.assertEqual(line("live", "2026-10-30", 1, 4), "dotted")
        # The rehearsal settle and ask: in the slot the run started in, not the next.
        self.assertEqual(line("rehearsal", "2026-11-05T0600Z", 1, 2), "solid")
        self.assertEqual(line("rehearsal", "2026-11-05T0610Z", 1, 2), "dotted")
        # The script draws a pair solid from the export's line, never on its own.
        self.assertIn('p.line === "solid"', self.js)

    def test_a_pair_edge_is_drawn_only_when_both_ends_opt_in(self):
        # loop#77: an edge says who dealt with whom, so a live node whose nodes/ entry does not opt in
        # (disclosure.pair_edges "public") has none drawn: its pairs stay dotted, the edges are only counted.
        with tempfile.TemporaryDirectory() as tmp:
            nodes = Path(tmp) / "nodes"
            shutil.copytree(FIX / "nodes", nodes)
            f = nodes / f"{nid(4)}.json"
            e = json.loads(f.read_text())
            del e["disclosure"]["pair_edges"]
            f.write_text(json.dumps(e))
            data = run(Path(tmp) / "_site", nodes=nodes)
            page = (Path(tmp) / "_site" / "watch" / "index.html").read_text(encoding="utf-8")
        before, after = on(self.data, "live", "2026-11-02"), on(data, "live", "2026-11-02")
        self.assertTrue(any(nid(4) in (x["from"], x["to"]) for x in before["edges"]))
        self.assertFalse(any(nid(4) in (x["from"], x["to"]) for x in after["edges"]))
        line = {(p["a"], p["b"]): p["line"] for p in after["pairs"]}
        self.assertEqual(line[(nid(1), nid(4))], "dotted")
        deal = after["measured"]["deal"]
        self.assertEqual(deal["drawn"] + deal["withheld"], before["measured"]["deal"]["drawn"])
        self.assertIn("disclosure.pair_edges", deal["why_withheld"])
        # The charts still count every deal and settlement, on every day; only the lines are withheld.
        values = lambda v, key: [(p["date"], p.get("value")) for m in v["metrics"] if m["key"] == key  # noqa: E731
                                 for p in m["points"]]
        for key in ("deals", "settlements"):
            self.assertEqual(values(data["views"]["live"], key), values(self.data["views"]["live"], key), key)
        withheld = sum(d["measured"][k].get("withheld_answered", 0) for d in data["views"]["live"]["days"]
                       for k in ("deal", "settle", "ask07") if d["measured"][k]["measured"])
        answered = lambda v: sum(x["outcome"] == "answered" for d in v["days"] for x in d["edges"])  # noqa: E731
        self.assertEqual(answered(data["views"]["live"]) + withheld, answered(self.data["views"]["live"]))
        # The page says so: the no-script table and the script's notes name the withheld count.
        self.assertIn(f"; {deal['withheld']} withheld</td>", page)
        self.assertIn('m.withheld + " withheld"', self.js)
        # The rehearsal is the loop's own TEST population and is not gated.
        self.assertEqual(data["views"]["rehearsal"], self.data["views"]["rehearsal"])

    def test_no_reporter_node_in_the_export_or_the_markup(self):
        for v in self.data["views"].values():
            for d in v["days"]:
                ids = [n["node_id"] for n in d["nodes"]] + [x for p in d["pairs"] for x in (p["a"], p["b"])]
                ids += [x for e in d["edges"] for x in (e["from"], e["to"])]
                self.assertNotIn("reporter", ids)
                self.assertTrue(all(stats_model.HEX64.match(i) for i in ids), d["date"])
                self.assertNotIn("ask", [e["kind"] for e in d["edges"]])
        self.assertNotIn("reporter", self.data["nodes"])
        self.assertNotIn("reporters", self.data)
        for text in (self.page, self.js):
            self.assertNotIn('data-node="reporter"', text)
            self.assertNotRegex(text, r"\bhub\b")
        # The card still names the node the reporter runs on.
        self.assertEqual(on(self.data, "live", "2026-11-02")["reporter_runs_on"], nid(1))
        self.assertIn('row(dl, "reporter", d.reporter_runs_on === id', self.js)

    # (j) On-demand runs

    def test_j_an_on_demand_run_is_its_own_labelled_point(self):
        live = self.data["views"]["live"]["days"]
        od = [d for d in live if d["set"] == "ondemand"]
        self.assertEqual([(d["date"], d["label"], d["file"]) for d in od],
                         [("2026-11-01T140509Z", "on-demand 14:05:09Z", "stats/data/ondemand/2026-11-01T140509Z.json")])
        i = [d["date"] for d in live].index("2026-11-01T140509Z")
        self.assertEqual((live[i - 1]["date"], live[i + 1]["date"]), ("2026-11-01", "2026-11-02"))  # between the days
        d = od[0]
        self.assertEqual(sorted(n["node_id"] for n in d["nodes"] if not n["left"]), [nid(1), nid(2), nid(4)])
        self.assertTrue(all(not n.get("first_day") for n in d["nodes"]))
        for kind in watch.STEPS:
            self.assertFalse(d["measured"][kind]["measured"])
            self.assertTrue(d["measured"][kind]["says"].startswith("not measured yet"))
        for m in self.data["views"]["live"]["metrics"]:
            pts = [p for p in m["points"] if p["set"] == "ondemand"]
            self.assertEqual([p["label"] for p in pts], [] if m["key"] in ("deals", "settlements") else ["on-demand 14:05:09Z"])
        self.assertIn('d.set === "ondemand"', self.js)

    def test_j_the_daily_entries_rows_and_counts_are_the_same_without_it(self):
        with tempfile.TemporaryDirectory() as d:
            without = run(Path(d), ondemand=None)
            page = (Path(d) / "watch" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(daily(without), daily(self.data))
        self.assertEqual(without["views"]["rehearsal"], self.data["views"]["rehearsal"])
        for a, b in zip(without["views"]["live"]["metrics"], self.data["views"]["live"]["metrics"]):
            self.assertEqual(a["points"], [p for p in b["points"] if p["set"] != "ondemand"], a["key"])
        r0, r1 = Rows(), Rows()
        r0.feed(page)
        r1.feed(self.page)
        self.assertEqual(r0.rows[("live", "days")], r1.rows[("live", "days")])
        self.assertNotIn(("live", "ondemand"), r0.rows)
        self.assertEqual([r["date"] for r in r1.rows[("live", "ondemand")]], ["2026-11-01T140509Z"])

    def test_j_a_missing_on_demand_directory_means_no_points(self):
        with tempfile.TemporaryDirectory() as d:
            data = run(Path(d), ondemand=Path(d) / "nothing-here")
        self.assertEqual({x["set"] for x in data["views"]["live"]["days"]}, {"daily"})

    def test_j_an_on_demand_file_not_in_the_scrubbed_format_fails_the_build(self):
        with tempfile.TemporaryDirectory() as d:
            od = Path(d) / "ondemand"
            shutil.copytree(FIX / "ondemand", od)
            f = od / "2026-11-01T140509Z.json"
            f.write_text(f.read_text().replace('"listed": 3', '"listed": 3, "extra": 1'))
            (od / "2026-11-01T1405Z.json").write_text("{}\n")
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = watch.main(["watch.py", "--daily", str(DAILY), "--rehearsal", str(FIX / "rehearsal"),
                                   "--ondemand", str(od), "--findings", str(FIX / "findings"), "--nodes", str(FIX / "nodes"),
                                   "--population", str(FIX / "population"), "--out", str(Path(d) / "out")])
        self.assertEqual(code, 1)
        self.assertIn("unexpected extra", err.getvalue())
        self.assertIn("not a <YYYY-MM-DD>T<HHMMSS>Z.json on-demand file", err.getvalue())

    def test_j_an_on_demand_file_is_held_to_the_day_file_checks(self):
        for change, want in (({"reporter_key_id": "someone@example.org"}, "reporter_key_id is not 64 hex"),
                             ({"listed": 1}, "nodes but only 1 listed"),
                             ({"missed_reason": "x"}, "missed_reason")):
            with tempfile.TemporaryDirectory() as d:
                od = Path(d) / "ondemand"
                shutil.copytree(FIX / "ondemand", od)
                f = od / "2026-11-01T140509Z.json"
                doc = json.loads(f.read_text())
                f.write_bytes(stats_model.canonical(doc | change)[0])
                err = io.StringIO()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                    code = watch.main(["watch.py", "--daily", str(DAILY), "--rehearsal", str(FIX / "rehearsal"),
                                       "--ondemand", str(od), "--findings", str(FIX / "findings"), "--nodes", str(FIX / "nodes"),
                                       "--population", str(FIX / "population"), "--out", str(Path(d) / "out")])
            self.assertEqual(code, 1, change)
            self.assertIn(want, err.getvalue())

    # Counts over time

    def test_each_charts_table_holds_the_exports_numbers(self):
        p = Charts()
        p.feed(self.page)
        for view, v in self.data["views"].items():
            self.assertEqual([m["key"] for m in v["metrics"]], [k for k, _ in watch.METRICS])
            for m in v["metrics"]:
                rows = p.rows[(view, m["key"])]
                self.assertEqual(len(rows), len(m["points"]), (view, m["key"]))
                for row, pt in zip(rows, m["points"]):
                    want = (pt["says"] if pt["value"] is None
                            else str(pt["value"]) + (f" ({pt['not_drawn']} not drawn)" if pt.get("not_drawn") else ""))
                    self.assertEqual(row, [pt["date"], pt["set"], pt["label"], want, pt["value"] is None])
        # The script rebuilds the same tables, cell for cell, when it reads data.json again.
        self.assertIn('p.value === null ? p.says : String(p.value) + (p.not_drawn ? " (" + p.not_drawn + " not drawn)" : "")',
                      self.js)

    def test_each_count_is_the_graphs_own(self):
        for v in self.data["views"].values():
            m = {x["key"]: {p["date"]: p for p in x["points"]} for x in v["metrics"]}
            for d in v["days"]:
                shown = [n for n in d["nodes"] if not n["left"]]
                if d["run"] == "settled":
                    self.assertEqual(m["nodes"][d["date"]]["value"], len(shown))
                    for o in watch.OUTCOMES:
                        self.assertEqual(m[o][d["date"]]["value"], sum(1 for a in asks(d) if a["outcome"] == o))
                    known = [n["records_at_checkpoint"] for n in shown if n["records_at_checkpoint"] is not None]
                    self.assertEqual(m["records"][d["date"]]["value"], sum(known) if known else None)
                else:
                    self.assertIsNone(m["nodes"][d["date"]]["value"])
                for key, kind in (("deals", "deal"), ("settlements", "settle")):
                    if d["set"] == "ondemand":
                        self.assertNotIn(d["date"], m[key])
                    elif d["measured"][kind]["measured"]:
                        self.assertEqual(m[key][d["date"]]["value"], len([e for e in d["edges"] if e["kind"] == kind]))
                    else:
                        self.assertIsNone(m[key][d["date"]]["value"])

    def test_a_count_with_no_finding_reads_not_measured_yet_never_0(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "no-findings").mkdir()
            data = run(Path(d), daily=ROOT / "stats" / "data" / "daily", rehearsal=ROOT / "stats" / "data" / "rehearsal",
                       findings=Path(d) / "no-findings", ondemand=None, nodes=ROOT / "nodes", commitments=ROOT / "lab" / "runs")
            page = (Path(d) / "watch" / "index.html").read_text(encoding="utf-8")
        p = Charts()
        p.feed(page)
        for view, v in data["views"].items():
            for key in ("deals", "settlements"):
                pts = next(m for m in v["metrics"] if m["key"] == key)["points"]
                self.assertTrue(pts)
                for pt in pts:
                    self.assertIsNone(pt["value"])
                    self.assertTrue(pt["says"].startswith("not measured yet"), pt)
                for row in p.rows[(view, key)]:
                    self.assertTrue(row[3].startswith("not measured yet"), row)
                    self.assertNotEqual(row[3], "0")
        self.assertIn('"not measured yet"', self.js)  # the chart with no value says so too

    # Edges from loop findings, and "not measured yet"

    def test_no_finding_for_the_day_says_not_measured_yet_naming_the_step(self):
        d = on(self.data, "live", "2026-11-02")
        for kind, step in (("settle", "06"), ("ask07", "07")):
            self.assertEqual(d["measured"][kind], {"measured": False, "step": step,
                                                   "says": f"not measured yet: no step {step} finding for this day"})
            self.assertEqual([e for e in d["edges"] if e["kind"] == kind], [])
        for kind in watch.STEPS:
            self.assertFalse(on(self.data, "live", "2026-10-28")["measured"][kind]["measured"])

    def test_a_live_deal_is_drawn_between_two_listed_nodes_and_an_unknown_sender_is_not(self):
        d = on(self.data, "live", "2026-11-02")
        deals = [e for e in d["edges"] if e["kind"] == "deal"]
        self.assertEqual(deals, [{"kind": "deal", "from": nid(1), "to": nid(4), "outcome": "answered"}])
        m = d["measured"]["deal"]
        self.assertEqual((m["measured"], m["result"], m["drawn"], m["not_drawn"]), (True, "NOT_CHECKED", 1, 1))
        self.assertEqual(m["finding"], "findings/2026-11-02-live/05-deal/finding.json")

    def test_rehearsal_findings_settle_and_ask_drawn_the_test_populations_deal_not(self):
        d = on(self.data, "rehearsal", "2026-11-05T0600Z")
        self.assertEqual([e for e in d["edges"] if e["kind"] == "settle"],
                         [{"kind": "settle", "from": nid(1), "to": nid(2), "outcome": "answered"}])
        self.assertEqual(sorted(e["outcome"] for e in d["edges"] if e["kind"] == "ask07"),
                         ["absent", "answered", "refused"])
        self.assertEqual((d["measured"]["deal"]["drawn"], d["measured"]["deal"]["not_drawn"]), (0, 2))
        self.assertFalse(on(self.data, "rehearsal", "2026-11-05T0610Z")["measured"]["settle"]["measured"])

    def test_the_reporter_backends_07_asks_come_from_the_node_it_runs_on(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "findings"
            shutil.copytree(FIX / "findings", f)
            step = f / "2026-11-02-live" / "07-ask"
            v = step / "node-four" / "attempts" / "20261102T060000Z" / "history_card.verification"
            v.parent.mkdir(parents=True)
            v.write_text("verified history_card/1\n")
            import finding
            finding.write(step, result="NOT_CHECKED", population="live", asserts="a fixture", pinned=[],
                          proof=[v.relative_to(step).as_posix()], detail="a fixture",
                          not_checked=[{"what": "a", "why": "b", "issue": "evidence-network/loop#1"}],
                          started="2026-11-02T06:00:00Z")
            data = run(Path(d) / "out", findings=f)
        day = on(data, "live", "2026-11-02")
        self.assertEqual([e for e in day["edges"] if e["kind"] == "ask07"],
                         [{"kind": "ask07", "from": nid(1), "to": nid(4), "outcome": "answered"}])
        self.assertEqual(next(p for p in day["pairs"] if (p["a"], p["b"]) == (nid(1), nid(4)))["kinds"], ["ask07", "deal"])

    def test_a_file_the_finding_does_not_hash_draws_no_line(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "findings"
            shutil.copytree(FIX / "findings", f)
            stray = f / "2026-11-05-rehearsal" / "07-ask" / "node-two" / "asks" / "node-one" / "exchange" / "outcome.json"
            stray.parent.mkdir(parents=True)
            stray.write_text('{"outcome": "artifact"}\n')  # not in the finding's proof: the format check ignores it
            data = run(Path(d) / "out", findings=f)
            base = run(Path(d) / "out2")
        self.assertEqual(on(data, "rehearsal", "2026-11-05T0600Z")["edges"], on(base, "rehearsal", "2026-11-05T0600Z")["edges"])
        with tempfile.TemporaryDirectory() as d:  # a stray directory is no participant either
            f = Path(d) / "findings"
            shutil.copytree(FIX / "findings", f)
            (f / "2026-11-05-rehearsal" / "05-deal" / "carol").mkdir()
            (f / "2026-11-05-rehearsal" / "05-deal" / "carol" / "x.txt").write_text("x\n")
            data = run(Path(d) / "out", findings=f)
        # not_drawn counts every edge the extractor found, so a stray participant changes it even when no line shows
        self.assertEqual(on(data, "rehearsal", "2026-11-05T0600Z")["measured"], on(base, "rehearsal", "2026-11-05T0600Z")["measured"])

    def test_a_settlement_that_did_not_hold_is_drawn_but_not_counted(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "findings"
            shutil.copytree(FIX / "findings", f)
            step = f / "2026-11-05-rehearsal" / "06-settle"
            checks = json.loads((step / "checks.json").read_text())
            checks["checks"]["finality"]["ok"] = False
            (step / "checks.json").write_text(json.dumps(checks, indent=2, sort_keys=True) + "\n")
            rec = json.loads((step / "finding.json").read_text())
            for p in rec["proof"]:
                if p["path"] == "checks.json":
                    p["sha256"] = hashlib.sha256((step / "checks.json").read_bytes()).hexdigest()
            (step / "finding.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
            data = run(Path(d) / "out", findings=f)
            base = run(Path(d) / "out2")
        def settlements(x):
            m = next(m for m in x["views"]["rehearsal"]["metrics"] if m["key"] == "settlements")
            return [p["value"] for p in m["points"] if p["date"] == "2026-11-05T0600Z"]
        self.assertEqual(settlements(base), [1])
        self.assertEqual(settlements(data), [0])

    def test_a_finding_that_fails_the_format_check_is_not_measured(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "findings"
            shutil.copytree(FIX / "findings", f)
            (f / "2026-11-02-live" / "05-deal" / "node-four" / "checks.json").write_text("{}\n")  # proof changed
            data = run(Path(d) / "out", findings=f)
        m = on(data, "live", "2026-11-02")["measured"]["deal"]
        self.assertFalse(m["measured"])
        self.assertIn("does not pass the format check", m["says"])
        self.assertEqual([e for e in on(data, "live", "2026-11-02")["edges"] if e["kind"] == "deal"], [])
        self.assertTrue(all(p["line"] == "dotted" for p in on(data, "live", "2026-11-02")["pairs"]))

    def test_the_committed_findings_05_06_07_draw_only_what_they_hold(self):
        with tempfile.TemporaryDirectory() as d:
            data = run(Path(d), daily=ROOT / "stats" / "data" / "daily", rehearsal=ROOT / "stats" / "data" / "rehearsal",
                       findings=ROOT / "findings", ondemand=None, nodes=ROOT / "nodes", commitments=ROOT / "lab" / "runs")
        for v in data["views"].values():
            for day in v["days"]:
                for kind in watch.STEPS:
                    m = day["measured"][kind]
                    if not m["measured"]:
                        self.assertIn("not measured yet", m["says"])
                    else:  # a finding committed since: every line drawn is an edge it holds
                        self.assertEqual(m["drawn"], len([e for e in day["edges"] if e["kind"] == kind]))
                held = {frozenset((e["from"], e["to"])) for e in day["edges"] if e["outcome"] in watch.EXCHANGED}
                self.assertEqual({frozenset((p["a"], p["b"])) for p in day["pairs"] if p["line"] == "solid"}, held)

    # (e) Nothing from elsewhere; the one read is data.json

    def test_e_no_external_url_in_the_page_or_its_files(self):
        for p in (self.out / "watch").rglob("*"):
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8")
            urls = set(re.findall(r"https?://[^\s\"'<>)]+", text))
            self.assertLessEqual(urls, D3_URLS if p.name == "d3.min.js" else {SVG_NS}, p.name)
            self.assertNotIn("@import", text)
            self.assertNotIn("<link", text)
        scripts = re.findall(r"<script([^>]*)>", self.page)
        self.assertEqual(scripts, [' type="application/json" id="watch-data"', ' src="vendor/d3-7.9.0/d3.min.js"',
                                   ' src="watch-layout.js"', ' src="watch.js"'])
        self.assertNotIn("url(", re.sub(r"url\(#[a-z-]+\)", "", self.page))
        for js in (self.js, self.layout_js):
            self.assertNotRegex(js, r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(")
            self.assertNotRegex(js, r"XMLHttpRequest|WebSocket|EventSource|import\(|sendBeacon")
            # d3's own fetch helpers are in the vendored file; the page's scripts never call them.
            self.assertNotRegex(js, r"d3\.(json|text|csv|tsv|dsv|xml|html|svg|image|blob|buffer)\(")
        self.assertNotIn("fetch(", self.layout_js)

    def test_e_the_only_url_the_script_reads_is_its_own_relative_data_json(self):
        calls = re.findall(r"\bfetch\(\s*([^,)]+)", self.js)
        self.assertEqual(calls, ['"data.json"'])  # relative, same origin: no scheme, no host, no leading slash
        self.assertIn("setInterval(poll, 60000);", self.js)
        self.assertIn('href="data.json"', self.page)

    # (f) No money, per node or at all

    def test_f_no_amount_or_band_in_the_export(self):
        for k in keys(self.data):
            self.assertIsNone(MONEY.search(k), k)
        blob = json.dumps(self.data)
        for planted in ("4200", "USDC", "b2", "band"):  # the 06 fixture's exchange and checks carry these
            self.assertNotIn(planted, blob)
        for s in strings(self.data):
            self.assertIsNone(re.search(r"\bamount|\bband\b", s, re.I), s)

    def test_f_no_amount_or_band_in_the_hover_card(self):
        show = self.js[self.js.index("function show("):self.js.index("function hide(")]
        self.assertIsNone(MONEY.search(show), MONEY.search(show))
        self.assertIsNone(MONEY.search(self.js))
        labels = re.findall(r'row\(dl, "([^"]+)"', show) + re.findall(r'html\("dt", "", "([^"]+)"\)', show)
        self.assertEqual(sorted(set(labels)), ["asks", "binding", "build", "history cards", "operator", "plugin",
                                               "records", "reporter", "this ", "verifier"])

    # (n) History-card badges

    def test_n_a_node_with_two_stapled_cards_shows_badge_2_and_lists_both_streams(self):
        two = self.data["nodes"][nid(2)]
        self.assertEqual(two["commitment"], "commitments/2026-10-28/join/node-two/commitment.json")
        self.assertEqual(two["history_cards"], [
            {"derivation": "history_card/1", "stream": "node-two-book-a", "community": "mesh-llm",
             "witnesses": ["witness-one"], "history": True, "since": None},
            {"derivation": "history_card/1", "stream": "node-two-book-b", "community": "personal-agents",
             "witnesses": [], "history": False, "since": "2026-10-20"}])
        self.assertEqual(self.data["nodes"][nid(1)]["history_cards"],  # evidence-commitment/0: one card, mesh-llm
                         [{"derivation": "history_card/1", "stream": "node-one-book", "community": "mesh-llm",
                           "witnesses": [], "history": True, "since": None}])
        self.assertEqual((self.data["nodes"][nid(3)]["commitment"], self.data["nodes"][nid(3)]["history_cards"]), (None, []))
        # The badge is the list's length; the card lists each stream and its witnesses, or "unwitnessed".
        self.assertIn("return k + ((data.nodes[m] || {}).history_cards || []).length;", self.js)
        self.assertIn('String(cards)', self.js)
        self.assertIn('"stream " + (c.stream ? cut(c.stream)', self.js)
        self.assertIn('"unwitnessed"', self.js)

    def test_n_a_real_node_reads_1_from_its_evidence_commitment_0(self):
        by_id, by_key = watch.listing(ROOT / "nodes")
        got = watch.commitments(ROOT / "lab" / "runs", ROOT / "nodes", by_id, by_key, lambda p: Path(p).name)
        self.assertEqual(sorted(got), sorted(by_id))
        for nid_, (_, c) in got.items():
            self.assertIn(c["profile"], ("evidence-commitment/0", "evidence-commitment/1"))
            cards = watch.history_cards(c)
            self.assertEqual(len(cards), 1, nid_)
            self.assertEqual(cards[0]["derivation"], "history_card/1")
            self.assertEqual((cards[0]["community"], cards[0]["history"]), ("mesh-llm", True))
            self.assertRegex(cards[0]["stream"], r"^capsules/[0-9a-f]{16}$")  # the plugin's stream name
            self.assertEqual(cards[0]["witnesses"], [])  # the lab books are unwitnessed

    def test_n_the_card_list_names_only_stream_derivation_and_witnesses(self):
        for n in self.data["nodes"].values():
            for c in n["history_cards"]:
                self.assertEqual(set(c), {"stream", "derivation", "community", "witnesses", "history", "since"})
        self.assertNotIn('"bytes"', json.dumps(self.data))
        self.assertNotIn('"sha256"', json.dumps(self.data))
        self.assertEqual(watch.history_cards({}), [])
        self.assertEqual(len(watch.history_cards({"history_cards": [{"stream": "x"}, {"stream": "y"}, {"stream": "z"}]})), 3)

    # Community rings and bound groups

    def dot(self, data, date, nid_):
        return next(x for x in on(data, "live", date)["drawn"] if nid_ in x["members"])

    def test_e_the_bound_fixture_draws_one_dot_two_rings_and_bound(self):
        self.assertEqual([(g["id"], g["subjects"], g["verifier"]) for g in self.data["groups"]],
                         [(BOUND, [nid(1), nid(4)],
                           "binding-verifier@0123456789abcdef0123456789abcdef01234567")])
        d = on(self.data, "live", "2026-11-02")
        bound = [x for x in d["drawn"] if x["bound"]]
        self.assertEqual([x["members"] for x in bound], [[nid(1), nid(4)]])
        self.assertEqual([(r["community"], r["history"]) for r in bound[0]["rings"]],
                         [("mesh-llm", True), ("personal-agents", True)])
        self.assertEqual(sorted(m for x in d["drawn"] for m in x["members"]), sorted(n["node_id"] for n in d["nodes"]))
        # Before node 4 joins, the group's one shown subject is still drawn as the bound dot.
        self.assertTrue(self.dot(self.data, "2026-10-28", nid(1))["bound"])
        # Links run between what is drawn: the group's own pair is inside it, and its pairs to node 2 are one link.
        self.assertEqual([(x["a"], x["b"], x["line"]) for x in d["links"]], [tuple(sorted((nid(2), BOUND))) + ("dotted",)])
        # The controller's key id (the operator's key) is nowhere in the export, in an id or in a path.
        self.assertNotIn("c" * 64, json.dumps(self.data))
        self.assertIn('dot.bound ? "bound"', self.js)
        self.assertIn('"community c-" + r.community', self.js)
        self.assertIn('row(dl, "verifier", g.verifier);', self.js)
        self.assertEqual(self.data["nodes"][nid(1)]["binding"], {"status": "verified", "group": BOUND})

    def test_e_the_revoked_fixture_draws_two_separate_dots(self):
        with tempfile.TemporaryDirectory() as d:
            data = run(Path(d), bindings=FIX / "bindings" / "revoked")
        self.assertEqual(data["groups"], [])
        day = on(data, "live", "2026-11-02")
        self.assertFalse(any(x["bound"] for x in day["drawn"]))
        self.assertEqual([x["members"] for x in day["drawn"] if set(x["members"]) & {nid(1), nid(4)}], [[nid(1)], [nid(4)]])
        for n in (1, 4):
            b = data["nodes"][nid(n)]["binding"]
            self.assertEqual((b["status"], b["says"], b["why"], b["cites"]),
                             ("revoked", "binding not verified", "its status is revoked", "evidence-network/loop#1"))
        self.assertIn("meta.binding.says", self.js)

    def test_a_verified_verdict_without_a_pinned_verifier_does_not_group(self):
        with tempfile.TemporaryDirectory() as d:
            unpinned = run(Path(d) / "a", bindings=FIX / "bindings" / "unpinned")
            nolock = run(Path(d) / "b", lock=None)
            nodir = run(Path(d) / "c", bindings=Path(d) / "no-such-dir")
        for data in (unpinned, nolock):
            self.assertEqual(data["groups"], [])
            self.assertFalse(any(x["bound"] for v in data["views"].values() for day in v["days"] for x in day["drawn"]))
            self.assertEqual(data["nodes"][nid(1)]["binding"]["why"], "its verifier is not pinned in the lock")
        self.assertEqual((nodir["groups"], nodir["nodes"][nid(1)]["binding"]), ([], None))

    def test_the_cleared_fixture_draws_an_empty_ring_no_history_since_and_no_warning(self):
        rings = self.dot(self.data, "2026-11-02", nid(2))["rings"]
        self.assertEqual(rings, [{"community": "mesh-llm", "history": True, "streams": ["node-two-book-a"], "since": None},
                                 {"community": "personal-agents", "history": False, "streams": ["node-two-book-b"],
                                  "since": "2026-10-20"}])
        self.assertIn('(r.history ? "" : " empty")', self.js)
        self.assertIn('"no history since " + (r.since', self.js)
        # The same look whether the stream is new or cleared: no warning class, nothing red, no score.
        css = re.search(r"svg \.community\.empty \{([^}]*)\}", self.page).group(1)
        self.assertEqual(css.strip(), "stroke-width: 1;")
        ring_code = self.js[self.js.index("dot.rings.forEach"):self.js.index('el("circle", { "class": "dot"')]
        self.assertNotRegex(ring_code, r"warn|error|alert|fail|refused|st-not_evidence")
        self.assertNotIn("cleared", json.dumps(self.data))

    def test_a_stream_with_no_history_reads_the_same_new_or_cleared(self):
        import base64
        def commitment(started):
            art = base64.urlsafe_b64encode(json.dumps({"evidence_stream": "s", "history": [],
                                                       "stream_started": started}).encode()).decode()
            b = base64.b64encode(json.dumps({"artifact": art}).encode()).decode()
            return {"profile": "evidence-commitment/1",
                    "history_cards": [{"stream": "s", "community": "muse", "derivation": "history_card/1", "bytes": b}]}
        new, cleared = watch.history_cards(commitment("2026-10-20")), watch.history_cards(commitment("2026-10-20"))
        self.assertEqual(new, cleared)
        self.assertEqual((new[0]["history"], new[0]["since"], new[0]["community"]), (False, "2026-10-20", "personal-agents"))

    # (m) Zoom, pan, rotate, reset; a stable layout

    def test_m_the_controls_and_their_keys(self):
        for b in ("zoom-in", "zoom-out", "rotate-left", "rotate-right", "reset"):
            self.assertRegex(self.page, rf'<button type="button" id="{b}"')
            self.assertIn(f'getElementById("{b}").addEventListener("click"', self.js)
        for key in ('"+"', '"-"', '"ArrowLeft"', '"ArrowRight"', '"ArrowUp"', '"ArrowDown"', '"["', '"]"', '"0"'):
            self.assertIn(f"k === {key}", self.js)
        for kbd in ("<kbd>+</kbd>", "<kbd>&minus;</kbd>", "<kbd>[</kbd>", "<kbd>]</kbd>", "<kbd>0</kbd>"):
            self.assertIn(kbd, self.page)
        self.assertIn("the arrow keys pan", self.page)
        self.assertIn("Shift and drag to rotate", self.page)
        self.assertIn("d3.zoom()", self.js)
        self.assertRegex(self.page, r'<svg id="graph"[^>]*tabindex="0"')
        # Reset: no rotation, the zoom's identity, and the places as the layout gives them.
        reset = self.js[self.js.index("function reset()"):self.js.index("function turn(")]
        self.assertIn("state.angle = 0;", reset)
        self.assertIn("zoom.transform, d3.zoomIdentity", reset)
        self.assertIn("reposition();", reset)
        self.assertIn("state.places = watchLayout(d3, view().days, seeds, data.size);", self.js)

    def test_m_each_node_is_seeded_from_its_node_id(self):
        for nid_, n in self.data["nodes"].items():
            self.assertEqual(n["seed"], watch.seed(nid_))
            self.assertTrue(0 < n["seed"][0] < watch.SIZE[0] and 0 < n["seed"][1] < watch.SIZE[1])
        self.assertEqual(self.data["size"], list(watch.SIZE))

    def test_m_the_layout_is_the_same_on_every_load_and_reset_restores_it(self):
        js = "const a = layout(d3, data.views.live.days, seeds, data.size); const b = layout(d3, data.views.live.days, seeds, data.size);"
        got = node_js(js + "process.stdout.write(JSON.stringify([a, b]));", self.out / "watch" / "data.json")
        self.assertEqual(got[0], got[1])
        self.assertEqual(len(got[0]), len(self.data["views"]["live"]["days"]))

    def test_m_nodes_already_placed_do_not_move_when_a_fourth_joins(self):
        got = node_js("process.stdout.write(JSON.stringify(layout(d3, data.views.live.days, seeds, data.size)));",
                      self.out / "watch" / "data.json")
        dates = [d["date"] for d in self.data["views"]["live"]["days"]]
        before, after = got[dates.index("2026-10-29")], got[dates.index("2026-10-30")]
        self.assertEqual(sorted(before), [nid(1), nid(2), nid(3)])
        self.assertIn(nid(4), after)
        for k, p in before.items():
            moved = ((after[k][0] - p[0]) ** 2 + (after[k][1] - p[1]) ** 2) ** .5
            self.assertLess(moved, 1.0, k)
        # The new node settles away from the others, inside the graph.
        for k, p in before.items():
            self.assertGreater(((after[nid(4)][0] - p[0]) ** 2 + (after[nid(4)][1] - p[1]) ** 2) ** .5, 40)
        # And it eases in, unless reduced motion is asked for.
        self.assertIn('"body arrive"', self.js)
        self.assertRegex(self.page, r"@media \(prefers-reduced-motion: reduce\) \{ svg \.ping, svg \.arrive \{ animation: none; \}")

    # (o) The vendored d3

    def test_o_d3_is_vendored_at_its_pinned_sha256_with_its_licence(self):
        self.assertEqual(hashlib.sha256((D3 / "d3.min.js").read_bytes()).hexdigest(), D3_SHA256)
        lic = (D3 / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Mike Bostock", lic)
        self.assertIn("Permission to use, copy, modify, and/or distribute this software", lic)  # ISC
        readme = (D3 / "README.md").read_text(encoding="utf-8")
        for s in ("d3@7.9.0", "dist/d3.min.js", "ISC", D3_SHA256,
                  "sha512-e1U46jVP+w7Iut8Jt8ri1YsPOvFpg46k+K8TpCb0P+zjCkjkPnV7WzfDJzMHy1LnA+wj5pLT1wjO901gLXeEhA=="):
            self.assertIn(s, readme)
        self.assertTrue((D3 / "d3.min.js").read_text(encoding="utf-8").startswith("// https://d3js.org v7.9.0 "))
        for f in ("d3.min.js", "LICENSE"):
            self.assertEqual((self.out / "watch" / watch.D3 / f).read_bytes(), (D3 / f).read_bytes())
        self.assertIn('href="vendor/d3-7.9.0/LICENSE"', self.page)

    # Neutral, the network's, no branding

    def test_branding_and_neutrality_as_on_the_stats_page(self):
        self.assertIn("This page is the evidence network's.", self.page)
        self.assertEqual(test_site.branding(self.out), [])
        # Every file, the vendored d3 included; its LICENSE carries its own copyright notice, as the ISC licence asks.
        for p in (self.out / "watch").rglob("*"):
            if not p.is_file() or p.name == "LICENSE":
                continue
            low = p.read_text(encoding="utf-8").lower()
            for b in test_site.BRANDS:
                if p.name == "d3.min.js" and b == "copyright":
                    continue  # its banner comment: "Copyright 2010-2023 Mike Bostock"
                self.assertIsNone(re.search(rf"\b{re.escape(b)}\b", low), (p.name, b))
            # The public leak-lint rule (.github/leak_lint.py BRACKET_ID): [ + three or more hyphenated segments + ].
            self.assertIsNone(re.search(r"\[[a-z0-9]{2,}(?:-[a-z0-9]{2,}){2,}\]", low), p.name)
        prose = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", self.page, flags=re.S))
        self.assertNotIn("%", prose)  # (in the script, % is the remainder operator)
        self.assertIsNone(re.search(r"\btop\b", prose, re.I))
        for text in (prose.lower(), self.js.lower(), self.layout_js.lower()):
            for word in ("league", "leaderboard", "percent", "reputation", "pricing", "premium", "customer",
                         "underwrit", "insur", "kyb", "registry"):
                self.assertFalse(word in text, word)
            for word in ("score", "rank", "total"):
                for m in re.finditer(word, text):
                    self.assertIn("no score, ranking", text[max(m.start() - 30, 0):m.end() + 30], word)
        self.assertIsNone(re.search(r"\b(checked|witnessed)\b", prose, re.I))
        body = re.sub(r"<script.*?</script>|<style.*?</style>", "", self.page, flags=re.S)
        for m in re.finditer(r"verified", body):
            self.assertRegex(body[max(m.start() - 6, 0):m.start()], r"<code>$")

    def test_nodes_by_node_id_and_no_score_per_node(self):
        for n in (n for v in self.data["views"].values() for d in v["days"] for n in d["nodes"]):
            self.assertLessEqual(set(n), {"node_id", "state", "questions", "asked", "asks", "records_at_checkpoint",
                                          "days_listed", "first_day", "left_on", "left", "node_line"})
        self.assertEqual(set(self.data["nodes"][nid(1)]), {"operator", "host_build", "plugin_version", "listed", "seed",
                                                           "commitment", "history_cards", "binding"})
        self.assertEqual(self.data["nodes"][nid(3)], {"listed": False, "seed": watch.seed(nid(3)), "commitment": None,
                                                      "history_cards": [], "binding": None})  # left: not in nodes/

    def test_no_answer_bytes_in_the_export(self):
        blob = json.dumps(self.data)
        self.assertNotIn("answer_sha256", blob)
        for day in self.days.values():
            for n in day.nodes:
                for a in n.answers.values():
                    if a.answer_sha256:
                        self.assertNotIn(a.answer_sha256, blob)

    # (g) Keyboard and fallback; the card beside the graph

    def test_g_keyboard_and_text_fallback(self):
        for a in ('tabindex: "0"', 'addEventListener("focus"', 'addEventListener("blur"', '"Escape"'):
            self.assertIn(a, self.js)
        self.assertIn('role="tooltip"', self.page)
        self.assertEqual(len(re.findall(r'<button type="button"', self.page)), 9)
        self.assertIn('<input type="range" id="day"', self.page)
        self.assertIn("<noscript>", self.page)
        self.assertIn('aria-live="polite"', self.page)
        self.assertIn("prefers-reduced-motion", self.js)
        self.assertIn("if (still) return;", self.js[self.js.index("function travel("):])  # no travelling pulse

    def test_the_card_sits_in_its_own_slot_beside_the_graph(self):
        stage = self.page[self.page.index('<div class="stage">'):self.page.index('<p class="keys"')]
        self.assertLess(stage.index('<figure class="graph">'), stage.index('<div class="side">'))
        self.assertLess(stage.index("</figure>"), stage.index('<div id="card"'))
        self.assertNotIn("card.style", self.js)  # never placed over the graph
        self.assertRegex(self.page, r"\.canvas \.stage \{[^}]*grid-template-columns: minmax\(0, 1fr\) 19rem;")

    def test_the_text_sits_below_the_fold(self):
        self.assertRegex(self.page, r"\.canvas \{ background: var\(--cv-bg\); color: var\(--cv-fg\); min-height: min\(100vh, 60rem\);")
        self.assertLess(self.page.index("</section>"), self.page.index('<p class="lede">'))

    def test_the_scripts_parse(self):
        if not os.access(NODE, os.X_OK):
            self.skipTest("SKIPPED LOUDLY: no node binary to syntax-check site/watch.js")
        for f in ("watch.js", "watch-layout.js"):
            r = subprocess.run([NODE, "--check", str(ROOT / "site" / f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    # /stats/ unchanged

    def test_stats_is_unchanged_by_the_watch_build(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "_site"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(build.main(["build.py", str(DAILY), "--findings", str(STATS_FINDINGS), "--lock",
                                             str(ROOT / "versions.lock"), "--out", str(out)]), 0)

            def tree():
                return {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(out.rglob("*")) if p.is_file()}
            before = tree()
            run(out)
            after = tree()
            self.assertEqual({k: v for k, v in after.items() if not k.startswith("watch/")}, before)
            self.assertEqual((out / "stats" / "index.html").read_bytes(), EXPECTED.read_bytes())


class WatchContrastTest(unittest.TestCase):
    """The page's tokens, in both schemes, with test_stats_contrast's reading of the CSS.

    The canvas is dark in both schemes, with its own tokens: a rule under .canvas
    sits on --cv-bg (or the card's --cv-card), the charts on the page's --bg."""

    def setUp(self):
        page = (ROOT / "site" / "watch.html").read_text(encoding="utf-8")
        self.css = page[page.index("<style>") + 7:page.index("</style>")]
        self.schemes = contrast.schemes(self.css)

    def background(self, sel, bg):
        if bg != "--bg":
            return bg
        if "#card" in sel:
            return "--cv-card"
        if "badgetext" in sel:
            return "--cv-badge"
        return "--cv-bg" if sel.startswith(".canvas") else "--bg"

    def test_one_token_set_redefined_for_dark(self):
        light, dark = self.schemes["light"], self.schemes["dark"]
        self.assertEqual(sorted(light), sorted(dark))
        self.assertEqual(len(re.findall(r"#[0-9a-fA-F]{3,6}\b", self.css)), len(light) + len(dark))

    def test_the_canvas_is_dark_in_both_schemes(self):
        for scheme, t in self.schemes.items():
            self.assertLess(contrast.luminance(t["--cv-bg"]), 0.02, scheme)
            self.assertLess(contrast.luminance(t["--cv-card"]), 0.03, scheme)
            self.assertGreater(contrast.luminance(t["--cv-fg"]), 0.7, scheme)

    def test_every_text_colour_reaches_4_5_to_1_in_both_schemes(self):
        found = contrast.pairs(self.css)
        self.assertGreater(len(found), 15)
        canvas = [sel for sel, _, _ in found if sel.startswith(".canvas")]
        self.assertGreater(len(canvas), 10)
        for scheme, t in self.schemes.items():
            for sel, fg, bg in found:
                bg = self.background(sel, bg)
                r = contrast.ratio(t[fg], t[bg])
                self.assertGreaterEqual(r, contrast.MIN_RATIO, f"{scheme}: {sel}: {fg} on {bg} is {r:.2f}:1")
            for fg in ("--muted", "--fg"):
                self.assertGreaterEqual(contrast.ratio(t[fg], t["--card"]), contrast.MIN_RATIO, (scheme, fg))
            for fg in ("--cv-muted", "--cv-fg"):
                self.assertGreaterEqual(contrast.ratio(t[fg], t["--cv-card"]), contrast.MIN_RATIO, (scheme, fg))
            self.assertGreaterEqual(contrast.ratio(t["--cv-badge-fg"], t["--cv-badge"]), contrast.MIN_RATIO, scheme)

    def test_lines_rings_and_dots_stand_out_from_the_canvas_at_3_to_1(self):
        for scheme, t in self.schemes.items():
            for tok in ("--cv-answered", "--cv-refused", "--cv-absent", "--cv-line", "--cv-solid", "--cv-ring",
                        "--cv-badge", "--cv-left", "--cv-accent", "--cv-com-mesh", "--cv-com-agents") + tuple(k for k in t if k.startswith("--st-")):
                self.assertGreaterEqual(contrast.ratio(t[tok], t["--cv-bg"]), 3, (scheme, tok))
            for tok in ("--chart", "--chart-od", "--muted"):
                self.assertGreaterEqual(contrast.ratio(t[tok], t["--bg"]), 3, (scheme, tok))

    def test_the_check_catches_a_dim_canvas_token(self):
        bad = self.css.replace("--cv-line: #66717d", "--cv-line: #2a3038", 1)
        t = contrast.schemes(bad)["light"]
        self.assertLess(contrast.ratio(t["--cv-line"], t["--cv-bg"]), 3)

    def test_every_font_size_is_12px_or_larger_and_the_graph_is_never_scaled(self):
        for sel, d in contrast.rules(self.css):
            if "font-size" in d:
                v = contrast.px(d["font-size"])
                self.assertIsNotNone(v, sel)
                self.assertGreaterEqual(v, contrast.MIN_PX, sel)
        page = (ROOT / "site" / "watch.html").read_text(encoding="utf-8")
        tag = re.search(r'<svg id="graph"[^>]*>', page).group(0)
        w = re.search(r'viewBox="0 0 (\d+) ', tag).group(1)
        self.assertIn(f'width="{w}"', tag)  # drawn 1:1 (zoom is the reader's); on a phone its box scrolls
        self.assertIn(f"svg#graph {{ display: block; width: {w}px;", self.css)
        self.assertEqual(int(w), watch.SIZE[0])


class FixturesTest(unittest.TestCase):
    def test_the_fixtures_are_what_make_fixtures_writes(self):
        sys.path.insert(0, str(FIX))
        import importlib.util
        spec = importlib.util.spec_from_file_location("watch_fixtures", FIX / "make_fixtures.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            mod.HERE = Path(d)
            mod.main()
            made = sorted(p.relative_to(d).as_posix() for p in Path(d).rglob("*") if p.is_file())
            have = sorted(p.relative_to(FIX).as_posix() for p in FIX.rglob("*") if p.is_file()
                          and p.name != "make_fixtures.py" and "__pycache__" not in p.parts)
            self.assertEqual(made, have)
            for rel in made:
                self.assertEqual((Path(d) / rel).read_bytes(), (FIX / rel).read_bytes(), rel)


if __name__ == "__main__":
    unittest.main()
