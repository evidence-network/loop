"""steps/goose.setup.sh's fetch(): a checkout of exactly the goose-g1 pin, a commit SHA or a release tag (loop#501).

Runs the script's own fetch() against a local remote that git's url.<base>.insteadOf puts in place of
https://github.com/, so no network is used: a tag pin is kept as refs/tags/<tag> at its commit, a checkout already at
the pin is kept with the remote gone, a checkout at another commit is replaced, and a SHA pin works as before.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETUP = ROOT / "steps" / "goose.setup.sh"
TAG = "capsulectl/v0.1.1"  # slash-containing and annotated, as the release tag is
REPO = "action-state-group/evidencebook"


def fetch_function():
    m = re.search(r"^fetch\(\) \{[^\n]*\n.*?^\}\n", SETUP.read_text(), re.M | re.S)
    assert m, "steps/goose.setup.sh has no fetch() function"
    return m.group(0)


class GooseFetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.remote = self.tmp / "github" / REPO
        self.remote.mkdir(parents=True)
        self.git(self.remote, "init", "-q")
        self.git(self.remote, "commit", "-q", "--allow-empty", "-m", "old")
        self.old = self.git(self.remote, "rev-parse", "HEAD")
        self.git(self.remote, "commit", "-q", "--allow-empty", "-m", "release")
        self.release = self.git(self.remote, "rev-parse", "HEAD")
        self.git(self.remote, "tag", "-a", "-m", "release", TAG)
        self.git(self.remote, "commit", "-q", "--allow-empty", "-m", "later")
        self.dest = self.tmp / "goose" / "src"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def git(self, where, *args):
        return subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                              check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()

    def fetch(self, ref):
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": f"url.file://{self.tmp / 'github'}/.insteadOf",
               "GIT_CONFIG_VALUE_0": "https://github.com/"}
        script = "set -euo pipefail\n" + fetch_function() + f'fetch "{REPO}" "$1" "$2"\n'
        return subprocess.run(["bash", "-c", script, "fetch", ref, str(self.dest)], env=env, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL)

    def head(self):
        return self.git(self.dest, "rev-parse", "HEAD")

    def remote_gone(self):
        self.remote.rename(self.remote.with_name("gone"))

    def test_tag_pin_checks_out_the_tag_commit_and_keeps_the_tag(self):
        r = self.fetch(TAG)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head(), self.release)
        self.assertEqual(self.git(self.dest, "rev-parse", f"refs/tags/{TAG}^{{commit}}"), self.release)
        self.assertEqual(self.git(self.dest, "tag", "-l"), TAG)  # the one tag, no others followed
        self.assertEqual(self.git(self.dest, "rev-parse", "--is-shallow-repository"), "true")

    def test_tag_pin_already_checked_out_is_kept_without_the_remote(self):
        self.assertEqual(self.fetch(TAG).returncode, 0)
        self.remote_gone()
        r = self.fetch(TAG)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head(), self.release)

    def test_an_old_sha_checkout_is_replaced_by_the_tag(self):
        self.assertEqual(self.fetch(self.old).returncode, 0)  # what the setup left under the previous SHA pin
        r = self.fetch(TAG)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head(), self.release)
        self.assertEqual(self.git(self.dest, "tag", "-l"), TAG)

    def test_sha_pin_still_works_and_is_kept(self):
        r = self.fetch(self.old)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head(), self.old)
        self.remote_gone()
        r = self.fetch(self.old)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head(), self.old)

    def test_a_missing_tag_fails(self):
        self.assertNotEqual(self.fetch("capsulectl/v9.9.9").returncode, 0)


if __name__ == "__main__":
    unittest.main()
