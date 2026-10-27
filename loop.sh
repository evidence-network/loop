#!/usr/bin/env bash
# loop.sh: run one stage of the loop's steps, then check their findings.
#
#   ./loop.sh --stage 1 [--population rehearsal|live] [--out DIR] [--steps 01,02,03,12]
#
# Each step runs as `steps/<NN>-<name> --population population/<p> --out DIR/<NN>-<name>`
# and writes one finding.json there (see steps/README.md). A step that is missing, or
# exits non-zero, gets a FAIL finding written here through steps/finding.py, naming what
# broke. Then `steps/finding.py check DIR` runs, status/findings.py writes DIR/index.md,
# and one line per step is printed: `NN name RESULT`.
#
# Exit 0: every step has a valid finding (FAIL and NOT_CHECKED included: the result is in
# the finding, not the exit code). Exit 1: the check refused a finding. Exit 2: usage.
#
# LOOP_STEPS_DIR runs the steps from another directory (tests use it).
# LOOP_STEP_TIMEOUT is each step's limit in seconds (default 1800).
# LOOP_FINDINGS is where the default out directory goes (default findings/).
set -uo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
STEPS_DIR=${LOOP_STEPS_DIR:-$ROOT/steps}
TIMEOUT=${LOOP_STEP_TIMEOUT:-1800}
export PYTHONDONTWRITEBYTECODE=1
# Where each step's setup.sh installed its pinned tools; every step reads the same default.
export LOOP_TOOLS=${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}

# The steps of each stage, and the name a step gets when it is missing.
declare -A NAMES=([01]=seal [02]=book [03]=advertise [12]=join)
STAGE_1="01 02 03 12"

usage() { sed -n '4p' "$0" | sed 's/^#   //' >&2; exit 2; }
die() { echo "loop.sh: $*" >&2; exit 2; }

stage="" population=rehearsal out="" only=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stage) stage=${2:-}; shift 2 || usage ;;
    --population) population=${2:-}; shift 2 || usage ;;
    --out) out=${2:-}; shift 2 || usage ;;
    --steps) only=${2:-}; shift 2 || usage ;;
    -h|--help) usage ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$stage" in
  1) steps=$STAGE_1 ;;
  "") usage ;;
  *) die "stage $stage has no steps yet" ;;
esac
case "$population" in
  rehearsal|live) ;;
  *) die "population is rehearsal or live, not '$population'" ;;
esac
pop_dir=$ROOT/population/$population
[ -f "$pop_dir/population.json" ] || die "$pop_dir/population.json is missing"

if [ -n "$only" ]; then
  for nn in ${only//,/ }; do
    [[ " $steps " == *" $nn "* ]] || die "step $nn is not in stage $stage ($steps)"
  done
  steps=${only//,/ }
fi

out=${out:-${LOOP_FINDINGS:-$ROOT/findings}/$(date -u +%F)-$population}
mkdir -p "$out" && out=$(cd "$out" && pwd) && [ -n "$out" ] || die "cannot make the out directory '$out'"

# Rehearsal and live findings never share a directory.
python3 -B - "$out" "$population" <<'PY' || die "$out holds findings of another population; use another --out"
import json, sys
from pathlib import Path
for f in Path(sys.argv[1]).glob("*/finding.json"):
    try:
        p = json.loads(f.read_text()).get("population")
    except (ValueError, AttributeError):
        continue
    if p != sys.argv[2]:
        sys.exit(1)
PY

# fail <step dir> <started> <detail> [proof file in step dir]
fail() {
  python3 -B - "$ROOT" "$@" "$population" <<'PY'
import sys
root, step_dir, started, detail, *rest = sys.argv[1:]
proof, population = (rest[:-1], rest[-1])
sys.path.insert(0, root + "/steps")
import finding
finding.write(step_dir, result="FAIL", population=population,
              asserts="the step runs to the end and writes its finding",
              pinned=[], proof=proof, detail=detail, started=started)
PY
}

ran=()
for nn in $steps; do
  found=()
  for f in "$STEPS_DIR/$nn"-*; do
    # A step is a file named exactly <NN>-<name>; its setup.sh, docs and data sit beside it.
    [ -f "$f" ] && [[ "$(basename "$f")" =~ ^[0-9]{2}-[a-z][a-z0-9-]*$ ]] && found+=("$f")
  done
  base=$nn-${NAMES[$nn]}
  [ ${#found[@]} -eq 1 ] && [[ $(basename "${found[0]}") =~ ^[0-9]{2}-[a-z][a-z0-9-]*$ ]] && base=$(basename "${found[0]}")
  sdir=$out/$base
  rm -rf "$sdir" && mkdir -p "$sdir"
  ran+=("$base")
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "== $base" >&2

  if [ ${#found[@]} -eq 0 ]; then
    fail "$sdir" "$started" "no step executable steps/$nn-* was found"
    continue
  elif [ ${#found[@]} -gt 1 ]; then
    fail "$sdir" "$started" "more than one step executable for $nn: $(basename -a "${found[@]}" | tr '\n' ' ')"
    continue
  elif [ ! -x "${found[0]}" ]; then
    fail "$sdir" "$started" "steps/$base is not executable"
    continue
  fi

  log=$sdir/loop-step.log
  (cd "$ROOT" && timeout "$TIMEOUT" "${found[0]}" --population "$pop_dir" --out "$sdir") 2>&1 | tee "$log" >&2
  rc=${PIPESTATUS[0]}
  if [ "$rc" -eq 124 ]; then
    fail "$sdir" "$started" "steps/$base did not finish within ${TIMEOUT}s; its output is loop-step.log" loop-step.log
  elif [ "$rc" -ne 0 ]; then
    fail "$sdir" "$started" "steps/$base exited $rc; its output is loop-step.log" loop-step.log
  fi
done

status=0
python3 -B "$ROOT/steps/finding.py" check "$out" || status=1
python3 -B - "$out" "$population" <<'PY' || status=1
import json, sys
from pathlib import Path
bad = 0
for f in sorted(Path(sys.argv[1]).glob("*/finding.json")):
    try:
        p = json.loads(f.read_text()).get("population")
    except (ValueError, AttributeError):
        continue
    if p != sys.argv[2]:
        print(f"{f.parent.name}: population is {p}, this run is {sys.argv[2]}", file=sys.stderr)
        bad = 1
sys.exit(bad)
PY
python3 -B "$ROOT/status/findings.py" "$out" >&2 || status=1

# One line per step; a finding the check refused reads MALFORMED, whatever it claims.
python3 -B - "$ROOT" "$out" "$population" "${ran[@]}" <<'PY'
import json, sys
from pathlib import Path
root, out, population, *ran = sys.argv[1:]
sys.path.insert(0, root + "/steps")
import finding
for base in ran:
    nn, name = base.split("-", 1)
    try:
        r = json.loads((Path(out) / base / "finding.json").read_text())
        ok = not finding.check_record(Path(out) / base, r) and r["population"] == population
    except (OSError, ValueError, TypeError, AttributeError):
        ok = False
    print(nn, name, r["result"] if ok else "MALFORMED")
PY
exit $status
