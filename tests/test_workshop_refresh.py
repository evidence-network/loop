"""The workshop refresh: the on-demand set, and scripts/refresh_now.sh that runs, commits and pushes it.

Covers the on-demand set's layout, names and RUN.json, that nothing is ever
MISSED between two runs, check_reports and the scrub with --set ondemand, the
daily scrubbed set and stats page untouched by an on-demand run beside two
daily days (acceptance (j)), and refresh_now.sh under fake consoles, a fake
systemctl and local bare origins: one commit, inside its own run only, and its
refusals. A fake clock stands in for time, as in test_offline_retry.py.
Run: python3 -B -m unittest discover -s tests -v
"""
import contextlib
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
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
SELF = retry.REPORTER["node_id"]
SELF_KEY = retry.REPORTER["key_id"]
RUN = "2026-10-28T143005Z"


def at(hh, mm, ss=0, day=28):
    return dt.datetime(2026, 10, day, hh, mm, ss, tzinfo=UTC)


def tree(root):
    """Every file under root, relative path -> sha256."""
    root = Path(root)
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


class OnDemandLayoutTest(unittest.TestCase):
    def test_a_run_is_named_by_the_second_it_starts_utc(self):
        o = layout.OnDemand
        self.assertEqual(o.name(o.of(at(14, 30, 5).replace(microsecond=999))), RUN)
        self.assertEqual(o.parse(RUN), at(14, 30, 5))
        self.assertEqual((o.set, o.noun, o.step, o.continuous), ("ondemand", "run", None, False))
        for bad in ("2026-10-28T1430Z", "2026-10-28", "2026-10-28T246000Z", "2026-10-28T146000Z",
                    "2026-10-28T143060Z", "2026-10-28T14:30:05Z", "2026-10-28T143005"):
            self.assertIsNone(o.pattern.match(bad), bad)
            with self.assertRaises(ValueError):
                o.parse(bad)
        self.assertEqual(layout.SETS, {"daily": layout.Daily, "rehearsal": layout.Rehearsal, "ondemand": o})
        # The three sets' names never match each other's pattern.
        for name, period in (("2026-10-28", layout.Daily), ("2026-10-28T1430Z", layout.Rehearsal), (RUN, o)):
            self.assertEqual([p for p in layout.SETS.values() if p.pattern.match(name)], [period], name)

    def test_the_run_before_a_run_is_the_latest_earlier_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for name in ("2026-10-27T090000Z", "2026-10-28T101010Z", RUN):
                (out / name).mkdir()
            (out / "README.md").write_text("not a run\n")
            self.assertEqual(layout.previous(out, RUN, layout.OnDemand), "2026-10-28T101010Z")
            self.assertEqual(layout.previous(out, "2026-10-28T101010Z", layout.OnDemand), "2026-10-27T090000Z")
            self.assertIsNone(layout.previous(out, "2026-10-27T090000Z", layout.OnDemand))
            # The continuous sets step back one period, as before.
            self.assertEqual(layout.previous(out, "2026-10-28"), "2026-10-27")
            self.assertEqual(layout.previous(out, "2026-10-28T0000Z", layout.Rehearsal), "2026-10-27T2350Z")


