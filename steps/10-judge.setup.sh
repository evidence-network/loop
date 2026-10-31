#!/usr/bin/env bash
# Install what step 10 (Judge) needs, exactly at its versions.lock pins: capsulectl and
# capsulectl-book from the evidencebook tag they pin (the scratch book the Results are sealed
# in, and the bundle verifier; steps/12-join.setup.sh builds both), and the inference-deals pack
# from the evidencebook-skills commit it pins (its contract and compiled.json, the terms, tiers
# and window the step applies). The pack goes to $LOOP_TOOLS/packs/inference-deals-<sha>
# (default ~/.cache/evidence-network-loop).
#
#   steps/10-judge.setup.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
"$root/steps/12-join.setup.sh"

read -r repo ref <<<"$(awk '$1 == "inference-deals" { print $2, $3 }' "$root/versions.lock")"
[ -n "${ref:-}" ] || { echo "10-judge.setup: inference-deals is not pinned" >&2; exit 1; }
[[ "$ref" =~ ^[0-9a-f]{40}$ ]] || { echo "10-judge.setup: inference-deals must be pinned to a full commit SHA (got $ref)" >&2; exit 1; }
dest="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/packs/inference-deals-$ref"
compiled=packs/inference-deals/compiled/compiled.json
# The contract file is the one compiled.json's contract_ref (name@version) names.
contract_of() { python3 -c 'import json,sys; n,_,v=json.load(open(sys.argv[1]))["contract_ref"].partition("@"); print(f"contracts/{n}/{v}.json")' "$1"; }
# The pack's own evaluator and the modules it imports (steps/10-judge EVALUATOR holds each to its git blob id).
evaluator="scripts/evaluators/inference_deals.py scripts/capsulectl_calls.py scripts/result_v0.py scripts/rollup.py"
have_all() { for f in "$compiled" "$(contract_of "$dest/$compiled")" $evaluator; do [ -f "$dest/$f" ] || return 1; done; }
if [ -f "$dest/$compiled" ] && have_all; then
  echo "10-judge.setup: inference-deals $repo $ref already in $dest" >&2; exit 0
fi

src="$(mktemp -d)"
trap 'rm -rf "$src"' EXIT
git -C "$src" init -q
git -C "$src" -c advice.detachedHead=false fetch -q --depth 1 "https://github.com/$repo" "$ref"
git -C "$src" -c advice.detachedHead=false checkout -q FETCH_HEAD
[ "$(git -C "$src" rev-parse HEAD)" = "$ref" ] || { echo "10-judge.setup: fetched $(git -C "$src" rev-parse HEAD), not $ref" >&2; exit 1; }
rm -rf "$dest.tmp"; mkdir -p "$dest.tmp"
for f in "$compiled" "$(contract_of "$src/$compiled")" $evaluator; do mkdir -p "$dest.tmp/$(dirname "$f")"; cp "$src/$f" "$dest.tmp/$f"; done
rm -rf "$dest"; mv "$dest.tmp" "$dest"
echo "10-judge.setup: inference-deals from $repo $ref in $dest" >&2
