"""scripts/live_harvest.py and .github/harvest_allowlist.py (evidence-network/loop#309): a live run becomes a data-only
pull request. Each test runs the script in a clone of a throwaway origin that holds a copy of this repository's code and
a baseline; `gh` is a stand-in that records what it was asked. Nothing reaches a network.
Run: python3 -B -m unittest tests.test_live_harvest -v
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402

COPY = ["scripts", "steps", "status", "baseline", "population", "versions.lock", "nodes", ".github"]
NAME = "2026-11-01-live-1"
STEPS = {"04-admit": "PASS", "05-deal": "PASS", "06-settle": "PASS", "07-ask": "PASS", "08-verify": "NOT_CHECKED"}
GH = r'''#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
case "$1 $2" in
  "release view") [ -n "${STUB_RELEASE_EXISTS:-}" ] ;;
  "pr create") echo "https://github.com/evidence-network/loop/pull/9999" ;;
  *) ;;
esac
'''


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def write_run(out, results, plant=None):
    for step, result in results.items():
        d = out / step
        d.mkdir(parents=True)
        (d / "proof.txt").write_text(f"proof of {step} {NAME}\n" + (plant or "" if step == "05-deal" else ""))
        nc = [{"what": "x", "why": "x", "issue": "evidence-network/loop#193"}] if result == "NOT_CHECKED" else []
        finding.write(d, result=result, population="live", asserts=f"stub {step}", pinned=[], proof=["proof.txt"],
                      detail="stub", not_checked=nc, started="2026-11-01T00:00:00Z")
    (out / "run.json").write_text("{}\n")


class HarvestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="live-harvest-test-"))
        src = self.tmp / "src"
        src.mkdir()
        for n in COPY:
            (shutil.copytree if (ROOT / n).is_dir() else shutil.copy2)(ROOT / n, src / n)
        (src / "baseline" / "live.json").unlink()
        prior = src / "findings" / "2026-10-01-live-1"
        write_run(prior, STEPS)
        subprocess.run([sys.executable, "-B", str(src / "scripts" / "ecosystem_gate.py"), "update", "--ecosystem", "mesh",
                        "--findings", str(prior)], check=True, cwd=src, capture_output=True)
        git("init", "-q", "-b", "main", cwd=src)
        git("add", ".", cwd=src)
        git("commit", "-q", "-m", "base", cwd=src)
        git("clone", "-q", "--bare", str(src), str(self.tmp / "origin.git"), cwd=self.tmp)
        self.clone = self.tmp / "clone"
        git("clone", "-q", str(self.tmp / "origin.git"), str(self.clone), cwd=self.tmp)
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        (bin_ / "gh").write_text(GH)
        (bin_ / "gh").chmod(0o755)
        self.log = self.tmp / "gh.log"
        self.env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", STUB_LOG=str(self.log))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_run(self, results=STEPS, paid=True, plant=None, extra_member=None):
        run_dir = self.tmp / "run"
        run_dir.mkdir(exist_ok=True)
        full = self.tmp / "pack" / "findings" / NAME
        write_run(full, results, plant)
        tgz = run_dir / f"{NAME}.tgz"
        with tarfile.open(tgz, "w:gz") as t:
            t.add(full, arcname=f"findings/{NAME}")
            if extra_member:
                p = self.tmp / "extra.txt"
                p.write_text("x\n")
                t.add(p, arcname=extra_member)
        (run_dir / f"{NAME}.tgz.sha256").write_text(hashlib.sha256(tgz.read_bytes()).hexdigest() + f"  {NAME}.tgz\n")
        (run_dir / f"{NAME}.json").write_text(json.dumps({"name": NAME, "exchange": {"paid": paid}, "steps": results}))
        return run_dir

    def harvest(self, *extra, **env):
        return subprocess.run([sys.executable, "-B", str(self.clone / "scripts" / "live_harvest.py"), "--from-dir",
                               str(self.tmp / "run"), *extra], cwd=self.clone, env={**self.env, **env},
                              capture_output=True, text=True, timeout=300)

    def gh_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def branches(self):
        return git("branch", "--list", "live-harvest/*", cwd=self.tmp / "origin.git")

    def show(self, path):
        return git("show", f"live-harvest/{NAME}:{path}", cwd=self.tmp / "origin.git")

    def test_clean_run_opens_a_data_only_pull_request(self):
        self.make_run()
        r = self.harvest()
        self.assertEqual(r.returncode, 0, r.stderr)
        files = git("ls-tree", "-r", "--name-only", f"live-harvest/{NAME}", "findings/" + NAME, cwd=self.tmp / "origin.git")
        self.assertEqual(sorted(files.splitlines()), sorted(
            [f"findings/{NAME}/{s}/finding.json" for s in STEPS] + [f"findings/{NAME}/run.json",
                                                                    f"findings/{NAME}/proof-archive.json"]))
        archive = json.loads(self.show(f"findings/{NAME}/proof-archive.json"))
        tgz = self.tmp / "run" / f"{NAME}.tgz"
        self.assertEqual(archive["sha256"], hashlib.sha256(tgz.read_bytes()).hexdigest())
        self.assertTrue(archive["url"].endswith(f"/releases/download/live-{NAME}/{NAME}.tgz"))
        self.assertEqual(json.loads(self.show("baseline/live.json"))["ecosystems"]["mesh"]["findings"], f"findings/{NAME}")
        calls = self.gh_calls()
        self.assertTrue(any(c.startswith("release create live-" + NAME) for c in calls), calls)
        self.assertTrue(any(c.startswith("pr create") for c in calls), calls)
        self.assertFalse(any(c.startswith("pr merge") for c in calls))
        # The commit is signed off, and the digest-only directory passes the checks, the allowlist and the gate's verify.
        self.assertIn("Signed-off-by:", git("log", "-1", "--format=%B", f"live-harvest/{NAME}", cwd=self.tmp / "origin.git"))
        git("fetch", "-q", "origin", f"live-harvest/{NAME}", cwd=self.clone)
        git("checkout", "-q", "FETCH_HEAD", cwd=self.clone)
        self.assertEqual(subprocess.run([sys.executable, "-B", "steps/finding.py", "check", f"findings/{NAME}"],
                                        cwd=self.clone).returncode, 0)
        self.assertEqual(subprocess.run([sys.executable, "-B", "scripts/ecosystem_gate.py", "verify"], cwd=self.clone,
                                        capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run([sys.executable, "-B", ".github/harvest_allowlist.py", "origin/main", "HEAD"],
                                        cwd=self.clone).returncode, 0)
        self.assertEqual(git("worktree", "list", cwd=self.clone).count("\n"), 0)  # the temporary worktree is gone

    def test_planted_preimage_publishes_nothing(self):
        self.make_run(plant="preimage: " + "ab" * 32 + "\n")
        r = self.harvest()
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(self.branches(), "")

    def test_member_outside_the_findings_directory_is_refused(self):
        self.make_run(extra_member="nodes/planted.txt")
        r = self.harvest()
        self.assertEqual(r.returncode, 6, r.stderr)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(self.branches(), "")

    def test_digest_mismatch_is_refused(self):
        run_dir = self.make_run()
        (run_dir / f"{NAME}.tgz.sha256").write_text("0" * 64 + "\n")
        self.assertEqual(self.harvest().returncode, 5)
        self.assertEqual(self.gh_calls(), [])

    def test_regression_commits_findings_but_not_the_baseline(self):
        self.make_run(results={**STEPS, "05-deal": "FAIL"})
        r = self.harvest()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(json.loads(self.show("baseline/live.json"))["ecosystems"]["mesh"]["findings"], "findings/2026-10-01-live-1")
        self.assertIn("proof-archive.json", self.show(f"findings/{NAME}").splitlines())
        self.assertTrue(any(c.startswith("pr create") for c in self.gh_calls()))
        r = self.harvest("--auto-merge")  # the branch exists now, and nothing is armed
        self.assertEqual(r.returncode, 4)
        self.assertFalse(any(c.startswith("pr merge") for c in self.gh_calls()))

    def test_regression_never_arms_auto_merge(self):
        self.make_run(results={**STEPS, "05-deal": "FAIL"})
        r = self.harvest("--auto-merge")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertFalse(any(c.startswith("pr merge") for c in self.gh_calls()))

    def test_refused_payment_leaves_the_baseline(self):
        self.make_run(paid=False)
        r = self.harvest()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(json.loads(self.show("baseline/live.json"))["ecosystems"]["mesh"]["findings"], "findings/2026-10-01-live-1")

    def test_auto_merge_only_with_the_flag(self):
        self.make_run()
        r = self.harvest("--auto-merge")
        self.assertEqual(r.returncode, 0, r.stderr)
        merges = [c for c in self.gh_calls() if c.startswith("pr merge")]
        self.assertEqual(len(merges), 1)
        self.assertIn("--auto", merges[0])
        self.assertNotIn("--admin", merges[0])

    def test_existing_release_or_branch_is_not_overwritten(self):
        self.make_run()
        self.assertEqual(self.harvest(STUB_RELEASE_EXISTS="1").returncode, 4)
        self.assertEqual(self.branches(), "")
        self.assertEqual(self.harvest().returncode, 0)
        self.assertEqual(self.harvest().returncode, 4)


def load_allowlist():
    spec = importlib.util.spec_from_file_location("harvest_allowlist", ROOT / ".github" / "harvest_allowlist.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class AllowlistTest(unittest.TestCase):
    def refused(self, *entries):
        return load_allowlist().refused(b"".join(f"{s}\0{p}\0".encode() for s, p in entries))

    def test_data_only_passes(self):
        self.assertEqual(self.refused(("A", "findings/2026-11-01-live-2/05-deal/finding.json"),
                                      ("A", "findings/2026-11-01-live-2/proof-archive.json"),
                                      ("M", "baseline/live.json")), [])

    def test_planted_extra_file_is_refused(self):
        for entry in (("A", "scripts/evil.py"), ("A", "findings/2026-11-01-demo/x"), ("A", "findings/2026-11-01-live-2"),
                      ("A", "findings/2026-11-01-live-0/x"), ("M", "findings/2026-10-31-live-17/run.json"),
                      ("D", "findings/2026-10-31-live-17/run.json"), ("A", "baseline/live.json"),
                      ("M", ".github/workflows/loop.yml"), ("A", "findings/2026-11-01-live-2/x\n")):
            self.assertEqual(len(self.refused(entry)), 1, entry)

    def test_script_on_a_real_diff(self):
        tmp = Path(tempfile.mkdtemp(prefix="allowlist-"))
        try:
            git("init", "-q", "-b", "main", cwd=tmp)
            (tmp / "baseline").mkdir()
            (tmp / "baseline" / "live.json").write_text("{}\n")
            git("add", ".", cwd=tmp)
            git("commit", "-q", "-m", "base", cwd=tmp)
            git("checkout", "-q", "-b", "h", cwd=tmp)
            (tmp / "findings" / "2026-11-01-live-1").mkdir(parents=True)
            (tmp / "findings" / "2026-11-01-live-1" / "run.json").write_text("{}\n")
            (tmp / "baseline" / "live.json").write_text("{\"a\": 1}\n")
            git("add", ".", cwd=tmp)
            git("commit", "-q", "-m", "data", cwd=tmp)
            script = str(ROOT / ".github" / "harvest_allowlist.py")
            self.assertEqual(subprocess.run([sys.executable, script, "main", "h"], cwd=tmp).returncode, 0)
            (tmp / "README.md").write_text("x\n")
            git("add", ".", cwd=tmp)
            git("commit", "-q", "-m", "extra", cwd=tmp)
            p = subprocess.run([sys.executable, script, "main", "h"], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(p.returncode, 1)
            self.assertIn("README.md", p.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DigestOnlyTest(unittest.TestCase):
    def make(self, tmp, archive):
        d = tmp / NAME
        write_run(d, {"05-deal": "PASS"})
        (d / "05-deal" / "proof.txt").unlink()
        if archive is not None:
            (d / "proof-archive.json").write_text(json.dumps(archive))
        return d

    def test_missing_proof_needs_a_valid_archive(self):
        tmp = Path(tempfile.mkdtemp(prefix="digest-only-"))
        try:
            ok = {"asset": f"{NAME}.tgz", "sha256": "a" * 64, "url": f"https://example.org/x/{NAME}.tgz"}
            self.assertEqual(finding.check(self.make(tmp, ok)), [])
            shutil.rmtree(tmp / NAME)
            self.assertTrue(finding.check(self.make(tmp, None)))
            for bad in ({**ok, "sha256": "A" * 64}, {**ok, "url": "http://example.org/" + NAME + ".tgz"},
                        {**ok, "asset": "other.tgz"}, {"asset": ok["asset"]}):
                shutil.rmtree(tmp / NAME)
                self.assertTrue(finding.check(self.make(tmp, bad)), bad)
            # A proof file that is present must still match its digest.
            shutil.rmtree(tmp / NAME)
            d = self.make(tmp, ok)
            (d / "05-deal" / "proof.txt").write_text("tampered\n")
            self.assertTrue(any("sha256" in e for e in finding.check(d)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
