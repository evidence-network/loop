#!/usr/bin/env bash
# lab-wallet-setup.sh N [--check|--balance]: part B of the lab wallet setup. Readies lab-N (N = 1, 2
# or 3) to run on the wallet that part A (lab-wallet-create.sh N) created in lab-N's payments
# directory, without touching the running node:
#   1. checks that A's wallet exists (its seed file and the payments/wallet-provider.json pin) and
#      that its wallet.lock is free, so A is not still running;
#   2. installs the same lexe-wallet build A used (lexe-wallet-plugin#7) into lab-N's HOME, or
#      enables it if a rollback disabled it.
# It neither writes config.toml nor restarts a node: lab.sh does both, for all nodes at once, so
# lab-1's new invite token reaches lab-2 and lab-3 and lab.sh stays the one writer of [payments].
# Once every node that pays is ready, while the lab is quiet:
#   lab/lab.sh stop
#   LAB_PAYMENTS="lab-1 lab-2 lab-3" lab/lab.sh start
# and keep LAB_PAYMENTS on every later start (start refuses otherwise). Then --balance runs
# get-balance on lab-N's console, which opens A's wallet (same directory, pin and seed).
# It never creates a wallet, reads the seed or pays. --check runs step 1 only and changes nothing.
#
# Environment (defaults are the lab as of 2026-10-31 07:05Z):
#   LAB_DIR        node root (/home/intangible/loop-lab)
#   LAB_PORT_BASE  lab.sh's port base (9800): lab-N's console is BASE + 10 + N
#   MESH_LLM_BIN   lab build of mesh-llm (/home/intangible/lab-1031/target/release/mesh-llm, e877a45df)
#   LEXE_PLUGIN    staged plugin binary (/home/intangible/lexe-9f830c1/lexe-wallet)
#   LEXE_SHA256    its expected sha256 (the #7 build staged by mesh-2717)
set -euo pipefail

usage() { echo "usage: $0 N [--check|--balance]   (N = 1, 2 or 3)" >&2; exit 2; }
[ $# -ge 1 ] && [ $# -le 2 ] || usage
N=$1
case "$N" in 1 | 2 | 3) ;; *) usage ;; esac
MODE=install
case "${2:-}" in --check) MODE=check ;; --balance) MODE=balance ;; "") ;; *) usage ;; esac

LAB_DIR=${LAB_DIR:-/home/intangible/loop-lab}
MESH_LLM_BIN=${MESH_LLM_BIN:-/home/intangible/lab-1031/target/release/mesh-llm}
LEXE_PLUGIN=${LEXE_PLUGIN:-/home/intangible/lexe-9f830c1/lexe-wallet}
LEXE_SHA256=${LEXE_SHA256:-6c82bd9fc64cdd177023d9c99990c4e7e5d931e258ee01217b59286e0a404255}

NODE=lab-$N
HOME_N=$LAB_DIR/nodes/$NODE
CONFIG=$HOME_N/config.toml
PORT=$((${LAB_PORT_BASE:-9800} + 10 + N)) # lab.sh console_port: BASE + 10 + index
WALLET_DIR=$HOME_N/payments/wallets/lexe-wallet

die() { echo "lab-wallet-setup: $*" >&2; exit 1; }
sha() { sha256sum "$1" | cut -d' ' -f1; }
[ "$(id -un)" = intangible ] || die "run as intangible (sudo -u intangible)"
[ -f "$CONFIG" ] || die "$CONFIG not found"
[ -x "$MESH_LLM_BIN" ] || die "$MESH_LLM_BIN not found"

if [ "$MODE" = balance ]; then
  grep -q '^\[payments\]' "$CONFIG" || die "$CONFIG has no [payments]: start the lab with LAB_PAYMENTS naming $NODE first"
  HOME=$HOME_N "$MESH_LLM_BIN" --config "$CONFIG" wallet --port "$PORT" get-balance
  exit 0
fi

# 1. A's wallet, and A not running.
[ -e "$WALLET_DIR/seedphrase.txt" ] || die "no wallet in $WALLET_DIR: run lab-wallet-create.sh $N (part A) first"
[ -e "$HOME_N/payments/wallet-provider.json" ] || die "no wallet pin in $HOME_N/payments: part A did not finish"
flock -n "$WALLET_DIR/wallet.lock" true || die "$WALLET_DIR/wallet.lock is held: is part A still running?"
if [ "$MODE" = check ]; then
  echo "check: $NODE has part A's wallet and pin, and wallet.lock is free; nothing changed"
  exit 0
fi

# 2. The plugin. plugins install takes an archive holding <name>/plugin.toml and the executable,
# the layout of the released lexe-wallet-v0.1.0 tarball, so the bare binary is packed that way.
# The running node does not load it until lab.sh starts it with [payments].
[ "$(sha "$LEXE_PLUGIN")" = "$LEXE_SHA256" ] || die "$LEXE_PLUGIN sha256 is not $LEXE_SHA256"
installed=$HOME_N/.mesh-llm/plugins/installed/lexe-wallet/lexe-wallet
if [ -f "$installed" ] && [ "$(sha "$installed")" = "$LEXE_SHA256" ]; then
  echo "plugin: lexe-wallet $LEXE_SHA256 already installed for $NODE"
else
  t=$(mktemp -d)
  trap 'rm -rf "$t"' EXIT
  mkdir "$t/lexe-wallet"
  cp "$LEXE_PLUGIN" "$t/lexe-wallet/lexe-wallet"
  printf 'name = "lexe-wallet"\nversion = "0.1.0"\n' > "$t/lexe-wallet/plugin.toml"
  tar -C "$t" -czf "$t/lexe-wallet-9f830c1-x86_64-unknown-linux-gnu.tar.gz" lexe-wallet
  HOME=$HOME_N MESH_LLM_NO_DEFAULT_PLUGINS=1 "$MESH_LLM_BIN" plugins install --name lexe-wallet \
    --version 0.1.0-9f830c1 --archive "$t/lexe-wallet-9f830c1-x86_64-unknown-linux-gnu.tar.gz"
fi
HOME=$HOME_N "$MESH_LLM_BIN" plugins enable lexe-wallet
echo "Done: $NODE is ready for its wallet. When every paying node is ready and the lab is quiet:"
echo "  lab/lab.sh stop && LAB_PAYMENTS=\"<the paying nodes>\" lab/lab.sh start"
echo "then $0 $N --balance. Keep LAB_PAYMENTS on every later start, or start refuses (it would drop the wallet)."
