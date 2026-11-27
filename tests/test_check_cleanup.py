"""scripts/check.sh's exit cleanup: a check that stops early stops the site's background run, everything it started
and removes its temporary directory, keeping the check's own exit status (evidence-network/loop#489)."""
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "check.sh"


def cleanup_block():
    text = CHECK.read_text()
    m = re.search(r"\(check-cleanup: begin\)\n(.*?)\n# \(check-cleanup: end\)", text, re.S)
    assert m, "scripts/check.sh has no check-cleanup block"
    return m.group(1)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


@unittest.skipUnless(shutil.which("timeout") and shutil.which("pgrep"), "needs GNU timeout and pgrep, as check.sh does")
class CleanupTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def check(self, background):
        """A check that starts BACKGROUND as the site run, then fails with status 3."""
        script = f"""set -euo pipefail
tmp=$(mktemp -d "{self.dir}/tmp.XXXXXX")
echo "$tmp" > "{self.dir}/tmpdir"
{cleanup_block()}
( {background} ) < /dev/null > /dev/null 2>&1 &
site_run=$!
for _ in $(seq 100); do [ -s "{self.dir}/pid" ] && break; sleep 0.05; done
sleep 0.3
exit 3
"""
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        return r, Path((self.dir / "tmpdir").read_text().strip())

    def test_a_step_under_timeout_is_stopped_with_the_run(self):
        # loop.sh runs a step as `timeout N step`; GNU timeout gives the step a process group of its own.
        r, tmp = self.check(f'timeout 60 bash -c \'echo $$ > "{self.dir}/pid"; exec sleep 60\'')
        self.assertEqual(r.returncode, 3, r.stderr)
        step = int((self.dir / "pid").read_text())
        deadline = time.monotonic() + 10
        while alive(step) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(step), "the step outlived the check")
        self.assertFalse(tmp.exists())

    def test_a_run_that_already_finished_still_lets_the_directory_go(self):
        r, tmp = self.check(f'echo done > "{self.dir}/pid"')
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertFalse(tmp.exists())


if __name__ == "__main__":
    unittest.main()
