#!/usr/bin/env bash
# Install step 02 Book's driver: steps/02-book.d built against the crates
# versions.lock pins; and, for the live population, capsulectl and
# capsulectl-book at their pins (steps/12-join.setup.sh).
#
#   steps/02-book.setup.sh
#
# Needs cargo and the crates.io index (the build fetches the pinned crates;
# Cargo.lock carries their checksums). Installs to
# ${LOOP_TOOLS:-~/.cache/evidence-network-loop}/02-book/bin/loop-step02-book,
# where steps/02-book looks for it.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CRATE="$ROOT/steps/02-book.d"
TOOLS="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/02-book"

# The crate version a versions.lock tag names: crates/<crate>-v<version>.
pinned_version() {
  local name=$1 crate=$2 ref
  ref=$(awk -v n="$name" '$1 == n { print $3 }' "$ROOT/versions.lock")
  case "$ref" in
    "crates/$crate-v"*) echo "${ref#crates/$crate-v}" ;;
    *) echo "02-book.setup: versions.lock $name is '$ref', not a crates/$crate-v<version> tag" >&2; exit 1 ;;
  esac
}

# The version Cargo.lock resolved for a crate.
locked_version() {
  awk -v c="$1" '$0 == "name = \"" c "\"" { getline; gsub(/version = |"/, ""); print }' "$CRATE/Cargo.lock"
}

for pin in evidencebook:evidencebook checkpointed-local-log:checkpointed-local-log evidence-request:evidence-request; do
  name=${pin%%:*} crate=${pin#*:}
  want=$(pinned_version "$name" "$crate")
  got=$(locked_version "$crate")
  if [ "$want" != "$got" ]; then
    echo "02-book.setup: Cargo.lock has $crate '$got', versions.lock pins $want" >&2
    exit 1
  fi
done

command -v cargo >/dev/null || { echo "02-book.setup: cargo is not installed" >&2; exit 1; }
CARGO_TARGET_DIR="$TOOLS/target" cargo build --release --locked --manifest-path "$CRATE/Cargo.toml"
mkdir -p "$TOOLS/bin"
install -m 0755 "$TOOLS/target/release/loop-step02-book" "$TOOLS/bin/loop-step02-book"
echo "02-book.setup: installed $TOOLS/bin/loop-step02-book (evidencebook $(locked_version evidencebook), checkpointed-local-log $(locked_version checkpointed-local-log))"

# On the live population the step drives each node's book with capsulectl and
# capsulectl-book, at the versions.lock pins step 12's setup installs.
"$ROOT/steps/12-join.setup.sh"
