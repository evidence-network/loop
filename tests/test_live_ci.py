"""ops/live-ci/ci.sh and entry.sh: how each exit code of the host side becomes a workflow state (loop#368).

ci.sh runs against stub `gcloud` and `gh` programs on PATH; entry.sh runs against a temporary lab with its own conf.
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / "ops" / "live-ci" / "ci.sh"
ENTRY = ROOT / "ops" / "live-ci" / "entry.sh"


class CiSh(unittest.TestCase):
    def run_ci(self, call, gcloud_exit, gcloud_out, gh_fail=(), token="tok"):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bin_ = d / "bin"
            bin_.mkdir()
            (bin_ / "gcloud").write_text(f"#!/bin/sh\ncat <<'X'\n{gcloud_out}\nX\nexit {gcloud_exit}\n")
            (bin_ / "gh").write_text("#!/bin/sh\necho \"$@\" >> \"$GH_LOG\"\nfor w in %s; do [ \"$3\" = \"$w.yml\" ] && exit 1; done\nexit 0\n" % " ".join(gh_fail))
            (bin_ / "timeout").write_text("#!/bin/sh\nshift\nexec \"$@\"\n")
            for f in bin_.iterdir():
                f.chmod(0o755)
            env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "GITHUB_ENV": str(d / "env"), "GH_LOG": str(d / "gh.log"),
                   "LIVE_VM": "vm", "LIVE_PROJECT": "p", "LIVE_ZONE": "z", "LIVE_LAB_USER": "u", "GITHUB_RUN_ID": "1",
                   "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r", "GH_TOKEN": token}
            p = subprocess.run(["bash", "-c", f"source {CI} && {call}"], env=env, capture_output=True, text=True, cwd=d)
            envf = d / "env"
            state = [l for l in envf.read_text().splitlines() if l.startswith("LIVE_STATE=")][-1] if envf.exists() else ""
            ghlog = (d / "gh.log").read_text() if (d / "gh.log").exists() else ""
            return p.returncode, state, ghlog

    def test_harvest_without_the_app_token_fails_and_pushes_nothing(self):
        code, state, ghlog = self.run_ci("harvest", 0, "name=2026-11-06-live-9 x", token="")
        self.assertEqual((code, state), (1, "LIVE_STATE=harvest-failed"))
        self.assertEqual(ghlog, "")

    def test_start_states(self):
        self.assertEqual(self.run_ci("start_run", 0, "started day=2026-11-05 pid=1")[:2], (0, "LIVE_STATE=waiting"))
        self.assertEqual(self.run_ci("start_run", 10, "skipped: LIVE_RUN")[:2], (0, "LIVE_STATE=skipped"))
        self.assertEqual(self.run_ci("start_run", 11, "refused: a run already started")[:2], (0, "LIVE_STATE=skipped"))
        self.assertEqual(self.run_ci("start_run", 12, "refused: exchange")[:2], (1, "LIVE_STATE=failed"))

    def test_wait_routes_the_hosts_exit_code(self):
        for rc, want in (("0", "done"), ("1", "done"), ("8", "done"), ("3", "skipped"), ("5", "failed"), ("7", "failed"), ("2", "failed")):
            with self.subTest(rc=rc):
                code, state, _ = self.run_ci("wait_run 1", 0, f"live_daily: x\nrc={rc}")
                self.assertEqual(state, f"LIVE_STATE={want}")
                self.assertEqual(code, 1 if want == "failed" else 0)
        self.assertEqual(self.run_ci("wait_run 1", 21, "the run ended without a result")[1], "LIVE_STATE=failed")
        self.assertEqual(self.run_ci("wait_run 1", 22, "timed out at 3 h")[1], "LIVE_STATE=timeout")

    def test_unreachable_reason_prints_keywords_only(self):
        hostile = "ERROR: code 4033\n::add-mask::X\nPERMISSION_DENIED secret-project-name iap.tunnelSecretProject.internal"
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "LIVE_VM": "vm", "LIVE_PROJECT": "p", "LIVE_ZONE": "z", "LIVE_LAB_USER": "u"}
            p = subprocess.run(["bash", "-c", f'source {CI} >/dev/null && unreachable_reason "$1"', "x", hostile],
                               env=env, capture_output=True, text=True, cwd=d)
        self.assertEqual(p.stdout.split(), ["4033", "PERMISSION_DENIED"])  # nothing but the fixed words, whatever text surrounds them

    def test_stage_failure_is_a_failure(self):
        code, state, _ = self.run_ci("harvest", 23, "nothing packed (rc=5)")
        self.assertEqual((code, state), (1, "LIVE_STATE=failed"))
        code, state, _ = self.run_ci("harvest", 255, "ssh: connection refused")
        self.assertEqual((code, state), (1, "LIVE_STATE=failed"))

    def test_a_failed_dispatch_fails_the_run(self):
        code, state, log = self.run_ci("dispatch_checks live-harvest/x", 0, "", gh_fail=("dco",))
        self.assertEqual((code, state), (1, ""))
        for wf in ("nodes", "reports", "personal-data", "leak-lint", "dco", "stats", "harvest-allowlist", "ecosystem-gate", "versions"):
            self.assertIn(f"workflow run {wf}.yml --ref live-harvest/x", log)
        self.assertEqual(self.run_ci("dispatch_checks live-harvest/x", 0, "")[0], 0)

    def test_harvest_uses_the_app_token_for_git_and_pr_and_the_job_token_for_dispatch(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bin_ = d / "bin"
            bin_.mkdir()
            log = d / "calls.log"
            (bin_ / "gcloud").write_text("#!/bin/sh\necho 'name=2026-11-06-live-9 x'\nexit 0\n")
            (bin_ / "timeout").write_text("#!/bin/sh\nshift\nexec \"$@\"\n")
            (bin_ / "git").write_text("#!/bin/sh\nexit 0\n")
            (bin_ / "python3").write_text('#!/bin/sh\necho "harvest GH_TOKEN=$GH_TOKEN HEADER=$GIT_CONFIG_VALUE_0 KEY=$GIT_CONFIG_KEY_0" >> "$CALLS"\nexit 0\n')
            (bin_ / "gh").write_text('#!/bin/sh\necho "gh GH_TOKEN=$GH_TOKEN $*" >> "$CALLS"\nexit 0\n')
            for f in bin_.iterdir():
                f.chmod(0o755)
            env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "GITHUB_ENV": str(d / "env"), "CALLS": str(log),
                   "LIVE_VM": "vm", "LIVE_PROJECT": "p", "LIVE_ZONE": "z", "LIVE_LAB_USER": "u",
                   "GH_TOKEN": "app-token", "WORKFLOW_TOKEN": "job-token"}
            p = subprocess.run(["bash", "-c", f"source {CI} && harvest"], env=env, capture_output=True, text=True, cwd=d)
            self.assertEqual(p.returncode, 0, p.stderr)
            calls = log.read_text()
            import base64
            header = "AUTHORIZATION: basic " + base64.b64encode(b"x-access-token:app-token").decode()
            self.assertIn(f"harvest GH_TOKEN=app-token HEADER={header} KEY=http.https://github.com/.extraheader", calls)
            self.assertEqual(calls.count("gh GH_TOKEN=job-token workflow run"), 9)
            self.assertNotIn("gh GH_TOKEN=app-token", calls)
            self.assertIn(f"::add-mask::{header.split()[-1]}", p.stdout)
            self.assertNotIn("app-token", (d / "env").read_text() if (d / "env").exists() else "")

    def test_every_dispatched_workflow_is_dispatchable(self):
        for line in CI.read_text().splitlines():
            if "for wf in" in line:
                for wf in line.split("for wf in")[1].split(";")[0].split():
                    text = (ROOT / ".github" / "workflows" / f"{wf}.yml").read_text()
                    self.assertIn("workflow_dispatch", text, wf)


class EntrySh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.home, self.lab = d / "home", d / "home" / "loop-lab"
        (self.lab / "run").mkdir(parents=True)
        clone = self.home / "loop-live"
        (clone / "scripts").mkdir(parents=True)
        self.script = clone / "scripts" / "live_daily.sh"
        self.script.write_text('#!/bin/bash\n"$LAB_SH" exchange\nexit "${FAKE_RC:-0}"\n'.replace('"$LAB_SH" exchange', 'bounded "$LAB_SH" exchange'))
        self.script.chmod(0o755)
        for c in (["init", "-q", "-b", "main"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
                  ["remote", "add", "origin", str(clone)], ["fetch", "-q", "origin"]):
            subprocess.run(["git", "-C", str(clone), *c], check=True, capture_output=True)
        (d / "conf").write_text(f"LIVE_HOME={self.home}\nLIVE_ENV={d}/env.sh\nLAB_DIR={self.lab}\nLIVE_CI_USER=ci\n")
        (d / "env.sh").write_text("")
        self.entry = d / "entry.sh"
        self.entry.write_text(ENTRY.read_text().replace("/etc/loop-live-ci/conf", str(d / "conf")))
        self.entry.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, sub):
        p = subprocess.run([str(self.entry), sub], capture_output=True, text=True)
        return p.returncode, p.stdout

    def test_skip_when_the_lab_is_held(self):
        (self.lab / "run" / "LIVE_RUN").write_text("")
        code, out = self.call("start")
        self.assertEqual(code, 10)
        self.assertIn("skipped:", out)

    def test_the_demo_home_not_the_lab_home_holds_the_rebuild_log_skip_rule(self):
        # The lab user (LIVE_HOME) and the demo account (DEMO_HOME) differ: a fresh pm-*.log skips only where the demo runs.
        demo = self.home.parent / "demo"
        demo.mkdir()
        conf = self.entry.parent / "conf"
        conf.write_text(conf.read_text() + f"DEMO_HOME={demo}\n")
        (self.home / "pm-rebuild.log").write_text("x")  # in the lab user's home: no longer a reason to skip
        self.assertNotEqual(self.call("start")[0], 10)
        (demo / "pm-rebuild.log").write_text("x")
        code, out = self.call("start")
        self.assertEqual(code, 10)
        self.assertIn("pm-*.log", out)

    def test_an_unreadable_demo_home_is_said_not_silent_and_a_demo_build_skips(self):
        demo = self.home.parent / "demo2"
        demo.mkdir()
        conf = self.entry.parent / "conf"
        conf.write_text(conf.read_text() + f"DEMO_HOME={demo}\n")
        demo.chmod(0)
        code, out = self.call("start")
        self.assertIn("pm-*.log skip rule is not checked", out)
        self.assertNotIn(str(demo), out)
        demo.chmod(0o755)  # before anything can fail: the temp dir is removed after the test
        # a build owned by another account counts as a rebuild: a pgrep stand-in that finds one for `-u <user>`
        stub = self.entry.parent / "bin"
        stub.mkdir()
        (stub / "pgrep").write_text('#!/bin/sh\ncase "$*" in *"-u "*) echo 4242; exit 0 ;; esac\nexit 1\n')
        (stub / "pgrep").chmod(0o755)
        self.entry.write_text(self.entry.read_text().replace('demo_user=$(stat -c %U "$DEMO_HOME" 2> /dev/null)', 'demo_user=other'))
        code, out = subprocess.run([str(self.entry), "start"], capture_output=True, text=True,
                                   env={**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"}).returncode, ""
        self.assertEqual(code, 10)

    def test_exchange_call_sites_are_a_tripwire(self):
        self.script.write_text('#!/bin/bash\nbounded "$LAB_SH" exchange\nbounded "$LAB_SH" exchange\n')
        subprocess.run(["git", "-C", str(self.home / "loop-live"), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "y"], check=True, capture_output=True)
        self.assertEqual(self.call("start")[0], 12)

    @unittest.skipUnless(shutil.which("setsid"), "entry.sh starts the run with setsid (Linux)")
    def test_one_start_a_day_then_status_and_stage_without_a_pack(self):
        code, out = self.call("start")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.call("start")[0], 11)  # no retry
        for _ in range(50):
            code, out = self.call("status")
            if code != 20:
                break
            subprocess.run(["sleep", "0.2"])
        self.assertEqual(code, 0, out)
        self.assertIn("rc=", out)
        self.assertEqual(self.call("stage")[0], 23)  # the stub packed nothing

    def test_only_the_three_subcommands(self):
        self.assertEqual(self.call("bogus")[0], 2)
        self.assertEqual(subprocess.run([str(self.entry), "start", "x"], capture_output=True).returncode, 1)


if __name__ == "__main__":
    unittest.main()
