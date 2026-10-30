#!/usr/bin/env bash
# One unattended daily run on the reporter node: pull, ask, check, commit, push.
# Run by reporter/systemd/loop-reporter.timer from a clone of this repository.
# Needs: the reporter node running (mesh-llm with plugin key announcement and
# capsules 0.1.5), its console at LOOP_CONSOLE, and LOOP_DATA_DIR: a checkout of
# the private raw store (evidence-network/loop-data) on main that the reporter's
# deploy key pushes to. The reports go there, under reports/daily; this clone
# is only read (scripts, nodes/, reporter.json) and never committed to.
# A run that cannot reach the node still commits that day's MISSED.
#
# `run_daily.sh --set rehearsal` (or LOOP_REPORT_SET=rehearsal) is the rehearsal:
# a test of this same path, run by reporter/systemd/loop-reporter-rehearsal.timer
# every 10 minutes. It writes one report per 10-minute UTC slot under
# reports/rehearsal, stages and pushes only that, and its retries are bounded so
# it settles inside the slot (scripts/daily_report.py). The default set is daily.
# The reporter's own node is asked through LOOP_SECOND_CONSOLE when it is set
# (and LOOP_SELF_NODE, its label, is checked), in every set.
#
# `run_daily.sh --set ondemand` is one extra ask round made by hand between
# dailies (scripts/refresh_now.sh runs it): one report under
# reports/ondemand/<YYYY-MM-DD>T<HHMMSS>Z, named by the second it started, its
# retries bounded to settle in about two minutes. It refuses to start while the
# daily, ask-again or rehearsal service is active, or with anything staged in
# the loop-data checkout; it commits only its own run's directory, and a run
# that cannot reach its own node writes and commits nothing (exit 2).
set -uo pipefail
cd "$(dirname "$0")/.."
console="${LOOP_CONSOLE:-http://127.0.0.1:3131}"
data_dir="${CAPSULES_DATA_DIR:-$HOME/.local/share/capsules}"
report_set="${LOOP_REPORT_SET:-daily}"
if [ "${1:-}" = --set ] && [ "$#" -eq 2 ]; then report_set="$2"; elif [ "$#" -ne 0 ]; then
  echo "usage: $0 [--set daily|rehearsal|ondemand]" >&2; exit 1
fi
case "$report_set" in
  daily) day="$(date -u +%F)" ;;
  # The 10-minute UTC slot this run reports, <YYYY-MM-DD>T<HHMM>Z: one clock read.
  rehearsal) now="$(date -u +%Y-%m-%dT%H%M)"; day="${now%?}0Z" ;;
  # The run, <YYYY-MM-DD>T<HHMMSS>Z: the second it starts.
  ondemand) day="$(date -u +%Y-%m-%dT%H%M%SZ)" ;;
  *) echo "LOOP_REPORT_SET must be daily, rehearsal or ondemand, not $report_set" >&2; exit 1 ;;
esac
if [ "$report_set" = ondemand ]; then
  # A scheduled run owns the checkout while it runs (the daily one for up to six
  # hours): never ask beside it. `is-active` says activating while a oneshot runs.
  for unit in loop-reporter.service loop-reporter-ask.service loop-reporter-rehearsal.service; do
    state="$(systemctl --user is-active "$unit" 2>&1)"
    case "$state" in
      inactive|failed|unknown) ;;
      active|activating|deactivating|reloading|refreshing)
        echo "$unit is $state: an on-demand run waits until it has finished" >&2; exit 1 ;;
      *) echo "cannot tell whether $unit is running ($state): not starting an on-demand run" >&2; exit 1 ;;
    esac
  done
  # Another run holding the loop-data checkout (scripts/loop_data.sh): refuse at once, never wait.
  lock_wait=0
fi

# The reporter judges every answer under the key the node's host announces on
# the mesh, never under a static map: refuse to run with one configured.
if [ -n "${CAPSULES_PEER_KEYS:-}" ]; then
  echo "CAPSULES_PEER_KEYS is set: the reporter takes node keys from the mesh announcement only" >&2
  exit 1
fi

. scripts/loop_data.sh || exit 1
if [ "$report_set" = ondemand ] && ! data_git diff --cached --quiet; then
  echo "changes are staged in $data, not starting an on-demand run:" >&2
  data_git diff --cached --name-only >&2
  exit 1
fi
# The deploy key pushes reports/<set> to loop-data's main and nothing else: run
# only on main, and only when every local commit not on origin/main stays in reports/<set>.
[ "$(data_git symbolic-ref --short HEAD 2>/dev/null)" = main ] || { echo "not on main: $data" >&2; exit 1; }
# --rebase: a report committed but not pushed last time goes out on top.
data_git pull --rebase --quiet origin main || exit 1
python3 scripts/daily_report.py run --set "$report_set" --date "$day" --console "$console" --data-dir "$data_dir" \
  --out "$reports"
status=$?
# 1 = refused before asking anyone (bad list, reporter key not set or not this
# node's): nothing to commit, and the next run will mark this day MISSED.
[ "$status" -eq 1 ] && exit 1
# An on-demand run that could not reach its own node wrote nothing: no commit.
[ "$report_set" = ondemand ] && [ "$status" -eq 2 ] && exit 2
python3 scripts/check_reports.py "$reports" --set "$report_set" || exit 1
# Nothing with personal data in it is pushed: this push skips the required
# checks, so a hit is caught here, before history, not after. The day's bytes
# are held on this node, outside the clone, and the day is recorded as a run
# that could not finish (MISSED, with why); nothing is staged or committed.
# Exit 3. The check is scripts/check_personal_data.py (the personal-data job).
if [ -f scripts/check_personal_data.py ] && ! python3 -B scripts/check_personal_data.py "$reports" >&2; then
  held="${LOOP_HELD_DIR:-$HOME/.local/share/loop/held}/$day-$(date -u +%H%M%S)"
  mkdir -p "$(dirname "$held")" || exit 1
  [ -d "$reports/$day" ] && { mv "$reports/$day" "$held" || exit 1; }
  # No continuity on demand: the run is held, and nothing is left in its place.
  if [ "$report_set" != ondemand ]; then
    mkdir -p "$reports/$day"
    echo "no report for $day: the run could not finish: its answers failed the personal-data check and are held on the reporter node" \
      > "$reports/$day/MISSED"
  fi
  echo "$day: personal-data check failed; held in $held; nothing committed" >&2
  exit 3
fi
# On demand, only this run's directory: never another run's, nor a leftover.
if [ "$report_set" = ondemand ]; then commit_path="reports/ondemand/$day"; else commit_path="reports/$report_set"; fi
data_git add "$commit_path" || exit 1
if [ "$report_set" = daily ]; then
  if [ "$status" -eq 0 ]; then msg="report: daily $day"; else msg="report: $day missed"; fi
elif [ "$report_set" = ondemand ]; then msg="report: on-demand $day"
elif [ "$status" -eq 0 ]; then msg="report: rehearsal $day (TEST)"; else msg="report: rehearsal $day missed (TEST)"; fi
if ! data_git diff --cached --quiet; then
  data_git commit --quiet -s -m "$msg" -- "$commit_path" || exit 1
fi
# Every outgoing commit, not just the net diff: a file added then deleted
# would still be pushed in history.
other="$(data_git log -m --format= --name-only origin/main..HEAD -- . ":!reports/$report_set")" || exit 1
if [ -n "$other" ]; then
  echo "local commits touch files outside reports/$report_set, not pushing:" >&2
  echo "$other" >&2
  exit 1
fi
data_git push --quiet origin HEAD:main || exit 1
exit "$status"
