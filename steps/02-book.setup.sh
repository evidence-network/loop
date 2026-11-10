#!/usr/bin/env bash
# Install step 02 Book's driver: steps/02-book.d built against the pins in
# versions.lock: evidencebook's Rust crate (the crates.io release the pinned
# crates/evidencebook-v<version> tag publishes), and the crates it uses
# (checkpointed-local-log, evidence-request, and
# AAC's agent-action-capsule, whose witness receipt verifier it calls). For the
# live population, capsulectl and capsulectl-book (steps/12-join.setup.sh).
#
#   steps/02-book.setup.sh
#
# Needs cargo, git and the crates.io index (the build fetches the pinned
# crates; Cargo.lock carries their checksums). Installs to
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

# Whether evidencebook's own Cargo.lock entry depends on crate $1 at version $2 (agent-action-capsule 0.10.0 still
# depends on evidence-request 0.4.1, so the lock carries two versions of it and Cargo names the version in the
# dependency list; evidence-request is pinned to the one evidencebook uses).
eb_depends_on() {
  awk -v d="\"$1 $2\"," '$0 == "name = \"evidencebook\"" { want = 1; next }
    want && $0 == "]" { exit }
    want && $0 == " " d { found = 1 }
    END { exit !found }' "$CRATE/Cargo.lock"
}

for pin in checkpointed-local-log:checkpointed-local-log evidence-request:evidence-request aac-rust:agent-action-capsule; do
  name=${pin%%:*} crate=${pin#*:}
  want=$(pinned_version "$name" "$crate")
  if [ "$crate" = evidence-request ]; then
    eb_depends_on "$crate" "$want" || { echo "02-book.setup: evidencebook's Cargo.lock entry does not depend on $crate $want, which versions.lock pins" >&2; exit 1; }
    continue
  fi
  got=$(locked "$crate" version)
  if [ "$want" != "$got" ]; then
    echo "02-book.setup: Cargo.lock has $crate '$got', versions.lock pins $want" >&2
    exit 1
  fi
done
# evidencebook: the crates.io crate the pinned crates/evidencebook-v<version> tag publishes.
eb_want=$(pin_ref evidencebook)
eb_want=${eb_want#crates/evidencebook-v}
eb_got=$(locked evidencebook version)
eb_source=$(locked evidencebook source)
if [ "$eb_want" != "$eb_got" ] || [ "$eb_source" != "registry+https://github.com/rust-lang/crates.io-index" ]; then
  echo "02-book.setup: Cargo.lock has evidencebook '$eb_got' from '$eb_source', versions.lock pins $(pin_ref evidencebook)" >&2
  exit 1
fi

command -v cargo >/dev/null || { echo "02-book.setup: cargo is not installed" >&2; exit 1; }
CARGO_TARGET_DIR="$TOOLS/target" cargo build --release --locked --manifest-path "$CRATE/Cargo.toml"
mkdir -p "$TOOLS/bin"
install -m 0755 "$TOOLS/target/release/loop-step02-book" "$TOOLS/bin/loop-step02-book"
echo "02-book.setup: installed $TOOLS/bin/loop-step02-book (evidencebook $eb_got, checkpointed-local-log $(locked checkpointed-local-log version), agent-action-capsule $(locked agent-action-capsule version))"

# On the live population the step drives each node's book with capsulectl and
# capsulectl-book, at the versions.lock pins step 12's setup installs.
"$ROOT/steps/12-join.setup.sh"
