#!/usr/bin/env bash
# Protect main on GitHub with two repository rulesets. Run once by a repository admin, at the
# public flip (see the checklist in the pull request that added this file). NOT run by CI.
#
#   scripts/apply_branch_protection.sh --dry-run   # print the rulesets, call nothing
#   scripts/apply_branch_protection.sh             # check prerequisites, then create/update
#   scripts/apply_branch_protection.sh verify      # print what GitHub now enforces on main
#
# 1. "main: history" - no deletion, no force-push. Nobody bypasses it, deploy keys included.
# 2. "main: review and checks" - changes by pull request, one approving review from a code
#    owner, and the required checks below (from GitHub Actions only). Deploy keys bypass it:
#    loop-data's stats publisher pushes the scrubbed set, stats/data/, straight to main with its
#    deploy key (the reporter's raw reports go to private loop-data instead, 2026-10-27). GitHub
#    cannot limit a key to a path, so .github/workflows/direct-push.yml fails any push to main
#    that is not a merged pull request and touches anything outside stats/data/. The bypass
#    covers EVERY deploy key with write access, so the repository must have exactly one: the
#    stats publisher's.
#
# Rulesets on a private repository need a paid plan; on a free organization, run this right
# after the repository is made public.
set -euo pipefail

REPO="${REPO:-evidence-network/loop}"
ORG="${REPO%%/*}"
# PLACEHOLDER team, as in .github/CODEOWNERS: it must exist before this runs.
TEAM="${TEAM:-loop-maintainers}"
# Job names of the checks a pull request must pass (keep in step with .github/workflows/).
REQUIRED_CHECKS=(nodes reports personal-data leak-lint dco site versions)
GITHUB_ACTIONS_APP_ID=15368

history_ruleset() {
  cat <<'JSON'
{
  "name": "main: history",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "bypass_actors": [],
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"}
  ]
}
JSON
}

review_ruleset() {
  local checks="" c
  for c in "${REQUIRED_CHECKS[@]}"; do
    checks+="${checks:+,}"$'\n'"          {\"context\": \"$c\", \"integration_id\": $GITHUB_ACTIONS_APP_ID}"
  done
  cat <<JSON
{
  "name": "main: review and checks",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "bypass_actors": [
    {"actor_id": null, "actor_type": "DeployKey", "bypass_mode": "always"}
  ],
  "rules": [
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 1,
      "require_code_owner_review": true,
      "dismiss_stale_reviews_on_push": true,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false
    }},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": false,
      "required_status_checks": [$checks
      ]
    }}
  ]
}
JSON
}

verify() {
  echo "# Rules GitHub enforces on main:"
  gh api "repos/$REPO/rules/branches/main" --jq '.[] | {type, ruleset_id, parameters}'
  echo "# Rulesets, with their bypass actors:"
  gh api "repos/$REPO/rulesets" --jq '.[].id' | while read -r id; do
    gh api "repos/$REPO/rulesets/$id" --jq '{id, name, enforcement, conditions, bypass_actors, rules}'
  done
  echo "# Write deploy keys (must be exactly one, the stats publisher's):"
  gh api "repos/$REPO/keys" --jq '[.[] | select(.read_only == false) | {id, title, created_at}]'
}

prerequisites() {
  local ok=1 n
  gh auth status >/dev/null 2>&1 || { echo "not logged in to gh" >&2; exit 1; }
  if ! gh api "orgs/$ORG/teams/$TEAM" --jq .slug >/dev/null 2>&1; then
    echo "team $ORG/$TEAM does not exist: create it (it is the code owner) or set TEAM=" >&2; ok=0
  elif ! gh api -H "Accept: application/vnd.github.v3.repository+json" \
         "orgs/$ORG/teams/$TEAM/repos/$REPO" --jq '.permissions.push' 2>/dev/null | grep -qx true; then
    echo "team $ORG/$TEAM has no write access to $REPO: a code owner needs it" >&2; ok=0
  fi
  n=$(gh api "repos/$REPO/keys" --jq '[.[] | select(.read_only == false)] | length')
  if [ "$n" -gt 1 ]; then
    echo "$REPO has $n write deploy keys; every one would bypass review. Keep only the stats publisher's." >&2; ok=0
  elif [ "$n" -eq 0 ]; then
    echo "note: no write deploy key yet; the stats publisher's nightly push needs one (read-only keys are fine)" >&2
  fi
  if ! gh api "repos/$REPO/actions/secrets" --jq '.secrets[].name' | grep -qx LEAK_LINT_TERMS; then
    echo "secret LEAK_LINT_TERMS is not set on $REPO: leak-lint would fail every pull request" >&2; ok=0
  fi
  [ "$ok" -eq 1 ] || exit 1
}

upsert() {  # upsert NAME JSON
  local id
  id=$(gh api "repos/$REPO/rulesets" --jq ".[] | select(.name == \"$1\") | .id")
  if [ -n "$id" ]; then
    gh api -X PUT "repos/$REPO/rulesets/$id" --input - <<<"$2" --jq '"updated \(.id): \(.name)"'
  else
    gh api -X POST "repos/$REPO/rulesets" --input - <<<"$2" --jq '"created \(.id): \(.name)"'
  fi
}

case "${1:-}" in
  --dry-run) history_ruleset; review_ruleset ;;
  verify) verify ;;
  "")
    prerequisites
    upsert "main: history" "$(history_ruleset)"
    upsert "main: review and checks" "$(review_ruleset)"
    verify ;;
  *) echo "usage: $0 [--dry-run | verify]" >&2; exit 2 ;;
esac
