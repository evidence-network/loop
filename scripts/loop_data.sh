# Sourced by run_daily.sh and ask_again.sh, from the root of the loop clone.
# The reports go to the private raw store, a checkout of evidence-network/loop-data
# named by LOOP_DATA_DIR (required, no default): it sets $data (that checkout),
# $reports (its reports/<set>: $report_set if the caller set it, else daily)
# and data_git (git in it). The loop clone is only
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
reports="$data/reports/${report_set:-daily}"
data_git() { git -C "$data" "$@"; }
# The run commits there (with -s): refuse before asking anyone when git has no identity to commit with,
# rather than leave a slot or a day half staged. Set one in that checkout (reporter/README.md).
data_git var GIT_COMMITTER_IDENT > /dev/null 2>&1 || {
  echo "no git identity to commit with in $data: set user.name and user.email there (reporter/README.md)" >&2
  return 1
}
# One run at a time in that checkout: a run started while another holds it (a
# daily run that starts during an on-demand one) waits for it, up to $lock_wait
# seconds (default 600; 0 refuses at once). The lock is held until the caller exits.
if command -v flock > /dev/null; then
  exec 9> "$(data_git rev-parse --absolute-git-dir)/loop-report.lock" || return 1
  if [ "${lock_wait:-600}" = 0 ]; then lock_opt=(-n); else lock_opt=(-w "${lock_wait:-600}"); fi
  flock "${lock_opt[@]}" 9 || { echo "another report run holds $data: not starting" >&2; return 1; }
fi
[ "$(git symbolic-ref --short HEAD 2>/dev/null)" = main ] || { echo "not on main: the loop clone" >&2; return 1; }
git pull --ff-only --quiet origin main || { echo "the loop clone does not fast-forward to origin/main" >&2; return 1; }
