#!/usr/bin/env python3
"""Run the cross rehearsal's two nodes and write the population step 13 judges them under (loop#440).

Usage: cross_rehearsal.py --work DIR [--tools DIR]

Runs scripts/cross_ask_session.py in the Goose venv (steps/goose.setup.sh): a mesh node and a Goose node, each with a
rehearsal key made for the run, ask each other for evidence both ways through the rehearsal relay. DIR/run holds the
proof tree; DIR/population/population.json names it and lists each node's key, the one step 13 and step 08 take as the
listed key (never one read from the bundle): {"population": "rehearsal", "cross": {"run", "nodes": {name: {key_id,
ecosystem}}}}. No live key, wallet or lab VM is touched, and DIR stays outside the findings.

Exit 0 once population.json is written; exit 3 when the Goose tools or capsulectl are not installed (steps/goose.setup.sh,
steps/07-ask.setup.sh); exit 1 when they are and the run did not complete (the tail of the run's log is printed).
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache" / "evidence-network-loop"))
NO_TOOLS = 3


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--tools", type=Path, default=TOOLS)
    a = ap.parse_args(argv)
    goose = a.tools / "goose"
    python, src = goose / "venv" / "bin" / "python", goose / "src"
    if not python.is_file() or not (src / "python" / "examples" / "goose" / "loop_server.py").is_file():
        print(f"cross_rehearsal: no Goose tools in {goose} (run steps/goose.setup.sh)", file=sys.stderr)
        return NO_TOOLS
    if not (a.tools / "bin" / "capsulectl").is_file():
        print(f"cross_rehearsal: no capsulectl in {a.tools / 'bin'} (run steps/07-ask.setup.sh)", file=sys.stderr)
        return NO_TOOLS
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    run = work / "run"
    r = subprocess.run([str(python), "-B", str(HERE / "cross_ask_session.py"), str(run)], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True,
                       env={**os.environ, "LOOP_GOOSE_SRC": str(src), "LOOP_TOOLS": str(a.tools),
                            "PYTHONDONTWRITEBYTECODE": "1"})
    (work / "cross_session.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        print("cross_rehearsal: the cross ask session failed; the end of its log:\n" + "\n".join(
            (r.stdout + r.stderr).splitlines()[-30:]), file=sys.stderr)
        return 1
    nodes = json.loads((run / "nodes.json").read_text())
    pop = work / "population"
    pop.mkdir(parents=True, exist_ok=True)
    doc = {"population": "rehearsal", "pinned": ["goose-g1"],
           "cross": {"run": str(run), "log": str(work / "cross_session.log"),
                     "nodes": {n: {"key_id": v["key_id"], "ecosystem": v["ecosystem"]} for n, v in nodes.items()}}}
    (pop / "population.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
