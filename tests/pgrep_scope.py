"""Limit `pgrep` to the processes this test process started (evidence-network/loop#489).

scripts/live_daily.sh and ops/live-ci/entry.sh refuse to run while any lab.sh or loop.sh process (or a cargo build)
runs on the host: they ask `pgrep`. In a test that also counts other test modules running beside it and any other
checkout's run on the same machine, so the result depended on what else the machine was doing. `scope()` puts a
`pgrep` first on PATH that asks the real one and keeps only the descendants of this process: the processes the test
itself starts to exercise the refusal still count, nothing else does.
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

STUB = """#!/bin/bash
# The real pgrep's answer, keeping only descendants of $LOOP_TEST_PID (tests/pgrep_scope.py).
out=""
for p in $("$LOOP_REAL_PGREP" "$@"); do
  q=$p
  while [ -n "$q" ] && [ "$q" -gt 1 ]; do
    if [ "$q" = "$LOOP_TEST_PID" ]; then out="$out$p"$'\\n'; break; fi
    q=$(ps -o ppid= -p "$q" 2> /dev/null | tr -d ' ')
  done
done
[ -n "$out" ] || exit 1
printf '%s' "$out"
"""


def scope():
    """Install the scoped pgrep for this process and its children, once."""
    if os.environ.get("LOOP_TEST_PID") == str(os.getpid()):
        return
    # Under a test that is itself scoped, the pgrep on PATH is the stub: keep asking the real one.
    real = os.environ.get("LOOP_REAL_PGREP") or shutil.which("pgrep")
    if not real:
        return  # no pgrep: the scripts under test find no other run either way
    d = Path(tempfile.mkdtemp(prefix="loop-pgrep-"))
    atexit.register(shutil.rmtree, d, True)
    (d / "pgrep").write_text(STUB)
    (d / "pgrep").chmod(0o755)
    os.environ.update(LOOP_REAL_PGREP=real, LOOP_TEST_PID=str(os.getpid()), PATH=f"{d}:{os.environ['PATH']}")
