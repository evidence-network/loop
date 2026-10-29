"""The reporter rehearsal: a 10-minute test of the daily run, kept apart from the daily set, and its wipe.

Covers the slot layout, the bounded retries, the reporter's own node asked through a
second console, the push of the rehearsal set, the scrubbed set, the stats site's
TEST page with its MISSED slots, the systemd units and the wipe of stats/data/rehearsal.
A fake clock stands in for time, as in test_offline_retry.py.
Run: python3 -B -m unittest discover -s tests -v
"""
import contextlib
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "site"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402
import check_reports  # noqa: E402
import daily_report  # noqa: E402
import report_layout as layout  # noqa: E402
import scrub_reports  # noqa: E402
import stats_model  # noqa: E402
import test_offline_retry as retry  # noqa: E402
import test_reporter as tr  # noqa: E402

UTC = dt.timezone.utc
SELF = retry.REPORTER["node_id"]  # the reporter's own node, as reporter.json names it
SELF_KEY = retry.REPORTER["key_id"]


def at(hh, mm, ss=0):
    return dt.datetime(2026, 10, 28, hh, mm, ss, tzinfo=UTC)


class SlotTest(unittest.TestCase):
    def test_a_slot_is_ten_minutes_utc_named_by_its_start(self):
        r = layout.Rehearsal
        self.assertEqual(r.name(r.of(at(14, 37, 59))), "2026-10-28T1430Z")
        self.assertEqual(r.name(r.of(at(0, 0))), "2026-10-28T0000Z")
        self.assertEqual(r.parse("2026-10-28T2350Z") + r.step, dt.datetime(2026, 10, 29, 0, 0, tzinfo=UTC))
        for bad in ("2026-10-28T1435Z", "2026-10-28T2460Z", "2026-10-28", "2026-10-28T14:30Z", "2026-10-28T1430"):
            self.assertIsNone(r.pattern.match(bad), bad)
            with self.assertRaises(ValueError):
                r.parse(bad)
        self.assertEqual(layout.SETS, {"daily": layout.Daily, "rehearsal": layout.Rehearsal, "ondemand": layout.OnDemand})


