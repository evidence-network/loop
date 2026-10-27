#!/usr/bin/env bash
# One unattended daily run on the reporter node: pull, ask, check, commit, push.
# Run by reporter/systemd/loop-reporter.timer from a clone of this repository.
# Needs: the reporter node running (mesh-llm with plugin key announcement and
# capsules 0.1.5), its console at LOOP_CONSOLE, and a git remote this clone can
# push to. A run that cannot reach the node still commits that day's MISSED.
set -uo pipefail
cd "$(dirname "$0")/.."
console="${LOOP_CONSOLE:-http://127.0.0.1:3131}"
data_dir="${CAPSULES_DATA_DIR:-$HOME/.local/share/capsules}"
day="$(date -u +%F)"

# The reporter judges every answer under the key the node's host announces on
# the mesh, never under a static map: refuse to run with one configured.
if [ -n "${CAPSULES_PEER_KEYS:-}" ]; then
  echo "CAPSULES_PEER_KEYS is set: the reporter takes node keys from the mesh announcement only" >&2
  exit 1
fi

# The deploy key pushes reports/daily to main and nothing else: run only on
# main, and only when every local commit not on origin/main stays in reports/daily.
[ "$(git symbolic-ref --short HEAD 2>/dev/null)" = main ] || { echo "not on main" >&2; exit 1; }
# --rebase: a report committed but not pushed last time goes out on top.
git pull --rebase --quiet origin main || exit 1
python3 scripts/daily_report.py run --date "$day" --console "$console" --data-dir "$data_dir"
status=$?
# 1 = refused before asking anyone (bad list, reporter key not set or not this
# node's): nothing to commit, and the next run will mark this day MISSED.
[ "$status" -eq 1 ] && exit 1
python3 scripts/check_reports.py reports/daily || exit 1
git add reports/daily || exit 1
if [ "$status" -eq 0 ]; then msg="report: daily $day"; else msg="report: $day missed"; fi
if ! git diff --cached --quiet; then
  git commit --quiet -s -m "$msg" || exit 1
fi
other="$(git diff --name-only origin/main HEAD -- . ':!reports/daily')" || exit 1
if [ -n "$other" ]; then
  echo "local commits touch files outside reports/daily, not pushing:" >&2
  echo "$other" >&2
  exit 1
fi
git push --quiet origin HEAD:main || exit 1
exit "$status"
