#!/usr/bin/env python3
"""Run unittest modules in parallel processes, one process per module, with the verdict a serial run gives.

    python3 [-B] scripts/unittest_parallel.py discover [-s DIR] [-p PATTERN] [--only M1,M2] [-j N]
    python3 [-B] scripts/unittest_parallel.py modules [-j N] MODULE [MODULE ...]

`discover` runs the same test set as `python3 -m unittest discover -s DIR -p PATTERN -v` (default tests,
test*.py): each matching file is run as `python3 -m unittest discover -s DIR -p FILE -v`, which imports it
exactly as the whole-directory discover does (DIR first on sys.path). `modules` runs each dotted name as
`python3 -m unittest MODULE -v` from the current directory, as `python3 -m unittest MODULE ...` does.
The children inherit the environment, the working directory and -B. Each module's output is printed whole
when it finishes, then one summary line; the exit status is 1 when any module failed (a failure, an error,
an import error or an unexpected success) or when no test ran at all, else 0. A module that holds no tests
counts as passing beside others that do, as it does in a serial run. `discover` refuses a start directory that
holds a package (a directory with __init__.py), since a whole-directory discover would recurse into it and this
runner would not. Ctrl-C or SIGTERM starts no further module and stops the running ones with everything they
started (each module runs in its own process group: SIGINT, then SIGKILL after 5 s). -j defaults to
LOOP_TEST_JOBS, else the CPU count.

Modules write only to their own temporary directories and pick ports from their pid, so they can run side by side,
except a module whose code under test refuses to run while any lab.sh or loop.sh process runs on the host
(scripts/live_daily.sh, ops/live-ci/entry.sh): the other modules start such processes. A module whose file holds
the line `# unittest_parallel: alone` runs by itself, after every other module has finished.
"""
import argparse
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

NO_TESTS = 5  # unittest's exit status when a run found no tests (Python 3.12+)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    d = sub.add_parser("discover")
    d.add_argument("-s", "--start", default="tests")
    d.add_argument("-p", "--pattern", default="test*.py")
    d.add_argument("--only", default=None, help="comma-separated module names: run only these of the discovered set")
    m = sub.add_parser("modules")
    m.add_argument("module", nargs="+")
    for p in (d, m):
        p.add_argument("-j", "--jobs", type=int, default=int(os.environ.get("LOOP_TEST_JOBS") or os.cpu_count() or 1))
    a = ap.parse_args(argv)
    # SIGTERM (a gate or CI stopping the run) is handled as Ctrl-C is: no queued module starts, running ones stop.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        return run_all(a)
    except KeyboardInterrupt:
        STOP.set()
        stop_all()
        print("\nunittest_parallel: interrupted; no further module started", file=sys.stderr)
        return 130


def run_all(a):
    py = [sys.executable] + (["-B"] if sys.dont_write_bytecode else [])
    if a.mode == "discover":
        packages = sorted(str(p.parent) for p in Path(a.start).glob("*/__init__.py"))
        if packages:
            print(f"unittest_parallel: {a.start} holds packages a whole-directory discover would recurse into "
                  f"({', '.join(packages)}); run them by name", file=sys.stderr)
            return 1
        files = sorted(Path(a.start).glob(a.pattern))
        if a.only is not None:
            want = {m for m in a.only.split(",") if m}
            unknown = want - {f.stem for f in files}
            if unknown:
                print(f"unittest_parallel: --only names modules not in {a.start}: {', '.join(sorted(unknown))}",
                      file=sys.stderr)
                return 1
            files = [f for f in files if f.stem in want]
        if not files:
            print(f"unittest_parallel: no {a.pattern} in {a.start}", file=sys.stderr)
            return 1
        tests = collect(py, a.start, a.pattern)
        jobs = []
        for f in files:
            base = py + ["-m", "unittest", "discover", "-s", a.start, "-p", f.name, "-v"]
            ids = tests.get(f.stem)
            classes = {}
            for t in ids or ():
                classes.setdefault(t.rsplit(".", 1)[0], []).append(t)
            if alone(f) or ids is None or len(classes) < 2:
                # Whole: an alone module, one that did not load (its own run shows the error) or a single class.
                jobs.append(Job(f.stem, base, alone(f), len(ids) if ids is not None else None))
            else:
                # One process per TestCase class, so a long module spreads over the workers. `-k MODULE.CLASS.*`
                # selects exactly that class's tests; the count each run reports is checked against the collection.
                jobs += [Job(cls, base + ["-k", f"{cls}.*"], False, len(t)) for cls, t in classes.items()]
        # The largest first, so a long job does not start last.
        jobs.sort(key=lambda j: -(j.expect or 0))
    else:
        jobs = [Job(mod, py + ["-m", "unittest", mod, "-v"], alone(Path(*mod.split(".")).with_suffix(".py")), None)
                for mod in a.module]

    started = time.monotonic()
    failed, ran = [], 0
    pool = ThreadPoolExecutor(max_workers=max(1, a.jobs))
    try:
        # The side-by-side modules first, then each `alone` module with nothing else running.
        for batch in ([j for j in jobs if not j.alone], *([j] for j in jobs if j.alone)):
            futs = {pool.submit(run, j.cmd): j for j in batch}
            for fut in as_completed(futs):
                j = futs[fut]
                rc, out, secs = fut.result()
                sys.stdout.write(f"\n=== {j.name} (exit {rc}, {secs:.1f}s)\n{out}")
                n = re.search(r"^Ran (\d+) tests? in", out, re.M)
                got = int(n.group(1)) if n else 0
                ran += got
                if j.expect is not None and got != j.expect:
                    sys.stdout.write(f"unittest_parallel: {j.name} ran {got} tests where the collection found {j.expect}\n")
                    failed.append(j.name)
                elif rc not in (0, NO_TESTS):
                    failed.append(j.name)
                sys.stdout.flush()
    except KeyboardInterrupt:
        STOP.set()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    took = time.monotonic() - started
    if failed:
        print(f"\nunittest_parallel: FAILED: {len(failed)} of {len(jobs)} runs ({', '.join(sorted(failed))}); "
              f"{ran} tests in {took:.1f}s", file=sys.stderr)
        return 1
    if ran == 0:
        print(f"\nunittest_parallel: FAILED: no test ran in {len(jobs)} runs", file=sys.stderr)
        return 1
    print(f"\nunittest_parallel: OK: {len(jobs)} runs, {ran} tests in {took:.1f}s")
    return 0


