#!/usr/bin/env bash
# The local check before a push (CONTRIBUTING.md), in two tiers:
#
#   scripts/check.sh          what a pull request runs: nodes, reports, personal-data, dco, versions, gate,
#                             commitments and site (the commands of their workflows), and leak-lint when
#                             LEAK_LINT_TERMS is set
#   scripts/check.sh --full   also loop.yml's loop job (every step's pinned tools, the step tests with
#                             LOOP_REQUIRE_TOOLS=1, stage 1, the cross stage, steps 10 and 11)
#
# The exception: the default tier skips the loop job (about 51 min in CI, 79 min on a Mac). Run --full before a push
# that changes anything outside loop.yml's push paths-ignore list; CI runs the job nightly and on such a push to
# main, and a failure there opens a ci-red issue (CONTRIBUTING.md).
#
# Exits non-zero on the first failure. Runs from any directory; writes nothing into the checkout (the rehearsal
# runs, the site and the summaries go to a temporary directory). The step tools install under LOOP_TOOLS
# (default ~/.cache/evidence-network-loop), as loop.sh's; the jobs that install nothing in CI run without them.
#
# Needs python3, git, go, cargo, openssl 3, bash 4+, GNU timeout (loop.sh), flock (macOS: brew install flock; without
# it scripts/loop_data.sh takes no lock and tests.test_workshop_refresh fails), network (the pins, the newest live
# run's release archive) and Google Chrome (the site's headless render). Cannot run locally: the leak scans without
# the LEAK_LINT_TERMS secret (CI's leak-lint check and the site's built-site scan; both are skipped and say so).
set -euo pipefail

full=0
case "${1:-}" in
  "") ;;
  --full) full=1 ;;
  *) echo "usage: scripts/check.sh [--full]" >&2; exit 2 ;;
esac

ROOT=$(cd "$(dirname "$0")/.." && pwd)
[ "$full" = 0 ] || export LOOP_TESTS=all
cd "$ROOT"
tmp=$(mktemp -d)
# The site's rehearsal run goes in the background (below); a check that stops early stops it and everything it started
# (loop.sh runs each step under GNU timeout, which puts the step in a process group of its own, so the whole tree is
# signalled, parents first so nothing new starts), waits for it, then removes $tmp. Best effort: the check's own exit
# status stands. (check-cleanup: begin)
site_run=""
tree() { local c; echo "$1"; for c in $(pgrep -P "$1"); do tree "$c"; done; }
stop_site_run() {
  [ -n "$site_run" ] || return 0
  # shellcheck disable=SC2046 # one pid per word
  kill -TERM $(tree "$site_run") 2> /dev/null || true
  wait "$site_run" 2> /dev/null || true
}
trap 'stop_site_run; rm -rf "$tmp"' EXIT
# (check-cleanup: end)
step() { printf '\n== %s (at %ss)\n' "$*" "$SECONDS"; }
# A command that must fail (`! cmd` would not stop a set -e script).
refused() { if "$@"; then echo "check: expected a refusal from: $*" >&2; exit 1; fi; }
day=$(date -u +%F)
git fetch -q origin main || { echo "check: could not fetch origin main (offline?); dco and the ecosystem gate compare against it" >&2; exit 1; }
base=$(git rev-parse origin/main)
# Each CI job starts on a fresh runner: the jobs below that install nothing see no step tools, so their tool-backed
# tests skip as in CI rather than run against whatever an earlier run left in LOOP_TOOLS. commitments sees only the
# capsule-cli its setup builds. The site job and --full use LOOP_TOOLS itself.
tools=${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}
export LOOP_TOOLS=$tools
mkdir -p "$tools/capsule-cli" "$tmp/no-tools" "$tmp/commitments-tools"
ln -s "$tools/capsule-cli" "$tmp/commitments-tools/capsule-cli"
bare() { LOOP_TOOLS="$tmp/no-tools" "$@"; }

step "nodes (nodes.yml)"
python3 scripts/check_nodes.py tests/nodes/good
refused python3 scripts/check_nodes.py tests/nodes/bad-key
refused python3 scripts/check_nodes.py tests/nodes/dup
refused python3 scripts/check_nodes.py tests/nodes/no-consent
refused python3 scripts/check_nodes.py tests/nodes/email-contact
refused python3 scripts/check_nodes.py tests/nodes/phone-contact
python3 scripts/check_nodes.py nodes

step "personal-data (personal-data.yml)"
python3 scripts/check_personal_data.py nodes reports
python3 -B scripts/check_no_preimages.py
bare python3 -B -m unittest tests.test_no_preimages -v

step "dco (dco.yml), commits since the merge base with origin/main"
python3 .github/dco.py "$(git merge-base origin/main HEAD)" "$(git rev-parse HEAD)"

