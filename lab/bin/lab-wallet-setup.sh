#!/usr/bin/env bash
# lab-wallet-setup.sh N: part B of the lab wallet setup. Moves lab-N (N = 1, 2 or 3) onto the
# wallet that part A (lab-wallet-create.sh N) created and funded in lab-N's payments directory.
#
# Run as intangible on the payer lab host, only while the lab is quiet: it restarts lab-N and
# refuses, changing nothing, while a loop or pm-live run is active. It also refuses unless A's
# wallet exists and its lock is free. Steps:
#   1. install the same lexe-wallet build A used (lexe-wallet-plugin#7) into lab-N's HOME;
#   2. back up lab-N's config.toml and add [payments] wallet = "lexe-wallet";
#   3. restart lab-N by replaying the launcher lab.sh wrote (run/lab-N.sh), after checking that it
#      starts exactly the running node's /proc cmdline; replayed in the node's /proc cwd and the
#      tmux session lab.sh uses. If the node does not come back, it rolls back and restarts as before;
#   4. get-balance on lab-N's console, which opens A's wallet (same directory, pin and seed).
# It never creates a wallet, reads the seed or pays. --check runs the checks, changes nothing and
# exits.
#
# Environment (defaults are the lab as of 2026-10-31 07:05Z):
#   LAB_DIR        node root (/home/intangible/loop-lab)
#   MESH_LLM_BIN   lab build of mesh-llm (/home/intangible/lab-1031/target/release/mesh-llm, e877a45df)
#   LEXE_PLUGIN    staged plugin binary (/home/intangible/lexe-9f830c1/lexe-wallet)
#   LEXE_SHA256    its expected sha256 (the #7 build staged by mesh-2717)
set -euo pipefail

usage() { echo "usage: $0 N [--check]   (N = 1, 2 or 3)" >&2; exit 2; }
[ $# -ge 1 ] || usage
N=$1
case "$N" in 1 | 2 | 3) ;; *) usage ;; esac
CHECK_ONLY=0
case "${2:-}" in --check) CHECK_ONLY=1 ;; "") ;; *) usage ;; esac

LAB_DIR=${LAB_DIR:-/home/intangible/loop-lab}
MESH_LLM_BIN=${MESH_LLM_BIN:-/home/intangible/lab-1031/target/release/mesh-llm}
LEXE_PLUGIN=${LEXE_PLUGIN:-/home/intangible/lexe-9f830c1/lexe-wallet}
LEXE_SHA256=${LEXE_SHA256:-6c82bd9fc64cdd177023d9c99990c4e7e5d931e258ee01217b59286e0a404255}

NODE=lab-$N
HOME_N=$LAB_DIR/nodes/$NODE
CONFIG=$HOME_N/config.toml
LAUNCHER=$LAB_DIR/run/$NODE.sh
LOG=$LAB_DIR/logs/$NODE.log
PORT=$((${LAB_PORT_BASE:-9800} + 10 + N)) # lab.sh console_port: BASE + 10 + index
SESSION=${LAB_TMUX_SESSION:-loop-lab}
WALLET_DIR=$HOME_N/payments/wallets/lexe-wallet
BACKUP=

die() { echo "lab-wallet-setup: $*" >&2; exit 1; }
w() { HOME=$HOME_N "$MESH_LLM_BIN" --config "$CONFIG" wallet --port "$PORT" "$@"; }
sha() { sha256sum "$1" | cut -d' ' -f1; }
[ "$(id -un)" = intangible ] || die "run as intangible (sudo -u intangible)"
[ -f "$CONFIG" ] || die "$CONFIG not found"
[ -x "$MESH_LLM_BIN" ] || die "$MESH_LLM_BIN not found"

# Restarting a lab node in the middle of a run breaks that run. These are the run drivers: lab.sh,
# loop.sh, a stage step, and the pm-live scripts the PM runs.
busy() { pgrep -u intangible -af -- '(lab/lab\.sh|loop\.sh|/steps/[0-9]|pm-live[^ ]*\.sh)' | grep -v -- "lab-wallet-setup" || true; }

node_pid() { pgrep -u intangible -f -- "serve --config $CONFIG( |\$)" | head -1; }

wait_console() { # SECONDS
  local i
  for ((i = 0; i < $1; i += 2)); do
    if [ "$N" = 1 ]; then # lab-1 serves the model; lab.sh waits for llama_ready
      curl -fsS "http://127.0.0.1:$PORT/api/status" 2> /dev/null |
        python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("llama_ready") is True else 1)' && return 0
    else
      curl -fsS "http://127.0.0.1:$PORT/api/status" > /dev/null 2>&1 && return 0
    fi
    sleep 2
  done
  return 1
}

