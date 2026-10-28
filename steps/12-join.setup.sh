#!/usr/bin/env bash
# Install what step 12 (Join) needs, exactly at its versions.lock pins:
# capsulectl (the core) and capsulectl-book (the book plugin), both built from
# source at the evidencebook tag they pin. That tag has no release assets, so
# its capsulectl/ module is built here: capsulectl-book from ./cmd/capsulectl-book,
# and the core from the capsule-cli version that module's go.mod and go.sum
# lock. The binaries go to $LOOP_TOOLS/bin (default
# ~/.cache/evidence-network-loop/bin), where join.sh and steps/02-book and
# 12-join look for them, with capsulectl.built-from naming the tag and commit.
# Needs git and go (the version capsulectl/go.mod names, or a newer one); openssl 3
# and python3 come from the host.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
bin="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/bin"
mkdir -p "$bin"
# capsulectl refuses a plugin that others can write (the file or its directory). Under a
# umask of 002 (the Ubuntu default) go build and mkdir leave both group-writable, so the
# binaries and their directory are made go-w here, on every run, the early exit included.
tighten() { chmod go-w "$bin" "$(dirname "$bin")"; for f in "$bin/capsulectl" "$bin/capsulectl-book"; do [ -e "$f" ] && chmod 755 "$f"; done; return 0; }
tighten

pin() { awk -v n="$1" '$1 == n { print $2, $3 }' "$root/versions.lock"; }
read -r core_repo core_tag <<<"$(pin capsulectl)"
read -r book_repo book_tag <<<"$(pin capsulectl-book)"
[ -n "${core_tag:-}" ] && [ -n "${book_tag:-}" ] || { echo "12-join.setup: capsulectl or capsulectl-book is not pinned" >&2; exit 1; }
# Both come from one evidencebook tag: the plugin embeds the core it is built against.
[ "$core_repo $core_tag" = "$book_repo $book_tag" ] || {
  echo "12-join.setup: capsulectl ($core_repo $core_tag) and capsulectl-book ($book_repo $book_tag) must be pinned to the same tag" >&2; exit 1; }
# Built already at this pin (steps/02-book.setup.sh runs this script too): nothing to do.
if [ -x "$bin/capsulectl" ] && [ -x "$bin/capsulectl-book" ] && \
   [ "$(cut -d' ' -f1,2 "$bin/capsulectl.built-from" 2>/dev/null)" = "$book_repo $book_tag" ]; then
  echo "12-join.setup: capsulectl and capsulectl-book from $book_repo $book_tag already in $bin" >&2
  exit 0
fi
command -v go >/dev/null || { echo "12-join.setup: go is not installed" >&2; exit 1; }

src="$(mktemp -d)"
trap 'rm -rf "$src"' EXIT
git -c advice.detachedHead=false clone -q --depth 1 --branch "$book_tag" "https://github.com/$book_repo" "$src/repo"
commit="$(git -C "$src/repo" rev-parse HEAD)"
cd "$src/repo/capsulectl"

# As the tag's own release build (capsulectl/scripts/release-build.sh): CGO off,
# -trimpath, no VCS stamp, and the core's and the plugin's versions injected.
CORE=github.com/action-state-group/capsule-cli
core_version="$(go list -m -f '{{.Version}}' "$CORE")"
core_commit="$(go mod download -json "$CORE@$core_version" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Origin"]["Hash"])')"
core_ld="-s -w -buildid= -X $CORE/cli.CLIVersion=$core_version -X $CORE/cli.CLICommit=$core_commit"
build() { CGO_ENABLED=0 go build -mod=readonly -trimpath -buildvcs=false "$@"; }
build -ldflags "$core_ld -X $CORE/cli.PluginVersion=$book_tag -X $CORE/cli.PluginCommit=$commit" \
  -o "$bin/capsulectl-book" ./cmd/capsulectl-book
build -ldflags "$core_ld" -o "$bin/capsulectl" "$CORE/cmd/capsulectl"
echo "$book_repo $book_tag $commit capsule-cli $core_version" >"$bin/capsulectl.built-from"
tighten

"$bin/capsulectl" --version >&2
"$bin/capsulectl-book" --version >&2
echo "12-join.setup: capsulectl ($CORE $core_version) and capsulectl-book $book_tag, built from $book_repo $book_tag ($commit), in $bin" >&2
