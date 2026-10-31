#!/usr/bin/env bash
# lab-wallet-setup.sh N: give lab-N (N = 1, 2 or 3) its own Lexe mainnet wallet for loop step 06.
#
# Run as intangible on the payer lab host, only while the lab is quiet: it restarts lab-N and
# refuses to start while a loop or pm-live run is active. Steps:
#   1. install the lexe-wallet plugin build staged for the lab (lexe-wallet-plugin#7, preimage
#      finality) into lab-N's HOME;
#   2. back up lab-N's config.toml and add [payments] wallet = "lexe-wallet";
#   3. restart lab-N by replaying the launcher lab.sh wrote (run/lab-N.sh), after checking that it
#      starts exactly the running node's /proc cmdline; replayed in the node's /proc cwd and the
#      tmux session lab.sh uses. If the node does not come back, it rolls back and restarts as before;
#   4. open the wallet: the plugin creates the seed and signs up a Lexe account on the first open;
#   5. set an automatic policy with a small daily budget;
#   6. print one fund-wallet invoice.
#
# It never reads, prints or copies the seed. Back up
#   $LAB_DIR/nodes/lab-N/payments/wallets/lexe-wallet/seedphrase.txt
# yourself, privately. Rerunning is safe: a plugin of the same sha is kept, the config line is
# added once, and an existing wallet is opened, not replaced. Every run prints a fresh invoice:
# pay only one per node. --invoice skips steps 1-5 and only prints a new invoice. --check runs the
# quiet-lab and launcher checks, changes nothing and exits.
#
# Environment (defaults are the lab as of 2026-10-31 07:05Z):
#   LAB_DIR        node root (/home/intangible/loop-lab)
#   MESH_LLM_BIN   lab build of mesh-llm (/home/intangible/lab-1031/target/release/mesh-llm, e877a45df)
#   LEXE_PLUGIN    staged plugin binary (/home/intangible/lexe-9f830c1/lexe-wallet)
#   LEXE_SHA256    its expected sha256 (the #7 build staged by mesh-2717)
#   FUND_SATS      invoice amount (1000); DAILY_SATS daily budget (20)
set -euo pipefail

usage() { echo "usage: $0 N [--invoice|--check]   (N = 1, 2 or 3)" >&2; exit 2; }
[ $# -ge 1 ] || usage
N=$1
case "$N" in 1 | 2 | 3) ;; *) usage ;; esac
INVOICE_ONLY=0 CHECK_ONLY=0
case "${2:-}" in --invoice) INVOICE_ONLY=1 ;; --check) CHECK_ONLY=1 ;; "") ;; *) usage ;; esac

LAB_DIR=${LAB_DIR:-/home/intangible/loop-lab}
MESH_LLM_BIN=${MESH_LLM_BIN:-/home/intangible/lab-1031/target/release/mesh-llm}
LEXE_PLUGIN=${LEXE_PLUGIN:-/home/intangible/lexe-9f830c1/lexe-wallet}
LEXE_SHA256=${LEXE_SHA256:-6c82bd9fc64cdd177023d9c99990c4e7e5d931e258ee01217b59286e0a404255}
FUND_SATS=${FUND_SATS:-1000}
DAILY_SATS=${DAILY_SATS:-20}

NODE=lab-$N
HOME_N=$LAB_DIR/nodes/$NODE
CONFIG=$HOME_N/config.toml
LAUNCHER=$LAB_DIR/run/$NODE.sh
LOG=$LAB_DIR/logs/$NODE.log
PORT=$((9810 + N)) # lab.sh console_port: BASE 9800 + 10 + index
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
  tmux new-window -d -t loop-lab -n "$NODE" -c "$1" "bash $LAUNCHER >> $LOG 2>&1"
}

if [ "$INVOICE_ONLY" = 0 ]; then
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
  tmux has-session -t loop-lab 2> /dev/null || die "tmux session loop-lab not found"
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

  # 2. Config, with a rollback copy of the config and the running process's argv, environ and cwd.
  BACKUP=$HOME_N/pre-wallet-$(date -u +%Y%m%dT%H%M%SZ)
  mkdir -m 700 "$BACKUP"
  cp -p "$CONFIG" "$BACKUP/config.toml"
  cp "/proc/$pid/cmdline" "$BACKUP/cmdline"
  (umask 077 && cp "/proc/$pid/environ" "$BACKUP/environ")
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

  # 4. Open the wallet. The first open creates the seed and the Lexe account.
  if [ -e "$WALLET_DIR/seedphrase.txt" ]; then
    echo "wallet: $WALLET_DIR already holds a seed; opening it"
  else
    echo "wallet: none yet; the first open creates the seed and a Lexe mainnet account"
  fi
  w get-balance
  [ -e "$WALLET_DIR/seedphrase.txt" ] || die "no seed file under $WALLET_DIR after get-balance"
  [ "$(stat -c %a "$WALLET_DIR")" = 700 ] || die "$WALLET_DIR is not mode 700"
  [ "$(stat -c %a "$WALLET_DIR/seedphrase.txt")" = 600 ] || die "seedphrase.txt is not mode 600"

  # 5. Policy: automatic within a small daily budget (9 sats per direction plus fees).
  w policy --mode automatic --daily-budget-sats "$DAILY_SATS"
fi

# 6. One invoice to pay from your own wallet. Funding never authorizes spending; the policy does.
echo
echo "Pay this invoice of $FUND_SATS sats for $NODE from your own wallet:"
w fund-wallet --amount-sats "$FUND_SATS"
echo
echo "After paying, check: HOME=$HOME_N $MESH_LLM_BIN --config $CONFIG wallet --port $PORT get-balance"
echo "Back up $WALLET_DIR/seedphrase.txt privately. This script never prints it."
