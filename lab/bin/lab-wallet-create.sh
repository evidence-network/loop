#!/usr/bin/env bash
# lab-wallet-create.sh N [SATS]: create lab-N's Lexe mainnet wallet (N = 1, 2 or 3) and, if SATS is
# above 0 (default 1000), print one funding invoice for SATS, without
# touching the running lab-N node. Part A of the lab wallet setup; lab-wallet-setup.sh is part B.
#
# Payment state lives next to the config file: <config dir>/payments/ (mesh-llm host
# config_state.rs payment_directory; the wallet CLI's expected_directory). This script starts a
# temporary `mesh-llm client` with a throwaway HOME and its own ports, whose config file sits in
# lab-N's directory, so its payment directory is exactly lab-N's:
#   $LAB_DIR/nodes/lab-N/payments/          ledger, policy and wallet-provider.json pin
#   $LAB_DIR/nodes/lab-N/payments/wallets/lexe-wallet/   seedphrase.txt, wallet.lock, wallet data
# Until lab.sh start writes [payments] for it (LAB_PAYMENTS), the running lab-N does not load a wallet plugin,
# so it never opens that directory.
# But once payments/payments.sqlite3 exists, the running lab-N opens the ledger to advertise prices and holds
# payments/service.lock (mesh-llm host node_ext.rs advertised_prices_source), so this script works only before lab-N
# has done that; on a second run, use lab-N's own console once lab.sh start has given it [payments].
#
# Steps: install the staged lexe-wallet build (lexe-wallet-plugin#7) into the throwaway HOME, start
# the temporary instance, open the wallet (the plugin creates the seed and signs up a Lexe account
# on the first open), set an automatic policy, print a fund-wallet invoice and wait for Enter while
# you pay it (skipped for SATS 0), show the balance, then stop the instance, check wallet.lock is free and remove the
# temporary config and HOME. It never reads, prints or copies the seed.
#
# Environment (defaults are the lab as of 2026-10-31):
#   LAB_DIR       node root (/home/intangible/loop-lab)
#   MESH_LLM_BIN  lab build (/home/intangible/lab-1031/target/release/mesh-llm, e877a45df)
#   LEXE_PLUGIN   staged plugin (/home/intangible/lexe-9f830c1/lexe-wallet); LEXE_SHA256 its sha256
#   DAILY_SATS    daily budget (20)
set -euo pipefail

usage() { echo "usage: $0 N [SATS]   (N = 1, 2 or 3; SATS 0 prints no invoice; default 1000)" >&2; exit 2; }
[ $# -ge 1 ] && [ $# -le 2 ] || usage
N=$1
case "$N" in 1 | 2 | 3) ;; *) usage ;; esac
FUND_SATS=${2:-1000}
[[ $FUND_SATS =~ ^[0-9]+$ ]] || usage

LAB_DIR=${LAB_DIR:-/home/intangible/loop-lab}
MESH_LLM_BIN=${MESH_LLM_BIN:-/home/intangible/lab-1031/target/release/mesh-llm}
LEXE_PLUGIN=${LEXE_PLUGIN:-/home/intangible/lexe-9f830c1/lexe-wallet}
LEXE_SHA256=${LEXE_SHA256:-6c82bd9fc64cdd177023d9c99990c4e7e5d931e258ee01217b59286e0a404255}
DAILY_SATS=${DAILY_SATS:-20}

NODE=lab-$N
NODE_DIR=$LAB_DIR/nodes/$NODE
TMP_CONFIG=$NODE_DIR/wallet-create.toml
WALLET_DIR=$NODE_DIR/payments/wallets/lexe-wallet
API_PORT=$((9930 + N))
CONSOLE_PORT=$((9940 + N)) # not lab-N's 981N

die() { echo "lab-wallet-create: $*" >&2; exit 1; }
sha() { sha256sum "$1" | cut -d' ' -f1; }
w() { HOME=$TMP_HOME "$MESH_LLM_BIN" --config "$TMP_CONFIG" wallet --port "$CONSOLE_PORT" "$@"; }
port_free() { ! ss -Hltn "sport = :$1" 2> /dev/null | grep -q .; }

[ "$(id -un)" = intangible ] || die "run as intangible (sudo -u intangible)"
[ -d "$NODE_DIR" ] || die "$NODE_DIR not found"
[ -x "$MESH_LLM_BIN" ] || die "$MESH_LLM_BIN not found"
[ "$(sha "$LEXE_PLUGIN")" = "$LEXE_SHA256" ] || die "$LEXE_PLUGIN sha256 is not $LEXE_SHA256"
# Once lab.sh start has written [payments] for lab-N (LAB_PAYMENTS), lab-N itself owns the wallet; fund it
# through lab-N's console instead.
! grep -q '^\[payments\]' "$NODE_DIR/config.toml" || die "$NODE_DIR/config.toml already has [payments]: lab-N owns the wallet now; use its console :$((${LAB_PORT_BASE:-9800} + 10 + N))"
[ ! -e "$TMP_CONFIG" ] || die "$TMP_CONFIG exists: another run is active or crashed; check for a 'client --config $TMP_CONFIG' process, then remove it"
port_free "$API_PORT" && port_free "$CONSOLE_PORT" || die "port $API_PORT or $CONSOLE_PORT is in use"
if [ -e "$NODE_DIR/payments/service.lock" ]; then
  flock -n "$NODE_DIR/payments/service.lock" true || die "$NODE_DIR/payments/service.lock is held: the running $NODE opened its ledger after the first run, so a temporary instance cannot. Use $NODE's console :$((${LAB_PORT_BASE:-9800} + 10 + N)) once lab.sh start has given it [payments] (LAB_PAYMENTS)"
