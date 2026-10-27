#!/usr/bin/env bash
# Install step 01's tools at their versions.lock pins, under $LOOP_TOOLS/01-seal
# (default ~/.cache/evidence-network-loop/01-seal, as loop.sh exports it):
#   venv/            capsule-emit from PyPI (the sealer, Python), its dependencies
#                    held to steps/01-seal.constraints.txt
#   bin/cross_language   the AAC Go module's Class 1 verifier (the second language)
# Needs python3 (>= 3.9), go and network. Nothing else installs for step 01.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/01-seal"
pin() { awk -v n="$1" '$1 == n { print $3 }' "$ROOT/versions.lock"; }
CAPSULE_EMIT="$(pin capsule-emit)"   # python/vX.Y.Z
AAC_GO="$(pin aac-go)"               # go/vX.Y.Z
[ -n "$CAPSULE_EMIT" ] && [ -n "$AAC_GO" ] || { echo "versions.lock lacks capsule-emit or aac-go" >&2; exit 1; }

python3 -m venv "$DEST/venv"
"$DEST/venv/bin/python" -m pip install --quiet --disable-pip-version-check \
  -c "$ROOT/steps/01-seal.constraints.txt" "capsule-emit==${CAPSULE_EMIT#python/v}"
GOBIN="$DEST/bin" GOFLAGS=-mod=mod go install \
  "github.com/action-state-group/agent-action-capsule/go/cmd/cross_language@${AAC_GO#go/}"
echo "01-seal: capsule-emit ${CAPSULE_EMIT#python/v} and aac-go ${AAC_GO#go/} in $DEST"
