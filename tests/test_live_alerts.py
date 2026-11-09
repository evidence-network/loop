"""scripts/live_alerts.py (evidence-network/loop#310): a FAIL fixture, a NOT_CHECKED fixture, a MALFORMED fixture and stale dates.
`gh` is a stand-in that records what it was asked and reports an open issue only when STUB_OPEN is set. Nothing reaches a network.
Run: python3 -B -m unittest tests.test_live_alerts -v
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
sys.path.insert(0, str(ROOT / "scripts"))
import finding  # noqa: E402
import live_alerts  # noqa: E402

GH = '#!/usr/bin/env bash\necho "$*" >> "$STUB_LOG"\n[ "$1 $2" = "issue list" ] && echo "${STUB_OPEN:-}"\nexit 0\n'


def write_step(run, step, result):
    d = Path(run) / step
    d.mkdir(parents=True)
    (d / "proof.txt").write_text("proof\n")
    nc = [{"what": "x", "why": "budget", "issue": "evidence-network/loop#193"}] if result == "NOT_CHECKED" else []
    finding.write(d, result=result, population="live", asserts=f"stub {step}", pinned=[], proof=["proof.txt"], detail="stub",
                  not_checked=nc, started="2026-11-01T00:00:00Z")


class AlertsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="live-alerts-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.gh = self.tmp / "gh"
        self.gh.write_text(GH)
        self.gh.chmod(0o755)
        self.log = self.tmp / "log"
        self.log.write_text("")
        self.env = {"GH": str(self.gh), "STUB_LOG": str(self.log)}
        for k, v in self.env.items():
            os.environ[k] = v
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in [*self.env, "STUB_OPEN"]])
        self.findings = self.tmp / "findings"

    def calls(self):
        return self.log.read_text().splitlines()

    def run_dir(self, name="2026-11-02-live-1", **steps):
        for step, result in steps.items():
            write_step(self.findings / name, step.replace("_", "-"), result)
        return self.findings / name

    def test_fail_opens_one_issue_with_owner(self):
        run = self.run_dir(**{"06_settle": "FAIL", "04_admit": "PASS"})
        self.assertEqual(live_alerts.alert_run(run, "https://run"), 1)
        c = self.calls()
        self.assertEqual(sum(x.startswith("issue create") for x in c), 1)
        self.assertTrue(any("--label loop-step-06" in x and "loop step 06 (settle) fails" in x for x in c))
        self.assertTrue(any("repository: unknown" in x for x in c))

    def test_open_issue_gets_a_comment_not_a_second_issue(self):
        os.environ["STUB_OPEN"] = "41"
        run = self.run_dir(**{"01_seal": "FAIL"})
        live_alerts.alert_run(run, "")
        c = self.calls()
        self.assertTrue(any(x.startswith("issue comment 41") for x in c))
        self.assertFalse(any(x.startswith("issue create") for x in c))

    def test_not_checked_pass_open_nothing(self):
        run = self.run_dir(**{"06_settle": "NOT_CHECKED", "04_admit": "PASS"})
        self.assertEqual(live_alerts.alert_run(run, ""), 0)
        self.assertEqual(self.calls(), [])

    def test_malformed_opens_an_issue(self):
        run = self.run_dir(**{"04_admit": "PASS"})
        (run / "04-admit" / "finding.json").write_text("{not json")
        (run / "05-deal").mkdir()
        self.assertEqual(sorted(r for _, _, r, _ in live_alerts.failing(run)), ["MALFORMED", "MALFORMED"])
        live_alerts.alert_run(run, "")
        self.assertEqual(sum(x.startswith("issue create") for x in self.calls()), 2)

    def test_wrong_typed_field_is_malformed_not_a_crash(self):
        import json
        run = self.run_dir(**{"04_admit": "PASS"})
        f = run / "04-admit" / "finding.json"
        r = json.loads(f.read_text())
        r["pinned"] = []
        f.write_text(json.dumps(r))
        self.assertEqual([x[2] for x in live_alerts.failing(run)], ["MALFORMED"])

    def test_stale_dates(self):
        self.run_dir("2026-10-31-live-3", **{"04_admit": "PASS"})
        self.run_dir("2026-10-30-live-9", **{"04_admit": "PASS"})
        today = datetime.date
        self.assertFalse(live_alerts.alert_stale(today(2026, 11, 1), "", self.findings))
        self.assertEqual(self.calls(), [])
        self.assertTrue(live_alerts.alert_stale(today(2026, 11, 2), "", self.findings))
        c = self.calls()
        self.assertTrue(any("--label loop-live-stale" in x and "2026-10-31" in x for x in c))
        self.assertFalse(any(x.startswith("issue close") for x in c))

    def test_stale_with_no_live_findings_and_open_issue(self):
        self.findings.mkdir()
        os.environ["STUB_OPEN"] = "7"
        self.assertTrue(live_alerts.alert_stale(datetime.date(2026, 11, 2), "", self.findings))
        self.assertTrue(any(x.startswith("issue comment 7") and "no live findings" in x for x in self.calls()))

    def test_a_goose_day_is_judged_on_its_own(self):
        # Mesh landed yesterday and Goose three days ago: only the Goose issue opens, under its own label.
        self.run_dir("2026-11-05-live-1", **{"04_admit": "PASS"})
        self.run_dir("goose/2026-11-02-live", **{"04_admit": "PASS"})
        self.run_dir("goose/2026-11-04-rehearsal", **{"04_admit": "PASS"})  # a rehearsal is not a live day
        today = datetime.date(2026, 11, 6)
        self.assertFalse(live_alerts.alert_stale(today, "", self.findings))
        self.assertTrue(live_alerts.alert_stale(today, "", self.findings, goose=True))
        c = self.calls()
        self.assertFalse(any("--label loop-live-stale" in x for x in c))
        self.assertTrue(any("--label loop-live-goose-stale" in x and "2026-11-02" in x for x in c))
        self.log.write_text("")
        self.run_dir("goose/2026-11-05-live", **{"04_admit": "PASS"})
        self.assertFalse(live_alerts.alert_stale(today, "", self.findings, goose=True))
        self.assertEqual(self.calls(), [])

    def test_no_goose_findings_at_all_is_stale(self):
        self.run_dir("2026-11-05-live-1", **{"04_admit": "PASS"})
        self.assertTrue(live_alerts.alert_stale(datetime.date(2026, 11, 6), "", self.findings, goose=True))
        self.assertTrue(any("no Goose live findings" in x for x in self.calls()))

    def day(self, date, run="settled", reason=None):
        d = self.tmp / "daily"
        d.mkdir(exist_ok=True)
        (d / f"{date}.json").write_text(json.dumps({"date": date, "run": run, "missed_reason": reason}))
        return d

    def test_reporter_stale_fires_on_old_data_only_and_missed_is_not_a_report(self):
        # The incident: the last real report is 10-29, every later day is MISSED. 11-08 must alert.
        daily = self.day("2026-10-29")
        for d in ("2026-10-30", "2026-11-07", "2026-11-08"):
            self.day(d, run="missed", reason="no answer from node" if d == "2026-11-08" else None)
        self.assertTrue(live_alerts.alert_reporter_stale(datetime.date(2026, 11, 9), "", daily))
        c = self.calls()
        self.assertTrue(any("--label loop-reporter-stale" in x and "2026-10-29" in x for x in c))
        self.assertTrue(any("MISSED is 2026-11-08: no answer from node" in x for x in c))  # the reporter's own reason is shown
        self.assertFalse(any(x.startswith("issue close") for x in self.calls()))

    def test_reporter_stale_quiet_on_fresh_data(self):
        self.log.write_text("")
        daily = self.day("2026-11-08")
        self.assertFalse(live_alerts.alert_reporter_stale(datetime.date(2026, 11, 9), "", daily))  # 1 day old
        self.assertEqual(self.calls(), [])
        self.assertTrue(live_alerts.alert_reporter_stale(datetime.date(2026, 11, 10), "", daily))  # 2 days old

    def test_reporter_stale_with_nothing_and_open_issue(self):
        os.environ["STUB_OPEN"] = "9"
        self.assertTrue(live_alerts.alert_reporter_stale(datetime.date(2026, 11, 9), "", self.tmp / "none"))
        self.assertTrue(any(x.startswith("issue comment 9") and "no daily report" in x for x in self.calls()))

    def test_changed_dirs_takes_only_live_runs(self):
        g = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *a], cwd=self.tmp, check=True,
                                      capture_output=True)
        g("init", "-q")
        (self.tmp / "README").write_text("x")
        g("add", "README"); g("commit", "-qm", "a")
        for n in ("2026-11-02-live-1", "2026-11-02-rehearsal", "goose/2026-11-02-live", "goose/2026-11-02-rehearsal"):
            (self.tmp / "findings" / n).mkdir(parents=True)
            (self.tmp / "findings" / n / "run.json").write_text("{}")
        g("add", "findings"); g("commit", "-qm", "b")
        old = live_alerts.ROOT
        live_alerts.ROOT = self.tmp
        try:
            got = live_alerts.changed_dirs("0000000000000000000000000000000000000000", "HEAD")
            forced = live_alerts.changed_dirs("1" * 40, "HEAD")
        finally:
            live_alerts.ROOT = old
        self.assertEqual([p.relative_to(self.tmp / "findings").as_posix() for p in got], ["2026-11-02-live-1", "goose/2026-11-02-live"])
        self.assertEqual([p.relative_to(self.tmp / "findings").as_posix() for p in forced], ["2026-11-02-live-1", "goose/2026-11-02-live"])


if __name__ == "__main__":
    unittest.main()
