#!/usr/bin/env bash
# One unattended live run of the loop on the lab host (evidence-network/loop#308): the run a scheduled task starts
# once a day (loop#314). It runs the multi-pass live-17 ran by hand, inside the lab payment guard:
#
#   scripts/live_daily.sh [--dry-run]
#
# Run it on the lab host as the lab operator, from a dedicated clone of this repository whose HEAD is origin/main
# (it fetches and refuses otherwise: steps 05 and 12 read nodes/ and revocations from main). In order:
#
#   1. take $LAB_DIR/run/LIVE_RUN (asg-lab-rebuild skips a host that has one); refuse when it exists, or when a
#      lab.sh or loop.sh process runs; the lock is removed on every exit, a kill included (not SIGKILL)
#   2. lab/lab.sh tools installs every step's pinned tools for this head into LIVE_TOOLS, then lab/lab.sh check and
#      lab/lab.sh running: a setup that fails, pin drift, a node down or on another build, or the lab's test witness down stops the run. tools comes
#      first because check fails on the last tools record: a day whose tools failed is retried the next day
#   3. the window: this run's start, at its whole minute (the plugin stamps records to the minute), goes into
#      population/live/population.json as window.from for steps 06-08 and into LOOP_SEALED_SINCE for 05; the file
#      is restored on exit
#   4. one lab/lab.sh exchange, never retried (lab-2 pays lab-1 1,000 msat; lab-3 asks too and pays 1,000 msat,
#      which lab-1 pays back (step 8b); with no funds it gets 402, loop#384). If lab-2's request does not succeed, step 06 is written here NOT_CHECKED, stating
#      the HTTP status seen (loop#321)
#      Each asker's status goes into the run record (exchange.askers), lab-3's included.
#   5. wait LIVE_CHECKPOINT_WAIT seconds (default 360) for each node's checkpoint over the exchange
#   6. steps 04, 05, bundles, 06, 09, stage 1 (01 02 03 12 07, then 08, which reproduces the earlier ones), 10, 11,
#      into findings/<UTC date>-live-<N>, N the next unused for that date in findings/, the held runs, the unpacked
#      ones (see 9) and the packed ones (a plain <date>-live counts as 1)
#   7. check_no_preimages.py, check_personal_data.py and a search for the lab's invite token over the findings (any
#      hit moves the findings out of the clone to $LIVE_HELD_DIR and nothing is packed), then steps/finding.py check
#   8. ecosystem_gate.py update on a copy of baseline/live.json, then check against baseline/live.json
#   8b. scripts/lab_circulate.py: when lab-3's request was answered 200 (its 1,000 msat charge settled), lab-1 pays lab-3 the
#      same amount back once through the guarded lab-to-lab payment (loop#384); otherwise nothing is paid. No balance or
#      ledger decides. The outcome goes into the record (circulate), and a refusal changes nothing else
#   8c. lab/goose.sh pay (loop#363): the Goose ecosystem's live run, last, after the Mesh pass has finished and its window
#      is restored: two Goose nodes, step 06 one real payment lab-1 to lab-2 of 1,000 msat inside the same lab guard
#      (goose_lightning.py refuses first when the day's log or lab-1's wallet leaves no room). The findings are
#      findings/goose/<UTC date>-live, put through the same privacy checks and steps/finding.py check as the Mesh run's.
#      It cannot change the Mesh result: a Goose pass that fails, is refused, is held or finds that its name is taken
#      leaves the Mesh findings, the exit code and the pack as they were; its outcome goes into the run record (goose)
#   9. $LIVE_OUT_DIR/<name>.tgz (the findings directory, and the Goose one when 8c produced it), its .sha256, and <name>.json, the run's record
#      (head, start, window, exchange outcome, each step's result, the gate's rc). Commits and pushes nothing. The findings
#      directory does not stay in the clone (the tgz holds it): a run that stopped before packing leaves it in
#      $LIVE_OUT_DIR/unpacked/<name>, which keeps its N taken, so the next run starts from a clean tree.
#
# --dry-run prints the plan, the name it would take and the checks it would make, and changes nothing (no lock,
# no fetch, no exchange).
#
# Exit: 0 the run finished and its findings are packed; 1 the same, but the gate found a regression against
# baseline/live.json; 2 usage or setup; 3 another run holds the lab (LIVE_RUN, lab.sh or loop.sh); 4 the clone is
# not a clean origin/main; 5 lab.sh tools, check or running failed; 6 finding.py check refused the findings;
# 7 a privacy check hit, findings held; 8 the run finished and is packed, but its one payment was refused, so 06 is
# NOT_CHECKED and the baseline must not move (no other step regressed; one that did makes it 1). Every refusal
# prints one line "live_daily: <reason>".
#
# Environment:
#   LOOP_LIVE_ENV         a file to source first for the lab's environment (LAB_DIR, MESH_LLM_BIN, CAPSULES_BIN,
#                         LAB_PAYMENTS, LAB_TWINS...), such as the lab operator's labenv file; optional
#   LAB_DIR               the lab directory (lab/lab.sh); required, here or from LOOP_LIVE_ENV
#   LIVE_TOOLS            the steps' LOOP_TOOLS: where lab.sh tools installs and the steps run them from (default
#                         ~/.cache/evidence-network-loop-live, the daily run's own, so no other clone's tools change)
#   LIVE_STEP_TIMEOUT     seconds each lab.sh call and step 10 or 11 may take (default 3600; loop.sh still limits each
#                         stage 1 step to LOOP_STEP_TIMEOUT); one that runs over is killed and its step is a FAIL
#   LIVE_CHECKPOINT_WAIT  seconds to wait after the exchange (default 360: a node cuts a checkpoint 300 s after activity)
#   LIVE_OUT_DIR          where the tgz and the run record go, and unpacked/ (mode 700) for findings of a run that
#                         stopped before packing (default $LAB_DIR/out/live)
#   LIVE_HELD_DIR         where findings that fail a privacy check go (default $LAB_DIR/held, mode 700)
#   LIVE_LAB_SH           the lab.sh to run (default lab/lab.sh here; tests use a stand-in)
#   LIVE_CIRCULATE        the executable step 8b runs (default scripts/lab_circulate.py; tests use a stand-in)
#   LIVE_STEPS_DIR        where steps 10 and 11 are (default steps/ here; tests use stand-ins)
#   LIVE_GOOSE_SH         the goose.sh step 8c runs (default lab/goose.sh here; tests use a stand-in)
#   LIVE_GOOSE_TIMEOUT    seconds step 8c's goose.sh pay may take (default 1800); it also gets at most the time left before
#                         LIVE_DEADLINE (default 9000 s after the start, under entry.sh's 3 h kill) minus a margin, and is
#                         skipped when that is under 600 s, so a slow Goose pass cannot cost the Mesh pack
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DRY=0
case "${1:-}" in
  --dry-run) DRY=1; shift ;;
  -h | --help) sed -n '5p' "$0" | sed 's/^#   //' >&2; exit 2 ;;