class OnDemandRunTest(unittest.TestCase):
    """daily_report.py run --set ondemand under a fake clock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.nodes = self.dir / "nodes"
        self.nodes.mkdir()
        self.out = self.dir / "reports" / "ondemand"
        self.reporter = self.dir / "reporter.json"
        # The daily schedule asks again over six hours; an on-demand run must not.
        self.reporter.write_text(json.dumps({**retry.REPORTER, "retry_after_s": retry.SCHEDULE}))
        self.clock = retry.Clock(at(14, 30, 5))
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
        argv = ["run", "--set", "ondemand", "--console", console or self.consoles[0].url, "--nodes", str(self.nodes),
                "--out", str(self.out), "--reporter", str(self.reporter), "--timeout", "0.2", *argv]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = daily_report.main(["daily_report.py", *argv], now=self.clock.now, sleep=self.clock.sleep)
        return code, out.getvalue() + err.getvalue()

    def run_once(self, *extra, code=0, console=None):
        got, out = self.main(*extra, console=console)
        self.assertEqual(got, code, out)
        return out

    def one_node(self, what="verified"):
        self.list_nodes(retry.node_id(1))
        return self.console({retry.node_id(1): retry.both(what)}, {retry.node_id(1): retry.key_id(1)})

    def test_a_run_writes_its_own_directory_in_the_daily_layout(self):
        self.one_node()
        self.run_once()
        run_dir = self.out / RUN
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [RUN])
        record = json.loads((run_dir / "RUN.json").read_text())
        self.assertEqual(record["set"], "ondemand")
        self.assertEqual(record["date"], RUN)
        self.assertEqual(record["started"], "2026-10-28T14:30:05Z")
        self.assertEqual(record["asked"], [retry.node_id(1)])
        readme = (run_dir / "README.md").read_text()
        self.assertTrue(readme.startswith(f"# On-demand report {RUN}\n"), readme)
        self.assertIn("never counted as a day", readme)
        self.assertIn("+30s, +60s", readme)
        self.assertTrue((run_dir / retry.node_id(1) / "checkpoints.verification").read_text().startswith("verified "))
        self.assertEqual(check_reports.check(self.out, period=layout.OnDemand), ([], [RUN]))
        # Never read as a day or a slot.
        self.assertEqual(check_reports.check(self.out)[0], [f"{RUN}: not a <YYYY-MM-DD> directory"])
        self.assertEqual(check_reports.check(self.out, period=layout.Rehearsal)[0],
                         [f"{RUN}: not a <YYYY-MM-DD>T<HHMM>Z 10-minute slot directory"])

    def test_the_daily_and_rehearsal_run_json_are_unchanged(self):
        self.one_node()
        for argv, name in (((), "2026-10-28"), (("--set", "rehearsal"), "2026-10-28T1430Z")):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = daily_report.main(["daily_report.py", "run", *argv, "--console", self.consoles[0].url,
                                          "--nodes", str(self.nodes), "--out", str(self.dir / name),
                                          "--reporter", str(self.reporter), "--timeout", "0.2"],
                                         now=self.clock.now, sleep=self.clock.sleep)
            self.assertEqual(code, 0, out.getvalue() + err.getvalue())
            self.assertNotIn("set", json.loads((self.dir / name / name / "RUN.json").read_text()))

    def test_the_run_is_named_by_date_when_given(self):
        self.one_node()
        self.run_once("--date", "2026-10-28T143001Z")
        self.assertEqual([p.name for p in self.out.iterdir()], ["2026-10-28T143001Z"])
        self.run_once("--date", "2026-10-28T1430Z", code=1)  # a slot is no run name

    def test_a_run_name_already_there_is_refused(self):
        c = self.one_node()
        self.out.mkdir(parents=True)
        (self.out / RUN).mkdir()
        out = self.run_once(code=1)
        self.assertIn("already exists", out)
        self.assertEqual(c.asked, [])

    def test_retries_are_bounded_to_about_two_minutes(self):
        c = self.one_node("unreachable")
        self.run_once()
        node = self.out / RUN / retry.node_id(1)
        self.assertEqual([a.name for a in layout.attempts(node)],
                         ["20261028T143005Z", "20261028T143035Z", "20261028T143105Z"])
        self.assertEqual(c.times(retry.node_id(1), "checkpoints"), 3)
        self.assertTrue((node / "checkpoints.verification").read_text().startswith("absence "))
        record = json.loads((self.out / RUN / "RUN.json").read_text())
        finished = dt.datetime.strptime(record["finished"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        self.assertLessEqual((finished - at(14, 30, 5)).total_seconds(), 120)

    def test_no_retry_starts_after_the_budget(self):
        self.one_node("unreachable")
        # The first pass runs long (a slow mesh): past the budget, nothing more is started.
        original = daily_report.ask_attempt

        def slow(*args, **kwargs):
            lines = original(*args, **kwargs)
            self.clock.t += dt.timedelta(seconds=daily_report.ONDEMAND_BUDGET_S)
            return lines
        daily_report.ask_attempt = slow
        try:
            out = self.run_once()
        finally:
            daily_report.ask_attempt = original
        self.assertIn("no time left in the run", out)
        self.assertEqual(len(layout.attempts(self.out / RUN / retry.node_id(1))), 1)

    def test_a_gap_between_runs_is_never_missed(self):
        self.one_node()
        self.run_once()
        self.clock.t = at(18, 12, 40, day=30)  # two days and some hours later
        self.run_once()
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(names, [RUN, "2026-10-30T181240Z"])
        self.assertFalse(any((self.out / n / "MISSED").exists() for n in names))
        # Nor does the check want anything between them, through any later time.
        for through in ("2026-10-30T181240Z", "2026-11-02T000000Z"):
            errs, runs = check_reports.check(self.out, layout.OnDemand.parse(through), period=layout.OnDemand)
            self.assertEqual((errs, runs), ([], names), through)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = check_reports.main(["check_reports.py", str(self.out), "--set", "ondemand",
                                       "--through", "2026-11-02T000000Z"])
        self.assertEqual(code, 0, err.getvalue())

    def test_absent_runs_are_counted_in_runs(self):
        self.one_node("unreachable")
        self.run_once()
        self.clock.t = at(20, 0, 0)
        self.run_once()
        readme = (self.out / "2026-10-28T200000Z" / "README.md").read_text()
        self.assertIn("absent 2 runs running", readme)
        self.assertIn("absent this run", (self.out / RUN / "README.md").read_text())
        self.assertEqual(layout.absent_days_running(self.out, retry.node_id(1), layout.OnDemand.parse(
            "2026-10-28T200000Z"), layout.OnDemand), 2)

    def test_an_unfinished_earlier_run_is_removed_not_marked(self):
        self.one_node()
        stale = self.out / "2026-10-28T120000Z"
        (stale / retry.node_id(1) / "attempts").mkdir(parents=True)
        out = self.run_once()
        self.assertIn("2026-10-28T120000Z: removed", out)
        self.assertEqual([p.name for p in self.out.iterdir()], [RUN])

    def test_a_reporter_that_cannot_reach_its_node_writes_nothing(self):
        self.list_nodes(retry.node_id(1))
        out = self.run_once(console="http://127.0.0.1:9", code=2)
        self.assertIn("no on-demand run", out)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_the_reporters_own_node_is_asked_through_the_second_console(self):
        self.list_nodes(retry.node_id(1), SELF, keys={SELF: SELF_KEY})
        self.console({retry.node_id(1): retry.both("verified"), SELF: retry.both("unreachable")},
                     {retry.node_id(1): retry.key_id(1), SELF: SELF_KEY})
        second = self.console({SELF: retry.both("verified")}, {SELF: SELF_KEY})
        self.run_once("--second-console", second.url)
        record = json.loads((self.out / RUN / "RUN.json").read_text())
        self.assertEqual(record["asked"], sorted([retry.node_id(1), SELF]))
        self.assertEqual(record["asked_through_second_console"], [SELF])
        self.assertEqual({p for p, _ in second.asked}, {SELF})
        self.assertEqual([p for p, _ in self.consoles[0].asked if p == SELF], [])


class OnDemandChecksTest(unittest.TestCase):
    """check_reports and the scrub with --set ondemand, and the daily set kept apart."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.raw = make_raw(cls.dir / "raw")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def copy(self):
        d = Path(tempfile.mkdtemp(dir=self.dir))
        shutil.copytree(self.raw, d / "reports")
        return d / "reports"

    def test_check_reports_with_the_set(self):
        reports = self.copy()
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(err):
            code = check_reports.main(["check_reports.py", str(reports / "ondemand"), "--set", "ondemand"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("2 runs ok", out.getvalue())
        # An on-demand RUN.json must say so, and is refused in the daily set.
        run_json = reports / "ondemand" / RUN / "RUN.json"
        record = json.loads(run_json.read_text())
        run_json.write_text(json.dumps({k: v for k, v in record.items() if k != "set"}))
        self.assertEqual(check_reports.check(reports / "ondemand", period=layout.OnDemand)[0],
                         [f'{RUN}: RUN.json must say "set": "ondemand" in the on-demand set'])
        day = reports / "daily" / "2026-10-27" / "RUN.json"
        day.write_text(json.dumps({**json.loads(day.read_text()), "set": "ondemand"}))
        self.assertEqual(check_reports.check(reports / "daily")[0],
                         ["2026-10-27: RUN.json is an on-demand run's, not a day's"])

    def test_the_scrub_writes_one_file_per_run(self):
        reports, out = self.copy(), self.dir / "scrub-ondemand" / "stats" / "data" / "ondemand"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scrub_reports.main(["scrub_reports.py", "scrub", str(reports / "ondemand"), str(out),
                                                 "--set", "ondemand"]), 0)
            self.assertEqual(scrub_reports.main(["scrub_reports.py", "check", str(out), "--set", "ondemand"]), 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()), [f"{RUN}.json", "2026-10-29T080000Z.json"])
        run = json.loads((out / f"{RUN}.json").read_text())
        self.assertEqual(run["date"], RUN)
        self.assertEqual(set(run), scrub_reports.DAY_KEYS)  # exactly the scrubbed format, nothing more
        self.assertEqual(run["run"], "settled")
        self.assertEqual(scrub_reports.check(out), [f"{n}: not <YYYY-MM-DD>.json" for n in sorted(
            p.name for p in out.iterdir())])
        runs = stats_model.load(out, layout.OnDemand)
        self.assertEqual([r.date for r in runs], ["2026-10-29T080000Z", RUN])  # newest first
        with self.assertRaises(stats_model.StatsError):
            stats_model.load(out)  # never read as days
        # A daily file is not an on-demand run either.
        daily_out = self.dir / "scrub-daily"
        scrub_reports.scrub(reports / "daily", daily_out, ROOT / "nodes" / "left")
        self.assertEqual(scrub_reports.check(daily_out, layout.OnDemand),
                         [f"{n}: not a <YYYY-MM-DD>T<HHMMSS>Z.json on-demand run" for n in
                          ("2026-10-27.json", "2026-10-28.json")])

    def test_j_an_on_demand_run_leaves_the_daily_set_and_page_byte_identical(self):
        """(j): two daily days, with and without an on-demand run beside them."""
        sites = {}
        for label, with_run in (("without", False), ("with", True)):
            reports = self.copy()
            if not with_run:
                shutil.rmtree(reports / "ondemand")
            stats = reports.parent / "stats" / "data"
            scrub_reports.scrub(reports / "daily", stats / "daily", ROOT / "nodes" / "left")
            if with_run:
                scrub_reports.scrub(reports / "ondemand", stats / "ondemand", ROOT / "nodes" / "left",
                                    layout.OnDemand)
                self.assertEqual(len(list((stats / "ondemand").iterdir())), 2)
            with contextlib.redirect_stdout(io.StringIO()):
                build.build(stats / "daily", ROOT / "tests" / "fixtures" / "stats" / "findings",
                            ROOT / "versions.lock", reports.parent / "_site", stats / "rehearsal")
            days = stats_model.load(stats / "daily")
            sites[label] = (tree(stats / "daily"), tree(reports.parent / "_site"),
                            [d.date for d in days], stats_model.sealed_per_day(days))
        self.assertEqual(sites["with"][2], ["2026-10-28", "2026-10-27"])
        self.assertTrue(sites["with"][0] and sites["with"][1])
        self.assertEqual(sites["with"], sites["without"])

    def test_a_misplaced_on_demand_run_never_counts_as_a_day(self):
        reports = self.copy()
        shutil.copytree(reports / "ondemand" / RUN, reports / "daily" / RUN)
        out = self.dir / "misplaced"
        scrub_reports.scrub(reports / "daily", out, ROOT / "nodes" / "left")
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["2026-10-27.json", "2026-10-28.json"])
        self.assertIn(f"{RUN}: not a <YYYY-MM-DD> directory", check_reports.check(reports / "daily")[0])
        (out / f"{RUN}.json").write_text("{}\n")
        with self.assertRaises(stats_model.StatsError):
            stats_model.load(out)


