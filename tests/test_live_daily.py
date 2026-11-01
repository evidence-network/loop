"""scripts/live_daily.sh (evidence-network/loop#308): the one unattended live run on the lab host.

Each test runs the script in a throwaway clone of a copy of this repository's code (no findings/), whose origin is a
bare repository it was pushed to, so HEAD is origin/main. A stand-in lab.sh records what it was asked, with which
window, and writes a finding per step; stand-in steps 10 and 11 do the same. Nothing here starts a node, pays or
reaches a network. Run: python3 -B -m unittest discover -s tests -v
"""
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# What the script and the checks it runs read: everything but the findings and the lab.
COPY = ["scripts", "steps", "status", "baseline", "population", "versions.lock", "nodes"]

WRITE_FINDING = r'''
import json, os, sys
sys.path.insert(0, os.environ["STUB_ROOT"] + "/steps")
import finding
step_dir, result = sys.argv[1:3]
os.makedirs(step_dir, exist_ok=True)
nc = [{"what": "stub", "why": "stub", "issue": "evidence-network/loop#193"}] if result == "NOT_CHECKED" else []
open(os.path.join(step_dir, "proof.txt"), "w").write("stub proof\n")
finding.write(step_dir, result=result, population="live", asserts="stub " + os.path.basename(step_dir), pinned=[],
              proof=["proof.txt"], detail="stub", not_checked=nc, started="2026-11-01T00:00:00Z")
'''

STUB_LAB = r'''#!/usr/bin/env bash
# A stand-in lab.sh: logs each call with the window it saw, and writes the findings the real subcommand would.
set -u
cmd=$1
pop=$(python3 -c 'import json; print(json.load(open("population/live/population.json")).get("window", {}).get("from", "-"))')
echo "$cmd since=${LOOP_SEALED_SINCE:--} window=$pop out=${LAB_OUT:--}" >> "$STUB_LOG"
w() { python3 -B "$STUB_ROOT/write_finding.py" "$LAB_OUT/$1" "$2"; }
case "$cmd" in
  check) exit "${STUB_CHECK_RC:-0}" ;;
  running) exit "${STUB_RUNNING_RC:-0}" ;;
  exchange)
    mkdir -p "$LAB_DIR/exchange"
    printf 'HTTP/1.1 %s\r\nx-mesh-served-by: x\r\n' "${STUB_PAID:-200 OK}" > "$LAB_DIR/exchange/lab-2.headers.txt"
    case "${STUB_PAID:-200 OK}" in 200*) ;; *) exit 22 ;; esac ;;
  admit) [ -n "${STUB_HANG:-}" ] && sleep 60; w 04-admit PASS ;;
  deal) w 05-deal PASS ;;
  bundles) ;;
  settle) w 06-settle PASS ;;
  ask) w 07-ask PASS ;;
  dispute) [ -n "${STUB_NO_09:-}" ] && exit 3; w 09-dispute PASS ;;
  loop) for s in 01-seal 02-book 03-advertise 12-join; do w $s PASS; done; w 08-verify NOT_CHECKED ;;
  *) echo "stub lab.sh: unexpected $cmd" >&2; exit 9 ;;
esac
'''

