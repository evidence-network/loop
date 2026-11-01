#!/usr/bin/env bash
# loop.sh: run one stage of the loop's steps, then check their findings.
#
#   ./loop.sh --stage 1 [--ecosystem mesh|muse|cross|goose] [--population rehearsal|live] [--out DIR] [--steps 01,02,03,12] [--strict]
#
# Each step runs as `steps/<NN>-<name> --population population/<p> --out DIR/<NN>-<name>`
# and writes one finding.json there (see steps/README.md). A step that is missing, or
# exits non-zero, gets a FAIL finding written here through steps/finding.py, naming what
# broke. Then `steps/finding.py check DIR` runs, status/findings.py writes DIR/index.md,
# and one line per step is printed: `NN name RESULT`. Before the first step it writes
# DIR/run.json: {"stage", "population", "started", "steps"} (UTC): started is the earliest of this run's start, an
# existing run.json's and each finding's already in DIR (loop#65); stage and steps are this run's.
#
# Exit 0: every step has a valid finding (FAIL and NOT_CHECKED included: the result is in
# the finding, not the exit code). Exit 1: the check refused a finding. Exit 2: usage.
# With --strict (CI) a rehearsal run whose summary has a FAIL or MALFORMED line exits 3, so
# the job fails; a live run never does (live runs report, they do not gate).
#
# --ecosystem (default mesh) names what the steps run against. mesh is the loop as it has always
# run: findings in findings/<date>-<population>/, run.json as above. muse and cross have no steps yet: each step
# gets a NOT_CHECKED finding saying so (owned by loop#150), written to findings/<ecosystem>/<date>-<population>/
# with "ecosystem" added to run.json, and nothing runs. goose (ecosystem 2, loop#315) covers all twelve rows: on the
# rehearsal population scripts/goose_rehearsal.py runs two G1 Goose nodes (tools: steps/goose.setup.sh) and each step
# it drives (today 01 and 06) runs on the population it wrote, FAIL with the run's log when that run fails; every other row
# is NOT_CHECKED citing the issue it waits on, and so are the driven rows on a host without the tools.
# Steps 01-12 are the same per ecosystem, so the gate compares the same assertion across them.
#
# LOOP_STEPS_DIR runs the steps from another directory (tests use it).
# LOOP_STEP_TIMEOUT is each step's limit in seconds (default 1800).
# LOOP_FINDINGS is where the default out directory goes (default findings/).
#
# Needs bash 4+ (associative arrays) and `timeout` from GNU coreutils; it stops with exit 2
# before any step runs when either is missing.
if [ "${BASH_VERSINFO:-0}" -lt 4 ]; then
  echo "loop.sh: needs bash 4 or later (this is ${BASH_VERSION:-not bash}); run it with a newer bash" >&2
  exit 2
fi
if ! command -v timeout >/dev/null 2>&1; then
  echo "loop.sh: needs timeout from GNU coreutils on PATH (macOS: brew install coreutils, then put its gnubin first)" >&2
  exit 2
fi
set -uo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
STEPS_DIR=${LOOP_STEPS_DIR:-$ROOT/steps}
TIMEOUT=${LOOP_STEP_TIMEOUT:-1800}
export PYTHONDONTWRITEBYTECODE=1
# Where each step's setup.sh installed its pinned tools; every step reads the same default.
export LOOP_TOOLS=${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}

# The steps of each stage, and the name a step gets when it is missing.
declare -A NAMES=([01]=seal [02]=book [03]=advertise [04]=admit [05]=deal [06]=settle [07]=ask [08]=verify
                  [09]=dispute [10]=judge [11]=report [12]=join)
# 07 asks after 12 joins the nodes and before 08 re-derives its finding (loop#62).
STAGE_1="01 02 03 12 07 08"
# The goose column covers all twelve rows (loop#315): each runs on the Goose rehearsal population when it has a Goose
# input, else it is NOT_CHECKED naming what it waits on.
GOOSE_STEPS="01 02 03 04 05 06 07 09 10 11 12 08"

usage() { sed -n '4p' "$0" | sed 's/^#   //' >&2; exit 2; }
die() { echo "loop.sh: $*" >&2; exit 2; }

