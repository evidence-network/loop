#!/usr/bin/env bash
# lab-wallet-setup.sh N [--check|--balance]: part B of the lab wallet setup. Readies lab-N (N = 1, 2
# or 3) to run on the wallet that part A (lab-wallet-create.sh N) created in lab-N's payments
# directory, without touching the running node:
#   1. checks that A's wallet exists (its seed file and the payments/wallet-provider.json pin) and
#      that its wallet.lock is free, so A is not still running;
#   2. installs the same lexe-wallet build A used (lexe-wallet-plugin#7) into lab-N's HOME, or
#      enables it if it was disabled.
# It neither writes config.toml nor restarts a node: lab.sh does both, for all nodes at once, so
# lab-1's new invite token reaches lab-2 and lab-3 and lab.sh stays the one writer of [payments].
# Once every node that pays is ready, while the lab is quiet:
#   lab/lab.sh stop
#   LAB_PAYMENTS="lab-1 lab-2 lab-3" lab/lab.sh start
# and keep LAB_PAYMENTS on every later start (start refuses otherwise). Then --balance runs
# get-balance on lab-N's console, which opens A's wallet (same directory, pin and seed).
# It never creates a wallet, reads the seed or pays. --check runs step 1 only and changes nothing.
#
# Environment (defaults: the lab user's home layout, LAB-BRINGUP.md "One user on both hosts"):
#   LAB_DIR        node root ($HOME/loop-lab)
#   LAB_PORT_BASE  lab.sh's port base (9800): lab-N's console is BASE + 10 + N
#   MESH_LLM_BIN   lab build of mesh-llm ($HOME/lab-bin/mesh-llm, the lab build)
#   LEXE_PLUGIN    staged plugin binary ($HOME/lab-bin/lexe-wallet)
#   LEXE_SHA256    its expected sha256 (the #7 build staged by mesh-2717)
#   LAB_PAY_PLUGIN pay-lightning: the host build has no node payments (mesh-llm#2654), so the node also gets the pay-lightning
#                  plugin (ln-payments-plugin), which finds the wallet in this home's payments/ again (same layout) and binds
#                  the installed wallet plugin; --balance then asks it through the node's console. Unset: the legacy [payments]
#   PAY_LIGHTNING_ARCHIVE / PAY_LIGHTNING_SHA256   its `just package` archive ($HOME/lab-bin/pay-lightning.tar.gz) and the
#                  sha256 of that archive (required with LAB_PAY_PLUGIN)
set -euo pipefail

usage() { echo "usage: $0 N [--check|--balance]   (N = 1, 2 or 3)" >&2; exit 2; }
[ $# -ge 1 ] && [ $# -le 2 ] || usage
N=$1
case "$N" in 1 | 2 | 3) ;; *) usage ;; esac
MODE=install
case "${2:-}" in --check) MODE=check ;; --balance) MODE=balance ;; "") ;; *) usage ;; esac

LAB_DIR=${LAB_DIR:-$HOME/loop-lab}
MESH_LLM_BIN=${MESH_LLM_BIN:-$HOME/lab-bin/mesh-llm}
LEXE_PLUGIN=${LEXE_PLUGIN:-$HOME/lab-bin/lexe-wallet}
LEXE_SHA256=${LEXE_SHA256:-6c82bd9fc64cdd177023d9c99990c4e7e5d931e258ee01217b59286e0a404255}
PAY_PLUGIN=${LAB_PAY_PLUGIN:-}
PAY_LIGHTNING_ARCHIVE=${PAY_LIGHTNING_ARCHIVE:-$HOME/lab-bin/pay-lightning.tar.gz}
PAY_LIGHTNING_SHA256=${PAY_LIGHTNING_SHA256:-}

NODE=lab-$N
HOME_N=$LAB_DIR/nodes/$NODE
CONFIG=$HOME_N/config.toml
PORT=$((${LAB_PORT_BASE:-9800} + 10 + N)) # lab.sh console_port: BASE + 10 + index
WALLET_DIR=$HOME_N/payments/wallets/lexe-wallet

die() { echo "lab-wallet-setup: $*" >&2; exit 1; }
sha() { sha256sum "$1" | cut -d' ' -f1; }
[ -d "$LAB_DIR" ] || die "$LAB_DIR not found"
[ "$(id -un)" = "$(stat -c %U "$LAB_DIR")" ] || die "run as the user that owns $LAB_DIR (the lab user: sudo -u <lab user>)"
[ -f "$CONFIG" ] || die "$CONFIG not found"
[ -x "$MESH_LLM_BIN" ] || die "$MESH_LLM_BIN not found"

if [ "$MODE" = balance ]; then
  if [ "$PAY_PLUGIN" = pay-lightning ]; then
    HOME=$HOME_N "$MESH_LLM_BIN" plugins call pay-lightning control --input '{"command":"balance"}' --port "$PORT"
    exit 0
  fi
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
if [ "$PAY_PLUGIN" = pay-lightning ]; then
  [ -n "$PAY_LIGHTNING_SHA256" ] || die "set PAY_LIGHTNING_SHA256 (the archive's sha256)"
  [ "$(sha "$PAY_LIGHTNING_ARCHIVE")" = "$PAY_LIGHTNING_SHA256" ] || die "$PAY_LIGHTNING_ARCHIVE sha256 is not $PAY_LIGHTNING_SHA256"
  ver=$(tar -xzOf "$PAY_LIGHTNING_ARCHIVE" pay-lightning/plugin.toml | sed -n 's/^version *= *"\(.*\)"/\1/p')
  HOME=$HOME_N MESH_LLM_NO_DEFAULT_PLUGINS=1 "$MESH_LLM_BIN" plugins install --name pay-lightning --version "$ver" \
    --archive "$PAY_LIGHTNING_ARCHIVE"
  HOME=$HOME_N "$MESH_LLM_BIN" plugins enable pay-lightning
  # pay-lightning keeps its ledger and finds the wallet under $HOME/.mesh-llm/payments; this lab's node homes keep them in
  # $HOME/payments (the layout the node's own payments feature used, schema 6 either way). One directory, two names: removing
  # the link returns the node to the legacy host build.
  [ -e "$HOME_N/.mesh-llm/payments" ] || ln -s ../payments "$HOME_N/.mesh-llm/payments"
fi
echo "Done: $NODE is ready for its wallet. When every paying node is ready and the lab is quiet:"
echo "  lab/lab.sh stop && LAB_PAYMENTS=\"<the paying nodes>\" lab/lab.sh start"
echo "then $0 $N --balance. Keep LAB_PAYMENTS on every later start, or start refuses (it would drop the wallet)."
