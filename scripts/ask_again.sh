#!/usr/bin/env bash
# Ask again, on the reporter node, after today's run: one node (ask_again.sh <node_id>)
# or every node with a question unanswered today (no argument). Pull, ask, check,
# commit, push. It only adds attempt directories; nothing already written changes.
# Run by hand, by reporter/systemd/loop-reporter-ask.timer, or by the dispatch
# workflow in reporter/workflows/ once installed. None of these is enabled here.
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

# As in run_daily.sh: the deploy key pushes reports/daily to main and nothing else.
[ "$(git symbolic-ref --short HEAD 2>/dev/null)" = main ] || { echo "not on main" >&2; exit 1; }
git pull --rebase --quiet origin main || exit 1
python3 scripts/daily_report.py ask "${which[@]}" --date "$day" --console "$console" --data-dir "$data_dir" || exit $?
python3 scripts/check_reports.py reports/daily || exit 1
# As in run_daily.sh: a personal-data hit is held on this node, never pushed.
# An ask only adds attempt files, so holding every file not yet in git leaves
# the day exactly as it was committed. Nothing is staged or committed. Exit 3.
if [ -f scripts/check_personal_data.py ] && ! python3 -B scripts/check_personal_data.py reports/daily >&2; then
  held="${LOOP_HELD_DIR:-$HOME/.local/share/loop/held}/$day-ask-$(date -u +%H%M%S)"
  git ls-files -z --others --exclude-standard -- reports/daily | while IFS= read -r -d '' f; do
    mkdir -p "$held/$(dirname "$f")" && mv "$f" "$held/$f"
  done
  find reports/daily -type d -empty -delete
  echo "$day: personal-data check failed; the new attempts are held in $held; nothing committed" >&2
  exit 3
fi
git add reports/daily || exit 1
if ! git diff --cached --quiet; then
  git commit --quiet -s -m "report: $day asked again ($what)" || exit 1
fi
other="$(git log -m --format= --name-only origin/main..HEAD -- . ':!reports/daily')" || exit 1
if [ -n "$other" ]; then
  echo "local commits touch files outside reports/daily, not pushing:" >&2
  echo "$other" >&2
  exit 1
fi
git push --quiet origin HEAD:main || exit 1
