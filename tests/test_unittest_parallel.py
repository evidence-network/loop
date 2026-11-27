"""scripts/unittest_parallel.py gives the verdict a serial unittest run gives, module by module in parallel."""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "unittest_parallel.py"

PASS = """
import unittest
class T(unittest.TestCase):
    def test_a(self): pass
    def test_b(self): pass
"""
FAIL = """
import unittest
class T(unittest.TestCase):
    def test_bad(self): self.assertEqual(1, 2)
"""
BROKEN = "import no_such_module_anywhere\n"
EMPTY = "X = 1\n"
# A sibling the discovered module imports: discover puts the start directory first on sys.path, and so must each child.
HELPER = "VALUE = 7\n"
USES_HELPER = """
import unittest
import helper_mod
class T(unittest.TestCase):
    def test_v(self): self.assertEqual(helper_mod.VALUE, 7)
"""


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.dir)])

    def write(self, name, body):
        (self.dir / name).write_text(textwrap.dedent(body))

    def run_runner(self, *args, cwd=None):
        return subprocess.run([sys.executable, "-B", str(RUNNER), *args], cwd=cwd or self.dir, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120)

    def test_all_passing_modules_pass_and_every_test_is_counted(self):
        self.write("test_one.py", PASS)
        self.write("test_two.py", PASS)
        self.write("helper_mod.py", HELPER)
        self.write("test_three.py", USES_HELPER)
        r = self.run_runner("discover", "-s", str(self.dir), "-j", "3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK: 3 runs, 5 tests", r.stdout)

    def test_a_failing_module_fails_the_run_and_is_named(self):
        self.write("test_good.py", PASS)
        self.write("test_bad.py", FAIL)
        r = self.run_runner("discover", "-s", str(self.dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED: 1 of 2 runs (test_bad)", r.stderr)
        self.assertIn("test_bad", r.stdout)  # its own output is printed whole

    def test_a_module_that_cannot_import_fails_the_run(self):
        self.write("test_broken.py", BROKEN)
        r = self.run_runner("discover", "-s", str(self.dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("test_broken", r.stderr)

    def test_a_module_with_no_tests_passes_as_in_a_serial_run(self):
        self.write("test_empty.py", EMPTY)
        self.write("test_one.py", PASS)
        r = self.run_runner("discover", "-s", str(self.dir))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_pattern_selects_the_files(self):
        self.write("test_one.py", PASS)
        self.write("check_bad.py", FAIL)
        self.assertEqual(self.run_runner("discover", "-s", str(self.dir)).returncode, 0)
        self.assertEqual(self.run_runner("discover", "-s", str(self.dir), "-p", "check*.py").returncode, 1)

    def test_no_matching_file_is_an_error_not_a_pass(self):
        r = self.run_runner("discover", "-s", str(self.dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no test*.py", r.stderr)

    def test_modules_mode_runs_dotted_names_from_the_working_directory(self):
        pkg = self.dir / "pkg"
        pkg.mkdir()
        (pkg / "test_one.py").write_text(textwrap.dedent(PASS))
        (pkg / "test_bad.py").write_text(textwrap.dedent(FAIL))
        self.assertEqual(self.run_runner("modules", "pkg.test_one").returncode, 0)
        r = self.run_runner("modules", "pkg.test_one", "pkg.test_bad")
        self.assertEqual(r.returncode, 1)
        self.assertIn("(pkg.test_bad)", r.stderr)

    def test_a_run_where_no_test_ran_fails_as_a_serial_discover_does(self):
        self.write("test_empty.py", EMPTY)
        r = self.run_runner("discover", "-s", str(self.dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no test ran", r.stderr)

    def test_a_package_the_whole_directory_discover_would_enter_is_refused(self):
        self.write("test_one.py", PASS)
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "__init__.py").write_text("")
        (self.dir / "sub" / "test_inner.py").write_text(textwrap.dedent(FAIL))
        r = self.run_runner("discover", "-s", str(self.dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("holds packages", r.stderr)

    def test_an_interrupt_starts_no_queued_module_and_stops_the_running_one(self):
        marks = self.dir / "marks"
        marks.mkdir()
        for i in range(3):
            self.write(f"test_slow{i}.py", f"""
                import time, unittest
                from pathlib import Path
                class T(unittest.TestCase):
                    def test_s(self):
                        import subprocess
                        child = subprocess.Popen(["sleep", "60"])  # what a module starts must stop with it
                        Path({str(marks)!r}, "{i}").write_text(str(child.pid))
                        time.sleep(60)
                """)
        for sig in (signal.SIGINT, signal.SIGTERM):
            for f in marks.iterdir():
                f.unlink()
            p = subprocess.Popen([sys.executable, "-B", str(RUNNER), "discover", "-s", str(self.dir), "-j", "1"],
                                 cwd=self.dir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
            deadline = time.monotonic() + 30
            while not any(marks.iterdir()) and time.monotonic() < deadline:
                time.sleep(0.05)
            p.send_signal(sig)
            _, err = p.communicate(timeout=30)
            self.assertEqual(p.returncode, 130, err)
            started = list(marks.iterdir())
            self.assertEqual(len(started), 1, f"{sig}: a queued module started")
            grandchild = int(started[0].read_text())
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and alive(grandchild):
                time.sleep(0.1)
            self.assertFalse(alive(grandchild), f"{sig}: the module's own child outlived the interrupt")

    def test_a_module_of_several_classes_runs_one_process_per_class_and_names_the_failing_one(self):
        self.write("test_multi.py", """
            import os, unittest
            class A(unittest.TestCase):
                def test_pid(self): print("pid", os.getpid())
                def test_b(self): pass
            class B(unittest.TestCase):
                def test_pid(self): print("pid", os.getpid())
            class Bad(unittest.TestCase):
                def test_bad(self): self.assertEqual(1, 2)
            """)
        r = self.run_runner("discover", "-s", str(self.dir), "-j", "3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED: 1 of 3 runs (test_multi.Bad); 4 tests", r.stderr)
        self.assertEqual(len(set(re.findall(r"^pid (\d+)", r.stdout, re.M))), 2, r.stdout)  # A and B apart


    def test_an_interrupt_while_the_tests_are_collected_stops_what_the_import_started(self):
        mark = self.dir / "collector-child"
        self.write("test_slowimport.py", f"""
            import subprocess, time, unittest
            from pathlib import Path
            child = subprocess.Popen(["sleep", "60"])
            Path({str(mark)!r}).write_text(str(child.pid))
            time.sleep(60)
            class T(unittest.TestCase):
                def test_t(self): pass
            """)
        p = subprocess.Popen([sys.executable, "-B", str(RUNNER), "discover", "-s", str(self.dir)], cwd=self.dir,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 30
        while not mark.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        p.send_signal(signal.SIGTERM)
        _, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 130, err)
        grandchild = int(mark.read_text())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and alive(grandchild):
            time.sleep(0.1)
        self.assertFalse(alive(grandchild), "the collection's child outlived the interrupt")

    def test_a_class_that_took_long_last_time_runs_one_process_per_test_with_the_same_verdict(self):
        self.write("test_long.py", """
            import os, unittest
            class L(unittest.TestCase):
                def test_a(self): print("pid", os.getpid())
                def test_ab(self): print("pid", os.getpid())
                def test_b(self): self.assertEqual(1, 2)
            """)
        times = self.dir / "times.json"
        times.write_text('{"test_long.L": 999}')
        r = self.run_runner("discover", "-s", str(self.dir), "--times", str(times), "-j", "3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED: 1 of 3 runs (test_long.L.test_b); 3 tests", r.stderr)  # test_a did not also run test_ab
        self.assertEqual(len(set(re.findall(r"^pid (\d+)", r.stdout, re.M))), 2, r.stdout)
        recorded = json.loads(times.read_text())
        self.assertIn("test_long.L.test_a", recorded)
        self.assertIn("test_long.L", recorded)

    def test_an_unreadable_times_file_changes_nothing(self):
        self.write("test_one.py", PASS)
        times = self.dir / "times.json"
        times.write_text("not json")
        r = self.run_runner("discover", "-s", str(self.dir), "--times", str(times))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK: 1 runs, 2 tests", r.stdout)

    def test_a_long_class_with_a_shared_fixture_is_not_split(self):
        marks = self.dir / "setups"
        self.write("test_shared.py", f"""
            import unittest
            class S(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    open({str(marks)!r}, "a").write("setup\\n")
                def test_a(self): pass
                def test_b(self): pass
            """)
        times = self.dir / "times.json"
        times.write_text('{"test_shared.S": 999}')
        r = self.run_runner("discover", "-s", str(self.dir), "--times", str(times))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK: 1 runs, 2 tests", r.stdout)
        self.assertEqual(marks.read_text().splitlines(), ["setup"])  # the fixture was built once


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie whose parent was killed is reaped by init; a live process answers ps with a state other than Z.
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


if __name__ == "__main__":
    unittest.main()
