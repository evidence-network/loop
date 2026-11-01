#!/usr/bin/env bash
# Install the Goose rehearsal's node: evidencebook's python/examples/goose (G1) at the goose-g1 pin in
# versions.lock, with the evidencebook Python package and `mcp` in a venv, under
# ${LOOP_TOOLS:-~/.cache/evidence-network-loop}/goose (src/ holds the checkout, venv/ the interpreter).
# scripts/goose_rehearsal.py runs it. Needs python3 (>= 3.10), git and network.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/goose"
read -r REPO REF < <(awk '$1 == "goose-g1" { print $2, $3 }' "$ROOT/versions.lock")
[ -n "${REF:-}" ] || { echo "versions.lock lacks goose-g1" >&2; exit 1; }

if [ "$(git -C "$DEST/src" rev-parse HEAD 2>/dev/null || true)" != "$REF" ]; then
  rm -rf "$DEST/src" && mkdir -p "$DEST/src"
  git -C "$DEST/src" init -q
  git -C "$DEST/src" fetch -q --depth 1 "https://github.com/$REPO" "$REF"
  git -C "$DEST/src" checkout -q FETCH_HEAD
fi
python3 -m venv "$DEST/venv"
"$DEST/venv/bin/python" -m pip install --quiet --disable-pip-version-check "$DEST/src/python" "mcp>=1.20"
echo "goose: $REPO@$REF in $DEST"
