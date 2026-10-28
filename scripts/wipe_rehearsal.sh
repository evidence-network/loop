#!/usr/bin/env bash
# Wipe the reporter rehearsal from this repository: remove stats/data/rehearsal/,
# its scrubbed set, in one commit. Nothing else changes: the daily set,
# stats/data/daily/, is never touched.
#
#   scripts/wipe_rehearsal.sh [LOOP_CHECKOUT]   (default: this checkout)
#
# The commit is everything staged, so the script refuses, and leaves the
# checkout as it found it, when the staged diff after the removal touches any
# path outside stats/data/rehearsal/ (a change staged beforehand, a symlink in
# its place). It commits and does not push: the commit goes to main by pull
# request. loop-data's publish-scrubbed also empties this set by itself once
# loop-data's own wipe (its scripts/wipe_rehearsal.sh) lands there.
# Exit 0 = wiped, or nothing to wipe; 1 = refused.
set -euo pipefail
keep="stats/data/rehearsal"
cd "${1:-$(dirname "$0")/..}"
cd "$(git rev-parse --show-toplevel)"
if [ -z "$(git ls-files -- "$keep")" ]; then
  echo "$keep: nothing to wipe"
  exit 0
fi
git rm -r -q --cached -- "$keep"
outside="$(git diff --cached --name-only --no-renames | grep -v "^$keep/" || true)"
if [ -n "$outside" ]; then
  git reset -q -- "$keep"
  echo "refused: the wipe would also commit paths outside $keep/:" >&2
  echo "$outside" >&2
  exit 1
fi
rm -rf -- "$keep"
git commit -q -s -m "stats: wipe the reporter rehearsal (TEST)" -m "Removes $keep/, the rehearsal's scrubbed set, and nothing else."
echo "wiped $keep in $(git rev-parse --short HEAD); push it to main by pull request"