class RehearsalRunTest(unittest.TestCase):
    """daily_report.py run --set rehearsal under a fake clock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.nodes = self.dir / "nodes"
        self.nodes.mkdir()
        self.out = self.dir / "reports" / "rehearsal"
        self.reporter = self.dir / "reporter.json"
        # The daily schedule asks again over six hours; the rehearsal must not.
        self.reporter.write_text(json.dumps({**retry.REPORTER, "retry_after_s": retry.SCHEDULE}))
        self.clock = retry.Clock(at(14, 30, 2))
        self.consoles = []

    def tearDown(self):
        for c in self.consoles:
            c.close()
        self.tmp.cleanup()

    def console(self, behaviour, announced):
        c = retry.FakeConsole(behaviour, announced)
        self.consoles.append(c)
        return c

    def list_nodes(self, *nids, keys=None):
        for nid in nids:
            kid = (keys or {}).get(nid, retry.key_id(int(nid[0], 16)))
            (self.nodes / f"{nid}.json").write_text(json.dumps(retry.entry(nid, kid)))

    def main(self, *argv, console=None):
        out, err = io.StringIO(), io.StringIO()
        argv = ["run", "--console", (console or self.consoles[0]).url, "--nodes", str(self.nodes),
                "--out", str(self.out), "--reporter", str(self.reporter), "--timeout", "0.2", *argv]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = daily_report.main(["daily_report.py", *argv], now=self.clock.now, sleep=self.clock.sleep)
        return code, out.getvalue() + err.getvalue()

    def run_slot(self, *extra, code=0):
        got, out = self.main("--set", "rehearsal", *extra)
        self.assertEqual(got, code, out)
        return out

    def one_node(self, what="verified"):
        self.list_nodes(retry.node_id(1))
        self.console({retry.node_id(1): retry.both(what)}, {retry.node_id(1): retry.key_id(1)})

    # The slot layout

    def test_a_run_writes_its_slot_with_the_daily_layout(self):
        self.one_node()
        self.run_slot()
        slot = self.out / "2026-10-28T1430Z"
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["2026-10-28T1430Z"])
        run = json.loads((slot / "RUN.json").read_text())
        self.assertEqual(run["date"], "2026-10-28T1430Z")
        self.assertEqual(run["asked"], [retry.node_id(1)])
        self.assertNotIn("asked_through_second_console", run)
        self.assertTrue((slot / "README.md").read_text().startswith("# Rehearsal report 2026-10-28T1430Z (TEST)\n"))
        self.assertTrue((slot / retry.node_id(1) / "checkpoints.verification").read_text().startswith("verified "))
        # check_reports holds a slot to exactly the daily rules.
        errs, slots = check_reports.check(self.out, period=layout.Rehearsal)
        self.assertEqual((errs, slots), ([], ["2026-10-28T1430Z"]))
        errs, _ = check_reports.check(self.out)
        self.assertEqual(errs, ["2026-10-28T1430Z: not a <YYYY-MM-DD> directory"])  # never read as a day

    def test_the_default_set_is_daily_and_unchanged(self):
        self.one_node()
        code, out = self.main()
        self.assertEqual(code, 0, out)
        self.assertEqual([p.name for p in self.out.iterdir()], ["2026-10-28"])
        self.assertTrue((self.out / "2026-10-28" / "README.md").read_text().startswith("# Daily report 2026-10-28\n"))

    def test_the_set_comes_from_the_environment_too(self):
        self.one_node()
        with unittest.mock.patch.dict(os.environ, {"LOOP_REPORT_SET": "rehearsal"}):
            code, out = self.main()
        self.assertEqual(code, 0, out)
        self.assertEqual([p.name for p in self.out.iterdir()], ["2026-10-28T1430Z"])

    def test_six_consecutive_slots_then_a_stopped_timer_is_missed(self):
        self.one_node()
        for n in range(6):
            self.clock.t = at(14, 30, 2) + dt.timedelta(minutes=10 * n)
            self.run_slot()
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(names, [f"2026-10-28T{h}Z" for h in ("1430", "1440", "1450", "1500", "1510", "1520")])
        # The timer is stopped for two slots; the next run marks them MISSED, as a missed day.
        self.clock.t = at(15, 50, 1)
        out = self.run_slot()
        self.assertIn("2026-10-28T1530Z: MISSED", out)
        for slot in ("1530", "1540"):
            missed = (self.out / f"2026-10-28T{slot}Z" / "MISSED").read_text()
            self.assertEqual(missed, f"no report for 2026-10-28T{slot}Z: the reporter did not run; noticed by the run "
                                     "of 2026-10-28T1550Z\n")
        self.assertEqual(check_reports.check(self.out, layout.Rehearsal.parse("2026-10-28T1550Z"),
                                             period=layout.Rehearsal)[0], [])
        # A slot with neither a report nor a mark is refused, exactly as a day is.
        shutil.rmtree(self.out / "2026-10-28T1540Z")
        self.assertEqual(check_reports.check(self.out, layout.Rehearsal.parse("2026-10-28T1550Z"),
                                             period=layout.Rehearsal)[0],
                         ["2026-10-28T1540Z: no report and no MISSED mark"])

    def test_a_slot_is_run_once(self):
        self.one_node()
        self.run_slot()
        self.clock.t = at(14, 39, 0)
        out = self.run_slot(code=1)
        self.assertIn("already holds a run", out)

    def test_a_slot_given_by_hand_must_be_a_slot(self):
        self.one_node()
        self.run_slot("--date", "2026-10-28T1430Z")
        for bad in ("2026-10-28T1435Z", "2026-10-28"):
            self.assertIn("not a 10-minute slot", self.run_slot("--date", bad, code=1))

    # Retries bounded to the slot

    def test_retries_are_bounded_to_the_slot_not_six_hours(self):
        self.one_node("hang")
        self.run_slot()
        node = self.out / "2026-10-28T1430Z" / retry.node_id(1)
        times = [layout.read_attempt(a)[0] for a in layout.attempts(node)]
        self.assertEqual(times, ["2026-10-28T14:30:02Z", "2026-10-28T14:32:02Z", "2026-10-28T14:34:02Z"])
        self.assertEqual(self.clock.slept, [120, 120])
        self.assertLess(self.clock.t, at(14, 40))
        self.assertTrue((node / "checkpoints.verification").read_text().startswith("absence "))

    def test_no_retry_starts_past_the_slots_budget(self):
        self.one_node("hang")
        self.clock.t = at(14, 36, 0)  # started late: 60 s left before the budget's end
        out = self.run_slot()
        self.assertIn("no time left in the slot", out)
        self.assertEqual(self.clock.slept, [])
        self.assertEqual(len(layout.attempts(self.out / "2026-10-28T1430Z" / retry.node_id(1))), 1)

    def test_no_request_waits_past_the_budget(self):
        self.list_nodes(retry.node_id(1))
        self.console({retry.node_id(1): retry.both("verified")}, {retry.node_id(1): retry.key_id(1)})
        waits = []
        real = daily_report.http
        with unittest.mock.patch.object(daily_report, "http", lambda m, u, b, t: waits.append(t) or real(m, u, b, t)):
            self.clock.t = at(14, 30, 2)
            got, out = self.main("--set", "rehearsal", "--timeout", "600")
        self.assertEqual(got, 0, out)
        self.assertEqual(max(waits), daily_report.REHEARSAL_TIMEOUT_S)
        # Two seconds before the budget ends, no request waits longer than that.
        shutil.rmtree(self.out)
        self.clock.t = at(14, 36, 58)
        waits.clear()
        with unittest.mock.patch.object(daily_report, "http", lambda m, u, b, t: waits.append(t) or real(m, u, b, t)):
            got, out = self.main("--set", "rehearsal", "--timeout", "600")
        self.assertEqual(got, 0, out)
        self.assertEqual(waits[0], daily_report.REHEARSAL_TIMEOUT_S)  # the ledger check, before any node
        self.assertEqual(set(waits[1:]), {2.0})

    # The reporter's own node, through a second console

    def self_setup(self, primary_self="unreachable"):
        """Node 1 and the reporter's own node listed. Its own console cannot ask it; a second console can."""
        self.list_nodes(retry.node_id(1), SELF, keys={SELF: SELF_KEY})
        self.console({retry.node_id(1): retry.both("verified"), SELF: retry.both(primary_self)},
                     {retry.node_id(1): retry.key_id(1), SELF: SELF_KEY})
        second = self.console({SELF: retry.both("verified")}, {SELF: SELF_KEY})
        self.population = self.dir / "population.json"
        self.population.write_text(json.dumps({"population": "live", "nodes": [
            {"name": "label-1", "node_id": retry.node_id(1)}, {"name": "label-self", "node_id": SELF}]}))
        return second

    def test_the_reporters_own_node_is_asked_through_the_second_console_and_verified(self):
        second = self.self_setup()
        for argv, out, slot in ((("--set", "rehearsal"), self.out, "2026-10-28T1430Z"), ((), None, "2026-10-28")):
            if out is None:  # the daily set alike
                self.out = self.dir / "reports" / "daily"
            got, text = self.main(*argv, "--second-console", second.url, "--self-node", "label-self",
                                  "--population", str(self.population))
            self.assertEqual(got, 0, text)
            node = self.out / slot / SELF
            for stem in ("checkpoints", "history_card"):
                self.assertTrue((node / f"{stem}.verification").read_text().startswith("verified "), slot)
                self.assertIn("listed_key=match", (node / f"{stem}.verification").read_text())
            self.assertEqual((node / "NODE").read_text(), "discloses: checkpoints, history_card/1\n")
            run = json.loads((self.out / slot / "RUN.json").read_text())
            self.assertEqual(run["asked"], sorted([retry.node_id(1), SELF]))
            self.assertEqual(run["asked_through_second_console"], [SELF])
            self.assertIn("asked through a second node's console", (self.out / slot / "README.md").read_text())
            self.assertEqual(check_reports.check(self.out, period=layout.SETS["rehearsal" if argv else "daily"])[0], [])
        # Its own console was never asked about it; the second console was asked about it alone.
        self.assertEqual([p for p, _ in self.consoles[0].asked if p == SELF], [])
        self.assertEqual({p for p, _ in second.asked}, {SELF})

    def test_the_second_console_comes_from_the_environment(self):
        second = self.self_setup()
        with unittest.mock.patch.dict(os.environ, {"LOOP_SECOND_CONSOLE": second.url}):
            self.run_slot()
        self.assertTrue((self.out / "2026-10-28T1430Z" / SELF / "checkpoints.verification").read_text()
                        .startswith("verified "))

    def test_without_a_second_console_the_reporters_node_is_not_asked(self):
        self.self_setup()
        self.run_slot()
        self.assertEqual(json.loads((self.out / "2026-10-28T1430Z" / "RUN.json").read_text())["asked"],
                         [retry.node_id(1)])
        self.assertEqual([p for p, _ in self.consoles[0].asked if p == SELF], [])

    def test_a_wrong_self_node_label_or_console_is_refused(self):
        second = self.self_setup()
        for extra, why in ((("--self-node", "label-x"), "is not a node in"),
                           (("--self-node", "label-1"), "not the reporter's node"),
                           (("--second-console", self.consoles[0].url + "/"), "the reporter's own console")):
            argv = ["--set", "rehearsal", "--second-console", second.url, "--population", str(self.population), *extra]
            got, out = self.main(*argv)
            self.assertEqual(got, 1, out)
            self.assertIn(why, out)
        self.assertFalse(self.out.exists())  # refused before anything is written

    # The scrubbed set

    def test_the_rehearsal_scrubs_to_one_file_per_slot(self):
        second = self.self_setup()
        for t in (at(14, 30, 2), at(14, 40, 2), at(15, 0, 2)):
            self.clock.t = t
            self.run_slot("--second-console", second.url)
        out = self.dir / "stats" / "data" / "rehearsal"
        written = scrub_reports.scrub(self.out, out, self.dir / "left", layout.Rehearsal)
        self.assertEqual(written, [f"2026-10-28T{s}Z.json" for s in ("1430", "1440", "1450", "1500")])
        self.assertEqual(scrub_reports.check(out, layout.Rehearsal), [])
        self.assertTrue(scrub_reports.check(out))  # never read as daily files
        settled = json.loads((out / "2026-10-28T1430Z.json").read_text())
        self.assertEqual(settled["date"], "2026-10-28T1430Z")
        self.assertEqual({n["node_id"]: n["questions"]["checkpoints"]["state"] for n in settled["nodes"]},
                         {retry.node_id(1): "verified", SELF: "verified"})
        missed = json.loads((out / "2026-10-28T1450Z.json").read_text())
        self.assertEqual((missed["run"], missed["missed_reason"]),
                         ("missed", "the reporter did not run; noticed by the run of 2026-10-28T1500Z"))
        days = stats_model.load(out, layout.Rehearsal)
        self.assertEqual([d.date for d in days][0], "2026-10-28T1500Z")


