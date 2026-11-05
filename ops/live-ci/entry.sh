#!/usr/bin/env bash
# The only thing the daily live workflow (.github/workflows/live-daily.yml) may run on the lab host, as the lab user:
#
#   sudo -n -u <lab user> /usr/local/sbin/loop-live-ci-entry start|status|stage
#
# Installed by the lab operator, owned by root, mode 0755 in a root-owned directory, and named by exact subcommand in
# sudoers (ops/live-ci/README.md); it is not fetched from this repository at run time, so a merged change here
# reaches the lab only when the operator reinstalls it. It takes no argument but the subcommand and reads no wallet,
# seed, key, macaroon, peer-key, join or invite file. Its settings come from /etc/loop-live-ci/conf (root-owned):
#   LIVE_HOME  the lab user's home       LIVE_ENV  the lab env file live_daily.sh sources       LAB_DIR  the lab directory
#   LIVE_CI_USER  the OS Login user of the workflow's service account (the only reader of the staged files)
#
#   start   skip rule (exit 10, "skipped: <reason>"), at most one start per UTC day, one start at a time (11), the
#           exchange call sites of live_daily.sh unchanged (12), then the real run in its own process group; exit 0.
#           The day's marker is written before any code from main runs; live_daily.sh itself refuses a busy lab (exit 3)
#   status  20 running, 0 finished (prints rc=<live_daily.sh's exit code>), 21 died without a result, 22 killed at the
#           time limit (TERM to the run's process group, KILL after 60 s)
#   stage   for a packed run (rc 0, 1 or 8) installs its .tgz, .tgz.sha256 and .json into /tmp/loop-live-out-<name>
#           and prints name=<name>; exit 23 when nothing was packed
# Output goes to a public Actions log, so it carries only states and exit codes; the run's own log stays on the host.
set -uo pipefail
CONF=/etc/loop-live-ci/conf
[ -r "$CONF" ] || { echo "entry: no $CONF" >&2; exit 2; }
# shellcheck disable=SC1090
. "$CONF"
: "${LIVE_HOME:?}" "${LIVE_ENV:?}" "${LAB_DIR:?}" "${LIVE_CI_USER:?}"
MAX_SECONDS=$((3 * 3600))
CLONE=$LIVE_HOME/loop-live
STATE=$LIVE_HOME/.loop-live-ci
umask 022
mkdir -p "$STATE"
clean() { sed -e "s#$LIVE_HOME#~#g" -e "s#$LAB_DIR#\$LAB_DIR#g"; }
reasons() { :; } # the run's log is not printed: read it on the host

start() {
  local day pid p
  # One start at a time: mkdir is atomic and the lock names its owner, so only a dead owner's lock is reclaimed and only
  # the owner removes it.
  if ! mkdir "$STATE/lock.d" 2> /dev/null; then
    kill -0 "$(cat "$STATE/lock.d/pid" 2> /dev/null)" 2> /dev/null && { echo "refused: another start is in progress"; exit 11; }
    rm -rf "$STATE/lock.d"
    mkdir "$STATE/lock.d" 2> /dev/null || { echo "refused: another start is in progress"; exit 11; }
  fi
  echo $$ > "$STATE/lock.d/pid"
  trap '[ "$(cat "$STATE/lock.d/pid" 2> /dev/null)" = "$$" ] && rm -rf "$STATE/lock.d"' EXIT
  day=$(date -u +%F)
  find /tmp -maxdepth 1 -name 'loop-live-out-*' -user "$(id -un)" -mtime +1 -exec rm -rf {} + 2> /dev/null
  skip() { echo "skipped: $1"; exit 10; }
  [ -e "$LAB_DIR/run/LIVE_RUN" ] && skip "a live run holds the lab (LIVE_RUN exists)"
  pgrep -f '[l]ab\.sh|[l]oop\.sh' > /dev/null && skip "a lab.sh or loop.sh process runs"
  for s in loop-reporter.service loop-reporter-ask.service; do
    systemctl is-active --quiet "$s" && skip "$s is active"
  done
  find "$LIVE_HOME" -maxdepth 1 -name 'pm-*.log' -mmin -30 | grep -q . && skip "a pm-*.log changed in the last 30 minutes"
  for p in $(pgrep -x 'cargo|just|cmake' 2> /dev/null); do
    case $(readlink "/proc/$p/cwd" 2> /dev/null) in "$LIVE_HOME"/lab-*) skip "a lab rebuild is running" ;; esac
  done
  [ -e "$STATE/started-$day" ] && { echo "refused: a run already started on $day (UTC); no retry"; exit 11; }

  cd "$CLONE" || { echo "no dedicated clone"; exit 1; }
  timeout 120 git fetch -q origin && git checkout -q main && git merge -q --ff-only origin/main || { echo "clone is not at origin/main"; exit 1; }
  [ -z "$(git status --porcelain)" ] || { echo "clone is not clean"; exit 1; }
  # A tripwire, not the payment ceiling (that is the node side): the script that came from main still makes exactly one exchange call.
  [ "$(grep -cE '"\$LAB_SH" +exchange' scripts/live_daily.sh)" = 1 ] || { echo "refused: live_daily.sh has changed its exchange calls"; exit 12; }
  rm -f "$STATE/rc" "$STATE/pid"
  echo "$day" > "$STATE/day"
  touch "$STATE/started-$day"
  nohup setsid bash -c 'echo $$ > "$1"; LOOP_LIVE_ENV="$2" scripts/live_daily.sh; echo $? > "$3"' _ \
    "$STATE/pid" "$LIVE_ENV" "$STATE/rc" > "$STATE/log" 2>&1 < /dev/null &
  sleep 3
  pid=$(cat "$STATE/pid" 2> /dev/null) || { echo "run did not record a pid"; exit 1; }
  echo "started day=$day pid=$pid"
}

