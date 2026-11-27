"""tests/pgrep_scope.py: the scoped pgrep sees the processes this test started and nothing else (loop#489)."""
import os
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pgrep_scope  # noqa: E402

pgrep_scope.scope()


def pgrep(*args):
    return subprocess.run(["pgrep", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.token = f"pgrep-scope-{uuid.uuid4().hex[:12]}"

    def start(self, argv0, detached):
        """A `sleep` named ARGV0: our child, or detached (a double fork, so it belongs to no test process)."""
        if detached:
            subprocess.run(["bash", "-c", f'(exec -a "{argv0}" sleep 30 &)'], check=True)
        else:
            p = subprocess.Popen(["bash", "-c", f'exec -a "{argv0}" sleep 30'])
            self.addCleanup(lambda: (p.kill(), p.wait()))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            r = subprocess.run([os.environ["LOOP_REAL_PGREP"], "-f", argv0], capture_output=True, text=True)
            if r.stdout.split():
                pids = r.stdout.split()
                if detached:
                    self.addCleanup(lambda: subprocess.run(["kill", *pids], capture_output=True))
                return pids
            time.sleep(0.05)
        self.fail(f"{argv0} did not start")

    def test_a_process_of_another_tree_is_not_seen(self):
        self.start(f"{self.token}-other lab.sh", detached=True)
        r = pgrep("-f", self.token)
        self.assertEqual((r.returncode, r.stdout), (1, ""))  # the real pgrep sees it; the scoped one does not

    def test_a_process_this_test_started_is_seen_beside_one_it_did_not(self):
        self.start(f"{self.token}-other lab.sh", detached=True)
        mine = self.start(f"{self.token}-mine lab.sh", detached=False)
        r = pgrep("-f", self.token)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), mine)

    def test_a_grandchild_is_seen(self):
        # The name goes in through the environment, so only the grandchild's command line holds the token.
        mark = Path(self.tmpdir()) / "pid"
        p = subprocess.Popen(["bash", "-c", 'bash -c \'echo $$ > "$MARK"; exec -a "$NAME" sleep 30\' & wait'],
                             env={**os.environ, "MARK": str(mark), "NAME": f"{self.token} loop.sh"})
        self.addCleanup(lambda: (p.kill(), p.wait()))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (mark.exists() and mark.read_text().strip()):
            time.sleep(0.05)
        grandchild = mark.read_text().strip()
        self.addCleanup(lambda: subprocess.run(["kill", grandchild], capture_output=True))
        r = pgrep("-f", self.token)
        self.assertEqual((r.returncode, r.stdout.split()), (0, [grandchild]))

    def tmpdir(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", d]))
        return d


if __name__ == "__main__":
    unittest.main()