fi
if [ -e "$WALLET_DIR/wallet.lock" ]; then
  flock -n "$WALLET_DIR/wallet.lock" true || die "$WALLET_DIR/wallet.lock is held by another process"
fi

TMP_HOME=$(mktemp -d)
PID=
cleanup() {
  if [ -n "$PID" ] && kill -0 "$PID" 2> /dev/null; then
    kill -TERM "$PID"
    for _ in $(seq 60); do kill -0 "$PID" 2> /dev/null || break; sleep 1; done
  fi
  rm -f "$TMP_CONFIG"
  rm -rf "$TMP_HOME"
}
trap cleanup EXIT

# 1. The plugin, packed like the released lexe-wallet-v0.1.0 tarball, into the throwaway HOME.
mkdir "$TMP_HOME/pkg" "$TMP_HOME/pkg/lexe-wallet"
cp "$LEXE_PLUGIN" "$TMP_HOME/pkg/lexe-wallet/lexe-wallet"
printf 'name = "lexe-wallet"\nversion = "0.1.0"\n' > "$TMP_HOME/pkg/lexe-wallet/plugin.toml"
tar -C "$TMP_HOME/pkg" -czf "$TMP_HOME/pkg/lexe-wallet-9f830c1-x86_64-unknown-linux-gnu.tar.gz" lexe-wallet
HOME=$TMP_HOME MESH_LLM_NO_DEFAULT_PLUGINS=1 "$MESH_LLM_BIN" plugins install --name lexe-wallet \
  --version 0.1.0-9f830c1 --archive "$TMP_HOME/pkg/lexe-wallet-9f830c1-x86_64-unknown-linux-gnu.tar.gz"

# 2. The temporary instance: no mesh joined (no --join/--auto/--discover), no relay (as lab.sh's nodes),
# its own ports.
(umask 077 && printf '[payments]\nwallet = "lexe-wallet"\n' > "$TMP_CONFIG")
HOME=$TMP_HOME MESH_LLM_NO_DEFAULT_PLUGINS=1 "$MESH_LLM_BIN" client --config "$TMP_CONFIG" \
  --port "$API_PORT" --console "$CONSOLE_PORT" --headless --disable-iroh-relays --log-format json < /dev/null > "$TMP_HOME/client.log" 2>&1 &
PID=$!
for _ in $(seq 60); do
  curl -fsS "http://127.0.0.1:$CONSOLE_PORT/api/status" > /dev/null 2>&1 && break
  kill -0 "$PID" 2> /dev/null || { tail -20 "$TMP_HOME/client.log" >&2; die "temporary instance exited"; }
  sleep 2
done
curl -fsS "http://127.0.0.1:$CONSOLE_PORT/api/status" > /dev/null || die "temporary instance not up on :$CONSOLE_PORT"
echo "temporary instance: pid $PID, console :$CONSOLE_PORT, payments in $NODE_DIR/payments"

# 3. Open the wallet. The first open creates the seed and the Lexe account.
if [ -e "$WALLET_DIR/seedphrase.txt" ]; then
  echo "wallet: $WALLET_DIR already holds a seed; opening it"
else
  echo "wallet: none yet; the first open creates the seed and a Lexe mainnet account"
fi
w get-balance
[ -e "$WALLET_DIR/seedphrase.txt" ] || die "no seed file under $WALLET_DIR after get-balance"
[ "$(stat -c %a "$WALLET_DIR")" = 700 ] || die "$WALLET_DIR is not mode 700"
[ "$(stat -c %a "$WALLET_DIR/seedphrase.txt")" = 600 ] || die "seedphrase.txt is not mode 600"

# 4. Policy, stored in lab-N's ledger, so lab-N keeps it.
w policy --mode automatic --daily-budget-sats "$DAILY_SATS"

# 5. Invoice, unless SATS is 0. The instance stays up until you press Enter. An unpaid funding
# invoice only expires: the host creates it in the wallet and records nothing in the ledger
# (mesh-llm-payments control.rs, ControlCommand::Fund).
if [ "$FUND_SATS" -gt 0 ]; then
  echo
  echo "Pay this invoice of $FUND_SATS sats for $NODE from your own wallet:"
  w fund-wallet --amount-sats "$FUND_SATS"
  echo
  read -r -p "Press Enter after the payment has gone through (the instance stays up until then) " _ < /dev/tty || true
else
  echo "funding: none for $NODE (SATS 0); it receives sats through paid exchanges"
fi
w get-balance

# 6. Stop the instance and release the lock (cleanup also runs on any failure).
kill -TERM "$PID"
for _ in $(seq 60); do kill -0 "$PID" 2> /dev/null || break; sleep 1; done
kill -0 "$PID" 2> /dev/null && die "temporary instance (pid $PID) did not stop within 60s"
PID=
flock -n "$WALLET_DIR/wallet.lock" true || die "wallet.lock is still held after the instance stopped"
echo
echo "Done: $NODE's wallet is in $WALLET_DIR and wallet.lock is free. lab-N itself was not touched."
echo "Back up $WALLET_DIR/seedphrase.txt privately. This script never prints it."
