#!/usr/bin/env python3
"""Make step 13's test fixtures, once. Needs the Goose tools (steps/goose.setup.sh) and capsulectl (steps/07-ask.setup.sh).

Usage: python3 -B tests/fixtures/step13/make_fixtures.py

Each fixture is a run of scripts/cross_ask_session.py: a mesh rehearsal node and a Goose rehearsal node ask each other
(rehearsal TEST keys made for the run), and the proof tree is what step 13 judges (<asker>/asks/<asked>/, <node>/advertised/,
entries/, nodes.json). The planted failures are made by an asker or a responder that misbehaves while the run goes, never
by editing bytes afterwards:

  good/              three asks each way: exchange (a signed refusal under the counterparties policy: a correct answer),
                     history_card (a signed artifact), exchange_late (a recorded absence, the late reply kept)
  wrong-key/         the answer to the mesh node's exchange ask is signed by a key other than the goose node's listed
                     key, and the mesh node records it under that key
  late-counted/      the mesh node records the reply that arrives after the deadline as an answer, not as an absence
  unsigned-refusal/  the mesh node's adapter answers the goose node's exchange ask with a refusal that carries no
                     signature, which the goose node cannot record as an answer: it records the absence, though a reply
                     was delivered by the deadline

The runs go in parallel, each waiting out its own one-minute deadline, so this takes a little over a minute.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TOOLS = Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache" / "evidence-network-loop"))
RUNS = {"good": [], "wrong-key": ["--plant", "wrong-key"], "late-counted": ["--plant", "late-counted"],
        "unsigned-refusal": ["--plant", "unsigned-refusal"]}


def main():
    python = TOOLS / "goose" / "venv" / "bin" / "python"
    env = {**os.environ, "LOOP_GOOSE_SRC": str(TOOLS / "goose" / "src"), "LOOP_TOOLS": str(TOOLS),
           "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory(prefix="step13-fixtures-") as tmp:
        procs = {name: subprocess.Popen([str(python), "-B", str(ROOT / "scripts" / "cross_ask_session.py"),
                                         str(Path(tmp) / name), *args], env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                 for name, args in RUNS.items()}
        for name, p in procs.items():
            out, _ = p.communicate()
            if p.returncode != 0:
                raise SystemExit(f"{name}: the session failed:\n{out[-2000:]}")
            dest = HERE / name
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(Path(tmp) / name, dest)
            print(f"wrote {dest.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
