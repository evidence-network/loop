#!/usr/bin/env bash
# The workshop refresh: one extra live ask round between dailies, committed and
# republished. Run by hand on the reporter node, from its clone of this repository:
#
#   ~/loop/scripts/refresh_now.sh
#
# It reads the settings the reporter's units use (reporter/systemd/): their
# defaults, then ~/.config/loop-reporter/env (LOOP_SECOND_CONSOLE,
# LOOP_SELF_NODE; LOOP_REPORTER_ENV names another file), then the caller's own
# environment, which wins. Then it is `run_daily.sh --set ondemand`: pull
# loop-data, ask every listed node both questions once more (bounded to about
# two minutes), check the run (check_reports.py --set ondemand, then the
# personal-data hold), commit reports/ondemand/<YYYY-MM-DD>T<HHMMSS>Z/ and
# nothing else, and push with the deploy key. loop-data's publish-scrubbed then
# publishes it to stats/data/ondemand/ here. It refuses while the daily,
# ask-again or rehearsal service is active. Exit codes are run_daily.sh's.
set -uo pipefail
cd "$(dirname "$0")/.."
env_file="${LOOP_REPORTER_ENV:-$HOME/.config/loop-reporter/env}"
declare -A given=()
for name in LOOP_CONSOLE CAPSULES_DATA_DIR LOOP_DATA_DIR LOOP_SECOND_CONSOLE LOOP_SELF_NODE; do
  [ -n "${!name+x}" ] && given[$name]=1
done
# The env file is systemd's EnvironmentFile: NAME=value lines, read, never run.
if [ -f "$env_file" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      LOOP_SECOND_CONSOLE=*|LOOP_SELF_NODE=*|LOOP_CONSOLE=*|CAPSULES_DATA_DIR=*|LOOP_DATA_DIR=*)
        name="${line%%=*}"
        [ -n "${given[$name]:-}" ] || export "$name=${line#*=}" ;;
    esac
  done < "$env_file"
fi
# No default console: 3131 is mesh-llm's default port, and on a shared host
# another node may answer there. The reporter's own console must be named.
[ -n "${LOOP_CONSOLE:-}" ] || {
  echo "LOOP_CONSOLE is not set (in $env_file or the environment): name the reporter node's console" >&2
  exit 1
}
export LOOP_CONSOLE
export CAPSULES_DATA_DIR="${CAPSULES_DATA_DIR:-$HOME/.local/share/capsules}"
export LOOP_DATA_DIR="${LOOP_DATA_DIR:-$HOME/loop-data}"
unset LOOP_REPORT_SET
exec bash scripts/run_daily.sh --set ondemand
