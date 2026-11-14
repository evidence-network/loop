#!/usr/bin/env python3
"""CI check for a rehearsal run of `loop.sh --stage cross --ecosystem cross` (loop#440).

Usage: check_cross_run.py RUN   (e.g. findings/cross/2026-11-14-rehearsal)

`loop.sh --strict` already fails a FAIL, but step 08 not being FAIL is not enough: a step 13 that 08 could not reproduce
only makes 08 NOT_CHECKED. This check refuses the run unless step 13 is PASS, both of its cells (goose -> mesh and
mesh -> goose) are PASS in pairs.json, and 08-verify/verified.json holds step 13 with status "reproduced". Standard
library only. Exit 0 = clean, 1 = refused.
"""
import json
import sys
from pathlib import Path

CELLS = (("goose", "mesh"), ("mesh", "goose"))


def problems(run):
    run = Path(run)
    errs = []
    try:
        pairs = json.loads((run / "13-ask-across" / "pairs.json").read_text(encoding="utf-8"))["rows"]
        rec = json.loads((run / "13-ask-across" / "finding.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [f"step 13's finding or pairs.json is not readable ({e})"]
    if rec.get("result") != "PASS":
        errs.append(f"step 13 is {rec.get('result')}, not PASS")
    have = {(r.get("asker"), r.get("responder")): r for r in pairs if isinstance(r, dict)}
    for cell in CELLS:
        r = have.get(cell)
        if r is None:
            errs.append(f"pairs.json has no row for {cell[0]} -> {cell[1]}")
        elif r.get("result") != "PASS":
            errs.append(f"{cell[0]} -> {cell[1]} is {r.get('result')}, not PASS: {str(r.get('detail'))[:300]}")
    try:
        steps = json.loads((run / "08-verify" / "verified.json").read_text(encoding="utf-8"))["steps"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        return errs + [f"08-verify/verified.json is not readable ({e})"]
    entry = [s for s in steps if isinstance(s, dict) and s.get("step") == "13"]
    if len(entry) != 1:
        errs.append("08-verify/verified.json does not hold step 13 exactly once")
    elif entry[0].get("status") != "reproduced":
        errs.append(f"08 did not reproduce step 13: status {entry[0].get('status')}, "
                    f"problems {entry[0].get('problems')}, not_checked {entry[0].get('not_checked')}")
    return errs


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    errs = problems(argv[1])
    for e in errs:
        print(e, file=sys.stderr)
    if not errs:
        print(f"{argv[1]}: step 13 PASS in both directions, reproduced by 08")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