stop_node() { # PID; lab.sh stop also SIGTERMs and waits for the shutdown checkpoint
  kill -TERM "$1"
  local i
  for ((i = 0; i < 90; i++)); do kill -0 "$1" 2> /dev/null || return 0; sleep 1; done
  die "$NODE (pid $1) did not exit within 90s of SIGTERM; restore $CONFIG from $BACKUP before restarting it"
}

launch() { # CWD
  tmux new-window -d -t "$SESSION" -n "$NODE" -c "$1" "$(printf 'bash %q >> %q 2>&1' "$LAUNCHER" "$LOG")"
}

b=$(busy)
if [ -n "$b" ]; then
  echo "The lab is busy: a run is active, so $NODE is not restarted and nothing was changed." >&2
  echo "Run this again when the lab is quiet. Active:" >&2
  echo "$b" >&2
  exit 3
fi

pid=$(node_pid) || true
[ -n "$pid" ] || die "$NODE is not running; start the lab with lab.sh first"
cwd=$(readlink "/proc/$pid/cwd")
live_cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" | sed 's/ $//')
[ -f "$LAUNCHER" ] || die "$LAUNCHER not found"
# The launcher lab.sh wrote must start exactly the running process, so the replay changes nothing
# but the config.
grep -qF -- "$live_cmd < /dev/null" "$LAUNCHER" || die "$LAUNCHER does not start the running $NODE's cmdline; restart it with lab.sh instead"
tmux has-session -t "$SESSION" 2> /dev/null || die "tmux session $SESSION not found"
[ -e "$WALLET_DIR/seedphrase.txt" ] || die "no wallet in $WALLET_DIR: run lab-wallet-create.sh $N (part A) first"
[ -e "$LAB_DIR/nodes/$NODE/payments/wallet-provider.json" ] || die "no wallet pin in $HOME_N/payments: part A did not finish"
flock -n "$WALLET_DIR/wallet.lock" true || die "$WALLET_DIR/wallet.lock is held: is part A still running?"
if [ "$CHECK_ONLY" = 1 ]; then
  echo "check: lab quiet; $NODE pid $pid in $cwd matches $LAUNCHER; nothing changed"
  exit 0
fi

# 1. The plugin. plugins install takes an archive holding <name>/plugin.toml and the executable,
# the layout of the released lexe-wallet-v0.1.0 tarball, so the bare binary is packed that way.
[ "$(sha "$LEXE_PLUGIN")" = "$LEXE_SHA256" ] || die "$LEXE_PLUGIN sha256 is not $LEXE_SHA256"
installed=$HOME_N/.mesh-llm/plugins/installed/lexe-wallet/lexe-wallet
if [ -f "$installed" ] && [ "$(sha "$installed")" = "$LEXE_SHA256" ]; then
  echo "plugin: lexe-wallet $LEXE_SHA256 already installed for $NODE"
  HOME=$HOME_N "$MESH_LLM_BIN" plugins enable lexe-wallet # a rollback of an earlier run disabled it
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

# 2. Config, with a rollback copy of the config and the running process's cwd.
BACKUP=$HOME_N/pre-wallet-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -m 700 "$BACKUP"
cp -p "$CONFIG" "$BACKUP/config.toml"
echo "$cwd" > "$BACKUP/cwd"
echo "rollback copy: $BACKUP"
if grep -q '^\[payments\]' "$CONFIG"; then
  grep -q '^wallet = "lexe-wallet"' "$CONFIG" || die "$CONFIG already has a [payments] section without wallet = \"lexe-wallet\"; resolve it by hand"
else
  printf '\n[payments]\nwallet = "lexe-wallet"\n' >> "$CONFIG"
fi

# 3. Restart.
echo "restart: stopping $NODE (pid $pid)"
stop_node "$pid"
launch "$cwd"
if ! wait_console 300; then
  echo "restart: $NODE did not come up on :$PORT within 300s; rolling back" >&2
  p=$(node_pid) || true
  [ -n "$p" ] && stop_node "$p"
  cp -p "$BACKUP/config.toml" "$CONFIG"
  HOME=$HOME_N "$MESH_LLM_BIN" plugins disable lexe-wallet || true
  launch "$cwd"
  wait_console 300 && die "rolled back: $NODE runs as before, without payments; see $LOG"
  die "rolled back the config, but $NODE is not up on :$PORT yet; see $LOG"
fi
echo "restart: $NODE is up on :$PORT (pid $(node_pid))"

# 4. lab-N opens A's wallet; the balance should show the funding.
w get-balance
echo "Done: $NODE runs with payments on, on the wallet part A funded. Rollback copy: $BACKUP"
echo "From now on every lab.sh start must name $NODE in LAB_PAYMENTS, or start refuses (it would drop the wallet)."
