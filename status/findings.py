#!/usr/bin/env python3
"""Write <run>/index.md: one row per step, its result, and links to its finding and proof.

Usage: findings.py RUN_DIR

RUN_DIR is one loop.sh run (findings/<date>-<population>/). This is the link list
only; the status page is built elsewhere. A row shows the finding's own result and
nothing more: a NOT_CHECKED step lists what was not checked and the issue that owns it.
Standard library only.
"""
import json
import sys
from pathlib import Path


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def row(d):
    nn, _, name = d.name.partition("-")
    f = d / "finding.json"
    try:
        r = json.loads(f.read_text(encoding="utf-8"))
        result = r["result"]
    except (OSError, ValueError, KeyError, TypeError):
        return f"| {nn} | {name} | no valid finding | | | |", None
    proofs = ", ".join(f"[{cell(p['path'])}]({d.name}/{p['path']})" for p in r.get("proof", []))
    notes = "; ".join(f"not checked: {cell(i['what'])} ({cell(i['issue'])})" for i in r.get("not_checked", []))
    if result == "FAIL" and r.get("detail"):
        notes = cell(r["detail"])
    return f"| {nn} | {name} | {result} | [finding.json]({d.name}/finding.json) | {proofs} | {notes} |", r.get("population")


def index(run_dir):
    run_dir = Path(run_dir)
    rows, pops = [], set()
    for d in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        line, pop = row(d)
        rows.append(line)
        if pop:
            pops.add(pop)
    pop = ", ".join(sorted(pops)) or "unknown"
    text = "\n".join([
        f"# Findings: {run_dir.name}",
        "",
        f"Population: {pop}. Each result is the step's own finding; open finding.json for what it asserts and pinned.",
        "",
        "| Step | Name | Result | Finding | Proof | Notes |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
    ])
    (run_dir / "index.md").write_text(text, encoding="utf-8")
    return text


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    index(argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
