#!/usr/bin/env bash
# Install the capsule-cli release versions.lock pins as `capsule-cli`, whose `capsulectl verify --history` checks the
# history_card/1 a commitment staples (steps 03 and 12, scripts/staple_verify.py; loop#401):
#
#   steps/capsule-cli.setup.sh
#
# Built from the pinned tag with the release's own flags (scripts/release-build.sh: CGO off, -trimpath, no VCS stamp,
# the version and commit injected), for this host. Needs git and go (the version the tag's go.mod names, or newer).
# Installs to ${LOOP_TOOLS:-~/.cache/evidence-network-loop}/capsule-cli/bin/capsulectl, a different file from the
# capsulectl steps/12-join.setup.sh builds from the evidencebook pin, with capsule-cli.built-from naming the tag
# and commit. A binary already built at this pin is kept.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/capsule-cli/bin"
read -r repo tag <<<"$(awk '$1 == "capsule-cli" { print $2, $3 }' "$root/versions.lock")"
[ -n "${tag:-}" ] || { echo "capsule-cli.setup: capsule-cli is not pinned in versions.lock" >&2; exit 1; }
if [ -x "$dest/capsulectl" ] && [ "$(cut -d' ' -f1,2 "$dest/capsule-cli.built-from" 2>/dev/null)" = "$repo $tag" ]; then
  echo "capsule-cli.setup: $repo $tag already in $dest" >&2
  exit 0
fi
command -v git >/dev/null || { echo "capsule-cli.setup: git is not installed" >&2; exit 1; }
command -v go >/dev/null || { echo "capsule-cli.setup: go is not installed" >&2; exit 1; }
src="$(mktemp -d)"
trap 'rm -rf "$src"' EXIT
git -c advice.detachedHead=false clone -q --depth 1 --branch "$tag" "https://github.com/$repo" "$src/repo"
commit="$(git -C "$src/repo" rev-parse HEAD)"
pkg="github.com/$repo/cli"
mkdir -p "$dest"
(cd "$src/repo" && CGO_ENABLED=0 go build -trimpath -buildvcs=false \
  -ldflags "-s -w -buildid= -X $pkg.CLIVersion=$tag -X $pkg.CLICommit=$commit" -o "$dest/capsulectl" ./cmd/capsulectl)
chmod 755 "$dest/capsulectl"
echo "$repo $tag $commit" >"$dest/capsule-cli.built-from"
"$dest/capsulectl" --version >&2
echo "capsule-cli.setup: $repo $tag ($commit) in $dest" >&2
