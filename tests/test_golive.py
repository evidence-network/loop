"""Going live: the rehearsal's confidence check, and the last clean slot promoted to day one.

The slots are written by the reporter itself, daily_report.py run --set rehearsal,
against fake consoles under a fake clock (as in test_rehearsal.py): node 1, and the
reporter's own node asked through a second console.
Run: python3 -B -m unittest discover -s tests -v
"""
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
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
import promote_rehearsal_slot as promote  # noqa: E402
import rehearsal_confidence as confidence  # noqa: E402
import report_layout as layout  # noqa: E402
import scrub_reports  # noqa: E402
import test_offline_retry as retry  # noqa: E402

UTC = dt.timezone.utc
N1 = retry.node_id(1)
SELF = retry.REPORTER["node_id"]
SELF_KEY = retry.REPORTER["key_id"]
START = dt.datetime(2026, 10, 28, 14, 30, 2, tzinfo=UTC)
LOCK = ROOT / "versions.lock"
PINS = confidence.pins(LOCK)


def slot(i):
    return layout.Rehearsal.name(layout.Rehearsal.of(START + dt.timedelta(minutes=10 * i)))


def write_build_json(path, capsules=PINS["capsules"], failures=()):
    """A build.json as lab/lab.sh check writes it."""
    path.write_text(json.dumps({
        "check_failures": list(failures), "failures": list(failures), "ok": not failures,
        "capsules": {"pin": capsules, "pin_commit": capsules if len(capsules) == 40 else "5" * 40,
                     "source_head": capsules if len(capsules) == 40 else "5" * 40, "binary_sha256": "9" * 64},
        "mesh_llm": {"source_head": "3" * 40, "binary_sha256": "3" * 64, "version": "mesh-llm lab build",
                     "contains": {n: PINS[n] for n in confidence.MESH_PINS}},
    }))
    return path


def make_set(root, plan, list_self=True):
    """Run the reporter once per slot in `plan`; returns (reports/rehearsal, nodes/).

    Each step is one slot: "clean"; "skip" (the timer stopped: the next run marks it MISSED);
    "node-absent" (node 1 answers nothing); "self-absent" (the reporter's own node answers
    nothing through the second console); "no-second" (no second console: no self row);
    "other-lock" (run under another versions.lock).
    """
    nodes, out, reporter = root / "nodes", root / "reports" / "rehearsal", root / "reporter.json"
    nodes.mkdir(parents=True)
    reporter.write_text(json.dumps(retry.REPORTER))
    for nid, kid in ((N1, retry.key_id(1)), (SELF, SELF_KEY)) if list_self else ((N1, retry.key_id(1)),):
        (nodes / f"{nid}.json").write_text(json.dumps(retry.entry(nid, kid)))
    primary = retry.FakeConsole({}, {N1: retry.key_id(1), SELF: SELF_KEY})
    second = retry.FakeConsole({}, {SELF: SELF_KEY})
    try:
        for i, what in enumerate(plan):
            if what == "skip":
                continue
            primary.behaviour = {N1: retry.both("unreachable" if what == "node-absent" else "verified"),
                                 SELF: retry.both("unreachable")}
            second.behaviour = {SELF: retry.both("unreachable" if what == "self-absent" else "verified")}
            clock = retry.Clock(START + dt.timedelta(minutes=10 * i))
            argv = ["daily_report.py", "run", "--set", "rehearsal", "--console", primary.url, "--nodes", str(nodes),
                    "--out", str(out), "--reporter", str(reporter), "--timeout", "0.2"]
            if what != "no-second" and list_self:
                argv += ["--second-console", second.url]
            lock = unittest.mock.patch.object(daily_report, "versions_lock_sha256", lambda: "0" * 64) \
                if what == "other-lock" else contextlib.nullcontext()
            sink = io.StringIO()
            with lock, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                code = daily_report.main(argv, now=clock.now, sleep=clock.sleep)
            assert code == 0, sink.getvalue()
    finally:
        primary.close()
        second.close()
    return out, nodes