esac
[ "$#" -eq 0 ] || { echo "usage: scripts/live_daily.sh [--dry-run]" >&2; exit 2; }

refuse() { # CODE REASON
  echo "live_daily: $2" >&2
  exit "$1"
}
log() { echo "live_daily: $*" >&2; }

if [ -n "${LOOP_LIVE_ENV:-}" ]; then
  [ -r "$LOOP_LIVE_ENV" ] || refuse 2 "LOOP_LIVE_ENV $LOOP_LIVE_ENV is not readable"
  # shellcheck disable=SC1090
  . "$LOOP_LIVE_ENV" > /dev/null || refuse 2 "sourcing LOOP_LIVE_ENV failed"
fi
cd "$ROOT" || exit 2 # an env file may cd elsewhere
[ -n "${LAB_DIR:-}" ] || refuse 2 "set LAB_DIR (or LOOP_LIVE_ENV)"
LAB_DIR=${LAB_DIR%/}
export LAB_DIR
LAB_SH=${LIVE_LAB_SH:-$ROOT/lab/lab.sh}
STEPS=${LIVE_STEPS_DIR:-$ROOT/steps}
CIRCULATE=${LIVE_CIRCULATE:-$ROOT/scripts/lab_circulate.py}
GOOSE_SH=${LIVE_GOOSE_SH:-$ROOT/lab/goose.sh}
WAIT=${LIVE_CHECKPOINT_WAIT:-360}
STEP_TIMEOUT=${LIVE_STEP_TIMEOUT:-3600}
GOOSE_TIMEOUT=${LIVE_GOOSE_TIMEOUT:-1800}
DEADLINE_S=${LIVE_DEADLINE:-9000}
START_S=$(date +%s)
export LOOP_TOOLS=${LIVE_TOOLS:-$HOME/.cache/evidence-network-loop-live}
OUT_DIR=${LIVE_OUT_DIR:-$LAB_DIR/out/live}
HELD_DIR=${LIVE_HELD_DIR:-$LAB_DIR/held}
LOCK=$LAB_DIR/run/LIVE_RUN
POP=population/live/population.json
[[ $WAIT =~ ^[0-9]+$ ]] || refuse 2 "LIVE_CHECKPOINT_WAIT must be a whole number of seconds"
[[ $GOOSE_TIMEOUT =~ ^[1-9][0-9]*$ && $DEADLINE_S =~ ^[1-9][0-9]*$ ]] || refuse 2 "LIVE_GOOSE_TIMEOUT and LIVE_DEADLINE must be positive whole numbers of seconds"
[[ $STEP_TIMEOUT =~ ^[1-9][0-9]*$ ]] || refuse 2 "LIVE_STEP_TIMEOUT must be a positive whole number of seconds"
command -v timeout > /dev/null || refuse 2 "needs timeout (GNU coreutils) on PATH"

STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
SINCE=${STARTED_AT:0:16}:00Z
DAY=${STARTED_AT:0:10}

# The next unused <DAY>-live-<N>: one past the highest the day has taken in findings/, among held runs, runs left in
# $OUT_DIR/unpacked and packed runs (their tgz or record), a plain <DAY>-live counting as 1.
next_name() {
  local d n max=0
  for d in "$ROOT/findings/$DAY"-live* "$HELD_DIR/$DAY"-live* "$OUT_DIR/unpacked/$DAY"-live* "$OUT_DIR/$DAY"-live*.json "$OUT_DIR/$DAY"-live*.tgz; do
    [ -e "$d" ] || continue
    d=${d##*/}
    d=${d%.json}
    d=${d%.tgz}
    if [ "$d" = "$DAY-live" ]; then n=1
    elif [[ $d =~ ^$DAY-live-([0-9]+)$ ]]; then n=$((10#${BASH_REMATCH[1]}))
    else continue
    fi
    [ "$n" -gt "$max" ] && max=$n
  done
  echo "$DAY-live-$((max + 1))"
}

# Another run on the lab: a process running a lab.sh or loop.sh script, from any clone, as the program or as a shell's
# script argument (`lab/lab.sh ...`, `bash lab/lab.sh ...`); not a process that only names one (an editor, a grep).
# This script starts its own only after this check.
other_run() {
  local p args w found=1
  for p in $(pgrep -f -- '(lab|loop)\.sh'); do
    [ "$p" = "$$" ] && continue
    args=$(ps -o args= -p "$p") || continue
    read -r -a w <<< "$args"
    if [[ ${w[0]##*/} =~ ^(bash|sh|dash|zsh)$ ]]; then
      w=("${w[@]:1}")
      # The shell's own options come before the script: -x, -eu, -o pipefail, +o posix.
      while [[ ${w[0]:-} == [-+]* ]]; do
        [[ ${w[0]} == [-+][oO] ]] && w=("${w[@]:1}")
        w=("${w[@]:1}")
      done
    fi
    case "${w[0]:-}" in lab.sh | */lab.sh | loop.sh | */loop.sh) echo "$p $args"; found=0 ;; esac
  done
  return $found
}
sha256() { if command -v sha256sum > /dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

NAME=$(next_name)
OUT=$ROOT/findings/$NAME
GNAME=$DAY-live # the Goose run's findings/goose/ name (8c)
GOUT=$ROOT/findings/goose/$GNAME

if [ "$DRY" = 1 ]; then
  echo "live_daily --dry-run at $(git -C "$ROOT" rev-parse --short HEAD 2> /dev/null || echo '<no git>'); nothing is changed"
  [ -e "$LOCK" ] && echo "  NOTE: $LOCK exists: a real run would refuse (exit 3)"
  other_run > /dev/null && echo "  NOTE: a lab.sh or loop.sh process runs: a real run would refuse (exit 3)"
  cat << EOF
  1. take $LOCK; git fetch origin main; HEAD must be origin/main with no tracked change
  2. $LAB_SH tools into LOOP_TOOLS=$LOOP_TOOLS; $LAB_SH check; $LAB_SH running
  3. window.from = $SINCE in $POP (restored on exit); LOOP_SEALED_SINCE=$SINCE
  4. $LAB_SH exchange, once: lab-2 pays lab-1 1,000 msat (lab-3 asks too); a refusal leaves 06 NOT_CHECKED, no retry
  5. wait ${WAIT}s for the checkpoints
  6. LAB_OUT=findings/$NAME: lab.sh admit, deal, bundles, settle, dispute, loop; $STEPS/10-judge, $STEPS/11-report
  7. check_no_preimages.py, check_personal_data.py, invite token search (a hit: held in $HELD_DIR); steps/finding.py check
  8. ecosystem_gate.py update on a copy of baseline/live.json, check against baseline/live.json
  8b. scripts/lab_circulate.py: when lab-3's request got 200, lab-1 pays lab-3 1,000 msat back, once (a second real payment)
  8c. $GOOSE_SH pay as the last pass: findings/goose/$GNAME, lab-1 pays lab-2 1,000 msat once (a third real payment); its privacy
      checks and finding.py check; it never changes the Mesh result
  9. $OUT_DIR/$NAME.tgz, $NAME.tgz.sha256, $NAME.json
EOF
  exit 0
fi

# --- 1. the lock, other runs, the clone -------------------------------------------------------------------------
mkdir -p "$LAB_DIR/run" || refuse 2 "cannot make $LAB_DIR/run"
if ! (set -o noclobber; printf '{"pid": %d, "started": "%s"}\n' "$$" "$STARTED_AT" > "$LOCK") 2> /dev/null; then
  refuse 3 "$LOCK exists ($(head -c 200 "$LOCK" 2> /dev/null)): another live run holds the lab, or one was killed; remove it only after checking"
fi
CHILD=""
POP_TOUCHED=0
OWN_OUT=0
OWN_GOUT=0
# Each process is stopped as it is listed, so a stopped parent cannot start another before the TERM; then all get it.
tree() { local c; kill -STOP "$1" 2> /dev/null; echo "$1"; for c in $(pgrep -P "$1"); do tree "$c"; done; }
kill_tree() {
  local all
  all=$(tree "$1")
  # shellcheck disable=SC2086
  kill -TERM $all 2> /dev/null; kill -CONT $all 2> /dev/null
}
# The run leaves no findings in the clone, so the next run starts from a clean tree: a packed run's copy is in its tgz;
# one that stopped before packing goes to $OUT_DIR/unpacked/$NAME (mode 700, since it may not have been scanned yet;
# its N stays taken, see next_name).
leave_clone() {
  if [ "$OWN_GOUT" = 1 ] && [ -d "$GOUT" ]; then
    if [ -f "$OUT_DIR/$NAME.tgz" ]; then rm -rf "$GOUT"
    else mkdir -p "$OUT_DIR/unpacked" && chmod 700 "$OUT_DIR/unpacked" && [ ! -e "$OUT_DIR/unpacked/goose-$GNAME" ] \
      && mv "$GOUT" "$OUT_DIR/unpacked/goose-$GNAME" && log "the Goose findings stay unpacked in $OUT_DIR/unpacked/goose-$GNAME"
    fi || log "could not move $GOUT out of the clone: remove it by hand before the next run"
  fi
  [ "$OWN_OUT" = 1 ] && [ -d "$OUT" ] || return 0
  if [ -f "$OUT_DIR/$NAME.tgz" ]; then rm -rf "$OUT"
  else mkdir -p "$OUT_DIR/unpacked" && chmod 700 "$OUT_DIR/unpacked" && [ ! -e "$OUT_DIR/unpacked/$NAME" ] \
    && mv "$OUT" "$OUT_DIR/unpacked/$NAME" && log "the findings stay unpacked in $OUT_DIR/unpacked/$NAME"
  fi || log "could not move $OUT out of the clone: remove it by hand before the next run"
}
cleanup() {
  [ -n "$CHILD" ] && kill_tree "$CHILD"
  leave_clone
  [ "$POP_TOUCHED" = 1 ] && git -C "$ROOT" checkout -q -- "$POP"
  rm -f "$LOCK"
}
trap cleanup EXIT
trap 'log "stopped by a signal"; exit 143' INT TERM HUP
# A long command runs in the background and is waited on, so a signal stops the run at once and its tree is killed.
waited() { "$@" & CHILD=$!; wait "$CHILD"; local rc=$?; CHILD=""; return $rc; }
# The same, killed after STEP_TIMEOUT seconds (exit 124).
bounded() { waited timeout -k 30 "$STEP_TIMEOUT" "$@"; }

others=$(other_run) && refuse 3 "another lab run is in progress: ${others//$'\n'/; }"
git -C "$ROOT" fetch -q origin main || refuse 4 "git fetch origin main failed"
HEAD=$(git -C "$ROOT" rev-parse HEAD)
MAIN=$(git -C "$ROOT" rev-parse FETCH_HEAD)
[ "$HEAD" = "$MAIN" ] || refuse 4 "HEAD $HEAD is not origin/main $MAIN: update the clone first"
[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ] || refuse 4 "the clone has tracked changes"
[ ! -e "$OUT" ] || refuse 4 "$OUT exists"
OWN_OUT=1
log "run $NAME at $HEAD, window from $SINCE"

# --- 2. the lab -----------------------------------------------------------------------------------------------------
mkdir -p "$LAB_DIR/logs" || refuse 2 "cannot make $LAB_DIR/logs"
bounded "$LAB_SH" tools > "$LAB_DIR/logs/live-tools.log" 2>&1
rc=$?
[ "$rc" = 0 ] || refuse 5 "lab.sh tools exited $rc: a step's pinned tool did not install in $LOOP_TOOLS (see $LAB_DIR/logs/live-tools.log)"
bounded "$LAB_SH" check > /dev/null
rc=$?
[ "$rc" = 0 ] || [ "$rc" = 1 ] || refuse 5 "lab.sh check exited $rc"
[ "$rc" = 0 ] || refuse 5 "lab.sh check failed: pin drift against versions.lock (see $LAB_DIR/build.json)"
bounded "$LAB_SH" running
rc=$?
[ "$rc" = 0 ] || [ "$rc" = 1 ] || refuse 5 "lab.sh running exited $rc"
[ "$rc" = 0 ] || refuse 5 "lab.sh running failed: a node or the test witness is down, or a node runs another build (see its stderr)"

# --- 3. the window --------------------------------------------------------------------------------------------------
POP_TOUCHED=1
python3 -B - "$POP" "$SINCE" << 'PY' || refuse 2 "cannot set the window in population/live/population.json"
import json, sys
path, since = sys.argv[1:]
pop = json.load(open(path))
pop["window"] = {"from": since}
open(path, "w").write(json.dumps(pop, indent=2) + "\n")
PY
export LOOP_SEALED_SINCE=$SINCE LAB_OUT=$OUT
mkdir -p "$OUT"

# --- 4. the one paid exchange ---------------------------------------------------------------------------------------
rm -f "$LAB_DIR/exchange/lab-2.headers.txt" "$LAB_DIR/exchange/lab-3.headers.txt"
bounded "$LAB_SH" exchange
exchange_rc=$?
status_line() { [ -s "$1" ] && tr -d '\r' < "$1" | awk 'toupper($1) ~ /^HTTP\// { s = $0 } END { print s }'; }
paid_status=$(status_line "$LAB_DIR/exchange/lab-2.headers.txt")
case "$paid_status" in
  "HTTP/"*" 200"*) refused="" ;;
  "") refused="no HTTP answer was recorded for lab-2's paid request to lab-1 (lab.sh exchange exited $exchange_rc)" ;;
  *) refused="lab-2's paid request to lab-1 got '$paid_status' (lab.sh exchange exited $exchange_rc)" ;;
esac
[ -z "$refused" ] && [ "$exchange_rc" -ne 0 ] && log "lab.sh exchange exited $exchange_rc after lab-2's paid exchange; the steps judge what was recorded"
[ -n "$refused" ] && log "$refused; step 06 is NOT_CHECKED, no retry"
# lab-3 asks too and pays 1,000 msat, which lab-1 pays back when it settles (step 8b); with no funds it gets 402. Recorded, never judged.
lab3_status=$(status_line "$LAB_DIR/exchange/lab-3.headers.txt")
log "lab-3's request to lab-1 got '${lab3_status:-no HTTP answer}'"

# --- 5. the checkpoints ---------------------------------------------------------------------------------------------
log "waiting ${WAIT}s for the checkpoints"
waited sleep "$WAIT"

# --- 6. the steps ---------------------------------------------------------------------------------------------------
# A step that writes no finding gets a FAIL naming why, as loop.sh does for stage 1.
ensure_finding() { # STEP_DIR_NAME RC
  [ -s "$OUT/$1/finding.json" ] && return 0
  mkdir -p "$OUT/$1"
  python3 -B - "$ROOT" "$OUT/$1" "$STARTED_AT" "steps/$1 exited $2 and wrote no finding" << 'PY'
import sys
root, step_dir, started, detail = sys.argv[1:]
sys.path.insert(0, root + "/steps")
import finding
finding.write(step_dir, result="FAIL", population="live", asserts="the step runs to the end and writes its finding",
              pinned=[], proof=[], detail=detail, started=started)
PY
}
settle_refused() {
  mkdir -p "$OUT/06-settle"
  python3 -B - "$ROOT" "$OUT/06-settle" "$STARTED_AT" "$SINCE" "$refused" << 'PY'
import importlib.machinery, importlib.util, sys
root, step_dir, started, since, refused = sys.argv[1:]
sys.path.insert(0, root + "/steps")
import finding
spec = importlib.util.spec_from_loader("settle", importlib.machinery.SourceFileLoader("settle", root + "/steps/06-settle"))
settle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settle)
finding.write(step_dir, result="NOT_CHECKED", population="live", asserts=settle.LIGHTNING_ASSERTS, pinned=[], proof=[],
              detail=f"{refused}; step 06 was not judged on this run's window (from {since}), and a payment may still "
                     "have settled. The run makes one paid exchange and never retries it; this is not a result of "
                     "the step's code.",
              not_checked=[{"what": f"a settlement of this run's paid exchange (window from {since})",
                            "why": f"{refused}; step 06 was not judged on this window, and a payment may still have settled",
                            "issue": "evidence-network/loop#321"}],
              started=started)
PY
}
bounded "$LAB_SH" admit; ensure_finding 04-admit $?
bounded "$LAB_SH" deal; ensure_finding 05-deal $?
if [ -z "$refused" ]; then
  bounded "$LAB_SH" bundles || log "lab.sh bundles exited $?; step 06 judges the checks it finds"
  bounded "$LAB_SH" settle; ensure_finding 06-settle $?
else
  settle_refused || refuse 6 "cannot write step 06's NOT_CHECKED finding"
fi
bounded "$LAB_SH" dispute; ensure_finding 09-dispute $?
bounded "$LAB_SH" loop || log "lab.sh loop exited $?"
bounded env LAB_DIR="$LAB_DIR" "$STEPS/10-judge" --population "$ROOT/population/live" --out "$OUT/10-judge"; ensure_finding 10-judge $?
bounded env LAB_DIR="$LAB_DIR" "$STEPS/11-report" --population "$ROOT/population/live" --out "$OUT/11-report"; ensure_finding 11-report $?

# --- 7. the checks --------------------------------------------------------------------------------------------------
# The privacy checks come first, so findings that failed nothing else are never left unscanned.
python3 -B "$ROOT/status/findings.py" "$OUT" > /dev/null || log "status/findings.py could not write index.md"
hold() {
  mkdir -p "$HELD_DIR" && chmod 700 "$HELD_DIR" && [ ! -e "$HELD_DIR/$NAME" ] && mv "$OUT" "$HELD_DIR/$NAME" \
    || refuse 7 "$1, and moving $OUT to $HELD_DIR failed: remove it by hand"
  refuse 7 "$1: the findings are held in $HELD_DIR/$NAME, nothing is packed"
}
python3 -B "$ROOT/scripts/check_no_preimages.py" "$OUT" > /dev/null || hold "check_no_preimages.py found a preimage"
python3 -B "$ROOT/scripts/check_personal_data.py" "$OUT" > /dev/null || hold "check_personal_data.py found personal data"
# The invite token is in node logs and must not be in the findings; grep names files only, never the token.
if [ -s "$LAB_DIR/run/invite.token" ] && grep -rlqF -f "$LAB_DIR/run/invite.token" "$OUT"; then
  hold "the lab's invite token is in the findings"
fi
python3 -B "$ROOT/steps/finding.py" check "$OUT" || refuse 6 "steps/finding.py check refused $OUT"

# --- 8. the gate ----------------------------------------------------------------------------------------------------
gate=$(mktemp) || exit 2
cp "$ROOT/baseline/live.json" "$gate"
gate_rc=0
# update refuses a step that regressed against the baseline, naming it; check then holds the pins to it.
gate_out=$(python3 -B "$ROOT/scripts/ecosystem_gate.py" update --ecosystem mesh --findings "$OUT" --baseline "$gate" 2>&1) \
  || gate_rc=$?
if [ "$gate_rc" = 0 ]; then
  gate_out=$(python3 -B "$ROOT/scripts/ecosystem_gate.py" check --old "$ROOT/baseline/live.json" --new "$gate" 2>&1) || gate_rc=$?
fi
[ -n "$gate_out" ] && echo "$gate_out" >&2
rm -f "$gate"
# With no paid exchange, 06 PASS -> NOT_CHECKED is that, not a regression of the step. Anything else the gate says
# (another step, a pin, a baseline it cannot read) still counts.
gate_other=$(grep -v -x -e 'mesh step 06: PASS -> NOT_CHECKED' -e '' <<< "$gate_out")

# --- 8b. lab-3's next charge ----------------------------------------------------------------------------------------
# After every step has read the books: a payment now falls before the next run's window. Its outcome is recorded and
# never changes the run's exit code (loop#384).
circ_out=$(mktemp) || circ_out=/dev/null # no temporary file: the report below falls back to unknown, the run goes on
bounded env LAB_DIR="$LAB_DIR" LIVE_LAB3_STATUS="$lab3_status" "$CIRCULATE" < /dev/null > "$circ_out" 2> /dev/null
circ_rc=$?
circulate=$(python3 -B - "$circ_out" "$circ_rc" << 'PY'
import json, sys
path, rc = sys.argv[1:]
try:
    report = json.loads(open(path).read().strip().splitlines()[-1])
    assert isinstance(report, dict) and isinstance(report.get("action"), str)
except Exception:
    # No usable report: a payment may or may not have gone out, so this is not "none".
    report = {"action": "unknown", "reason": f"lab_circulate.py gave no report (exit {rc})"}
print(json.dumps(report, sort_keys=True))
PY
)
[ "$circ_out" = /dev/null ] || rm -f "$circ_out"
log "lab-3 top-up: $circulate"

# --- 8c. the Goose pass -----------------------------------------------------------------------------------------------
# Last, so a Goose failure can change nothing the Mesh run produced. The Mesh window goes first: the Goose run reads the
# live population file as committed, not with this run's window.
goose_status=ran
goose_reason=""
goose_rc=""
goose_step06=""
goose_skip() { goose_status=$1; goose_reason=$2; log "goose: $1: $2"; }
if [ "$POP_TOUCHED" = 1 ]; then git -C "$ROOT" checkout -q -- "$POP" && POP_TOUCHED=0; fi
unset LOOP_SEALED_SINCE
if [ "$POP_TOUCHED" = 1 ]; then
  goose_skip skipped "the Mesh window could not be restored"
elif [ -e "$GOUT" ] || [ -e "$HELD_DIR/goose-$GNAME" ] || [ -e "$OUT_DIR/unpacked/goose-$GNAME" ] \
     || grep -qsF "\"name\": \"$GNAME\"" "$OUT_DIR/$DAY"-live*.json; then
  # (a packed run of this day that carried it, and has not reached main yet, counts as taking it)
  goose_skip skipped "findings/goose/$GNAME is already taken"
else
  # lab.sh tools (step 2) already installed every step's setup, goose.setup.sh included, into the same LOOP_TOOLS.
  left=$((DEADLINE_S - ($(date +%s) - START_S) - 300))
  gt=$GOOSE_TIMEOUT
  [ "$left" -lt "$gt" ] && gt=$left
  if [ "$left" -lt 600 ]; then
    goose_skip skipped "only ${left}s are left before the run's deadline; the Mesh pack comes first"
  else
    OWN_GOUT=1
    mkdir -p "$(dirname "$GOUT")"
    # Reasons name files by their short names only: the record is published (the lab's paths are not).
    waited timeout -k 30 "$gt" env LAB_DIR="$LAB_DIR" LAB_OUT="$GOUT" GOOSE_IN_LIVE_DAILY=1 "$GOOSE_SH" pay > "$LAB_DIR/logs/live-goose.log" 2>&1
    goose_rc=$?
    if ! compgen -G "$GOUT/*/finding.json" > /dev/null; then
      goose_skip failed "goose.sh pay exited $goose_rc and wrote no findings (logs/live-goose.log on the lab host)"
    fi
  fi
  if [ "$goose_status" = ran ]; then
    # What the pack may hold, by the harvest's own member rule: nothing else reaches the release (a symlink, a stray file).
    if ! python3 -B - "$ROOT" "$GOUT" "$GNAME" << 'PY'
import os, sys
sys.path.insert(0, sys.argv[1] + "/scripts")
import live_harvest
root, prefix = sys.argv[2], f"findings/goose/{sys.argv[3]}"
for d, dirs, files in os.walk(root, followlinks=False):
    for n in dirs + files:
        p = os.path.join(d, n)
        rel = prefix + "/" + os.path.relpath(p, root)
        if os.path.islink(p) or not live_harvest.allowed_member(rel, prefix) or not (os.path.isdir(p) or os.path.isfile(p)):
            sys.exit(f"not allowed in the pack: {rel}")
PY
    then
      mkdir -p "$OUT_DIR/unpacked" && chmod 700 "$OUT_DIR/unpacked" && [ ! -e "$OUT_DIR/unpacked/goose-$GNAME" ] \
        && mv "$GOUT" "$OUT_DIR/unpacked/goose-$GNAME" || rm -rf "$GOUT"
      goose_skip refused "the Goose findings hold a member the harvest would refuse; kept as unpacked/goose-$GNAME on the lab host, not packed"
    elif ! python3 -B "$ROOT/scripts/check_no_preimages.py" "$GOUT" > /dev/null \
       || ! python3 -B "$ROOT/scripts/check_personal_data.py" "$GOUT" > /dev/null \
       || { [ -s "$LAB_DIR/run/invite.token" ] && grep -rlqF -f "$LAB_DIR/run/invite.token" "$GOUT"; }; then
      mkdir -p "$HELD_DIR" && chmod 700 "$HELD_DIR" && [ ! -e "$HELD_DIR/goose-$GNAME" ] && mv "$GOUT" "$HELD_DIR/goose-$GNAME" \
        || rm -rf "$GOUT" # never left in the clone, where the pack would take it
      goose_skip held "a privacy check hit the Goose findings; held as goose-$GNAME on the lab host, not packed"
    elif ! python3 -B "$ROOT/steps/finding.py" check "$GOUT" > /dev/null 2>&1; then
      mkdir -p "$OUT_DIR/unpacked" && chmod 700 "$OUT_DIR/unpacked" && [ ! -e "$OUT_DIR/unpacked/goose-$GNAME" ] \
        && mv "$GOUT" "$OUT_DIR/unpacked/goose-$GNAME" || rm -rf "$GOUT"
      goose_skip refused "steps/finding.py check refused the Goose findings; kept as unpacked/goose-$GNAME on the lab host, not packed"
    else
      goose_step06=$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["result"])' "$GOUT/06-settle/finding.json" 2> /dev/null || echo missing)
      log "goose: $GNAME packed with 06-settle $goose_step06 (goose.sh pay exited $goose_rc)"
      # A guard refusal leaves 06 NOT_CHECKED with no payment; the session logged why (names and amounts only).
      if [ "$goose_step06" != PASS ]; then
        goose_reason=$(grep -a -h -m1 '^goose_live: payment refused: ' "$LAB_DIR/logs/live-goose.log" 2> /dev/null | cut -c1-300 | tr -d '\r')
        goose_reason=${goose_reason#goose_live: }
      fi
    fi
  fi
fi

# --- 9. the pack ----------------------------------------------------------------------------------------------------
mkdir -p "$OUT_DIR" || refuse 2 "cannot make $OUT_DIR"
pack=("findings/$NAME")
[ "$goose_status" = ran ] && pack+=("findings/goose/$GNAME")
tar czf "$OUT_DIR/$NAME.tgz.tmp" -C "$ROOT" "${pack[@]}" && mv "$OUT_DIR/$NAME.tgz.tmp" "$OUT_DIR/$NAME.tgz" \
  || refuse 2 "cannot write $OUT_DIR/$NAME.tgz"
(cd "$OUT_DIR" && sha256 "$NAME.tgz" > "$NAME.tgz.sha256") || refuse 2 "cannot hash $NAME.tgz"
python3 -B - "$OUT" "$OUT_DIR/$NAME.json" "$NAME" "$HEAD" "$STARTED_AT" "$SINCE" "$refused" "$paid_status" "$gate_rc" "$lab3_status" "$circulate" "$goose_status" "$goose_reason" "$goose_rc" "$goose_step06" "$GNAME" << 'PY'
import json, sys
from pathlib import Path
out, path, name, head, started, since, refused, status, gate_rc, lab3_status, circulate, g_status, g_reason, g_rc, g_06, g_name = sys.argv[1:]
steps = {}
for f in sorted(Path(out).glob("*/finding.json")):
    steps[f.parent.name] = json.loads(f.read_text())["result"]
run = {"name": name, "head": head, "started": started, "window_from": since,
       "exchange": {"paid": not refused, "status": status or None, "refused": refused or None,
                    "askers": {"lab-2": status or None, "lab-3": lab3_status or None}},
       "circulate": json.loads(circulate),
       "goose": {"status": g_status, "name": g_name if g_status == "ran" else None, "reason": g_reason or None,
                 "exit": int(g_rc) if g_rc else None, "step_06": g_06 or None},
       "steps": steps, "gate_check_rc": int(gate_rc)}
Path(path).write_text(json.dumps(run, indent=2, sort_keys=True) + "\n")
for step, result in steps.items():
    print(step, result)
PY
log "done: $OUT_DIR/$NAME.tgz ($(cut -c1-12 "$OUT_DIR/$NAME.tgz.sha256")), gate check rc $gate_rc"
if [ "$gate_rc" != 0 ] && { [ -z "$refused" ] || [ -n "$gate_other" ] || [ -z "$gate_out" ]; }; then exit 1; fi
[ -n "$refused" ] && exit 8
exit 0