stage="" ecosystem=mesh population=rehearsal out="" only="" strict=no
while [ $# -gt 0 ]; do
  case "$1" in
    --stage) stage=${2:-}; shift 2 || usage ;;
    --ecosystem) ecosystem=${2:-}; shift 2 || usage ;;
    --population) population=${2:-}; shift 2 || usage ;;
    --out) out=${2:-}; shift 2 || usage ;;
    --steps) only=${2:-}; shift 2 || usage ;;
    --strict) strict=yes; shift ;;
    -h|--help) usage ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$stage" in
  1) steps=$STAGE_1; [ "$ecosystem" = goose ] && steps=$GOOSE_STEPS ;;
  "") usage ;;
  *) die "stage $stage has no steps yet" ;;
esac
case "$population" in
  rehearsal|live) ;;
  *) die "population is rehearsal or live, not '$population'" ;;
esac
case "$ecosystem" in
  mesh|muse|cross|goose) ;;
  *) die "ecosystem is mesh, muse, cross or goose, not '$ecosystem'" ;;
esac
pop_dir=$ROOT/population/$population
[ "$ecosystem" = mesh ] && { [ -f "$pop_dir/population.json" ] || die "$pop_dir/population.json is missing"; }

if [ -n "$only" ]; then
  for nn in ${only//,/ }; do
    [[ " $steps " == *" $nn "* ]] || die "step $nn is not in stage $stage ($steps)"
  done
  steps=${only//,/ }
  # 08 reproduces the findings of the steps that ran before it in this run, so it runs last.
  if [[ " $steps " == *" 08 "* ]]; then steps="${steps/08/} 08"; steps=$(echo $steps); fi
fi

findings_root=${LOOP_FINDINGS:-$ROOT/findings}
[ "$ecosystem" = mesh ] || findings_root=$findings_root/$ecosystem
out=${out:-$findings_root/$(date -u +%F)-$population}
mkdir -p "$out" && out=$(cd "$out" && pwd) && [ -n "$out" ] || die "cannot make the out directory '$out'"

# Rehearsal and live findings never share a directory, nor do two ecosystems' (a run.json without
# "ecosystem" is mesh).
python3 -B - "$out" "$population" "$ecosystem" <<'PY' || die "$out holds findings of another population or ecosystem; use another --out"
import json, sys
from pathlib import Path
out, population, ecosystem = sys.argv[1:]
for f in Path(out).glob("*/finding.json"):
    try:
        p = json.loads(f.read_text()).get("population")
    except (ValueError, AttributeError):
        continue
    if p != population:
        sys.exit(1)
try:
    e = json.loads((Path(out) / "run.json").read_text()).get("ecosystem", "mesh")
except (OSError, ValueError, AttributeError):
    e = ecosystem
if e != ecosystem:
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

# The run's start, before the first step: what the status page measures its minutes from. A directory that
# already holds this day's steps (lab.sh admit, deal, settle, dispute or ask write into it before or after loop.sh) keeps the
# earliest start: of a run.json already there and of each finding there (evidence-network/loop#65).
python3 -B - "$out/run.json" "$stage" "$population" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ecosystem" $steps <<'PY' || die "cannot write $out/run.json"
import datetime as dt, json, sys
from pathlib import Path
path, stage, population, started, ecosystem, *steps = sys.argv[1:]
FORM = "%Y-%m-%dT%H:%M:%SZ"  # zero-padded, so the strings order as the times do
earlier = [Path(path)] + sorted(Path(path).parent.glob("*/finding.json"))
for f in earlier:
    try:
        doc = json.loads(f.read_text())
        t = doc["started"]
        if dt.datetime.strptime(t, FORM).strftime(FORM) != t:  # a real UTC time in exactly loop.sh's form
            continue
    except (OSError, ValueError, KeyError, TypeError):
        continue
    if doc.get("population") == population:  # the guard above already refuses another population's finding
        started = min(started, t)
run = {"stage": stage, "population": population, "started": started, "steps": steps}
if ecosystem != "mesh":
    run["ecosystem"] = ecosystem
with open(path, "w") as f:
    f.write(json.dumps(run, indent=2, sort_keys=True) + "\n")
PY

# The Goose rehearsal: G1's two nodes, run by scripts/goose_rehearsal.py when its tools are installed (steps/goose.setup.sh);
# a step it lists (population.json "goose".steps) then runs on the population it wrote, the rest are NOT_CHECKED below.
# The steps with a Goose input are the driver's (--steps). When the tools are not installed (exit 3) such a step is
# NOT_CHECKED; when they are and the run failed, it is a FAIL with the run's log, never a quiet NOT_CHECKED.
goose_steps="" goose_driven="" goose_state=none
if [ "$ecosystem" = goose ] && [ "$population" = rehearsal ]; then
  goose_driven=$(python3 -B "$ROOT/scripts/goose_rehearsal.py" --steps)
  goose_work=$(mktemp -d) && trap 'rm -rf "$goose_work"' EXIT
  goose_log=$goose_work/goose-rehearsal.log
  python3 -B "$ROOT/scripts/goose_rehearsal.py" --work "$goose_work/work" >"$goose_log" 2>&1
  case $? in
    0) goose_state=ran; pop_dir=$goose_work/work/population; goose_steps=$goose_driven ;;
    3) goose_state=no_tools ;;
    *) goose_state=failed ;;
  esac
  cat "$goose_log" >&2
  [ "$goose_state" = ran ] || echo "loop.sh: the Goose rehearsal did not run ($goose_state); the steps it drives are not run" >&2
