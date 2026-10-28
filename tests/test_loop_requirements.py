"""loop.sh: without bash 4+ or `timeout` it stops at once with a clear message, before any step runs."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOOP = ROOT / "loop.sh"
DASH = shutil.which("dash")


class LoopRequirementsTest(unittest.TestCase):
    def run_loop(self, cmd, env=None):
        with tempfile.TemporaryDirectory() as tmp:
            e = dict(os.environ, LOOP_FINDINGS=str(Path(tmp) / "findings"))
            e.update(env or {})
            r = subprocess.run([*cmd, "--stage", "1"], capture_output=True, text=True, env=e, timeout=60)
            self.assertFalse((Path(tmp) / "findings").exists())  # nothing ran
        return r

    def test_no_timeout_on_path_stops_with_exit_2(self):
        with tempfile.TemporaryDirectory() as bin_:
            os.symlink(shutil.which("bash"), Path(bin_) / "bash")  # bash only: no timeout, no python
            r = self.run_loop([str(LOOP)], env={"PATH": bin_})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("loop.sh: needs timeout from GNU coreutils", r.stderr)

    @unittest.skipUnless(DASH, "SKIPPED LOUDLY: no dash to run loop.sh under a shell without BASH_VERSINFO")
    def test_a_shell_without_bash_4_stops_with_exit_2(self):
        r = self.run_loop([DASH, str(LOOP)])
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("loop.sh: needs bash 4 or later", r.stderr)


if __name__ == "__main__":
    unittest.main()
