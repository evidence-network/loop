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
echo "$cmd since=${LOOP_SEALED_SINCE:--} window=$pop out=${LAB_OUT:--} tools=${LOOP_TOOLS:--}" >> "$STUB_LOG"
w() { python3 -B "$STUB_ROOT/write_finding.py" "$LAB_OUT/$1" "$2"; }
case "$cmd" in
  check) exit "${STUB_CHECK_RC:-0}" ;;
  running) exit "${STUB_RUNNING_RC:-0}" ;;
  tools) exit "${STUB_TOOLS_RC:-0}" ;;
  exchange)
    mkdir -p "$LAB_DIR/exchange"
    printf 'HTTP/1.1 %s\r\nx-mesh-served-by: x\r\n' "${STUB_PAID:-200 OK}" > "$LAB_DIR/exchange/lab-2.headers.txt"
    printf 'HTTP/1.1 402 Payment Required\r\n' > "$LAB_DIR/exchange/lab-3.headers.txt"
    case "${STUB_PAID:-200 OK}" in 200*) ;; *) exit 22 ;; esac ;;
  admit) [ -n "${STUB_HANG:-}" ] && sleep 60; w 04-admit PASS ;;
  deal) w 05-deal PASS ;;
  bundles) ;;
  settle) w 06-settle PASS ;;
  dispute) [ -n "${STUB_NO_09:-}" ] && exit 3; w 09-dispute PASS ;;
  loop) for s in 01-seal 02-book 03-advertise 12-join 07-ask; do w $s PASS; done; w 08-verify NOT_CHECKED ;;
  *) echo "stub lab.sh: unexpected $cmd" >&2; exit 9 ;;