class Job:
    def __init__(self, name, cmd, alone, expect):
        self.name, self.cmd, self.alone, self.expect = name, cmd, alone, expect


# Prints {module: [test id, ...]} for every module a whole-directory discover loads; null for one that failed to load.
COLLECT = """
import json, sys, unittest
def flat(s):
    for t in s:
        yield from flat(t) if isinstance(t, unittest.TestSuite) else [t]
out = {}
for t in flat(unittest.defaultTestLoader.discover(sys.argv[1], pattern=sys.argv[2])):
    if type(t).__name__ == "_FailedTest" or type(t).__module__ == "unittest.loader":
        out[t.id().rsplit(".", 1)[-1]] = None
    elif out.get(t.id().split(".", 1)[0], []) is not None:
        out.setdefault(t.id().split(".", 1)[0], []).append(t.id())
with open(sys.argv[3], "w") as f:
    json.dump(out, f)
"""


def collect(py, start, pattern):
    """The test ids per module, from one discover in a child (the import side effects stay there; it runs as a module
    run does, so an interrupt stops it too). The ids go to a file: a module may print while it is imported."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "tests.json"
        rc, log, _ = run(py + ["-c", COLLECT, start, pattern, str(out)])
        if rc != 0 or not out.is_file():
            print(f"unittest_parallel: collecting the tests failed; running whole modules\n{log}", file=sys.stderr)
            return {}
        return json.loads(out.read_text())


def stop_all(grace=5.0):
    """Stop every running module and what it started: SIGINT to each process group first, so unittest stops and the
    modules' own cleanups (atexit, addCleanup) run, then SIGKILL to whatever is left after the grace period."""
    with LOCK:
        groups = [p.pid for p in RUNNING]
    for g in groups:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(g, signal.SIGINT)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(alive(g) for g in groups):
        time.sleep(0.1)
    for g in groups:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(g, signal.SIGKILL)


def alive(group):
    try:
        os.killpg(group, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def alone(path):
    """True when the module's file asks to run with no other module beside it."""
    try:
        return any(line.strip() == "# unittest_parallel: alone" for line in path.read_text().splitlines())
    except OSError:
        return False  # a name with no file here (a module found elsewhere on sys.path) runs side by side


STOP = threading.Event()
LOCK = threading.Lock()
RUNNING = set()


def run(cmd):
    t = time.monotonic()
    with LOCK:
        if STOP.is_set():
            return 130, "", 0.0
        # Its own process group, so an interrupt reaches everything the module started (stop_all).
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             start_new_session=True)
        RUNNING.add(p)
    # Interrupted here (KeyboardInterrupt in the main thread), it stays in RUNNING for stop_all.
    out, _ = p.communicate()
    with LOCK:
        RUNNING.discard(p)
    return p.returncode, out, time.monotonic() - t


if __name__ == "__main__":
    sys.exit(main())
