#!/usr/bin/env bash
# Helpers for .github/workflows/live-daily.yml, sourced by its steps. The lab host, project, zone and lab user come from
# environment secrets (LIVE_VM, LIVE_PROJECT, LIVE_ZONE, LIVE_LAB_USER; secrets are masked in every log line, including step headers); nothing about the
# operator's infrastructure is committed. Each remote call runs the one command the host's sudoers rule allows.
set -u

for v in LIVE_VM LIVE_PROJECT LIVE_ZONE LIVE_LAB_USER; do
  [ -n "${!v:-}" ] || { echo "::error::environment secret $v is not set"; return 2 2> /dev/null || exit 2; }
  echo "::add-mask::${!v}"
done
export CLOUDSDK_CORE_PROJECT=$LIVE_PROJECT CLOUDSDK_CORE_DISABLE_PROMPTS=1

setstate() { echo "LIVE_STATE=$1" >> "$GITHUB_ENV"; LIVE_STATE=$1; }

vm() { # SUBCOMMAND: start | status | stage; `cd /` first, or the lab user's find reports the CI account's home path in the public log
  timeout 150 gcloud compute ssh "$LIVE_VM" --zone "$LIVE_ZONE" --tunnel-through-iap --quiet --verbosity=error --strict-host-key-checking=no --ssh-flag=-q \
    --command "cd / && sudo -n -u $LIVE_LAB_USER /usr/local/sbin/loop-live-ci-entry $1" < /dev/null
}

# Only fixed keywords from a failed call reach the public log, never its text: that keeps names, host output and
# workflow commands (`::add-mask::`) out of it, and no secret can be cut in half by a truncation.
unreachable_reason() {
  grep -oE 'PERMISSION_DENIED|FAILED_PRECONDITION|NOT_FOUND|RESOURCE_EXHAUSTED|UNAUTHENTICATED|4003|4033|4047|4033|timed out|Connection (refused|closed|reset)|USER_PROJECT_DENIED|SERVICE_DISABLED|serviceusage|accessNotConfigured|Insufficient|oslogin|Permission denied|publickey|serviceAccountUser|osLogin|tunnelResourceAccessor|tunnelInstances|not authorized|Could not|Unable to' <<< "$1" | sort -u | tr '\n' ' ' | grep . || echo "no known error keyword"
}

# One-shot check of which gcloud step the CI account is refused at; prints ok/fail per step, never their output.
probe_access() {
  local n=0 t out rc
  for t in instances project os-login ssh; do
    n=$((n + 1))
    out=$(case $t in
      instances) timeout 60 gcloud compute instances describe "$LIVE_VM" --zone "$LIVE_ZONE" ;;
      project) timeout 60 gcloud compute project-info describe ;;
      os-login) timeout 60 gcloud compute os-login describe-profile ;;
      ssh) timeout 60 gcloud compute ssh "$LIVE_VM" --zone "$LIVE_ZONE" --tunnel-through-iap --dry-run ;;
    esac < /dev/null 2>&1) && rc=0 || rc=$?
    if [ "$rc" -eq 0 ]; then echo "::notice::access probe $n: ok"
    else echo "::warning::access probe $n: fail rc=$rc bytes=${#out}: $(probe_words "$out") / $(unreachable_reason "$out")"; fi
  done
}

# Probe failures print generic words only (a fixed list, case-insensitive), so the log shows the shape of an error without its text.
probe_words() {
  grep -oiE '\b(40[0-9]|429|50[0-9])\b|permission|denied|forbidden|disabled|enabled|not found|does not exist|quota|billing|invalid|expired|scope|token|credential|authentication|authorization|unauthorized|required|missing|profile|account|policy|constraint|restricted|consumer|consumer_invalid|user project|login|metadata|key|import|public key|impersonat' <<< "$1" | tr 'A-Z' 'a-z' | sort -u | tr '\n' ',' | grep . || echo "no known word"
}

