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

Modules write only to their own temporary directories and pick ports from their pid, so they run side by side; the
scripts that refuse while a lab.sh or loop.sh runs on the host are tested with a pgrep scoped to the test's own
processes (tests/pgrep_scope.py).
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
    d.add_argument("--times", default=os.environ.get("LOOP_TEST_TIMES"),
                   help="a JSON file of each run's seconds last time (default LOOP_TEST_TIMES): read to start the "
                        "longest first and to split a long class into one run per test, then updated")
    m = sub.add_parser("modules")
    m.add_argument("module", nargs="+")
    m.add_argument("--times", default=os.environ.get("LOOP_TEST_TIMES"), help="as for discover")
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
        tests, shared = collect(py, "discover", a.start, a.pattern)
        modules = [(f.stem, py + ["-m", "unittest", "discover", "-s", a.start, "-p", f.name, "-v"]) for f in files]
    else:
        tests, shared = collect(py, "names", *a.module)
        modules = [(m, py + ["-m", "unittest", m, "-v"]) for m in a.module]
    times = load_times(a.times)
    jobs = []
    for mod, base in modules:
        ids = tests.get(mod)
        classes = {}
        for t in ids or ():
            classes.setdefault(t.rsplit(".", 1)[0], []).append(t)
        if ids is None:
            # Whole: a module that did not load, or one the collection did not reach (its own run shows why).
            jobs.append(Job(mod, base, None))
            continue
        for cls, t in classes.items():
            if len(t) > 1 and times.get(cls, 0) > SPLIT_SECONDS and cls not in shared:
                # A class that took long last time and shares no fixture between its tests: one run per test.
                # "*ID" matches that test id exactly.
                jobs += [Job(i, base + ["-k", f"*{i}"], 1, group=cls) for i in t]
            elif len(classes) == 1:
                jobs.append(Job(mod, base, len(t), group=cls))
            else:
                # One process per TestCase class: `-k MODULE.CLASS.*` selects exactly its tests; the count each run
                # reports is checked against the collection.
                jobs.append(Job(cls, base + ["-k", f"{cls}.*"], len(t), group=cls))
    # The longest first (by last time's seconds, else by test count), so a long run does not start last.
    jobs.sort(key=lambda j: (-times.get(j.name, times.get(j.group, 0)), -(j.expect or 0)))

    started = time.monotonic()
    failed, ran, took_by = [], 0, {}
    pool = ThreadPoolExecutor(max_workers=max(1, a.jobs))
    try:
        futs = {pool.submit(run, j.cmd): j for j in jobs}
        for fut in as_completed(futs):
            j = futs[fut]
            rc, out, secs = fut.result()
            took_by[j] = secs
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
    save_times(a.times, took_by)
    if failed:
        print(f"\nunittest_parallel: FAILED: {len(failed)} of {len(jobs)} runs ({', '.join(sorted(failed))}); "
              f"{ran} tests in {took:.1f}s", file=sys.stderr)
        return 1
    if ran == 0:
        print(f"\nunittest_parallel: FAILED: no test ran in {len(jobs)} runs", file=sys.stderr)
        return 1
    print(f"\nunittest_parallel: OK: {len(jobs)} runs, {ran} tests in {took:.1f}s")
    return 0


# A class whose run took longer than this last time runs one process per test.
SPLIT_SECONDS = 30


class Job:
    def __init__(self, name, cmd, expect, group=None):
        self.name, self.cmd, self.expect, self.group = name, cmd, expect, group or name


def load_times(path):
    """Last time's seconds per run name and per class; empty when there is no file or it cannot be read. Only the
    order of the runs and the split of a long class depend on it, never which tests run or their verdict."""
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text())
        return {k: float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_times(path, took_by):
    if not path or not took_by:
        return
    times = load_times(path)
    per_class = {}
    for j, secs in took_by.items():
        times[j.name] = round(secs, 1)
        per_class[j.group] = per_class.get(j.group, 0) + secs
    for cls, secs in per_class.items():
        times[cls] = round(secs, 1)
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(f"{path}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(times, sort_keys=True, indent=0))
        os.replace(tmp, path)
    except OSError as e:
        print(f"unittest_parallel: could not record the run times in {path}: {e}", file=sys.stderr)


# Prints {module: [test id, ...]} for every module a whole-directory discover loads; null for one that failed to load.
COLLECT = """
import json, sys, unittest
def flat(s):
    for t in s:
        yield from flat(t) if isinstance(t, unittest.TestSuite) else [t]
mode, out_path, args = sys.argv[1], sys.argv[2], sys.argv[3:]
loader = unittest.defaultTestLoader
if mode == "discover":
    groups = [(None, loader.discover(args[0], pattern=args[1]))]
else:
    sys.path.insert(0, "")
    groups = [(name, loader.loadTestsFromName(name)) for name in args]
out, shared = {}, set()
for name, suite in groups:
    for t in flat(suite):
        if type(t).__name__ == "_FailedTest" or type(t).__module__ == "unittest.loader":
            out[name or t.id().rsplit(".", 1)[-1]] = None
            continue
        mod = name or t.id().split(".", 1)[0]
        if out.get(mod, []) is None:
            continue
        out.setdefault(mod, []).append(t.id())
        # A class whose tests share a fixture (setUpClass, or the module's setUpModule) is never split per test.
        cls = type(t)
        if (cls.setUpClass.__func__ is not unittest.TestCase.setUpClass.__func__
                or hasattr(sys.modules.get(cls.__module__), "setUpModule")):
            shared.add(t.id().rsplit(".", 1)[0])
with open(out_path, "w") as f:
    json.dump({"tests": out, "shared": sorted(shared)}, f)
"""


def collect(py, mode, *args):
    """The test ids per module, from one discover (or load by name) in a child (the import side effects stay there; it runs as a module
    run does, so an interrupt stops it too). The ids go to a file: a module may print while it is imported."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "tests.json"
        rc, log, _ = run(py + ["-c", COLLECT, mode, str(out), *args])
        if rc != 0 or not out.is_file():
            print(f"unittest_parallel: collecting the tests failed; running whole modules\n{log}", file=sys.stderr)
            return {}, set()
        data = json.loads(out.read_text())
        return data["tests"], set(data["shared"])


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