PLANS = {
    "confident": ["clean", "skip", *["clean"] * 12],
    "eleven": ["clean", "skip", *["clean"] * 11],
    "node_absent": ["clean", "skip", *["clean"] * 6, "node-absent", *["clean"] * 6],
    "self_absent": ["clean", "skip", *["clean"] * 12, "self-absent"],
    "no_missed": ["clean"] * 13,
    "other_lock": ["clean", "skip", *["clean"] * 5, "other-lock", *["clean"] * 6],
    "late_failure": ["clean", "skip", "clean", "clean", "skip", "node-absent", *["clean"] * 12],
}


class Sets:
    """Every plan's slots, written once for the module."""
    tmp = None
    sets = {}

    @classmethod
    def get(cls, name):
        if cls.tmp is None:
            cls.tmp = tempfile.TemporaryDirectory()
        if name not in cls.sets:
            if name == "self_unlisted":
                cls.sets[name] = make_set(Path(cls.tmp.name) / name, ["clean", "skip", *["clean"] * 12],
                                          list_self=False)
            else:
                cls.sets[name] = make_set(Path(cls.tmp.name) / name, PLANS[name])
        return cls.sets[name]


def tearDownModule():
    if Sets.tmp is not None:
        Sets.tmp.cleanup()


class ConfidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.build = write_build_json(Path(self.tmp.name) / "build.json")

    def verdict(self, name, *extra, build_json=True):
        reports, nodes = Sets.get(name)
        argv = ["rehearsal_confidence.py", str(reports), "--nodes", str(nodes), "--lock", str(LOCK), *extra]
        if build_json:
            argv += ["--build-json", str(self.build)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = confidence.main(argv)
        return code, out.getvalue()

    def test_twelve_clean_slots_after_a_missed_slot_that_recovers_is_confident(self):
        code, out = self.verdict("confident")
        self.assertEqual(code, 0, out)
        first = out.splitlines()[0]
        self.assertEqual(first, f"CONFIDENT: 12 clean slots {slot(2)}..{slot(13)}, recovered at {slot(2)}")
        code, out = self.verdict("confident", "--json")
        details = json.loads(out.split("\n", 1)[1])
        self.assertEqual(details["run"], {"first": slot(2), "last": slot(13), "length": 12})
        self.assertEqual(details["first_unclean"]["slot"], slot(1))
        self.assertTrue(details["first_unclean"]["why"].startswith("MISSED: no report for"))
        self.assertEqual((details["planted_stop"]["missed"], details["planted_stop"]["next_clean"]), (slot(1), True))
        self.assertEqual(details["build"]["in_reports"]["host_build"], "not recorded")
        # The reporter's own row really is there, verified, through the second console.
        run = layout.run_record(Sets.get("confident")[0] / slot(13))
        self.assertEqual(run["asked_through_second_console"], [SELF])

    def test_eleven_clean_slots_is_not_yet(self):
        code, out = self.verdict("eleven")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith(f"NOT YET: 11 consecutive clean slots, need 12; {slot(1)} is not clean: MISSED"),
                        out)
        self.assertEqual(self.verdict("eleven", "--slots", "11")[0], 0)

    def test_a_node_with_no_answer_is_not_yet_naming_the_slot(self):
        code, out = self.verdict("node_absent")
        self.assertEqual(code, 1)
        self.assertIn(f"NOT YET: 6 consecutive clean slots, need 12; {slot(8)} is not clean: "
                      f"node {N1[:12]} checkpoints: absence", out)

    def test_the_reporters_own_row_absent_is_not_yet_naming_the_slot(self):
        code, out = self.verdict("self_absent")
        self.assertEqual(code, 1)
        self.assertIn(f"NOT YET: 0 consecutive clean slots, need 12; {slot(14)} is not clean: "
                      f"the reporter's own node {SELF[:12]} checkpoints: absence", out)

    def test_no_self_row_at_all_is_not_yet(self):
        # The reporter's node not listed and no second console: every node listed answered,
        # but the reporter's own row is missing.
        code, out = self.verdict("self_unlisted")
        self.assertEqual(code, 1, out)
        self.assertIn(f"the reporter's own node {SELF[:12]} has no row asked through a second console", out)

    def test_no_missed_slot_is_not_yet(self):
        code, out = self.verdict("no_missed")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("NOT YET: no MISSED slot before the run"), out)

    def test_a_slot_under_another_versions_lock_is_not_yet(self):
        code, out = self.verdict("other_lock")
        self.assertEqual(code, 1)
        self.assertIn(f"{slot(7)} is not clean: taken under versions.lock 000000000000, not the current "
                      f"{confidence.lock_sha256(LOCK)[:12]}", out)
        # The same slots judged against that other lock: every other slot is then the odd one out.
        other = Path(self.tmp.name) / "versions.lock"
        other.write_text(LOCK.read_text() + "# another lock\n")
        code, out = self.verdict("confident", "--lock", str(other))
        self.assertEqual(code, 1)
        self.assertIn("taken under versions.lock", out)

    def test_the_recovery_after_the_latest_missed_slot_must_be_clean(self):
        code, out = self.verdict("late_failure")
        self.assertEqual(code, 1)
        self.assertIn(f"the slot after the MISSED slot {slot(4)}, {slot(5)}, is not clean", out)

    def test_the_build_must_come_from_lab_check(self):
        code, out = self.verdict("confident", build_json=False)
        self.assertEqual(code, 1)
        self.assertIn("NOT YET: no --build-json: the reports do not record a node's build", out)
        write_build_json(self.build, capsules="v0.1.5")
        code, out = self.verdict("confident")
        self.assertEqual(code, 1)
        self.assertIn(f"capsules v0.1.5 (555555555555), versions.lock pins {PINS['capsules']}", out)
        write_build_json(self.build, failures=["mesh-llm source has uncommitted changes"])
        code, out = self.verdict("confident")
        self.assertEqual(code, 1)
        self.assertIn("lab/lab.sh check failed: mesh-llm source has uncommitted changes", out)

    def test_a_slot_still_under_way_is_left_out_and_a_gap_is_not_clean(self):
        reports, nodes = Sets.get("confident")
        copy = Path(self.tmp.name) / "rehearsal"
        shutil.copytree(reports, copy)
        (copy / slot(14)).mkdir()  # the timer's current run, still asking
        with contextlib.redirect_stdout(io.StringIO()):
            code = confidence.main(["x", str(copy), "--nodes", str(nodes), "--build-json", str(self.build)])
        self.assertEqual(code, 0)
        shutil.rmtree(copy / slot(14))
        shutil.rmtree(copy / slot(10))
        names = [s for s, why in confidence.judged(copy, confidence.listed(nodes), confidence.lock_sha256(LOCK))
                 if why == "no report and no MISSED mark"]
        self.assertEqual(names, [slot(10)])

    def test_a_listed_node_never_asked_is_not_clean(self):
        reports, nodes = Sets.get("confident")
        more = Path(self.tmp.name) / "nodes"
        shutil.copytree(nodes, more)
        nid = retry.node_id(2)
        (more / f"{nid}.json").write_text(json.dumps(retry.entry(nid, retry.key_id(2))))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = confidence.main(["x", str(reports), "--nodes", str(more), "--build-json", str(self.build)])
        self.assertEqual(code, 1)
        self.assertIn(f"not {nid[:12]}, listed in nodes/", out.getvalue())


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="reporter",
               GIT_AUTHOR_EMAIL="reporter@example.invalid", GIT_COMMITTER_NAME="reporter",
               GIT_COMMITTER_EMAIL="reporter@example.invalid")
    return env


