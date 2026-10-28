#!/usr/bin/env bash
# Install step 02 Book's driver: steps/02-book.d built against the pins in
# versions.lock: evidencebook's Rust crate from the evidencebook tag, and the
# crates that tag's crate uses (checkpointed-local-log, evidence-request, and
# AAC's agent-action-capsule, whose witness receipt verifier it calls). For the
# live population, capsulectl and capsulectl-book (steps/12-join.setup.sh).
#
#   steps/02-book.setup.sh
#
# Needs cargo, git and the crates.io index (the build fetches the pinned
# crates and the evidencebook tag; Cargo.lock carries their checksums and the
# tag's commit). Installs to
# ${LOOP_TOOLS:-~/.cache/evidence-network-loop}/02-book/bin/loop-step02-book,
# where steps/02-book looks for it.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CRATE="$ROOT/steps/02-book.d"
TOOLS="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/02-book"

pin_ref() { awk -v n="$1" '$1 == n { print $3 }' "$ROOT/versions.lock"; }

# The crate version a versions.lock tag names: crates/<crate>-v<version>.
pinned_version() {
  local name=$1 crate=$2 ref
  ref=$(pin_ref "$name")
  case "$ref" in
    "crates/$crate-v"*) echo "${ref#crates/$crate-v}" ;;
    *) echo "02-book.setup: versions.lock $name is '$ref', not a crates/$crate-v<version> tag" >&2; exit 1 ;;
  esac
}

# A Cargo.lock field (version, source) of a crate.
locked() {
  awk -v c="$1" -v f="$2" '$0 == "name = \"" c "\"" { want = 1; next }
    want && $1 == f { gsub(/^[a-z]+ = |"/, ""); print; exit }' "$CRATE/Cargo.lock"
}

for pin in checkpointed-local-log:checkpointed-local-log evidence-request:evidence-request aac-rust:agent-action-capsule; do
  name=${pin%%:*} crate=${pin#*:}
  want=$(pinned_version "$name" "$crate")
  got=$(locked "$crate" version)
  if [ "$want" != "$got" ]; then
    echo "02-book.setup: Cargo.lock has $crate '$got', versions.lock pins $want" >&2
    exit 1
  fi
done
# evidencebook: the crate in the repository at the pinned tag.
eb_repo=$(awk '$1 == "evidencebook" { print $2 }' "$ROOT/versions.lock")
eb_tag=$(pin_ref evidencebook)
eb_source=$(locked evidencebook source)
case "$eb_source" in
  "git+https://github.com/$eb_repo?tag=$eb_tag#"*) ;;
  *) echo "02-book.setup: Cargo.lock has evidencebook from '$eb_source', versions.lock pins $eb_repo $eb_tag" >&2; exit 1 ;;
esac

command -v cargo >/dev/null || { echo "02-book.setup: cargo is not installed" >&2; exit 1; }
CARGO_TARGET_DIR="$TOOLS/target" cargo build --release --locked --manifest-path "$CRATE/Cargo.toml"
mkdir -p "$TOOLS/bin"
install -m 0755 "$TOOLS/target/release/loop-step02-book" "$TOOLS/bin/loop-step02-book"
echo "02-book.setup: installed $TOOLS/bin/loop-step02-book (evidencebook $eb_tag at ${eb_source##*#}, checkpointed-local-log $(locked checkpointed-local-log version), agent-action-capsule $(locked agent-action-capsule version))"

# On the live population the step drives each node's book with capsulectl and
# capsulectl-book, at the versions.lock pins step 12's setup installs.
"$ROOT/steps/12-join.setup.sh"
