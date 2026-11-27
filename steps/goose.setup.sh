#!/usr/bin/env bash
# Install the Goose node: evidencebook's python/examples/goose at the goose-g1 pin in versions.lock, a thin MCP client
# of the capsulectl-deal plugin (x-deal-v1), under ${LOOP_TOOLS:-~/.cache/evidence-network-loop}/goose:
#   src/                     the evidencebook checkout at the pin
#   bin/capsulectl-deal      built from that checkout's capsulectl/ module (./cmd/capsulectl-deal)
#   bin/capsule-engine       built from capsule-engine at its own pin (the admission a deal's agreement cites)
#   venv/                    the interpreter, with cryptography, mcp and websockets (the Nostr mailbox)
# scripts/goose_rehearsal.py and scripts/goose_live.py run it, naming the two binaries to the node processes
# (GOOSE_CAPSULECTL_DEAL, GOOSE_CAPSULE_ENGINE). Needs python3 (>= 3.10), git, go and network.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/goose"
read -r REPO REF < <(awk '$1 == "goose-g1" { print $2, $3 }' "$ROOT/versions.lock")
[ -n "${REF:-}" ] || { echo "versions.lock lacks goose-g1" >&2; exit 1; }
read -r CE_REPO CE_REF < <(awk '$1 == "capsule-engine" { print $2, $3 }' "$ROOT/versions.lock")
[ -n "${CE_REF:-}" ] || { echo "versions.lock lacks capsule-engine" >&2; exit 1; }
command -v go >/dev/null || { echo "goose.setup: go is not installed" >&2; exit 1; }

# fetch REPO REF DIR: a checkout of exactly REF, kept when already there. REF is a full commit SHA or a release tag; a
# tag is kept as refs/tags/<tag>, so the checkout can say it is at the release (steps/finding.py checkout_ref, which
# the Goose runs record as the revision the steps compare with the pin).
fetch() {
  local spec want
  if [[ "$2" =~ ^[0-9a-f]{40}$ ]]; then
    spec="$2"; want="$2"
  else
    spec="+refs/tags/$2:refs/tags/$2"; want="$(git -C "$3" rev-parse -q --verify "refs/tags/$2^{commit}" 2>/dev/null || true)"
  fi
  if [ -z "$want" ] || [ "$(git -C "$3" rev-parse HEAD 2>/dev/null || true)" != "$want" ]; then
    rm -rf "$3" && mkdir -p "$3"
    git -C "$3" init -q
    git -C "$3" fetch -q --no-tags --depth 1 "https://github.com/$1" "$spec"
    git -C "$3" -c advice.detachedHead=false checkout -q "FETCH_HEAD^{commit}"
  fi
}
fetch "$REPO" "$REF" "$DEST/src"
fetch "$CE_REPO" "$CE_REF" "$DEST/capsule-engine"
mkdir -p "$DEST/bin"
# As evidencebook's own CI builds them for the Goose node (.github/workflows/ci.yml, the goose job).
go -C "$DEST/src/capsulectl" build -o "$DEST/bin/capsulectl-deal" ./cmd/capsulectl-deal
GOWORK=off go -C "$DEST/capsule-engine/go" build -o "$DEST/bin/capsule-engine" ./cmd/capsule-engine
echo "$REPO $REF capsule-engine $CE_REPO $CE_REF" >"$DEST/bin/built-from"
python3 -m venv "$DEST/venv"
"$DEST/venv/bin/python" -m pip install --quiet --disable-pip-version-check cryptography "mcp==2.3.0" "websockets==17.4"
echo "goose: $REPO@$REF and $CE_REPO@$CE_REF in $DEST"
