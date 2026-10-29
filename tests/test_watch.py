"""The watch page (site/watch.py): the network as a graph, day by day, from committed files only.

  (b) the nodes and edges of a shown day reconcile with that day's JSON;
  (c) a node that joins on day N is not in the data before N, and is from N;
      a node that leaves is there before its left_on and marked left after;
  (d) answered, refused and absent are three outcomes with three distinct
      strokes and end markers; rehearsal and live are two data sets;
  (e) the built page and its files reference no external URL;
  (f) no amount or band, in the export or in the hover card's markup;
  the stats page's branding and neutrality checks run over /watch/; an edge
  kind with no finding for the day says "not measured yet"; /stats/ is
  unchanged by the watch build; contrast in both schemes.

Fixtures: tests/fixtures/stats/daily/ (node 4 joins on 2026-10-30, node 3
leaves on 2026-11-01) and tests/fixtures/watch/ (made by make_fixtures.py
there: nodes/, population/, a rehearsal set and findings in each step's shape).

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
MONEY = re.compile(r"amount|band|msat|currency|price|fee\b|usd|usdc|sats?\b|payment", re.I)


def nid(n):
    return str(n) * 64


def run(out, daily=DAILY, rehearsal=FIX / "rehearsal", findings=FIX / "findings"):
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = watch.main(["watch.py", "--daily", str(daily), "--rehearsal", str(rehearsal), "--findings",
                           str(findings), "--nodes", str(FIX / "nodes"), "--population", str(FIX / "population"),
                           "--out", str(out)])
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


class Rows(HTMLParser):
    """The fallback tables: view -> [{data-k: cell text}] with each row's date."""

    def __init__(self):
        super().__init__()
        self.view, self.rows, self.cell = None, {}, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "details" and "data-view" in a:
            self.view = a["data-view"]
            self.rows[self.view] = []
        elif tag == "tr" and "data-date" in a:
            self.rows[self.view].append({"date": a["data-date"]})
        elif tag == "td" and "data-k" in a:
            self.cell = a["data-k"]
            self.rows[self.view][-1][self.cell] = ""

    def handle_endtag(self, tag):
        if tag == "td":
            self.cell = None

    def handle_data(self, data):
        if self.cell:
            self.rows[self.view][-1][self.cell] += data


class WatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "_site"
        cls.data = run(cls.out)
        cls.page = (cls.out / "watch" / "index.html").read_text(encoding="utf-8")
        cls.js = (cls.out / "watch" / "watch.js").read_text(encoding="utf-8")
        cls.days = {d.date: d for d in stats_model.load(DAILY)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_page_and_its_files_are_written_under_watch(self):
        self.assertEqual(sorted(p.name for p in (self.out / "watch").iterdir()), ["data.json", "index.html", "watch.js"])
        self.assertEqual((self.out / "watch" / "watch.js").read_bytes(), (ROOT / "site" / "watch.js").read_bytes())

    def test_the_page_carries_the_export_inline_byte_for_byte_the_same_data(self):
        blob = re.search(r'<script type="application/json" id="watch-data">(.*?)</script>', self.page, re.S).group(1)
        self.assertEqual(json.loads(blob.replace("<\\/", "</")), self.data)
        self.assertEqual(self.data["format"], "watch/1")

    def test_two_builds_are_identical(self):
        with tempfile.TemporaryDirectory() as d:
            run(Path(d))
            cmp = filecmp.dircmp(self.out / "watch", Path(d) / "watch")
            self.assertEqual(cmp.diff_files, [])
            self.assertEqual((cmp.left_only, cmp.right_only), ([], []))

    # (b) The shown day against its JSON

    def test_b_nodes_and_edges_reconcile_with_each_days_json(self):
        for entry in self.data["views"]["live"]["days"]:
            day = self.days.get(entry["date"])
            drawn = [n for n in entry["nodes"] if not n["left"]]
            asks = [e for e in entry["edges"] if e["kind"] == "ask"]
            if day is None or day.missed:
                self.assertEqual((drawn, asks), ([], []), entry["date"])
                continue
            on_day = [n for n in day.nodes if not (n.left_on and entry["date"] >= n.left_on)]
            self.assertEqual(sorted(n["node_id"] for n in drawn), sorted(n.node_id for n in on_day), entry["date"])
            self.assertEqual(entry["listed"], day.listed)
            self.assertEqual(len(asks), 2 * len(day.nodes), entry["date"])
            want = {o: 0 for o in watch.OUTCOMES}
            for n in day.nodes:
                for a in n.answers.values():
                    want["refused" if a.state == "refusal" else "absent" if a.state == "absence" else "answered"] += 1
                    match = [e for e in asks if e["to"] == n.node_id and e["question"] == stats_model.QUESTIONS[a.stem]]
                    self.assertEqual([m["state"] for m in match], [a.state])
            got = {o: sum(1 for e in asks if e["outcome"] == o) for o in watch.OUTCOMES}
            self.assertEqual(got, want, entry["date"])
            for n in drawn:
                d = next(x for x in day.nodes if x.node_id == n["node_id"])
                self.assertEqual(sum(n["asks"].values()), 2)
                self.assertEqual(n["records_at_checkpoint"], d.answers["checkpoints"].records)

    def test_b_on_the_committed_data_too(self):
        with tempfile.TemporaryDirectory() as d:
            data = run(Path(d), daily=ROOT / "stats" / "data" / "daily", rehearsal=ROOT / "stats" / "data" / "rehearsal",
                       findings=ROOT / "findings")
        for view, root, period in (("live", ROOT / "stats" / "data" / "daily", stats_model.layout.Daily),
                                   ("rehearsal", ROOT / "stats" / "data" / "rehearsal", stats_model.layout.Rehearsal)):
            files = {d.date: d for d in stats_model.load(root, period)}
            for entry in data["views"][view]["days"]:
                day = files.get(entry["date"])
                n = 0 if day is None or day.missed else len(day.nodes)
                self.assertEqual(len([x for x in entry["nodes"] if not x["left"]]), n)
                self.assertEqual(len([e for e in entry["edges"] if e["kind"] == "ask"]), 2 * n)

    def test_the_text_table_holds_the_graphs_counts(self):
        p = Rows()
        p.feed(self.page)
        for view, v in self.data["views"].items():
            rows = {r["date"]: r for r in p.rows[view]}
            self.assertEqual(sorted(rows), sorted(d["date"] for d in v["days"]))
            for d in v["days"]:
                r = rows[d["date"]]
                drawn = [n for n in d["nodes"] if not n["left"]]
                asks = [e for e in d["edges"] if e["kind"] == "ask"]
                if d["run"] == "settled":
                    self.assertEqual(r["nodes"], f"{len(drawn)} of {d['listed']} listed")
                self.assertEqual(r["left"], str(len(d["nodes"]) - len(drawn)))
                for o in watch.OUTCOMES:
                    n = sum(1 for e in asks if e["outcome"] == o)
                    self.assertEqual(r[f"ask-{o}"], f"{n} of {len(asks)} asks" if asks else "-")
                for kind in watch.STEPS:
                    m = d["measured"][kind]
                    self.assertIn("not measured yet" if not m["measured"] else f"{m['not_drawn']} not drawn", r[kind])

    # (c) Joining and leaving

    def test_c_a_fourth_node_joins_on_its_day_and_not_before(self):
        four = nid(4)
        for d in self.data["views"]["live"]["days"]:
            ids = [n["node_id"] for n in d["nodes"]]
            if d["date"] < "2026-10-30":
                self.assertNotIn(four, ids, d["date"])
                self.assertNotIn(four, [e["to"] for e in d["edges"]], d["date"])
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
            self.assertNotIn(three, [e["to"] for e in d["edges"]])  # not asked once it left
        self.assertIn("left", self.js)  # the script fades it: class "left"
        self.assertRegex(self.page, r"svg \.left \{ opacity: \.\d+; \}")

    def test_a_leaver_still_in_its_left_on_day_file_is_marked_left(self):
        with tempfile.TemporaryDirectory() as d:
            daily = Path(d) / "daily"
            shutil.copytree(DAILY, daily)
            f = daily / "2026-11-01.json"
            day = json.loads(f.read_text())
            gone = {"absent_days_running": 1, "left_on": "2026-11-01", "node_id": nid(3), "node_line": "discloses: checkpoints",
                    "questions": {k: {"answer_sha256": None, "attempts": [{"at": "2026-11-01T06:00:00Z", "state": "absence"}],
                                      "log_size_at_checkpoint": None, "records_at_checkpoint": None, "state": "absence"}
                                  for k in ("checkpoints", "history_card")}}
            day["nodes"] = sorted(day["nodes"] + [gone], key=lambda n: n["node_id"])
            day["listed"] = 4
            f.write_text(json.dumps(day, sort_keys=True, indent=2) + "\n")
            data = run(Path(d) / "out", daily=daily)
        n = next(x for x in on(data, "live", "2026-11-01")["nodes"] if x["node_id"] == nid(3))
        self.assertTrue(n["left"])
        self.assertEqual(n["days_listed"], 3)
        self.assertFalse(next(x for x in on(data, "live", "2026-10-30")["nodes"] if x["node_id"] == nid(3))["left"])

    # (d) Three distinct visuals; two data sets

    def test_d_refusal_absence_and_answer_are_three_distinct_strokes_and_markers(self):
        style = {o: re.search(rf"svg \.o-{o} \{{([^}}]*)\}}", self.page).group(1) for o in watch.OUTCOMES}
        dashes = {o: re.search(r"stroke-dasharray: ([\d ]+)", s).group(1) if "dasharray" in s else "solid"
                  for o, s in style.items()}
        self.assertEqual(len(set(dashes.values())), 3, dashes)
        markers = {o: re.search(rf'<marker id="m-{o}"[^>]*>(.*?)</marker>', self.page).group(1) for o in watch.OUTCOMES}
        self.assertEqual(len({re.sub(r'class="[^"]*"', "", m) for m in markers.values()}), 3, markers)
        self.assertIn('"marker-end": "url(#m-" + x.outcome + ")"', self.js)
        self.assertIn('"edge o-" + x.outcome', self.js)
        self.assertEqual(watch.outcome("refusal"), "refused")
        self.assertEqual(watch.outcome("absence"), "absent")
        for s in ("verified", "no_checkpoint_yet", "no_announced_key", "not_evidence"):
            self.assertEqual(watch.outcome(s), "answered")
        seen = {e["outcome"] for v in self.data["views"].values() for d in v["days"] for e in d["edges"]}
        self.assertEqual(seen, set(watch.OUTCOMES))

    def test_d_rehearsal_and_live_are_separate_data_sets_never_summed(self):
        live, reh = self.data["views"]["live"], self.data["views"]["rehearsal"]
        self.assertEqual((live["test"], reh["test"]), (False, True))
        self.assertTrue(all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["date"]) for d in live["days"]))
        self.assertTrue(all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{4}Z", d["date"]) for d in reh["days"]))
        self.assertTrue(all(d["file"] is None or d["file"].startswith("stats/data/daily/") for d in live["days"]))
        self.assertTrue(all(d["file"] is None or d["file"].startswith("stats/data/rehearsal/") for d in reh["days"]))
        self.assertEqual(sorted(self.data["views"]), ["live", "rehearsal"])
        # One view drawn at a time: the script draws data.views[state.view] only.
        self.assertIn("return data.views[state.view];", self.js)
        self.assertNotRegex(self.js, r"views\.live[^;]*views\.rehearsal|views\.rehearsal[^;]*views\.live")
        self.assertEqual(self.page.count('<details class="fallback"'), 2)

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
        # The run started in the 06:00 slot: the next slot has no finding of its own.
        self.assertFalse(on(self.data, "rehearsal", "2026-11-05T0610Z")["measured"]["settle"]["measured"])

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

    def test_on_the_committed_findings_05_06_07_are_not_measured_yet(self):
        with tempfile.TemporaryDirectory() as d:
            data = run(Path(d), daily=ROOT / "stats" / "data" / "daily", rehearsal=ROOT / "stats" / "data" / "rehearsal",
                       findings=ROOT / "findings")
        for v in data["views"].values():
            for day in v["days"]:
                for kind in watch.STEPS:
                    self.assertFalse(day["measured"][kind]["measured"])

    # (e) Nothing from elsewhere

    def test_e_no_external_url_in_the_page_or_its_files(self):
        for p in (self.out / "watch").iterdir():
            text = p.read_text(encoding="utf-8")
            urls = re.findall(r"https?://[^\s\"'<>)]+", text)
            self.assertEqual(set(urls) - {SVG_NS}, set(), p.name)
            self.assertNotIn("@import", text)
            self.assertNotIn("<link", text)
            self.assertNotRegex(text, r"\bfetch\(|XMLHttpRequest|WebSocket|EventSource|import\(")
        scripts = re.findall(r"<script([^>]*)>", self.page)
        self.assertEqual(scripts, [' type="application/json" id="watch-data"', ' src="watch.js"'])
        self.assertNotIn("url(", re.sub(r"url\(#[a-z-]+\)", "", self.page))
        self.assertNotRegex(self.js, r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(")

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
        labels = re.findall(r'row\(dlr?, "([^"]+)"', show)
        self.assertEqual(sorted(set(labels)), ["asks", "build", "key id", "operator", "plugin", "records", "runs on", "this "])

    # Neutral, the network's, no branding

    def test_branding_and_neutrality_as_on_the_stats_page(self):
        self.assertIn("This page is the evidence network's.", self.page)
        self.assertEqual(test_site.branding(self.out), [])
        for p in (self.out / "watch").iterdir():
            low = p.read_text(encoding="utf-8").lower()
            for b in test_site.BRANDS:
                self.assertIsNone(re.search(rf"\b{re.escape(b)}\b", low), (p.name, b))
            # The public leak-lint rule: [ + three or more hyphenated lowercase segments + ].
            self.assertIsNone(re.search(r"\[[a-z0-9]+(?:-[a-z0-9]+){2,}\]", low), p.name)
        prose = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", self.page, flags=re.S))
        self.assertNotIn("%", prose)  # (in the script, % is the remainder operator)
        self.assertIsNone(re.search(r"\btop\b", prose, re.I))  # (in the script, style.top is a position)
        for text in (prose.lower(), self.js.lower()):
            for word in ("league", "leaderboard", "percent", "reputation", "pricing", "premium", "customer",
                         "underwrit", "insur", "kyb", "registry"):
                self.assertFalse(word in text, word)
            for word in ("score", "rank", "total"):
                for m in re.finditer(word, text):
                    self.assertIn("no score, ranking", text[max(m.start() - 30, 0):m.end() + 30], word)
        # The page's own words claim no check: "verified" only as a state name, in code.
        self.assertIsNone(re.search(r"\b(checked|witnessed)\b", prose, re.I))
        body = re.sub(r"<script.*?</script>|<style.*?</style>", "", self.page, flags=re.S)
        for m in re.finditer(r"verified", body):
            self.assertRegex(body[max(m.start() - 6, 0):m.start()], r"<code>$")

    def test_nodes_by_node_id_and_no_score_per_node(self):
        self.assertIn("var list = Object.keys(ids).sort();", self.js)
        for n in (n for v in self.data["views"].values() for d in v["days"] for n in d["nodes"]):
            self.assertLessEqual(set(n), {"node_id", "state", "questions", "asks", "records_at_checkpoint",
                                          "days_listed", "first_day", "left_on", "left", "node_line"})
        self.assertEqual(set(self.data["nodes"][nid(1)]), {"operator", "host_build", "plugin_version", "listed"})
        self.assertEqual(self.data["nodes"][nid(3)], {"listed": False})  # left: not in nodes/

    def test_no_answer_bytes_in_the_export(self):
        blob = json.dumps(self.data)
        self.assertNotIn("answer_sha256", blob)
        for day in self.days.values():
            for n in day.nodes:
                for a in n.answers.values():
                    if a.answer_sha256:
                        self.assertNotIn(a.answer_sha256, blob)

    # (g) Keyboard and fallback

    def test_g_keyboard_and_text_fallback(self):
        for a in ('tabindex: "0"', 'addEventListener("focus"', 'addEventListener("blur"', '"Escape"'):
            self.assertIn(a, self.js)
        self.assertIn('role="tooltip"', self.page)
        self.assertEqual(len(re.findall(r'<button type="button"', self.page)), 4)
        self.assertIn('<input type="range" id="day"', self.page)
        self.assertIn("<noscript>", self.page)
        self.assertIn('aria-live="polite"', self.page)
        self.assertIn("prefers-reduced-motion", self.js)

    def test_the_script_parses(self):
        node = shutil.which("node") or os.path.expanduser("~/.local/opt/node-v22.23.3/bin/node")
        if not os.access(node, os.X_OK):
            self.skipTest("SKIPPED LOUDLY: no node binary to syntax-check site/watch.js")
        r = subprocess.run([node, "--check", str(ROOT / "site" / "watch.js")], capture_output=True, text=True)
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
    """The page's tokens, in both schemes, with test_stats_contrast's reading of the CSS."""

    def setUp(self):
        page = (ROOT / "site" / "watch.html").read_text(encoding="utf-8")
        self.css = page[page.index("<style>") + 7:page.index("</style>")]
        self.schemes = contrast.schemes(self.css)

    def test_one_token_set_redefined_for_dark(self):
        light, dark = self.schemes["light"], self.schemes["dark"]
        self.assertEqual(sorted(light), sorted(dark))
        self.assertEqual(len(re.findall(r"#[0-9a-fA-F]{3,6}\b", self.css)), len(light) + len(dark))

    def test_every_text_colour_reaches_4_5_to_1_in_both_schemes(self):
        found = contrast.pairs(self.css)
        self.assertGreater(len(found), 8)
        for scheme, t in self.schemes.items():
            for sel, fg, bg in found:
                if "hublabel" in sel:
                    bg = "--hub"  # the reporter's label sits on its box
                if sel in ("#card dt", "#card dd") or sel.startswith("#card"):
                    bg = "--card"
                r = contrast.ratio(t[fg], t[bg])
                self.assertGreaterEqual(r, contrast.MIN_RATIO, f"{scheme}: {sel}: {fg} on {bg} is {r:.2f}:1")
            for fg in ("--muted", "--fg"):
                self.assertGreaterEqual(contrast.ratio(t[fg], t["--card"]), contrast.MIN_RATIO, (scheme, fg))

    def test_edges_and_node_rings_stand_out_from_the_page_at_3_to_1(self):
        for scheme, t in self.schemes.items():
            for tok in ("--e-answered", "--e-refused", "--e-absent", "--ring", "--hub", "--muted"):
                self.assertGreaterEqual(contrast.ratio(t[tok], t["--bg"]), 3, (scheme, tok))

    def test_every_font_size_is_12px_or_larger_and_the_graph_is_never_scaled(self):
        for sel, d in contrast.rules(self.css):
            if "font-size" in d:
                v = contrast.px(d["font-size"])
                self.assertIsNotNone(v, sel)
                self.assertGreaterEqual(v, contrast.MIN_PX, sel)
        page = (ROOT / "site" / "watch.html").read_text(encoding="utf-8")
        tag = re.search(r'<svg id="graph"[^>]*>', page).group(0)
        w = re.search(r'viewBox="0 0 (\d+) ', tag).group(1)
        self.assertIn(f'width="{w}"', tag)  # drawn 1:1; on a phone its box scrolls
        self.assertIn(f"svg#graph {{ display: block; width: {w}px;", self.css)


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