STUB_STEP = r'''#!/usr/bin/env bash
out=$4
echo "$(basename "$0") since=${LOOP_SEALED_SINCE:--}" >> "$STUB_LOG"
python3 -B "$STUB_ROOT/write_finding.py" "$out" PASS
'''


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class LiveDailyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="live-daily-"))
        src = self.tmp / "src"
        src.mkdir()
        for name in COPY:
            (shutil.copytree if (ROOT / name).is_dir() else shutil.copy2)(ROOT / name, src / name)
        git("init", "-q", "-b", "main", cwd=src)
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "add", ".", cwd=src)
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "copy", cwd=src)
        git("clone", "-q", "--bare", str(src), str(self.tmp / "origin.git"), cwd=self.tmp)
        self.clone = self.tmp / "clone"
        git("clone", "-q", str(self.tmp / "origin.git"), str(self.clone), cwd=self.tmp)
        (self.clone / "findings").mkdir()
        self.lab = self.tmp / "lab"
        (self.lab / "run").mkdir(parents=True)
        stubs = self.tmp / "stubs"
        (stubs / "steps").mkdir(parents=True)
        (stubs / "write_finding.py").write_text(WRITE_FINDING.replace('os.environ["STUB_ROOT"] + "/steps"',
                                                                      repr(str(self.clone / "steps"))))
        (stubs / "lab.sh").write_text(STUB_LAB)
        for s in ("10-judge", "11-report"):
            (stubs / "steps" / s).write_text(STUB_STEP)
        for f in [stubs / "lab.sh", stubs / "steps" / "10-judge", stubs / "steps" / "11-report"]:
            f.chmod(0o755)
        self.log = self.tmp / "calls.log"
        self.env = dict(os.environ, LAB_DIR=str(self.lab), LIVE_LAB_SH=str(stubs / "lab.sh"),
                        LIVE_STEPS_DIR=str(stubs / "steps"), LIVE_CHECKPOINT_WAIT="0", STUB_LOG=str(self.log),
                        STUB_ROOT=str(stubs), LIVE_OUT_DIR=str(self.lab / "out"))
        self.env.pop("LOOP_LIVE_ENV", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_daily(self, *args, **env):
        return subprocess.run(["bash", str(self.clone / "scripts" / "live_daily.sh"), *args], cwd=self.clone,
                              env={**self.env, **env}, capture_output=True, text=True, timeout=600)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def day(self):
        return time.strftime("%Y-%m-%d", time.gmtime())

    def assert_untouched(self):
        self.assertFalse((self.lab / "run" / "LIVE_RUN").exists())
        self.assertEqual(git("status", "--porcelain", "--untracked-files=no", cwd=self.clone), "")

    def test_run_writes_findings_pack_and_record(self):
        r = self.run_daily()
        self.assertEqual(r.returncode, 0, r.stderr)
        name = f"{self.day()}-live-1"
        out = self.clone / "findings" / name
        self.assertEqual(len(list(out.glob("*/finding.json"))), 12)
        record = json.loads((self.lab / "out" / f"{name}.json").read_text())
        self.assertTrue(record["exchange"]["paid"])
        self.assertEqual(record["steps"]["06-settle"], "PASS")
        self.assertEqual(record["steps"]["08-verify"], "NOT_CHECKED")
        self.assertEqual(record["head"], git("rev-parse", "HEAD", cwd=self.clone))
        since = record["window_from"]
        self.assertRegex(since, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:00Z$")
        # The window and the cutoff are the run's start for every step after the exchange; the exchange runs once.
        calls = self.calls()
        self.assertEqual([c.split()[0] for c in calls],
                         ["check", "running", "exchange", "admit", "deal", "bundles", "settle", "ask", "dispute",
                          "loop", "10-judge", "11-report"])
        for c in calls[2:10]:
            self.assertIn(f"since={since} window={since} out={out}", c)
        tgz = self.lab / "out" / f"{name}.tgz"
        listing = subprocess.run(["tar", "tzf", str(tgz)], capture_output=True, text=True, check=True).stdout.split()
        self.assertTrue(all(p.startswith(f"findings/{name}") for p in listing), listing[:5])
        self.assertIn(f"{name}.tgz", (self.lab / "out" / f"{name}.tgz.sha256").read_text())
        self.assert_untouched()

    def test_next_name_is_one_past_the_highest_for_the_day(self):
        for d in (f"{self.day()}-live", f"{self.day()}-live-3", "2026-01-01-live-9", f"{self.day()}-demo-7"):
            (self.clone / "findings" / d).mkdir()
        r = self.run_daily("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"findings/{self.day()}-live-4", r.stdout)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.lab / "run" / "LIVE_RUN").exists())

    def test_second_run_the_same_day_takes_n_plus_one(self):
        self.assertEqual(self.run_daily().returncode, 0)
        r = self.run_daily()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.clone / "findings" / f"{self.day()}-live-2" / "06-settle" / "finding.json").is_file())

    def test_refused_payment_is_06_not_checked_with_the_reason_and_no_retry(self):
        r = self.run_daily(STUB_PAID="402 Payment Required")
        self.assertEqual(r.returncode, 8, r.stderr)  # packed; the gate's 06 PASS -> NOT_CHECKED is the refusal
        out = self.clone / "findings" / f"{self.day()}-live-1"
        f = json.loads((out / "06-settle" / "finding.json").read_text())
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("402 Payment Required", f["detail"])
        self.assertIn("402 Payment Required", f["not_checked"][0]["why"])
        names = [c.split()[0] for c in self.calls()]
        self.assertEqual(names.count("exchange"), 1)
        self.assertNotIn("settle", names)
        self.assertNotIn("bundles", names)
        record = json.loads((self.lab / "out" / f"{self.day()}-live-1.json").read_text())
        self.assertFalse(record["exchange"]["paid"])
        self.assert_untouched()

    def test_a_step_that_writes_no_finding_is_a_fail(self):
        r = self.run_daily(STUB_NO_09="1")
        self.assertEqual(r.returncode, 1, r.stderr)  # the gate: 09 PASS -> FAIL against baseline/live.json
        self.assertIn("step 09: PASS -> FAIL", r.stderr)
        f = json.loads((self.clone / "findings" / f"{self.day()}-live-1" / "09-dispute" / "finding.json").read_text())
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("exited 3", f["detail"])

    def test_a_refusal_does_not_hide_another_regression(self):
        r = self.run_daily(STUB_PAID="402 Payment Required", STUB_NO_09="1")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_refuses_when_the_lock_exists(self):
        (self.lab / "run" / "LIVE_RUN").write_text("{}\n")
        r = self.run_daily()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("LIVE_RUN exists", r.stderr)
        self.assertTrue((self.lab / "run" / "LIVE_RUN").exists())  # not ours: left alone
        self.assertEqual(self.calls(), [])

    def test_refuses_on_lab_check_or_a_node_down(self):
        for env in ({"STUB_CHECK_RC": "1"}, {"STUB_RUNNING_RC": "1"}):
            self.log.unlink(missing_ok=True)
            r = self.run_daily(**env)
            self.assertEqual(r.returncode, 5, r.stderr)
            self.assertNotIn("exchange", [c.split()[0] for c in self.calls()])
            self.assertEqual(list((self.clone / "findings").iterdir()), [])
            self.assert_untouched()

    def test_refuses_off_origin_main_or_with_tracked_changes(self):
        (self.clone / "versions.lock").write_text((self.clone / "versions.lock").read_text() + "# local\n")
        r = self.run_daily()
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("tracked changes", r.stderr)
        git("checkout", "-q", "--", "versions.lock", cwd=self.clone)
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "x",
            cwd=self.clone)
        r = self.run_daily()
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("is not origin/main", r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.lab / "run" / "LIVE_RUN").exists())

    def test_privacy_hit_holds_the_findings_and_packs_nothing(self):
        (self.lab / "run" / "invite.token").write_text("stub-invite-token-0123456789\n")
        # The stand-in 10-judge leaks the token into its proof.
        step = Path(self.env["LIVE_STEPS_DIR"]) / "10-judge"
        step.write_text(STUB_STEP + 'echo "token stub-invite-token-0123456789" > "$out/leak.txt"\n')
        r = self.run_daily()
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertNotIn("stub-invite-token", r.stderr + r.stdout)
        name = f"{self.day()}-live-1"
        self.assertFalse((self.clone / "findings" / name).exists())
        self.assertTrue((self.lab / "held" / name / "10-judge" / "leak.txt").is_file())
        self.assertFalse((self.lab / "out" / f"{name}.tgz").exists())
        self.assert_untouched()

    def test_killed_mid_run_removes_the_lock_and_restores_the_population(self):
        p = subprocess.Popen(["bash", str(self.clone / "scripts" / "live_daily.sh")], cwd=self.clone,
                             env={**self.env, "STUB_HANG": "1"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lock = self.lab / "run" / "LIVE_RUN"
        deadline = time.time() + 120
        while not any(c.startswith("admit") for c in self.calls()) and time.time() < deadline:
            time.sleep(0.2)
        self.assertTrue(lock.exists())
        self.assertNotEqual(git("status", "--porcelain", "--untracked-files=no", cwd=self.clone), "")
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=30)
        self.assertEqual(p.returncode, 143)
        self.assert_untouched()

    def test_dry_run_changes_nothing(self):
        r = self.run_daily("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("exchange, once", r.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual(list((self.clone / "findings").iterdir()), [])
        self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
