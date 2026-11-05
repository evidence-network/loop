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
    def run_ci(self, call, gcloud_exit, gcloud_out, gh_fail=()):
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
                   "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r"}
            p = subprocess.run(["bash", "-c", f"source {CI} && {call}"], env=env, capture_output=True, text=True, cwd=d)
            envf = d / "env"
            state = [l for l in envf.read_text().splitlines() if l.startswith("LIVE_STATE=")][-1] if envf.exists() else ""
            ghlog = (d / "gh.log").read_text() if (d / "gh.log").exists() else ""
            return p.returncode, state, ghlog

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
        (d / "conf").write_text(f"LIVE_HOME={self.home}\nLIVE_ENV={d}/env.sh\nLAB_DIR={self.lab}\n")
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
