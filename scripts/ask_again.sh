#!/usr/bin/env bash
# Ask again, on the reporter node, after today's run: one node (ask_again.sh <node_id>)
# or every node with a question unanswered today (no argument). Pull, ask, check,
# commit, push. It only adds attempt directories; nothing already written changes.
# Run by hand, by reporter/systemd/loop-reporter-ask.timer, or by the dispatch
# workflow in reporter/workflows/ once installed. None of these is enabled here.
# As in run_daily.sh, the reports live in the loop-data checkout at LOOP_DATA_DIR;
# this clone is only read.
set -uo pipefail
cd "$(dirname "$0")/.."
console="${LOOP_CONSOLE:-http://127.0.0.1:3131}"
data_dir="${CAPSULES_DATA_DIR:-$HOME/.local/share/capsules}"
day="$(date -u +%F)"

if [ -n "${CAPSULES_PEER_KEYS:-}" ]; then
  echo "CAPSULES_PEER_KEYS is set: the reporter takes node keys from the mesh announcement only" >&2
  exit 1
fi
if [ "$#" -gt 0 ]; then which=(--node "$1"); what="$1"; else which=(--absent); what="nodes absent"; fi

. scripts/loop_data.sh || exit 1
# As in run_daily.sh: the deploy key pushes reports/daily to loop-data's main and nothing else.
[ "$(data_git symbolic-ref --short HEAD 2>/dev/null)" = main ] || { echo "not on main: $data" >&2; exit 1; }
data_git pull --rebase --quiet origin main || exit 1
python3 scripts/daily_report.py ask "${which[@]}" --date "$day" --console "$console" --data-dir "$data_dir" --out "$reports" || exit $?
python3 scripts/check_reports.py "$reports" || exit 1
# As in run_daily.sh: a personal-data hit is held on this node, never pushed.
# An ask only adds attempt files, so holding every file not yet in git leaves
# the day exactly as it was committed. Nothing is staged or committed. Exit 3.
if [ -f scripts/check_personal_data.py ] && ! python3 -B scripts/check_personal_data.py "$reports" >&2; then
  held="${LOOP_HELD_DIR:-$HOME/.local/share/loop/held}/$day-ask-$(date -u +%H%M%S)"
  data_git ls-files -z --others --exclude-standard -- reports/daily | while IFS= read -r -d '' f; do
    mkdir -p "$held/$(dirname "$f")" && mv "$data/$f" "$held/$f"
  done
  find "$reports" -mindepth 1 -type d -empty -delete
  echo "$day: personal-data check failed; the new attempts are held in $held; nothing committed" >&2
  exit 3
fi
data_git add reports/daily || exit 1
if ! data_git diff --cached --quiet; then
  data_git commit --quiet -s -m "report: $day asked again ($what)" || exit 1
fi
other="$(data_git log -m --format= --name-only origin/main..HEAD -- . ':!reports/daily')" || exit 1
if [ -n "$other" ]; then
  echo "local commits touch files outside reports/daily, not pushing:" >&2
  echo "$other" >&2
  exit 1
fi
data_git push --quiet origin HEAD:main || exit 1
