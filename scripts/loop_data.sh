# Sourced by run_daily.sh and ask_again.sh, from the root of the loop clone.
# The reports go to the private raw store, a checkout of evidence-network/loop-data
# named by LOOP_DATA_DIR (required, no default): it sets $data (that checkout),
# $reports (its reports/daily) and data_git (git in it). The loop clone is only
# read: it is brought up to date with origin/main, fast-forward only, and never
# committed to or pushed from.
if [ -z "${LOOP_DATA_DIR:-}" ]; then
  echo "LOOP_DATA_DIR is not set: name the loop-data checkout the reports are pushed to" >&2
  return 1
fi
data="$(cd "$LOOP_DATA_DIR" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "LOOP_DATA_DIR=$LOOP_DATA_DIR is not a git checkout" >&2
  return 1
}
if [ "$data" = "$(git rev-parse --show-toplevel)" ]; then
  echo "LOOP_DATA_DIR is this loop clone: the reports go to the loop-data checkout, never here" >&2
  return 1
fi
reports="$data/reports/daily"
data_git() { git -C "$data" "$@"; }
[ "$(git symbolic-ref --short HEAD 2>/dev/null)" = main ] || { echo "not on main: the loop clone" >&2; return 1; }
git pull --ff-only --quiet origin main || { echo "the loop clone does not fast-forward to origin/main" >&2; return 1; }
