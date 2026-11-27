# Sourced by the step setups that build a Rust crate (02-book, 05-deal, ledger-check; evidence-network/loop#489):
#
#   cargo_build_cached CRATE_DIR TOOLS_DIR BINARY
#
# Runs `cargo build --release --locked` for CRATE_DIR into TOOLS_DIR/target and installs BINARY as TOOLS_DIR/bin/BINARY,
# unless that binary was already built from the same inputs: every file of the crate (Cargo.toml, Cargo.lock, src/),
# the cargo and rustc versions and RUSTFLAGS, hashed into TOOLS_DIR/bin/BINARY.built-from. Cargo's own freshness check
# keys on the crate's path and file times, so every checkout or worktree of this repository rebuilt the same pinned
# sources (about 45 s each for 02-book); the hash keys on content only. Any change to an input rebuilds.
cargo_build_cached() {
  local crate=$1 tools=$2 bin=$3
  command -v flock > /dev/null || { echo "cargo_build_cached: flock is not installed (macOS: brew install flock)" >&2; return 1; }
  mkdir -p "$tools"
  # One build per tools directory at a time: checkouts at different sources share it, and the binary and its stamp
  # must come from the same build.
  (
    flock 9
    _cargo_build_cached_locked "$crate" "$tools" "$bin"
  ) 9> "$tools/.build.lock"
}

_cargo_build_cached_locked() {
  local crate=$1 tools=$2 bin=$3 key
  key=$(
    cd "$crate" &&
      find . -path ./target -prune -o -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 &&
      cargo --version && rustc --version && echo "RUSTFLAGS=${RUSTFLAGS:-}"
  ) || return 1
  key=$(printf '%s\n' "$key" | shasum -a 256 | cut -d' ' -f1)
  if [ -x "$tools/bin/$bin" ] && [ "$(cat "$tools/bin/$bin.built-from" 2>/dev/null)" = "$key" ]; then
    echo "$bin: already built from these sources ($key)" >&2
    return 0
  fi
  rm -f "$tools/bin/$bin.built-from"
  CARGO_TARGET_DIR="$tools/target" cargo build --release --locked --manifest-path "$crate/Cargo.toml" || return 1
  mkdir -p "$tools/bin"
  install -m 0755 "$tools/target/release/$bin" "$tools/bin/$bin" || return 1
  echo "$key" > "$tools/bin/$bin.built-from"
}