fi

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

  if [ "$goose_state" = failed ] && [[ " $goose_driven " == *" $nn "* ]]; then
    cp "$goose_log" "$sdir/goose-rehearsal.log"
    fail "$sdir" "$started" "the Goose rehearsal run failed before step $nn; its log is goose-rehearsal.log" goose-rehearsal.log
    continue
  fi
  goose_note=""
  [ "$goose_state" = no_tools ] && [[ " $goose_driven " == *" $nn "* ]] && goose_note="step $nn has a Goose input, but the Goose tools are not installed on this host (steps/goose.setup.sh)"
  if [ "$ecosystem" != mesh ] && ! { [ "$ecosystem" = goose ] && [[ " $goose_steps " == *" $nn "* ]]; }; then
    python3 -B - "$ROOT" "$sdir" "$started" "$ecosystem" "$nn" "$population" "$goose_note" <<'PY'
import sys
root, step_dir, started, ecosystem, nn, population, note = sys.argv[1:]
# The issue that owns a step with no run yet: loop#150 for muse and cross. For goose (loop#315: its steps 01-12 are
# the same assertions as mesh's, run on evidencebook's G1 node) each step names what it waits on in G1 or in the
# loop: 01 and 03 a live Goose population (loop#330; the rehearsal runs them), 02 the book bundle, 08 loop#193; 04 and 05 the G1 mappings, 06 the live rail, 07 the answerer, 09 the referee delivery; 10, 11 and 12 loop#315 (G1's loop_join returns the commitment).
GOOSE = {"01": "evidence-network/loop#330", "02": "action-state-group/evidencebook#301",
         "03": "evidence-network/loop#330", "04": "action-state-group/evidencebook#302",
         "05": "action-state-group/evidencebook#303", "06": "action-state-group/capsule-emit#408",
         "07": "action-state-group/capsule-emit#407", "08": "evidence-network/loop#193",
         "09": "action-state-group/capsule-emit#393", "12": "evidence-network/loop#315"}
owner = GOOSE.get(nn, "evidence-network/loop#315") if ecosystem == "goose" else "evidence-network/loop#150"
why, detail = "no step for this ecosystem exists yet", f"The {ecosystem} ecosystem has no step {nn} yet; loop.sh ran nothing for it."
if note:  # a step with a Goose input, on a host without the Goose tools
    owner, why, detail = "evidence-network/loop#330", "the Goose tools are not installed", f"{note}; loop.sh ran nothing for it."
sys.path.insert(0, root + "/steps")
import finding
finding.write(step_dir, result="NOT_CHECKED", population=population,
              asserts=f"step {nn} holds for the {ecosystem} ecosystem", pinned=[], proof=[],
              detail=detail,
              not_checked=[{"what": f"step {nn} for the {ecosystem} ecosystem",
                            "why": why,
                            "issue": owner}],
              started=started)
PY
    continue
  fi
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
summary=$(python3 -B - "$ROOT" "$out" "$population" "${ran[@]}" <<'PY'
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
)
echo "$summary"
if [ "$strict" = yes ]; then
  red=$(awk '$3 == "FAIL" || $3 == "MALFORMED"' <<<"$summary")
  if [ "$population" != rehearsal ]; then
    echo "loop.sh: --strict gates only the rehearsal population; $population results are reported, not gated" >&2
  elif [ -n "$red" ]; then
    echo "loop.sh: --strict: rehearsal steps not green:" >&2
    echo "$red" >&2
    [ "$status" -ne 0 ] || status=3
  fi
fi
exit $status
