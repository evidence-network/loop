#!/usr/bin/env bash
# Install the offline ledger check steps 04, 05, 07, 08 and 09 share (loop#276, loop#288): steps/ledger-check.d built --locked against the
# checkpointed-local-log version versions.lock pins, the log the capsules plugin signs its ledger checkpoints with.
#
#   steps/ledger-check.setup.sh
#
# Needs cargo and the crates.io index. Installs to
# ${LOOP_TOOLS:-~/.cache/evidence-network-loop}/ledger-check/bin/loop-ledger-check, where steps/ledger_proof.py looks.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CRATE="$ROOT/steps/ledger-check.d"
TOOLS="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/ledger-check"

cll=$(awk '$1 == "checkpointed-local-log" { print $3 }' "$ROOT/versions.lock")
case "$cll" in
  "crates/checkpointed-local-log-v"*) want=${cll#crates/checkpointed-local-log-v} ;;
  *) echo "ledger-check.setup: versions.lock checkpointed-local-log is '$cll', not a crates/checkpointed-local-log-v<version> tag" >&2; exit 1 ;;
esac
got=$(awk '$0 == "name = \"checkpointed-local-log\"" { want = 1; next } want && $1 == "version" { gsub(/"/, "", $3); print $3; exit }' "$CRATE/Cargo.lock")
if [ "$want" != "$got" ]; then
  echo "ledger-check.setup: Cargo.lock has checkpointed-local-log '$got', versions.lock pins $want" >&2
  exit 1
fi
command -v cargo >/dev/null || { echo "ledger-check.setup: cargo is not installed" >&2; exit 1; }
CARGO_TARGET_DIR="$TOOLS/target" cargo build --release --locked --manifest-path "$CRATE/Cargo.toml"
mkdir -p "$TOOLS/bin"
install -m 0755 "$TOOLS/target/release/loop-ledger-check" "$TOOLS/bin/loop-ledger-check"
echo "ledger-check.setup: installed $TOOLS/bin/loop-ledger-check (checkpointed-local-log $got)"
