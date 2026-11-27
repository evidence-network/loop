#!/usr/bin/env python3
"""Run unittest modules in parallel processes, one process per module, with the verdict a serial run gives.

    python3 [-B] scripts/unittest_parallel.py discover [-s DIR] [-p PATTERN] [-j N]
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
runner would not. Ctrl-C or SIGTERM starts no further module and stops the running ones. -j defaults to
LOOP_TEST_JOBS, else the CPU count.

Modules write only to their own temporary directories and pick ports from their pid, so they can run side by side,
except a module whose code under test refuses to run while any lab.sh or loop.sh process runs on the host
(scripts/live_daily.sh, ops/live-ci/entry.sh): the other modules start such processes. A module whose file holds
the line `# unittest_parallel: alone` runs by itself, after every other module has finished.
"""
import argparse
import os
import re
import signal
import subprocess
import sys
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
    m = sub.add_parser("modules")
    m.add_argument("module", nargs="+")
    for p in (d, m):
        p.add_argument("-j", "--jobs", type=int, default=int(os.environ.get("LOOP_TEST_JOBS") or os.cpu_count() or 1))
    a = ap.parse_args(argv)

    py = [sys.executable] + (["-B"] if sys.dont_write_bytecode else [])
    if a.mode == "discover":
        packages = sorted(str(p.parent) for p in Path(a.start).glob("*/__init__.py"))
        if packages:
            print(f"unittest_parallel: {a.start} holds packages a whole-directory discover would recurse into "
                  f"({', '.join(packages)}); run them by name", file=sys.stderr)
            return 1
        files = sorted(Path(a.start).glob(a.pattern))
        if not files:
            print(f"unittest_parallel: no {a.pattern} in {a.start}", file=sys.stderr)
            return 1
        # The biggest files first, so a long module does not start last.
        files.sort(key=lambda f: -f.stat().st_size)
        jobs = [(f.stem, py + ["-m", "unittest", "discover", "-s", a.start, "-p", f.name, "-v"], alone(f)) for f in files]
    else:
        jobs = [(mod, py + ["-m", "unittest", mod, "-v"], alone(Path(*mod.split(".")).with_suffix(".py"))) for mod in a.module]

    # SIGTERM (a gate or CI stopping the run) is handled as Ctrl-C is: no queued module starts, running ones stop.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    started = time.monotonic()
    failed, ran = [], 0
    pool = ThreadPoolExecutor(max_workers=max(1, a.jobs))
    try:
        # The side-by-side modules first, then each `alone` module with nothing else running.
        for batch in ([j for j in jobs if not j[2]], *([j] for j in jobs if j[2])):
            futs = {pool.submit(run, cmd): name for name, cmd, _ in batch}
            for fut in as_completed(futs):
                name = futs[fut]
                rc, out, secs = fut.result()
                sys.stdout.write(f"\n=== {name} (exit {rc}, {secs:.1f}s)\n{out}")
                sys.stdout.flush()
                n = re.search(r"^Ran (\d+) tests? in", out, re.M)
                ran += int(n.group(1)) if n else 0
                if rc not in (0, NO_TESTS):
                    failed.append(name)
    except KeyboardInterrupt:
        STOP.set()
        pool.shutdown(wait=False, cancel_futures=True)
        with LOCK:
            for p in RUNNING:
                p.kill()
        print("\nunittest_parallel: interrupted; no further module started", file=sys.stderr)
        return 130
    pool.shutdown()
    took = time.monotonic() - started
    if failed:
        print(f"\nunittest_parallel: FAILED: {len(failed)} of {len(jobs)} modules ({', '.join(sorted(failed))}); "
              f"{ran} tests in {took:.1f}s", file=sys.stderr)
        return 1
    if ran == 0:
        print(f"\nunittest_parallel: FAILED: no test ran in {len(jobs)} modules", file=sys.stderr)
        return 1
    print(f"\nunittest_parallel: OK: {len(jobs)} modules, {ran} tests in {took:.1f}s")
    return 0


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
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        RUNNING.add(p)
    try:
        out, _ = p.communicate()
    finally:
        with LOCK:
            RUNNING.discard(p)
    return p.returncode, out, time.monotonic() - t


if __name__ == "__main__":
    sys.exit(main())