start_run() {
  local out rc
  # A harvest-only dispatch (after a failed harvest) starts nothing: it polls the result of today's run.
  if [ "${HARVEST_ONLY:-false}" = true ]; then setstate waiting; return 0; fi
  out=$(vm start 2>&1) && rc=0 || rc=$?
  echo "$out"
  case $rc in
    0) setstate waiting ;;
    10 | 11) setstate skipped; echo "::notice::live run not started: $(echo "$out" | grep -a -E '^(skipped|refused):' | head -1)" ;;
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
      0) echo "$out"
         # live_daily.sh's own exit code: 0, 1 and 8 are packed runs; 3 is a busy lab (a skip); the rest ran and packed nothing.
         case $(sed -n 's/^rc=//p' <<< "$out" | tail -1) in
           0 | 1 | 8) setstate done ;;
           3) setstate skipped; echo "::notice::the lab was busy" ;;
           *) setstate failed; echo "::error::the run ended without a pack"; return 1 ;;
         esac
         return 0 ;;
      21) echo "$out"; setstate failed; return 1 ;;
      22) echo "$out"; setstate timeout; return 1 ;;
      *) bad=$((bad + 1)); echo "::warning::status unreachable (rc=$rc, try $bad): $(unreachable_reason "$out")"
         [ "$bad" -eq 1 ] && probe_access
         [ "$bad" -ge 5 ] && { setstate failed; echo "::error::status unreachable"; return 1; } ;;
    esac
    sleep 120
  done
  return 0
}

# The pull request is opened with the App token, whose events start the pull_request checks. Until that is shown to
# work alone, also start each required check by dispatch on the harvest branch with the job's own token.
dispatch_checks() { # BRANCH
  local wf failed=0
  for wf in nodes reports personal-data leak-lint dco stats harvest-allowlist ecosystem-gate versions; do
    GH_TOKEN=${WORKFLOW_TOKEN:-${GH_TOKEN:-}} gh workflow run "$wf.yml" --ref "$1" || { echo "::error::could not dispatch $wf on $1"; failed=1; }
  done
  return $failed
}

harvest() {
  local out name rc auth
  # The App token pushes the branch and opens the pull request, so the pull request starts its own checks (loop#395).
  [ -n "${GH_TOKEN:-}" ] || { setstate harvest-failed; echo "::error::no harvest App token (HARVEST_APP_* secrets)"; return 1; }
  auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')
  echo "::add-mask::$auth"
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0="http.https://github.com/.extraheader" GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $auth"
  out=$(vm stage 2>&1) && rc=0 || rc=$?
  echo "$out"
  [ "$rc" = 0 ] || { setstate failed; echo "::error::stage failed (exit $rc)"; return 1; }
  name=$(sed -n 's/^name=\([^ ]*\) .*/\1/p' <<< "$out")
  [[ $name =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-live-[1-9][0-9]*$ ]] || { echo "::error::bad run name"; return 1; }
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  rc=0
  out=$(LIVE_HARVEST_IAP=1 LIVE_HARVEST_VM=$LIVE_VM LIVE_HARVEST_ZONE=$LIVE_ZONE LIVE_HARVEST_REMOTE_DIR=/tmp/loop-live-out-$name \
    python3 -B scripts/live_harvest.py --name "$name" < /dev/null 2>&1) || rc=$?
  echo "$out"
  # A harvest-only retry after the branch was pushed: the pull request exists; only the checks may be missing.
  if [ "$rc" = 4 ] && grep -q "origin already has live-harvest/$name" <<< "$out"; then
    echo "::notice::the findings branch exists; dispatching its checks again"
    dispatch_checks "live-harvest/$name" || { setstate harvest-failed; return 1; }
    return 0
  fi
  echo "harvest_rc=$rc" >> "$GITHUB_ENV"
  # exit 0: PR open and the baseline moved; 1: PR open, baseline unmoved (a regression or a refused payment)
  if [ "$rc" = 0 ] || [ "$rc" = 1 ]; then dispatch_checks "live-harvest/$name" || { setstate harvest-failed; return 1; }; fi
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