class RehearsalSiteTest(unittest.TestCase):
    """The stats site: the rehearsal on its own page under TEST; a slot without a settled run reads MISSED."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.findings = d / "findings"
        tr_site = __import__("test_site")
        tr_site.make_findings(cls.findings)
        cls.data = tr_site.DATA
        cls.slots = d / "rehearsal"
        cls.slots.mkdir()
        # Four slots: settled, missed (marked), no file at all, settled.
        node = {"node_id": "1" * 64, "node_line": "discloses: checkpoints, history_card/1", "absent_days_running": 0,
                "left_on": None, "questions": {s: {"state": "verified", "attempts": [{"at": "2026-10-28T14:30:03Z",
                                                                                     "state": "verified"}],
                                                   "log_size_at_checkpoint": None, "records_at_checkpoint": None,
                                                   "answer_sha256": "a" * 64} for s in ("checkpoints", "history_card")}}
        settled = {"date": None, "run": "settled", "missed_reason": None, "versions_lock_sha256": "b" * 64,
                   "reporter_key_id": "c" * 64, "listed": 1, "nodes": [node]}
        missed = {"date": None, "run": "missed", "missed_reason": "the reporter did not run; noticed by the run of "
                  "2026-10-28T1500Z", "versions_lock_sha256": None, "reporter_key_id": None, "listed": 0, "nodes": []}
        for slot, day in (("1430", settled), ("1440", missed), ("1500", settled)):
            name = f"2026-10-28T{slot}Z"
            (cls.slots / f"{name}.json").write_text(scrub_reports.dump({**day, "date": name}))
        cls.with_ = d / "with"
        cls.without = d / "without"
        for out, rehearsal in ((cls.with_, cls.slots), (cls.without, None)):
            err = io.StringIO()
            argv = ["build.py", str(cls.data), "--findings", str(cls.findings), "--lock", str(ROOT / "versions.lock"),
                    "--out", str(out)] + (["--rehearsal", str(rehearsal)] if rehearsal else [])
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                assert build.main(argv) == 0, err.getvalue()
        cls.page = (cls.with_ / "stats" / "rehearsal" / "index.html").read_text()
        cls.daily = (cls.with_ / "stats" / "index.html").read_text()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def row(self, slot):
        return re.search(rf'<tr data-slot="2026-10-28T{slot}Z">(.*?)</tr>\n', self.page, re.S).group(1)

    def test_the_rehearsal_page_is_labelled_test(self):
        self.assertIn('<h1><span class="test">TEST</span> Reporter rehearsal</h1>', self.page)
        self.assertIn('<caption><span class="test">TEST</span>', self.page)
        self.assertIn("<title>Loop stats: TEST reporter rehearsal</title>", self.page)
        self.assertIn("<strong>TEST data.</strong>", self.page)

    def test_a_slot_with_no_settled_run_reads_missed(self):
        self.assertEqual([m for m in re.findall(r'<tr data-slot="([^"]+)">', self.page)],
                         [f"2026-10-28T{s}Z" for s in ("1500", "1450", "1440", "1430")])  # newest first
        self.assertIn('<td class="missed">MISSED</td>', self.row("1440"))  # marked by the reporter
        self.assertIn("noticed by the run of", self.row("1440"))
        self.assertIn('<td class="missed">MISSED</td>', self.row("1450"))  # no file at all
        self.assertIn("no report and no MISSED mark", self.row("1450"))
        for slot in ("1430", "1500"):
            self.assertNotIn("MISSED", self.row(slot))
            self.assertIn('<span class="count">1 of 1 listed node</span>', self.row(slot))
            self.assertIn('<code class="outcome o-verified">verified</code>', self.row(slot))
        self.assertIn('<span class="count">2 of 4 slots</span> with a settled run', self.page)

    def test_the_daily_page_links_it_under_test_and_shows_no_slot(self):
        self.assertIn('<strong>TEST</strong>: the reporter rehearsal', self.daily)
        self.assertIn('<a href="rehearsal/">', self.daily)
        self.assertNotIn("data-slot", self.daily)
        self.assertNotIn("2026-10-28T14", self.daily)
        self.assertNotIn("data/rehearsal", self.daily)
        self.assertNotIn("data-slot", self.daily)
        self.assertEqual(sorted(p.name for p in (self.with_ / "stats" / "data" / "rehearsal").iterdir()),
                         sorted(p.name for p in self.slots.iterdir()))
        self.assertEqual(sorted(p.name for p in (self.with_ / "stats" / "data" / "daily").iterdir()),
                         sorted(p.name for p in self.data.glob("*.json")))

    def test_with_no_rehearsal_the_site_is_as_before(self):
        self.assertFalse((self.without / "stats" / "rehearsal").exists())
        self.assertFalse((self.without / "stats" / "data" / "rehearsal").exists())
        daily = (self.without / "stats" / "index.html").read_text()
        link = build.rehearsal_link(stats_model.load(self.slots, layout.Rehearsal))
        self.assertTrue(link)
        self.assertEqual(self.daily.count(link), 1)
        self.assertEqual(self.daily.replace(link, ""), daily)  # the one TEST line is all that differs
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir(exist_ok=True)
        self.assertEqual(build.rehearsal_link(stats_model.load(empty, layout.Rehearsal)), "")

    def test_the_rehearsal_page_is_static_and_off_reports(self):
        self.assertNotIn("<script", self.page.lower())
        self.assertIsNone(re.search(r"\son[a-z]+=", self.page))
        self.assertIsNone(re.search(r'(?:href|src)="(?:https?:)?//', self.page))
        for href in re.findall(r'href="([^"]*)"', self.page):
            self.assertIsNone(re.search(r"(^|/)reports(/|$)", href), href)
        for name in (".request.json", ".response.json", ".verification", "peer-key.json", "RUN.json"):
            self.assertNotIn(name, self.page)

    def test_a_slot_file_the_site_cannot_read_fails_the_build(self):
        bad = Path(self.tmp.name) / "bad"
        shutil.copytree(self.slots, bad)
        day = json.loads((bad / "2026-10-28T1430Z.json").read_text())
        (bad / "2026-10-28T1435Z.json").write_text(scrub_reports.dump({**day, "date": "2026-10-28T1435Z"}))
        (bad / "2026-10-28.json").write_text(scrub_reports.dump({**day, "date": "2026-10-28"}))
        with self.assertRaises(stats_model.StatsError) as caught:
            build.build(self.data, self.findings, ROOT / "versions.lock", Path(self.tmp.name) / "bad-site", bad)
        self.assertEqual(caught.exception.problems, ["2026-10-28T1435Z.json: 2026-10-28T1435Z is not a slot",
                                                     "2026-10-28.json: not a <YYYY-MM-DD>T<HHMM>Z.json slot file"])
        self.assertFalse((Path(self.tmp.name) / "bad-site").exists())


class RunRehearsalTest(unittest.TestCase):
    """run_daily.sh --set rehearsal in a throwaway loop clone, pushing to a scratch loop-data."""

    setUp = tr.RunDailyTest.setUp
    tearDown = tr.RunDailyTest.tearDown
    seed = tr.RunDailyTest.seed
    git = tr.RunDailyTest.git
    head = tr.RunDailyTest.head
    origin_head = tr.RunDailyTest.origin_head

    def run_daily(self, *args):
        return subprocess.run(["bash", str(self.clone / "scripts" / "run_daily.sh"), *args], cwd=self.clone,
                              env=self.env, capture_output=True, text=True)

    def test_a_rehearsal_slot_is_pushed_touching_only_reports_rehearsal(self):
        before = self.origin_head()
        done = self.run_daily("--set", "rehearsal")
        self.assertEqual(done.returncode, 0, done.stderr)
        changed = self.git(self.data_origin, "diff", "--name-only", before, "main").split()
        self.assertTrue(changed)
        slots = {name.split("/")[2] for name in changed}
        self.assertEqual(len(slots), 1)
        self.assertRegex(slots.pop(), layout.Rehearsal.pattern)
        self.assertTrue(all(name.startswith("reports/rehearsal/") for name in changed), changed)
        subject = self.git(self.data_origin, "log", "-1", "--format=%s", "main")
        self.assertRegex(subject, r"^report: rehearsal \d{4}-\d{2}-\d{2}T\d{2}[0-5]0Z \(TEST\)$")
        self.assertEqual(self.git(self.data, "ls-files", "reports/daily").split(), ["reports/daily/.gitkeep"])

    def test_the_environment_selects_the_rehearsal_too(self):
        self.env["LOOP_REPORT_SET"] = "rehearsal"
        done = self.run_daily()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("(TEST)", self.git(self.data_origin, "log", "-1", "--format=%s", "main"))

    def test_a_rehearsal_never_pushes_a_commit_touching_reports_daily(self):
        before = self.origin_head()
        (self.data / "reports" / "daily" / "2026-10-28").mkdir()
        (self.data / "reports" / "daily" / "2026-10-28" / "MISSED").write_text("planted\n")
        self.git(self.data, "add", "reports/daily")
        self.git(self.data, "commit", "--quiet", "-m", "a daily change")
        done = self.run_daily("--set", "rehearsal")
        self.assertEqual(done.returncode, 1)
        self.assertIn("outside reports/rehearsal", done.stderr)
        self.assertIn("reports/daily/2026-10-28/MISSED", done.stderr)
        self.assertEqual(self.origin_head(), before)

    def test_an_unknown_set_is_refused(self):
        before = self.origin_head()
        for args, env in ((("--set", "weekly"), None), ((), "weekly"), (("--weekly",), None)):
            if env:
                self.env["LOOP_REPORT_SET"] = env
            done = self.run_daily(*args)
            self.assertEqual(done.returncode, 1, (args, env))
        self.assertEqual(self.console.asked, [])
        self.assertEqual(self.origin_head(), before)


class UnitsTest(unittest.TestCase):
    """The rehearsal timer runs beside the daily one; the daily units are not changed."""

    SYSTEMD = ROOT / "reporter" / "systemd"

    def ini(self, name):
        return [line.strip() for line in (self.SYSTEMD / name).read_text().splitlines()
                if line.strip() and not line.startswith("#")]

    def test_the_rehearsal_timer_runs_every_ten_minutes_the_rehearsal_set(self):
        self.assertIn("OnCalendar=*:0/10", self.ini("loop-reporter-rehearsal.timer"))
        self.assertNotIn("Persistent=true", self.ini("loop-reporter-rehearsal.timer"))
        service = self.ini("loop-reporter-rehearsal.service")
        self.assertIn("Environment=LOOP_REPORT_SET=rehearsal", service)
        self.assertIn("ExecStart=%h/loop/scripts/run_daily.sh", service)
        self.assertIn("Environment=LOOP_DATA_DIR=%h/loop-data", service)
        self.assertIn("EnvironmentFile=-%h/.config/loop-reporter/env", service)
        self.assertTrue(any(l.startswith("TimeoutStartSec=") for l in service))

    def test_the_daily_units_do_not_name_the_rehearsal(self):
        for name in ("loop-reporter.service", "loop-reporter.timer"):
            text = (self.SYSTEMD / name).read_text()
            self.assertNotIn("rehearsal", text.lower(), name)
            self.assertNotIn("LOOP_REPORT_SET", text, name)
        self.assertIn("OnCalendar=*-*-* 00:30:00 UTC", self.ini("loop-reporter.timer"))
        # The daily unit reads the same optional settings through a drop-in, not an edit.
        self.assertEqual(self.ini("reporter-env.conf"),
                         ["[Service]", "EnvironmentFile=-%h/.config/loop-reporter/env"])

    def test_the_readme_has_the_install_switch_and_wipe_commands(self):
        text = (ROOT / "reporter" / "README.md").read_text()
        for command in ("cp ~/loop/reporter/systemd/loop-reporter-rehearsal.* ~/.config/systemd/user/",
                        "systemctl --user enable --now loop-reporter-rehearsal.timer",
                        "systemctl --user disable --now loop-reporter-rehearsal.timer",
                        "systemctl --user enable --now loop-reporter.timer",
                        "scripts/wipe_rehearsal.sh"):
            self.assertIn(command, text)


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class WipeTest(unittest.TestCase):
    """scripts/wipe_rehearsal.sh on a scratch repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "loop"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="wiper",
                        GIT_AUTHOR_EMAIL="wiper@example.invalid", GIT_COMMITTER_NAME="wiper",
                        GIT_COMMITTER_EMAIL="wiper@example.invalid")
        self.git(Path(self.tmp.name), "init", "-q", "-b", "main", str(self.repo))
        for rel in ("stats/data/daily/2026-10-28.json", "stats/data/rehearsal/2026-10-28T1430Z.json",
                    "stats/data/rehearsal/2026-10-28T1440Z.json", "README.md"):
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(f"{rel}\n")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", "seed")
        self.before = self.git(self.repo, "rev-parse", "HEAD").strip()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True, text=True).stdout

    def wipe(self):
        return subprocess.run(["bash", str(ROOT / "scripts" / "wipe_rehearsal.sh"), str(self.repo)], env=self.env,
                              capture_output=True, text=True)

    def test_one_commit_removes_the_rehearsal_and_nothing_else(self):
        done = self.wipe()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.git(self.repo, "rev-list", "--count", f"{self.before}..HEAD").strip(), "1")
        changed = self.git(self.repo, "diff", "--name-status", self.before, "HEAD").splitlines()
        self.assertEqual(changed, ["D\tstats/data/rehearsal/2026-10-28T1430Z.json",
                                   "D\tstats/data/rehearsal/2026-10-28T1440Z.json"])
        self.assertTrue((self.repo / "stats/data/daily/2026-10-28.json").is_file())
        self.assertFalse((self.repo / "stats/data/rehearsal").exists())
        self.assertIn("Signed-off-by: wiper", self.git(self.repo, "log", "-1", "--format=%B"))
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "")
        again = self.wipe()
        self.assertEqual(again.returncode, 0)
        self.assertIn("nothing to wipe", again.stdout)

    def test_a_staged_change_outside_the_rehearsal_is_refused(self):
        (self.repo / "stats/data/daily/2026-10-28.json").write_text("changed\n")
        self.git(self.repo, "add", "stats/data/daily")
        done = self.wipe()
        self.assertEqual(done.returncode, 1)
        self.assertIn("stats/data/daily/2026-10-28.json", done.stderr)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").strip(), self.before)
        # Left as it was found: the daily change still staged, the rehearsal still tracked and on disk.
        self.assertEqual(self.git(self.repo, "diff", "--cached", "--name-only").split(),
                         ["stats/data/daily/2026-10-28.json"])
        self.assertTrue((self.repo / "stats/data/rehearsal/2026-10-28T1430Z.json").is_file())

    def test_a_staged_deletion_of_the_daily_set_is_refused(self):
        self.git(self.repo, "rm", "-q", "-r", "stats/data/daily")
        done = self.wipe()
        self.assertEqual(done.returncode, 1)
        self.assertIn("stats/data/daily/2026-10-28.json", done.stderr)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").strip(), self.before)

    def test_a_rehearsal_path_that_is_not_a_directory_is_refused(self):
        self.git(self.repo, "rm", "-q", "-r", "stats/data/rehearsal")
        os.symlink("daily", self.repo / "stats/data/rehearsal")
        self.git(self.repo, "add", "stats/data/rehearsal")
        self.git(self.repo, "commit", "-q", "-m", "a symlink")
        head = self.git(self.repo, "rev-parse", "HEAD").strip()
        done = self.wipe()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").strip(), head)
        self.assertTrue((self.repo / "stats/data/daily/2026-10-28.json").is_file())


if __name__ == "__main__":
    unittest.main()