esac
'''

STUB_GOOSE = r'''#!/usr/bin/env bash
# A stand-in goose.sh: logs the call with the Mesh window it must no longer see, and writes the findings pay would.
set -u
cmd=$1
pop=$(python3 -c 'import json; print(json.load(open("population/live/population.json")).get("window", {}).get("from", "-"))')
echo "goose-$cmd since=${LOOP_SEALED_SINCE:--} window=$pop in_live_daily=${GOOSE_IN_LIVE_DAILY:--} out=${LAB_OUT:--}" >> "$STUB_LOG"
w() { python3 -B "$STUB_ROOT/write_finding.py" "$LAB_OUT/$1" "$2"; }
case "$cmd" in
  pay)
    [ -n "${STUB_GOOSE_HANG:-}" ] && sleep 60
    [ -n "${STUB_GOOSE_REFUSED:-}" ] && echo "goose_live: payment refused: lab-1 has 1 pending payment(s): the lab is not idle" >&2
    [ -n "${STUB_GOOSE_NONE:-}" ] && exit 2
    for s in 01-seal 02-book 03-advertise 07-ask; do w $s PASS; done
    w 06-settle "${STUB_GOOSE_06:-PASS}"
    [ -n "${STUB_GOOSE_LEAK:-}" ] && echo "token stub-invite-token-0123456789" > "$LAB_OUT/06-settle/leak.txt"
    exit "${STUB_GOOSE_RC:-0}" ;;
  *) echo "stub goose.sh: unexpected $cmd" >&2; exit 9 ;;
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
        (stubs / "goose.sh").write_text(STUB_GOOSE)
        (stubs / "goose.sh").chmod(0o755)
        for s in ("10-judge", "11-report"):
            (stubs / "steps" / s).write_text(STUB_STEP)
        (stubs / "circulate").write_text('#!/usr/bin/env bash\necho "circulate" >> "$STUB_LOG"\n'
                                         'echo \'{"action": "sent", "amount_msat": 1500}\'\n')
        (stubs / "circulate").chmod(0o755)
        for f in [stubs / "lab.sh", stubs / "steps" / "10-judge", stubs / "steps" / "11-report"]:
            f.chmod(0o755)
        self.log = self.tmp / "calls.log"
        self.env = dict(os.environ, LAB_DIR=str(self.lab), LIVE_LAB_SH=str(stubs / "lab.sh"), LIVE_GOOSE_SH=str(stubs / "goose.sh"),
                        LIVE_STEPS_DIR=str(stubs / "steps"), LIVE_CHECKPOINT_WAIT="0", STUB_LOG=str(self.log),
                        STUB_ROOT=str(stubs), LIVE_OUT_DIR=str(self.lab / "out"),
                        LIVE_TOOLS=str(self.lab / "tools"), LIVE_CIRCULATE=str(stubs / "circulate"))
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
        # Untracked files count: a run leaves no findings in the clone, or the next run's clone is dirty.
        self.assertEqual(git("status", "--porcelain", cwd=self.clone), "")

    def packed(self, name):
        """The run's findings directory, unpacked from its tgz into a fresh directory."""
        dest = Path(tempfile.mkdtemp(dir=self.tmp))
        subprocess.run(["tar", "xzf", str(self.lab / "out" / f"{name}.tgz"), "-C", str(dest)], check=True)
        return dest / "findings" / name

    def packed_goose(self, name, gname):
        dest = Path(tempfile.mkdtemp(dir=self.tmp))
        subprocess.run(["tar", "xzf", str(self.lab / "out" / f"{name}.tgz"), "-C", str(dest)], check=True)
        return dest / "findings" / "goose" / gname

    def record(self, name):
        return json.loads((self.lab / "out" / f"{name}.json").read_text())

    def test_a_goose_pass_that_fails_changes_nothing_in_the_mesh_run(self):
        mesh = self.run_daily()
        self.assertEqual(mesh.returncode, 0, mesh.stderr)
        base = self.record(f"{self.day()}-live-1")
        for f in (self.lab / "out").glob("*"):
            f.unlink()
        for env, status in (({"STUB_GOOSE_HANG": "1", "LIVE_GOOSE_TIMEOUT": "2"}, "failed"), ({"STUB_GOOSE_NONE": "1"}, "failed"),
                           ({"LIVE_DEADLINE": "400"}, "skipped")):
            r = self.run_daily(**env)
            self.assertEqual(r.returncode, 0, (env, r.stderr))
            name = f"{self.day()}-live-1"
            rec = self.record(name)
            self.assertEqual(rec["goose"]["status"], status, env)
            self.assertIsNone(rec["goose"]["name"])
            self.assertEqual(rec["steps"], base["steps"])
            self.assertEqual(rec["gate_check_rc"], base["gate_check_rc"])
            listing = subprocess.run(["tar", "tzf", str(self.lab / "out" / f"{name}.tgz")], capture_output=True, text=True,
                                     check=True).stdout.split()
            self.assertFalse(any("goose" in p for p in listing), listing[:3])
            self.assert_untouched()
            for f in (self.lab / "out").glob("*"):
                f.unlink()

    def test_a_refused_goose_payment_is_packed_with_06_not_checked(self):
        r = self.run_daily(STUB_GOOSE_06="NOT_CHECKED", STUB_GOOSE_RC="1", STUB_GOOSE_REFUSED="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = self.record(f"{self.day()}-live-1")
        self.assertEqual(rec["goose"]["reason"], "payment refused: lab-1 has 1 pending payment(s): the lab is not idle")
        self.assertNotIn(str(self.lab), json.dumps(rec))  # the published record names no path of the lab host
        self.assertEqual(rec["goose"]["step_06"], "NOT_CHECKED")
        self.assertEqual(rec["goose"]["exit"], 1)
        self.assertEqual(rec["goose"]["status"], "ran")

    def test_a_member_the_harvest_would_refuse_keeps_the_goose_findings_out_of_the_pack(self):
        for link in ('ln -s /etc/hosts "$LAB_OUT/06-settle/link"', 'ln "$LAB_OUT/06-settle/proof.txt" "$LAB_OUT/01-seal/hard"'):
            self.refused_member(link)
            for f in (self.lab / "out").glob("*"):
                if f.is_file():
                    f.unlink()
            shutil.rmtree(self.lab / "out" / "unpacked", ignore_errors=True)

    def refused_member(self, link):
        gl = Path(self.env["LIVE_GOOSE_SH"])
        gl.write_text(STUB_GOOSE.replace("exit \"${STUB_GOOSE_RC:-0}\"", link + '\n    exit "${STUB_GOOSE_RC:-0}"'))
        r = self.run_daily()
        self.assertEqual(r.returncode, 0, r.stderr)
        name = f"{self.day()}-live-1"
        rec = self.record(name)
        self.assertEqual(rec["goose"]["status"], "refused")
        self.assertNotIn(str(self.lab), json.dumps(rec))
        self.assertTrue((self.lab / "out" / "unpacked" / f"goose-{self.day()}-live").is_dir())
        listing = subprocess.run(["tar", "tzf", str(self.lab / "out" / f"{name}.tgz")], capture_output=True, text=True, check=True).stdout
        self.assertNotIn("goose", listing)
        self.assert_untouched()

    def test_a_privacy_hit_in_the_goose_findings_holds_only_them(self):
        (self.lab / "run" / "invite.token").write_text("stub-invite-token-0123456789\n")
        r = self.run_daily(STUB_GOOSE_LEAK="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("stub-invite-token", r.stderr + r.stdout)
        name = f"{self.day()}-live-1"
        rec = self.record(name)
        self.assertEqual(rec["goose"]["status"], "held")
        self.assertTrue((self.lab / "held" / f"goose-{self.day()}-live" / "06-settle" / "leak.txt").is_file())
        self.assertEqual(len(list(self.packed(name).glob("*/finding.json"))), 12)
        self.assert_untouched()

    def test_a_second_run_the_same_day_does_not_take_the_goose_name_again(self):
        self.assertEqual(self.run_daily().returncode, 0)
        r = self.run_daily()
        self.assertEqual(r.returncode, 0, r.stderr)
        second = self.record(f"{self.day()}-live-2")
        self.assertEqual(second["goose"]["status"], "skipped")
        self.assertIn("already taken", second["goose"]["reason"])
        self.assertEqual(self.calls().count(f"goose-pay since=- window=- in_live_daily=1 out={self.clone / 'findings' / 'goose' / (self.day() + '-live')}"), 1)

    def test_a_top_up_that_fails_or_prints_nonsense_is_recorded_unknown_and_changes_nothing_else(self):
        for body in ("exit 7", "echo '{bad}'", "echo 'not json'"):
            circ = Path(self.env["LIVE_CIRCULATE"])
            circ.write_text(f"#!/usr/bin/env bash\n{body}\n")
            r = self.run_daily()
            self.assertEqual(r.returncode, 0, (body, r.stderr))
            record = json.loads(sorted((self.lab / "out").glob("*.json"))[-1].read_text())
            self.assertEqual(record["circulate"]["action"], "unknown", body)
            self.assertEqual(record["steps"]["06-settle"], "PASS")
            for f in (self.lab / "out").glob("*"):
                f.unlink()

    def test_run_writes_findings_pack_and_record(self):
        r = self.run_daily()
        self.assertEqual(r.returncode, 0, r.stderr)
        name = f"{self.day()}-live-1"
        out = self.clone / "findings" / name
        self.assertEqual(len(list(self.packed(name).glob("*/finding.json"))), 12)
        self.assertFalse(out.exists())
        record = json.loads((self.lab / "out" / f"{name}.json").read_text())
        self.assertTrue(record["exchange"]["paid"])
        # Each asker's status is recorded, lab-3's 402 included, and the top-up runs once after the steps (loop#384).
        self.assertEqual(record["exchange"]["askers"], {"lab-2": "HTTP/1.1 200 OK", "lab-3": "HTTP/1.1 402 Payment Required"})
        self.assertEqual(record["circulate"], {"action": "sent", "amount_msat": 1500})
        self.assertEqual(self.calls().count("circulate"), 1)
        self.assertEqual(record["steps"]["06-settle"], "PASS")
        self.assertEqual(record["steps"]["08-verify"], "NOT_CHECKED")
        self.assertEqual(record["head"], git("rev-parse", "HEAD", cwd=self.clone))
        since = record["window_from"]
        self.assertRegex(since, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:00Z$")
        # The window and the cutoff are the run's start for every step after the exchange; the exchange runs once.
        calls = self.calls()
        self.assertEqual([c.split()[0] for c in calls],
                         ["tools", "check", "running", "exchange", "admit", "deal", "bundles", "settle", "dispute",
                          "loop", "10-judge", "11-report", "circulate", "goose-pay"])
        self.assertIn(f"tools={self.lab / 'tools'}", calls[0])  # the run's own tools, first: check reads their record
        for c in calls[3:10]:
            self.assertIn(f"since={since} window={since} out={out} tools=", c)
        # The Goose pass is last and sees neither the Mesh window nor the Mesh cutoff; it runs under this run's lock.
        gname = f"{self.day()}-live"
        self.assertEqual(calls[-1], f"goose-pay since=- window=- in_live_daily=1 out={self.clone / 'findings' / 'goose' / gname}")
        self.assertEqual(record["goose"], {"status": "ran", "name": gname, "reason": None, "exit": 0, "step_06": "PASS"})
        goose = self.packed_goose(name, gname)
        self.assertEqual(sorted(d.name for d in goose.iterdir() if (d / "finding.json").is_file()),
                         ["01-seal", "02-book", "03-advertise", "06-settle", "07-ask"])
        self.assertEqual(json.loads((goose / "06-settle" / "finding.json").read_text())["result"], "PASS")
        tgz = self.lab / "out" / f"{name}.tgz"
        listing = subprocess.run(["tar", "tzf", str(tgz)], capture_output=True, text=True, check=True).stdout.split()
        self.assertTrue(all(p.startswith((f"findings/{name}", f"findings/goose/{gname}")) for p in listing), listing[:5])
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

    def test_a_tgz_without_its_record_keeps_its_n(self):
        (self.lab / "out").mkdir()
        (self.lab / "out" / f"{self.day()}-live-2.tgz").write_text("x")
        r = self.run_daily("--dry-run")
        self.assertIn(f"findings/{self.day()}-live-3", r.stdout)

    def test_held_and_packed_runs_keep_their_n(self):
        (self.lab / "held" / f"{self.day()}-live-1").mkdir(parents=True)
        (self.lab / "out").mkdir()
        (self.lab / "out" / f"{self.day()}-live-2.json").write_text("{}\n")
        r = self.run_daily("--dry-run")
        self.assertIn(f"findings/{self.day()}-live-3", r.stdout)

    def test_a_process_that_only_names_lab_sh_is_not_a_run(self):
        decoy = subprocess.Popen(["bash", "-c", 'exec -a "vim lab/lab.sh loop.sh" sleep 60'])
        try:
            r = self.run_daily()
        finally:
            decoy.kill()
            decoy.wait()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_refuses_while_a_lab_sh_runs(self):
        busy = self.tmp / "busy" / "lab"
        busy.mkdir(parents=True)
        (busy / "lab.sh").write_text("sleep 60\n")
        for argv in (["bash", str(busy / "lab.sh"), "exchange"], ["bash", "-x", "-o", "pipefail", str(busy / "lab.sh")]):
            other = subprocess.Popen(argv, stderr=subprocess.DEVNULL)
            try:
                r = self.run_daily()
            finally:
                other.kill()
                other.wait()
            self.assertEqual(r.returncode, 3, (argv, r.stderr))
            self.assertIn("another lab run is in progress", r.stderr)
            self.assertEqual(self.calls(), [])
            self.assert_untouched()

    def test_second_run_the_same_day_takes_n_plus_one(self):
        self.assertEqual(self.run_daily().returncode, 0)
        r = self.run_daily()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.packed(f"{self.day()}-live-2") / "06-settle" / "finding.json").is_file())

    def test_refused_payment_is_06_not_checked_with_the_reason_and_no_retry(self):
        r = self.run_daily(STUB_PAID="402 Payment Required")
        self.assertEqual(r.returncode, 8, r.stderr)  # packed; the gate's 06 PASS -> NOT_CHECKED is the refusal
        out = self.packed(f"{self.day()}-live-1")
        f = json.loads((out / "06-settle" / "finding.json").read_text())
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("402 Payment Required", f["detail"])
        self.assertIn("402 Payment Required", f["not_checked"][0]["why"])
        self.assertEqual(f["not_checked"][0]["issue"], "evidence-network/loop#321")
        self.assertNotIn("wallet", f["detail"] + f["not_checked"][0]["why"])  # only what was seen
        self.assertIn("a payment may still have settled", f["not_checked"][0]["why"])
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
        f = json.loads((self.packed(f"{self.day()}-live-1") / "09-dispute" / "finding.json").read_text())
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("exited 3", f["detail"])

    def test_a_step_over_its_time_is_killed_and_a_fail(self):
        r = self.run_daily(STUB_HANG="1", LIVE_STEP_TIMEOUT="2")
        self.assertEqual(r.returncode, 1, r.stderr)  # 04 PASS -> FAIL against the baseline
        f = json.loads((self.packed(f"{self.day()}-live-1") / "04-admit" / "finding.json").read_text())
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("exited 124", f["detail"])

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
        for env in ({"STUB_CHECK_RC": "1"}, {"STUB_RUNNING_RC": "1"}, {"STUB_TOOLS_RC": "1"}):
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

    def test_a_run_that_stops_before_packing_leaves_its_findings_outside_the_clone(self):
        step = Path(self.env["LIVE_STEPS_DIR"]) / "11-report"
        step.write_text(STUB_STEP + 'echo "{" > "$out/finding.json"\n')  # finding.py check refuses a malformed finding
        r = self.run_daily()
        self.assertEqual(r.returncode, 6, r.stderr)
        name = f"{self.day()}-live-1"
        self.assertTrue((self.lab / "out" / "unpacked" / name / "10-judge" / "finding.json").is_file())
        self.assertFalse((self.lab / "out" / f"{name}.tgz").exists())
        self.assertEqual((self.lab / "out" / "unpacked").stat().st_mode & 0o777, 0o700)  # may be unscanned
        self.assert_untouched()
        # Its N stays taken, and the next run starts from the clean clone.
        self.assertIn(f"findings/{self.day()}-live-2", self.run_daily("--dry-run").stdout)

    def test_dry_run_changes_nothing(self):
        r = self.run_daily("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("exchange, once", r.stdout)
        self.assertIn(f"findings/goose/{self.day()}-live", r.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual(list((self.clone / "findings").iterdir()), [])
        self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
