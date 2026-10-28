#!/usr/bin/env bash
# Install step 05 Deal and push's driver: steps/05-deal.d built against the
# pins in versions.lock (evidencebook's Rust crate from the evidencebook tag,
# and the checkpointed-local-log crate that tag's crate embeds). For the live
# population, the AAC Go verifier (aac-go) that steps/01-seal.setup.sh installs.
#
#   steps/05-deal.setup.sh
#
# Needs cargo, git and the crates.io index (the build fetches the pinned
# crates and the evidencebook tag; Cargo.lock carries their checksums and the
# tag's commit). Installs to
# ${LOOP_TOOLS:-~/.cache/evidence-network-loop}/05-deal/bin/loop-step05-deal,
# where steps/05-deal looks for it.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CRATE="$ROOT/steps/05-deal.d"
TOOLS="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/05-deal"

pin_ref() { awk -v n="$1" '$1 == n { print $3 }' "$ROOT/versions.lock"; }

# A Cargo.lock field (version, source) of a crate.
locked() {
  awk -v c="$1" -v f="$2" '$0 == "name = \"" c "\"" { want = 1; next }
    want && $1 == f { gsub(/^[a-z]+ = |"/, ""); print; exit }' "$CRATE/Cargo.lock"
}

cll=$(pin_ref checkpointed-local-log)
case "$cll" in
  "crates/checkpointed-local-log-v"*) want=${cll#crates/checkpointed-local-log-v} ;;
  *) echo "05-deal.setup: versions.lock checkpointed-local-log is '$cll', not a crates/checkpointed-local-log-v<version> tag" >&2; exit 1 ;;
esac
got=$(locked checkpointed-local-log version)
if [ "$want" != "$got" ]; then
  echo "05-deal.setup: Cargo.lock has checkpointed-local-log '$got', versions.lock pins $want" >&2
  exit 1
fi
eb_repo=$(awk '$1 == "evidencebook" { print $2 }' "$ROOT/versions.lock")
eb_tag=$(pin_ref evidencebook)
eb_source=$(locked evidencebook source)
case "$eb_source" in
  "git+https://github.com/$eb_repo?tag=$eb_tag#"*) ;;
  *) echo "05-deal.setup: Cargo.lock has evidencebook from '$eb_source', versions.lock pins $eb_repo $eb_tag" >&2; exit 1 ;;
esac

command -v cargo >/dev/null || { echo "05-deal.setup: cargo is not installed" >&2; exit 1; }
CARGO_TARGET_DIR="$TOOLS/target" cargo build --release --locked --manifest-path "$CRATE/Cargo.toml"
mkdir -p "$TOOLS/bin"
install -m 0755 "$TOOLS/target/release/loop-step05-deal" "$TOOLS/bin/loop-step05-deal"
echo "05-deal.setup: installed $TOOLS/bin/loop-step05-deal (evidencebook $eb_tag at ${eb_source##*#}, checkpointed-local-log $got)"

# On the live population the step recomputes each held half's capsule_id with
# the AAC Go verifier at its versions.lock pin, which step 01's setup installs.
"$ROOT/steps/01-seal.setup.sh"