def make_raw(root):
    """A raw store: reports/daily with two days, reports/ondemand with two runs between and after."""
    t = OnDemandRunTest("run")
    t.setUp()
    try:
        t.list_nodes(retry.node_id(1), retry.node_id(2))
        t.console({retry.node_id(1): retry.both("verified"), retry.node_id(2): retry.both("unreachable")},
                  {retry.node_id(1): retry.key_id(1), retry.node_id(2): retry.key_id(2)})
        for name, when in (("daily", at(0, 30, day=27)), ("daily", at(0, 30)), ("ondemand", at(14, 30, 5)),
                           ("ondemand", at(8, 0, day=29))):
            t.clock.t = when
            t.out = t.dir / "reports" / name
            argv = ["--set", name] if name != "daily" else ["--set", "daily"]
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = daily_report.main(["daily_report.py", "run", *argv, "--console", t.consoles[0].url,
                                          "--nodes", str(t.nodes), "--out", str(t.out), "--reporter",
                                          str(t.reporter), "--timeout", "0.2"], now=t.clock.now, sleep=t.clock.sleep)
            assert code == 0, out.getvalue() + err.getvalue()
        shutil.copytree(t.dir / "reports", root)
    finally:
        t.tearDown()
    return root


FAKE_SYSTEMCTL = """#!/usr/bin/env bash
# A stand-in for `systemctl --user is-active UNIT`: FAKE_ACTIVE names the unit that is running.
[ "$1" = --user ] && [ "$2" = is-active ] || exit 64
if [ -n "${FAKE_BUS_DOWN:-}" ]; then echo "Failed to connect to bus: No medium found" >&2; exit 1; fi
if [ "$3" = "${FAKE_ACTIVE:-}" ]; then echo activating; exit 0; fi
echo inactive; exit 3
"""
HOLD = "import sys\nprint('reports/ondemand/x: an email address', file=sys.stderr)\nsys.exit(1)\n"


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class RefreshNowTest(unittest.TestCase):
    """scripts/refresh_now.sh in a throwaway loop clone, pushing to a scratch loop-data."""

    git = tr.RunDailyTest.git
    head = tr.RunDailyTest.head
    origin_head = tr.RunDailyTest.origin_head
    seed = tr.RunDailyTest.seed
    tearDown = tr.RunDailyTest.tearDown

    def setUp(self):
        tr.RunDailyTest.setUp(self)
        tmp = Path(self.tmp.name)
        shutil.copy2(ROOT / "scripts" / "refresh_now.sh", self.clone / "scripts" / "refresh_now.sh")
        self.git(self.clone, "add", "scripts/refresh_now.sh")
        self.git(self.clone, "commit", "--quiet", "-m", "refresh")
        self.git(self.clone, "push", "--quiet", "origin", "main")
        self.loop_head = self.head(self.origin)
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "systemctl").write_text(FAKE_SYSTEMCTL)
        (bin_dir / "systemctl").chmod(0o755)
        self.env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        self.env["HOME"] = str(tmp / "home")
        self.env["LOOP_HELD_DIR"] = str(tmp / "held")
        self.env.pop("LOOP_REPORT_SET", None)
        for name in ("LOOP_SECOND_CONSOLE", "LOOP_SELF_NODE", "LOOP_REPORTER_ENV", "FAKE_ACTIVE"):
            self.env.pop(name, None)

    def refresh(self):
        return subprocess.run(["bash", str(self.clone / "scripts" / "refresh_now.sh")], cwd=tmp_cwd(self),
                              env=self.env, capture_output=True, text=True)

    def pushed(self, before):
        """The commits pushed since `before`, each with the paths it touched."""
        shas = self.git(self.data_origin, "rev-list", f"{before}..main").split()
        return [(self.git(self.data_origin, "log", "-1", "--format=%s", s).strip(),
                 self.git(self.data_origin, "show", "--format=", "--name-only", s).split()) for s in shas]

    def unchanged(self, before, done):
        self.assertEqual(self.origin_head(), before, done.stdout + done.stderr)
        self.assertEqual(self.git(self.data, "rev-parse", "HEAD").strip(), before)
        self.assertFalse((self.data / "reports" / "ondemand").exists() and any(
            p.name != "README.md" for p in (self.data / "reports" / "ondemand").iterdir()))

    def test_one_commit_inside_its_own_run_only(self):
        before = self.origin_head()
        # Untracked leftovers beside the run: never part of its commit.
        (self.data / "reports" / "ondemand").mkdir(parents=True)
        (self.data / "reports" / "ondemand" / "README.md").write_text("left here by hand\n")
        (self.data / "notes.txt").write_text("not for the repo\n")
        done = self.refresh()
        self.assertEqual(done.returncode, 0, done.stderr)
        pushed = self.pushed(before)
        self.assertEqual(len(pushed), 1, pushed)
        subject, paths = pushed[0]
        run = subject.removeprefix("report: on-demand ")
        self.assertRegex(run, layout.OnDemand.pattern)
        self.assertTrue(paths)
        self.assertTrue(all(p.startswith(f"reports/ondemand/{run}/") for p in paths), paths)
        self.assertIn(f"reports/ondemand/{run}/RUN.json", paths)
        self.assertEqual(json.loads(self.git(self.data_origin, "show", f"main:reports/ondemand/{run}/RUN.json"))["set"],
                         "ondemand")
        self.assertIn("Signed-off-by: reporter", self.git(self.data_origin, "log", "-1", "--format=%B", "main"))
        self.assertEqual(self.git(self.data, "ls-files", "reports/daily").split(), ["reports/daily/.gitkeep"])
        self.assertFalse((self.clone / "reports").exists())
        # A second run is its own commit and its own directory.
        middle = self.origin_head()
        time.sleep(1.1)  # run names are per second
        done = self.refresh()
        self.assertEqual(done.returncode, 0, done.stderr)
        (subject2, paths2), = self.pushed(middle)
        run2 = subject2.removeprefix("report: on-demand ")
        self.assertNotEqual(run2, run)
        self.assertTrue(all(p.startswith(f"reports/ondemand/{run2}/") for p in paths2), paths2)

    def test_a_staged_change_outside_is_refused_before_asking(self):
        before = self.origin_head()
        (self.data / "README.md").write_text("changed\n")
        self.git(self.data, "add", "README.md")
        done = self.refresh()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("changes are staged", done.stderr)
        self.assertIn("README.md", done.stderr)
        self.assertEqual(self.console.asked, [])
        self.unchanged(before, done)

    def test_a_staged_change_inside_reports_ondemand_is_refused_too(self):
        before = self.origin_head()
        other = self.data / "reports" / "ondemand" / "2026-10-28T010101Z"
        other.mkdir(parents=True)
        (other / "MISSED").write_text("planted\n")
        self.git(self.data, "add", "reports/ondemand")
        done = self.refresh()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("changes are staged", done.stderr)
        self.assertEqual(self.origin_head(), before)

    def test_a_local_commit_outside_is_not_pushed(self):
        before = self.origin_head()
        (self.data / "README.md").write_text("changed\n")
        self.git(self.data, "commit", "--quiet", "-am", "local change")
        done = self.refresh()
        self.assertEqual(done.returncode, 1)
        self.assertIn("outside reports/ondemand", done.stderr)
        self.assertEqual(self.origin_head(), before)

    def test_not_on_main_is_refused(self):
        before = self.origin_head()
        self.git(self.data, "switch", "--quiet", "-c", "elsewhere")
        done = self.refresh()
        self.assertEqual(done.returncode, 1)
        self.assertIn("not on main", done.stderr)
        self.assertEqual(self.console.asked, [])
        self.assertEqual(self.origin_head(), before)

    def test_refused_while_a_daily_or_rehearsal_run_is_active(self):
        before = self.origin_head()
        for unit in ("loop-reporter.service", "loop-reporter-ask.service", "loop-reporter-rehearsal.service"):
            self.env["FAKE_ACTIVE"] = unit
            done = self.refresh()
            self.assertEqual(done.returncode, 1, unit)
            self.assertIn(f"{unit} is activating", done.stderr)
        self.env.pop("FAKE_ACTIVE")
        self.env["FAKE_BUS_DOWN"] = "1"
        done = self.refresh()
        self.assertEqual(done.returncode, 1)
        self.assertIn("cannot tell whether loop-reporter.service is running", done.stderr)
        self.assertEqual(self.console.asked, [])
        self.unchanged(before, done)

    def test_refused_while_another_run_holds_the_checkout(self):
        before = self.origin_head()
        lock = Path(self.git(self.data, "rev-parse", "--absolute-git-dir").strip()) / "loop-report.lock"
        with open(lock, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            done = self.refresh()
        self.assertEqual(done.returncode, 1)
        self.assertIn("another report run holds", done.stderr)
        self.assertEqual(self.console.asked, [])
        self.assertEqual(self.origin_head(), before)

    def test_a_reporter_down_commits_nothing(self):
        before = self.origin_head()
        self.env["LOOP_CONSOLE"] = "http://127.0.0.1:9"
        done = self.refresh()
        self.assertEqual(done.returncode, 2, done.stderr)
        self.unchanged(before, done)

    def test_a_personal_data_hit_is_held_and_nothing_committed(self):
        (self.clone / "scripts" / "check_personal_data.py").write_text(HOLD)
        self.git(self.clone, "add", "scripts/check_personal_data.py")
        self.git(self.clone, "commit", "--quiet", "-m", "hold")
        self.git(self.clone, "push", "--quiet", "origin", "main")
        self.loop_head = self.head(self.origin)
        before = self.origin_head()
        done = self.refresh()
        self.assertEqual(done.returncode, 3, done.stderr)
        self.unchanged(before, done)
        held = list((Path(self.tmp.name) / "held").iterdir())
        self.assertEqual(len(held), 1)
        self.assertTrue((held[0] / "RUN.json").is_file())

    def test_no_console_named_refuses_and_never_falls_back_to_3131(self):
        before = self.origin_head()
        del self.env["LOOP_CONSOLE"]
        self.env["LOOP_REPORTER_ENV"] = str(Path(self.tmp.name) / "no-such-env")
        done = self.refresh()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("LOOP_CONSOLE is not set", done.stderr)
        self.assertEqual(self.console.asked, [])
        self.assertEqual(self.origin_head(), before)

    def test_settings_come_from_the_env_file_and_the_caller_wins(self):
        env_file = Path(self.tmp.name) / "reporter.env"
        flag = Path(self.tmp.name) / "ran"
        env_file.write_text(f"# the reporter's settings\nLOOP_CONSOLE=http://127.0.0.1:9\n"
                            f"LOOP_DATA_DIR={self.data}\nLOOP_SELF_NODE=$(touch {flag})\n")
        self.env["LOOP_REPORTER_ENV"] = str(env_file)
        del self.env["LOOP_DATA_DIR"]  # from the file; LOOP_CONSOLE from the caller
        before = self.origin_head()
        done = self.refresh()
        # The file's LOOP_SELF_NODE is taken as text, never run, and is no label in the population.
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("--self-node '$(touch", done.stderr)
        self.assertFalse(flag.exists())
        self.assertEqual(self.console.asked, [])
        # Surrounding quotes are dropped, as systemd's EnvironmentFile does.
        # and the whitespace around them; LOOP_CONSOLE comes from the file here.
        env_file.write_text(f'LOOP_CONSOLE= "{self.env.pop("LOOP_CONSOLE")}" \nLOOP_DATA_DIR=\'{self.data}\'\n')
        done = self.refresh()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(self.pushed(before)), 1)


def tmp_cwd(test):
    """Run from outside the clone, as an operator would: the script finds its own clone."""
    return test.tmp.name


if __name__ == "__main__":
    unittest.main()