step "versions (versions.yml)"
bare python3 -B -m unittest tests.test_versions_lock -v
python3 -B scripts/check_versions_lock.py versions.lock

step "gate (ecosystem-gate.yml), against origin/main"
bare python3 -B -m unittest tests.test_ecosystem_gate -v
python3 -B scripts/ecosystem_gate.py verify
if git cat-file -e "$base:baseline/live.json" 2>/dev/null; then
  git show "$base:baseline/live.json" > "$tmp/old.json"
  python3 -B scripts/ecosystem_gate.py check --old "$tmp/old.json" --new baseline/live.json
fi

case "$(git rev-parse --abbrev-ref HEAD)" in
  live-harvest/*)
    step "harvest-allowlist (harvest-allowlist.yml)"
    git show "$(git merge-base origin/main HEAD):.github/harvest_allowlist.py" > "$tmp/harvest_allowlist.py"
    python3 -B "$tmp/harvest_allowlist.py" "$(git merge-base origin/main HEAD)" "$(git rev-parse HEAD)"
    ;;
esac

step "leak-lint (leak-lint.yml)"
if [ -n "${LEAK_LINT_TERMS:-}" ]; then
  LEAK_LINT_REVEAL=true python3 .github/leak_lint.py .
else
  echo "skipped: LEAK_LINT_TERMS is not set (a repository secret; CI runs it on every pull request)"
fi

step "commitments (commitments.yml)"
LOOP_TOOLS="$tmp/commitments-tools" steps/03-advertise.setup.sh
# commitments.yml's four modules, one process per module, in parallel (scripts/unittest_parallel.py).
LOOP_TOOLS="$tmp/commitments-tools" python3 -B scripts/unittest_parallel.py modules tests.test_step03_advertise tests.test_nostr_carrier tests.test_commitment1 tests.test_staple_verify
LOOP_TOOLS="$tmp/commitments-tools" python3 -B steps/03-advertise --population tests/fixtures/step03/population/rehearsal --out "$tmp/commitments/03-advertise"
python3 -B steps/finding.py check "$tmp/commitments"
grep -q '"result": "PASS"' "$tmp/commitments/03-advertise/finding.json"
python3 -B scripts/check_commitment.py nodes/commitments
python3 -B scripts/check_binding.py bindings
python3 -B scripts/check_revocations.py nodes/revocations

# The site job's step setups and its stage 1 rehearsal run depend on nothing the unit tests do, so they start now and
# run beside them; the site step below waits for them and prints their output. As in stats.yml, a failing setup or a
# loop.sh that exits non-zero is a warning, not a failure: the site is built from whatever the run wrote.
cp -R findings "$tmp/findings"
(
  for s in steps/*.setup.sh; do
    [ -e "$s" ] || continue
    bash "$s" || echo "warning: $s exited $?"
  done
  ./loop.sh --stage 1 --population rehearsal --out "$tmp/findings/$day-rehearsal" || echo "warning: loop.sh exited $?"
) < /dev/null > "$tmp/site-run.log" 2>&1 &
site_run=$!

step "reports (reports.yml)"
# The modules this change can affect (scripts/test_impact.py: what each module names and imports, against the changes
# since the merge base with origin/main and the working tree's), in parallel (scripts/unittest_parallel.py). reports.yml
# runs every module on each pull request, after a merge and nightly; --full and LOOP_TESTS=all run every module here.
selected=$(python3 -B scripts/test_impact.py --explain)
# Each run's time last time (outside the temporary LOOP_TOOLS the bare runs get): the longest start first, and a long
# test class runs one process per test.
export LOOP_TEST_TIMES="$tools/test-times.json"
case "$selected" in
  ALL) bare python3 scripts/unittest_parallel.py discover -s tests ;;
  "") echo "no unit test module depends on what this change touches" ;;
  *) bare python3 scripts/unittest_parallel.py discover -s tests --only "$(echo $selected | tr ' ' ,)" ;;
esac
python3 scripts/check_reports.py reports/daily

# stats.yml's site job. Its rehearsal run, the hydrated live run and the built site go to $tmp: the findings are
# a copy of the committed ones with the fresh run beside them, as the job's checkout holds them.
step "site (stats.yml)"
# The job's unit tests (test_site, test_status_page, test_stats_redesign, test_stats_contrast, test_rehearsal,
# test_watch, test_growth) already ran in the reports step above, in the same bare environment.
python3 -B site/build.py tests/fixtures/stats/daily --findings tests/fixtures/stats/findings --lock versions.lock --out "$tmp/fixture-site"
diff -u tests/fixtures/stats/expected/index.html "$tmp/fixture-site/stats/index.html"
echo "-- the setups and the stage 1 rehearsal run, started before the reports step (at ${SECONDS}s):"
wait "$site_run" || { echo "check: the site's setup and rehearsal run could not complete" >&2; cat "$tmp/site-run.log" >&2; exit 1; }
site_run=""
cat "$tmp/site-run.log"
python3 -B scripts/hydrate_findings.py "$tmp/findings"
site="$tmp/_site"
python3 -B site/build.py stats/data/daily --findings "$tmp/findings" --lock versions.lock --out "$site" --rehearsal stats/data/rehearsal
python3 -B site/watch.py --daily stats/data/daily --rehearsal stats/data/rehearsal --ondemand stats/data/ondemand --findings "$tmp/findings" --nodes nodes --population population --commitments lab/runs --bindings bindings --lock versions.lock --out "$site"
python3 -B site/growth.py --out "$site"
python3 scripts/check_personal_data.py "$site/growth/data.json" "$site/growth/index.html"
python3 scripts/check_personal_data.py "$site/watch/data.json" "$site/watch/index.html"
chrome=$(command -v google-chrome || command -v chromium || echo "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
[ -x "$chrome" ] || { echo "site: Google Chrome is needed for the headless render (set PATH to google-chrome)" >&2; exit 1; }
shots="$tmp/watch-screenshots"
mkdir -p "$shots"
for view in live rehearsal; do
  for scheme in light dark; do
    dark=""; [ "$scheme" = dark ] && dark="--force-dark-mode"
    "$chrome" --headless=new --no-sandbox --disable-gpu --hide-scrollbars --window-size=1300,1400 \
      $dark --virtual-time-budget=5000 --screenshot="$shots/watch-$view-$scheme.png" \
      "file://$site/watch/index.html#$view"
    test "$(wc -c < "$shots/watch-$view-$scheme.png")" -gt 20000
  done
done
if [ -n "${LEAK_LINT_TERMS:-}" ]; then
  cp -R "$site" "$tmp/site-scan"
  git -C "$tmp/site-scan" init -q
  git -C "$tmp/site-scan" add -A
  LEAK_LINT_REVEAL=true python3 -B .github/leak_lint.py "$tmp/site-scan"
else
  echo "skipped: the built site's leak scan (LEAK_LINT_TERMS is not set)"
fi

if [ "$full" = 0 ]; then
  printf '\ncheck: pass in %ss (scripts/check.sh --full also runs the nightly loop job)\n' "$SECONDS"
  exit 0
fi

# loop.yml's loop job, as the nightly run does it: a setup that fails fails the check, and a missing tool fails a
# step test instead of skipping it.
step "loop (loop.yml): each step's pins"
for s in steps/*.setup.sh; do
  [ -e "$s" ] || continue
  echo "-- $s"
  bash "$s"
done

step "loop (loop.yml): step tests with the pinned tools"
# The module list is read from loop.yml's own step, so the two cannot drift.
modules=$(awk '/^ *run: python3 -B -m unittest tests\.test_step01_seal / { sub(/^ *run: python3 -B -m unittest /, ""); sub(/ -v$/, ""); print; exit }' \
  .github/workflows/loop.yml)
[ -n "$modules" ] || { echo "check: could not read the step-test modules from loop.yml" >&2; exit 1; }
# shellcheck disable=SC2086 # one word per module
LOOP_REQUIRE_TOOLS=1 python3 -B scripts/unittest_parallel.py modules $modules

step "loop (loop.yml): stage 1 on the rehearsal population"
run="$tmp/loop/$day-rehearsal"
./loop.sh --stage 1 --population rehearsal --strict --out "$run" | tee "$tmp/loop-summary.txt"

step "loop (loop.yml): the cross stage (step 13) on the rehearsal population"
cross="$tmp/loop/cross/$day-rehearsal"
./loop.sh --stage cross --ecosystem cross --population rehearsal --strict --out "$cross" | tee "$tmp/loop-summary-cross.txt"
python3 -B scripts/check_cross_run.py "$cross"

step "loop (loop.yml): steps 10 and 11 on the rehearsal population"
steps/10-judge --population population/rehearsal --out "$run/10-judge"
steps/11-report --population population/rehearsal --out "$run/11-report"
python3 -B steps/finding.py check "$run"
python3 -B status/findings.py "$run"
# The job's own result check, read from loop.yml so there is one copy; it appends to loop-summary.txt in $tmp.
awk '/python3 -B - "\$run" <<.PY./ { on = 1; next } on && /^ *PY$/ { exit } on { sub(/^          /, ""); print }' \
  .github/workflows/loop.yml > "$tmp/judge_check.py"
[ -s "$tmp/judge_check.py" ] || { echo "check: could not read the steps 10 and 11 check from loop.yml" >&2; exit 1; }
(cd "$tmp" && python3 -B - "$run" < judge_check.py)

printf '\ncheck --full: pass in %ss\n' "$SECONDS"
