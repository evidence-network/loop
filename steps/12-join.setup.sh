#!/usr/bin/env bash
# Install what step 12 (Join) needs, exactly at its versions.lock pins:
# capsulectl (the core) and capsulectl-book (the book plugin), linux-amd64
# release binaries, each checked against its release's SHA256SUMS. They go to
# $LOOP_TOOLS/bin (default ~/.cache/evidence-network-loop/bin), where join.sh
# and steps/12-join look for them. openssl 3 and python3 come from the host.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
bin="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/bin"
mkdir -p "$bin"

pin() { awk -v n="$1" '$1 == n { print $2, $3 }' "$root/versions.lock"; }
read -r core_repo core_tag <<<"$(pin capsulectl)"
read -r book_repo book_tag <<<"$(pin capsulectl-book)"
[ -n "${core_tag:-}" ] && [ -n "${book_tag:-}" ] || { echo "12-join.setup: capsulectl or capsulectl-book is not pinned" >&2; exit 1; }
# A capsulectl-book release takes the version of the capsulectl it is built for.
[ "${book_tag##*/}" = "$core_tag" ] || { echo "12-join.setup: capsulectl-book $book_tag is not built for capsulectl $core_tag" >&2; exit 1; }

fetch() { # REPO TAG ASSET NAME: download one release asset, check it against SHA256SUMS, install it as NAME
  local repo="$1" tag="$2" asset="$3" dest="$bin/$4" tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  local base="https://github.com/$repo/releases/download/$tag"
  curl -fsSL -o "$tmp/$asset" "$base/$asset"
  curl -fsSL -o "$tmp/SHA256SUMS" "$base/SHA256SUMS"
  (cd "$tmp" && grep " $asset\$" SHA256SUMS | sha256sum -c --strict -) >&2
  install -m 0755 "$tmp/$asset" "$dest"
}

version="${core_tag#v}"
fetch "$core_repo" "$core_tag" "capsulectl-v$version-linux-amd64" capsulectl
fetch "$book_repo" "$book_tag" "capsulectl-book-v$version-linux-amd64" capsulectl-book
"$bin/capsulectl" --version >&2
echo "12-join.setup: capsulectl $core_tag and capsulectl-book $book_tag in $bin" >&2
