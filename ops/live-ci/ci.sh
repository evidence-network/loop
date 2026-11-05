#!/usr/bin/env bash
# Helpers for .github/workflows/live-daily.yml, sourced by its steps. The lab host, project, zone and lab user come from
# repository variables (LIVE_VM, LIVE_PROJECT, LIVE_ZONE, LIVE_LAB_USER) and are masked in the log; nothing about the
# operator's infrastructure is committed. Each remote call runs the one command the host's sudoers rule allows.
set -u

for v in LIVE_VM LIVE_PROJECT LIVE_ZONE LIVE_LAB_USER; do
  [ -n "${!v:-}" ] || { echo "::error::repository variable $v is not set"; return 2 2> /dev/null || exit 2; }
  echo "::add-mask::${!v}"
done
export CLOUDSDK_CORE_PROJECT=$LIVE_PROJECT CLOUDSDK_CORE_DISABLE_PROMPTS=1

setstate() { echo "LIVE_STATE=$1" >> "$GITHUB_ENV"; LIVE_STATE=$1; }

vm() { # SUBCOMMAND: start | status | stage
  gcloud compute ssh "$LIVE_VM" --zone "$LIVE_ZONE" --quiet --strict-host-key-checking=no \
    --command "sudo -n -u $LIVE_LAB_USER /opt/loop-live-ci/entry.sh $1" < /dev/null
}

start_run() {
  local out rc
  out=$(vm start 2>&1) && rc=0 || rc=$?
  echo "$out"
  case $rc in
    0) setstate waiting ;;
    10) setstate skipped; echo "::notice::live run skipped: $(echo "$out" | grep -a '^skipped:' | head -1)" ;;
    *) setstate failed; echo "::error::the run did not start (exit $rc)"; return 1 ;;
  esac
}

# Poll for up to MINUTES; one transient ssh failure (255) is tolerated five times in a row.
wait_run() {
  local end=$((SECONDS + $1 * 60)) bad=0 out rc
  while [ "$SECONDS" -lt "$end" ]; do
    out=$(vm status 2>&1) && rc=0 || rc=$?
    case $rc in
      20) bad=0 ;;
      0) echo "$out"; setstate done; return 0 ;;
      21) echo "$out"; setstate failed; return 1 ;;
      22) echo "$out"; setstate timeout; return 1 ;;
      *) bad=$((bad + 1)); [ "$bad" -ge 5 ] && { echo "$out"; setstate failed; echo "::error::status unreachable"; return 1; } ;;
    esac
    sleep 120
  done
  return 0
}

# The pull request is opened with GITHUB_TOKEN, which starts no pull_request workflow: start each required check by
# dispatch on the harvest branch, so it reports on the pull request's head commit.
dispatch_checks() { # BRANCH
  local wf
  for wf in nodes reports personal-data leak-lint dco stats harvest-allowlist ecosystem-gate; do
    gh workflow run "$wf.yml" --ref "$1" || echo "::warning::could not dispatch $wf on $1"
  done
}

harvest() {
  local out name rc
  out=$(vm stage 2>&1) && rc=0 || rc=$?
  echo "$out"
  [ "$rc" = 0 ] || { echo "::notice::nothing to harvest"; return 0; }
  name=$(sed -n 's/^name=\([^ ]*\) .*/\1/p' <<< "$out")
  [[ $name =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-live-[1-9][0-9]*$ ]] || { echo "::error::bad run name"; return 1; }
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  rc=0
  LIVE_HARVEST_VM=$LIVE_VM LIVE_HARVEST_ZONE=$LIVE_ZONE LIVE_HARVEST_REMOTE_DIR=/tmp/loop-live-out-$name \
    python3 -B scripts/live_harvest.py --name "$name" < /dev/null || rc=$?
  echo "harvest_rc=$rc" >> "$GITHUB_ENV"
  # exit 0: PR open and the baseline moved; 1: PR open, baseline unmoved (a regression or a refused payment)
  if [ "$rc" = 0 ] || [ "$rc" = 1 ]; then dispatch_checks "live-harvest/$name"; fi
  [ "$rc" = 1 ] && echo "::warning::findings PR opened; the baseline did not move (regression or refused payment)"
  [ "$rc" -le 1 ] || { setstate harvest-failed; return 1; }
}

report_failure() { # STATE
  local title="loop-live-failed" n body
  body="The daily live run on $(date -u +%F) did not complete: state \`$1\`. Run: $GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
  n=$(gh issue list --state open --search "\"$title\" in:title" --json number --jq '.[0].number // empty')
  if [ -n "$n" ]; then gh issue comment "$n" --body "$body"
  else gh issue create --title "$title" --body "$body"; fi
}