def digests(root, skip=()):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file() and str(p.relative_to(root)) not in skip}


class PromoteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        reports, self.nodes = Sets.get("confident")
        self.data = tmp / "loop-data"
        shutil.copytree(reports, self.data / "reports" / "rehearsal")
        (self.data / "reports" / "daily").mkdir()
        (self.data / "reports" / "daily" / ".gitkeep").write_text("")
        self.reporter = tmp / "reporter.json"
        self.reporter.write_text(json.dumps(retry.REPORTER))
        self.daily = self.data / "reports" / "daily"
        self.day = "2026-10-28"

    def promote(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        argv = ["promote_rehearsal_slot.py", "--data", str(self.data), "--nodes", str(self.nodes),
                "--lock", str(LOCK), "--reporter", str(self.reporter), *argv]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                unittest.mock.patch.dict(os.environ, git_env()):
            code = promote.main(argv)
        return code, out.getvalue() + err.getvalue()

    def assert_refused(self, why, *argv):
        before = digests(self.data)
        code, out = self.promote(*argv)
        self.assertEqual(code, 1, out)
        self.assertIn(why, out)
        self.assertEqual(digests(self.data), before)  # nothing written
        return out

    def test_the_last_clean_slot_becomes_day_one_live(self):
        code, out = self.promote("--last-clean")
        self.assertEqual(code, 0, out)
        self.assertIn(f"promoted rehearsal/{slot(13)} to reports/daily/{self.day} as day one (live)", out)
        src, day = self.data / "reports" / "rehearsal" / slot(13), self.daily / self.day
        # Every byte the nodes signed, and every file of each node's day, is the slot's.
        self.assertEqual(digests(day, promote.REWRITTEN), digests(src, promote.REWRITTEN))
        self.assertEqual(sorted(digests(day)), sorted(digests(src)))
        run, was = layout.run_record(day), layout.run_record(src)
        self.assertEqual((run["date"], run["label"], run["promoted_from"]), (self.day, "live", f"rehearsal/{slot(13)}"))
        self.assertEqual({k: v for k, v in run.items() if k not in ("date", "label", "promoted_from")},
                         {k: v for k, v in was.items() if k != "date"})
        readme = (day / "README.md").read_text()
        self.assertTrue(readme.startswith(f"# Daily report {self.day}\n\nDay one of the daily report, promoted from "
                                          f"the rehearsal slot `rehearsal/{slot(13)}`"), readme)
        self.assertNotIn("TEST", readme)
        self.assertEqual(readme.count("\n- `"), 2)  # both node lines kept
        # The daily watchdog with reporter.json passes on the daily set.
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            code = check_reports.main(["check_reports.py", str(self.daily), "--through", self.day,
                                       "--reporter", str(self.reporter)])
        self.assertEqual(code, 0, sink.getvalue())
        self.assertIn("1 day ok", sink.getvalue())
        # The rehearsal itself is left as it was, for the wipe.
        self.assertTrue((self.data / "reports" / "rehearsal" / slot(13) / "README.md").read_text().endswith("\n"))
        self.assertIn("(TEST)", (src / "README.md").read_text())

    def test_the_stats_site_shows_day_one_live_with_no_test_label(self):
        self.assertEqual(self.promote("--last-clean")[0], 0)
        tmp = Path(self.tmp.name)
        data = tmp / "stats" / "data" / "daily"
        self.assertEqual(scrub_reports.scrub(self.daily, data, tmp / "left"), [f"{self.day}.json"])
        self.assertEqual(scrub_reports.check(data), [])
        day = json.loads((data / f"{self.day}.json").read_text())
        self.assertEqual(day["run"], "settled")
        self.assertEqual({n["node_id"]: n["questions"]["history_card"]["state"] for n in day["nodes"]},
                         {N1: "verified", SELF: "verified"})
        findings = tmp / "findings"
        __import__("test_site").make_findings(findings)
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            # As the stats workflow builds it, after the wipe emptied stats/data/rehearsal.
            code = build.main(["build.py", str(data), "--findings", str(findings), "--lock", str(LOCK),
                               "--out", str(tmp / "site"), "--rehearsal", str(tmp / "stats" / "data" / "rehearsal")])
        self.assertEqual(code, 0, sink.getvalue())
        page = (tmp / "site" / "stats" / "index.html").read_text()
        self.assertIn(self.day, page)
        self.assertIn("2 of 2 listed nodes", page)
        self.assertNotIn("TEST", page)
        self.assertNotIn('class="test"', page)
        self.assertFalse((tmp / "site" / "stats" / "rehearsal").exists())

    def test_a_slot_that_is_not_clean_is_refused(self):
        self.assert_refused(f"rehearsal/{slot(1)} is not clean: MISSED", "--slot", slot(1))
        reports, nodes = Sets.get("node_absent")
        shutil.rmtree(self.data / "reports" / "rehearsal")
        shutil.copytree(reports, self.data / "reports" / "rehearsal")
        self.assert_refused(f"rehearsal/{slot(8)} is not clean: node {N1[:12]} checkpoints: absence",
                            "--slot", slot(8))
        self.assertEqual(self.promote("--slot", slot(9))[0], 0)  # a clean one goes

    def test_last_clean_skips_a_newer_slot_that_is_not_clean(self):
        reports, nodes = Sets.get("self_absent")
        shutil.rmtree(self.data / "reports" / "rehearsal")
        shutil.copytree(reports, self.data / "reports" / "rehearsal")
        code, out = self.promote("--last-clean")
        self.assertEqual(code, 0, out)
        self.assertEqual(layout.run_record(self.daily / self.day)["promoted_from"], f"rehearsal/{slot(13)}")

    def test_a_slot_that_is_not_a_slot_or_not_there_is_refused(self):
        self.assert_refused("is not a 10-minute slot", "--slot", "2026-10-28T1435Z")
        self.assert_refused("no such slot", "--slot", "2026-10-29T0000Z")

    def test_a_day_that_exists_is_refused(self):
        (self.daily / self.day).mkdir()
        (self.daily / self.day / "MISSED").write_text("no report for 2026-10-28: planted\n")
        self.assert_refused(f"reports/daily/{self.day} exists already", "--last-clean")

    def test_a_daily_set_that_fails_the_watchdog_is_refused(self):
        # An earlier day, then nothing marked between it and day one: the daily watchdog is red.
        (self.daily / "2026-10-20").mkdir()
        (self.daily / "2026-10-20" / "MISSED").write_text("no report for 2026-10-20: planted\n")
        self.assert_refused("2026-10-21: no report and no MISSED mark", "--last-clean")
        # Without that earlier day the same promotion goes: the refusal was the gap alone.
        shutil.rmtree(self.daily / "2026-10-20")
        self.assertEqual(self.promote("--last-clean")[0], 0)

    def test_a_rehearsal_readme_that_is_not_one_is_refused(self):
        readme = self.data / "reports" / "rehearsal" / slot(13) / "README.md"
        readme.write_text(readme.read_text().replace("(TEST)", "(test run)"))
        self.assert_refused("does not start with", "--slot", slot(13))


@unittest.skipUnless(shutil.which("git"), "needs git")
class PromoteCommitTest(unittest.TestCase):
    """--commit in a scratch loop-data clone with its own origin, as on the reporter node."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.env = git_env()
        reports, self.nodes = Sets.get("confident")
        self.origin, self.data = tmp / "loop-data.git", tmp / "loop-data"
        self.git(tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git(tmp, "init", "-q", "-b", "main", str(self.data))
        shutil.copytree(reports, self.data / "reports" / "rehearsal")
        (self.data / "reports" / "daily").mkdir()
        (self.data / "reports" / "daily" / ".gitkeep").write_text("")
        (self.data / "README.md").write_text("loop-data\n")
        self.git(self.data, "add", "-A")
        self.git(self.data, "commit", "-q", "-m", "seed")
        self.git(self.data, "remote", "add", "origin", str(self.origin))
        self.git(self.data, "push", "-q", "-u", "origin", "main")
        self.reporter = tmp / "reporter.json"
        self.reporter.write_text(json.dumps(retry.REPORTER))
        self.before = self.head()

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True, text=True).stdout

    def head(self, rev="HEAD"):
        return self.git(self.data, "rev-parse", rev).strip()

    def promote(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        argv = ["promote_rehearsal_slot.py", "--data", str(self.data), "--nodes", str(self.nodes), "--lock", str(LOCK),
                "--reporter", str(self.reporter), "--commit", *argv]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                unittest.mock.patch.dict(os.environ, self.env, clear=True):
            code = promote.main(argv)
        return code, out.getvalue() + err.getvalue()

    def test_one_commit_holding_only_the_day_ready_for_the_deploy_key(self):
        code, out = self.promote("--last-clean")
        self.assertEqual(code, 0, out)
        self.assertIn("committed, not pushed", out)
        self.assertEqual(self.git(self.data, "rev-list", "--count", f"{self.before}..HEAD").strip(), "1")
        changed = self.git(self.data, "diff", "--name-only", self.before, "HEAD").split()
        self.assertTrue(changed)
        self.assertTrue(all(p.startswith("reports/daily/2026-10-28/") for p in changed), changed)
        body = self.git(self.data, "log", "-1", "--format=%B")
        self.assertTrue(body.startswith(f"report: daily 2026-10-28, day one (promoted from rehearsal/{slot(13)})"))
        self.assertIn("Signed-off-by: reporter", body)
        self.assertEqual(self.git(self.data, "status", "--porcelain"), "")
        self.assertEqual(self.git(self.origin, "rev-parse", "main").strip(), self.before)  # not pushed
        self.git(self.data, "push", "-q", "origin", "HEAD:main")
        self.assertEqual(self.git(self.origin, "rev-parse", "main").strip(), self.head())

    def test_a_change_staged_outside_the_day_is_refused_and_left_as_found(self):
        (self.data / "README.md").write_text("changed\n")
        self.git(self.data, "add", "README.md")
        code, out = self.promote("--last-clean")
        self.assertEqual(code, 1, out)
        self.assertIn("outside reports/daily/2026-10-28/: README.md", out)
        self.assertEqual(self.head(), self.before)
        self.assertFalse((self.data / "reports" / "daily" / "2026-10-28").exists())
        self.assertEqual(self.git(self.data, "diff", "--cached", "--name-only").split(), ["README.md"])

    def test_a_staged_deletion_in_the_rehearsal_is_refused(self):
        self.git(self.data, "rm", "-q", "-r", f"reports/rehearsal/{slot(0)}")
        code, out = self.promote("--last-clean")
        self.assertEqual(code, 1, out)
        self.assertIn(f"reports/rehearsal/{slot(0)}/RUN.json", out)
        self.assertEqual(self.head(), self.before)

    def test_an_outgoing_commit_is_refused_before_anything_is_written(self):
        (self.data / "README.md").write_text("changed\n")
        self.git(self.data, "commit", "-q", "-am", "local")
        head = self.head()
        code, out = self.promote("--last-clean")
        self.assertEqual(code, 1, out)
        self.assertIn("commits not on origin/main", out)
        self.assertEqual(self.head(), head)
        self.assertFalse((self.data / "reports" / "daily" / "2026-10-28").exists())

    def test_only_on_main(self):
        self.git(self.data, "switch", "-q", "-c", "other")
        code, out = self.promote("--last-clean")
        self.assertEqual(code, 1, out)
        self.assertIn("is not on main", out)


class ReadmeTest(unittest.TestCase):
    def test_going_live_has_the_commands_in_order(self):
        text = (ROOT / "reporter" / "README.md").read_text()
        section = text.split("## Going live", 1)[1]
        steps = ["scripts/rehearsal_confidence.py",
                 "systemctl --user disable --now loop-reporter-rehearsal.timer",
                 "systemctl --user is-active loop-reporter-rehearsal.service",
                 "scripts/promote_rehearsal_slot.py --last-clean",
                 "push origin HEAD:main",
                 "scripts/wipe_rehearsal.sh",
                 "systemctl --user enable --now loop-reporter.timer"]
        at = [section.find(s) for s in steps]
        self.assertNotIn(-1, at, list(zip(steps, at)))
        self.assertEqual(at, sorted(at))
        self.assertIn("reporter-env.conf", section)
        self.assertIn("for unit in loop-reporter loop-reporter-ask", section)


if __name__ == "__main__":
    unittest.main()