status() {
  local pid age
  if [ -s "$STATE/rc" ]; then
    reasons "$STATE/log"
    echo "rc=$(cat "$STATE/rc")"
    exit 0
  fi
  pid=$(cat "$STATE/pid" 2> /dev/null) || { echo "no pid"; exit 21; }
  if kill -0 "$pid" 2> /dev/null; then
    age=$(($(date +%s) - $(stat -c %Y "$STATE/pid" 2> /dev/null || stat -f %m "$STATE/pid")))
    [ "$age" -le "$MAX_SECONDS" ] && { echo "running ${age}s"; exit 20; }
    kill -TERM -- "-$pid" 2> /dev/null # the whole group, so the script's own handler removes LIVE_RUN
    for _ in $(seq 12); do pgrep -g "$pid" > /dev/null || break; sleep 5; done
    pgrep -g "$pid" > /dev/null && { kill -KILL -- "-$pid" 2> /dev/null; sleep 2; }
    echo "timed out at 3 h; group gone: $(pgrep -g "$pid" > /dev/null && echo no || echo yes); LIVE_RUN gone: $([ -e "$LAB_DIR/run/LIVE_RUN" ] && echo no || echo yes)"
    reasons "$STATE/log"
    exit 22
  fi
  echo "the run ended without a result"
  reasons "$STATE/log"
  exit 21
}

stage() {
  local rc name out
  rc=$(cat "$STATE/rc" 2> /dev/null) || { echo "no result"; exit 23; }
  case $rc in 0 | 1 | 8) ;; *) echo "nothing packed (rc=$rc)"; reasons "$STATE/log"; exit 23 ;; esac
  name=$(sed -n 's#^live_daily: done: .*/\([0-9]\{4\}-[0-9][0-9]-[0-9][0-9]-live-[1-9][0-9]*\)\.tgz .*#\1#p' "$STATE/log" | tail -1)
  [ -n "$name" ] || { echo "no packed name in the log"; exit 23; }
  out=/tmp/loop-live-out-$name
  # Raw run files are pre-allowlist: readable by the lab user and the workflow's login only.
  install -d -m 700 "$out" && setfacl -m "u:$LIVE_CI_USER:rx" "$out" || exit 23
  for e in .tgz .tgz.sha256 .json; do
    install -m 600 "$LAB_DIR/out/live/$name$e" "$out/$name$e" && setfacl -m "u:$LIVE_CI_USER:r" "$out/$name$e" || exit 23
  done
  echo "name=$name rc=$rc"
}

case "${1:-}" in
  start) [ "$#" = 1 ] && start ;;
  status) [ "$#" = 1 ] && status ;;
  stage) [ "$#" = 1 ] && stage ;;
  *) echo "usage: entry.sh start|status|stage" >&2; exit 2 ;;
esac
