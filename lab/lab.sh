#!/usr/bin/env bash
# lab/lab.sh: bring up the three lab nodes on one host, join them, run stage 1 once, and pack
# what goes back into this repository. LAB-BRINGUP.md is the runbook: run the commands in its
# order, from a clone of this repository on the lab host.
#
#   lab/lab.sh [--dry-run] check|tools|start|status|exchange|join|entries|loop|pack|stop|all
#
#   check     confirm the host build and the plugin against versions.lock; writes $LAB_DIR/build.json
#   tools     install every stage-1 step's pinned tools (each steps/<NN>-<name>.setup.sh)
#   start     lab-1 makes the mesh and serves the model; lab-2 and lab-3 join it. No relay.
#   status    each node: pid, ports, node id, and the plugin keys its console lists
#   exchange  lab-2 and lab-3 each ask lab-1 for one chat completion, so every node's book has a record
#   join      ./join.sh for each node, with its plugin key and a new controller key
#   entries   write nodes/<node_id>.json, population/live/population.json and reporter/reporter.json here
#   loop      ./loop.sh --stage 1 --population live, asking from the reporter node's console
#   pack      one tarball of what goes back: $LAB_DIR/out/loop-lab-<date>.tgz
#   stop      SIGTERM every node and wait for its shutdown checkpoint
#   all       check, start, exchange, wait for the first checkpoints, join, entries, loop, pack
#
# --dry-run prints every command it would run (and, for pack, every file it would take) and
# changes nothing.
#
# Environment (LAB-BRINGUP.md, "Prerequisites"):
#   LAB_DIR             where the nodes live: one HOME per node under $LAB_DIR/nodes (required)
#   MESH_LLM_BIN        the lab host build's mesh-llm binary
#   MESH_LLM_SRC        the mesh-llm source tree that binary was built from (check)
#   MESH_LLM_RUNTIME    its native runtimes directory (optional)
#   CAPSULES_BIN        the capsules plugin binary
#   CAPSULES_SRC        the plugin source tree that binary was built from (check)
#   LAB_MODEL           a GGUF model file for lab-1 to serve (start)
#   LAB_MODEL_NAME      the name it is served under (default lab-model)
#   LAB_OPERATOR        the GitHub handle of whoever runs the nodes (entries)
#   LAB_PORT_BASE       default 9800: API base+1..3, console base+11..13, QUIC base+21..23
#   LAB_REPORTER        the reporter node (default lab-3)
#   LAB_CHECKPOINT_WAIT seconds `all` waits after the exchange for each first checkpoint (default 360)
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
NODES=(lab-1 lab-2 lab-3)
DRY=0
if [ "${1:-}" = --dry-run ]; then DRY=1; shift; fi
cmd=${1:-}

die() { echo "lab.sh: $*" >&2; exit 2; }
[ -n "${LAB_DIR:-}" ] || die "set LAB_DIR (see LAB-BRINGUP.md)"
LAB_DIR=${LAB_DIR%/}
BASE=${LAB_PORT_BASE:-9800}
REPORTER=${LAB_REPORTER:-lab-3}
MODEL_NAME=${LAB_MODEL_NAME:-lab-model}
TODAY=$(date -u +%F)

# In a dry run an unset path reads as its own name, so the plan still prints.
need() { local v=$1; [ -n "${!v:-}" ] || { [ "$DRY" = 1 ] && printf -v "$v" '<%s>' "$v" && return 0; die "set $v"; }; }

say() { printf '+'; printf ' %q' "$@"; printf '\n'; }
run() { if [ "$DRY" = 1 ]; then say "$@"; else "$@"; fi; }

idx() { case "$1" in lab-1) echo 1 ;; lab-2) echo 2 ;; lab-3) echo 3 ;; *) die "unknown node $1" ;; esac; }
api_port() { echo $((BASE + $(idx "$1"))); }
console_port() { echo $((BASE + 10 + $(idx "$1"))); }
bind_port() { echo $((BASE + 20 + $(idx "$1"))); }
console() { echo "http://127.0.0.1:$(console_port "$1")"; }
home_of() { echo "$LAB_DIR/nodes/$1"; }
data_of() { echo "$LAB_DIR/nodes/$1/.local/share/capsules"; }
pidfile() { echo "$LAB_DIR/run/$1.pid"; }
logfile() { echo "$LAB_DIR/logs/$1.log"; }
token_file() { echo "$LAB_DIR/run/invite.token"; }
pid_of() { local f; f=$(pidfile "$1"); [ -s "$f" ] && cat "$f"; }
alive() { local p; p=$(pid_of "$1") && kill -0 "$p" 2> /dev/null; }
# The node id the plugin learned from its host: the same file reporter_key.py reads.
node_id() {
  if [ "$DRY" = 1 ]; then echo "<$1 node_id>"; return 0; fi
  local f; f=$(data_of "$1")/self-peer-id
  [ -s "$f" ] || { echo "lab.sh: $1 has no $f yet" >&2; return 1; }
  tr -d '[:space:]' < "$f"
}
json_field() { python3 -c 'import json,sys; v=json.load(sys.stdin).get(sys.argv[1]); print("" if v is None else v)' "$1"; }

# wait_for SECONDS WHAT CMD...: retry CMD until it succeeds.
wait_for() {
  local seconds=$1 what=$2; shift 2
  if [ "$DRY" = 1 ]; then echo "  (wait up to ${seconds}s: $what)"; return 0; fi
  local deadline=$((SECONDS + seconds))
  until "$@" > /dev/null 2>&1; do
    [ "$SECONDS" -ge "$deadline" ] && { echo "lab.sh: timed out after ${seconds}s: $what" >&2; return 1; }
    sleep 3
  done
  echo "ready: $what"
}

model_ready() { curl -fsS "$(console lab-1)/api/status" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("llama_ready") is True else 1)'; }
routes_model() {
  curl -fsS "http://127.0.0.1:$(api_port "$1")/v1/models" \
    | python3 -c 'import json,sys; sys.exit(0 if any(m.get("id") == sys.argv[1] for m in json.load(sys.stdin).get("data", [])) else 1)' "$MODEL_NAME"
}
knows_own_id() { [ -s "$(data_of "$1")/self-peer-id" ]; }
port_free() { ! ss -Hltn "sport = :$1" 2> /dev/null | grep -q .; }

# --- check ------------------------------------------------------------------------------------
pin() { awk -v n="$1" '$1 == n { print $3 }' "$ROOT/versions.lock"; }
cmd_check() {
  need MESH_LLM_SRC; need MESH_LLM_BIN; need CAPSULES_SRC; need CAPSULES_BIN
  local mesh_pin pr_pin cap_pin
  mesh_pin=$(pin mesh-llm) pr_pin=$(pin mesh-llm-pr-2620) cap_pin=$(pin capsules)
  [ -n "$mesh_pin" ] && [ -n "$cap_pin" ] || die "versions.lock has no mesh-llm or capsules pin"
  run git -C "$MESH_LLM_SRC" rev-parse HEAD
  run git -C "$MESH_LLM_SRC" merge-base --is-ancestor "$mesh_pin" HEAD
  [ -z "$pr_pin" ] || run git -C "$MESH_LLM_SRC" merge-base --is-ancestor "$pr_pin" HEAD
  run git -C "$MESH_LLM_SRC" status --porcelain --untracked-files=no
  run git -C "$CAPSULES_SRC" rev-parse HEAD
  run git -C "$CAPSULES_SRC" status --porcelain --untracked-files=no
  run sha256sum "$MESH_LLM_BIN" "$CAPSULES_BIN"
  run "$MESH_LLM_BIN" --version
  [ "$DRY" = 1 ] && { echo "  > $LAB_DIR/build.json"; return 0; }

  local fails=() mesh_head cap_head
  mesh_head=$(git -C "$MESH_LLM_SRC" rev-parse HEAD)
  cap_head=$(git -C "$CAPSULES_SRC" rev-parse HEAD)
  git -C "$MESH_LLM_SRC" merge-base --is-ancestor "$mesh_pin" HEAD || fails+=("mesh-llm source $mesh_head does not contain the mesh-llm pin $mesh_pin")
  if [ -n "$pr_pin" ]; then
    git -C "$MESH_LLM_SRC" merge-base --is-ancestor "$pr_pin" HEAD || fails+=("mesh-llm source $mesh_head does not contain the mesh-llm-pr-2620 pin $pr_pin")
  fi
  [ -z "$(git -C "$MESH_LLM_SRC" status --porcelain --untracked-files=no)" ] || fails+=("mesh-llm source has uncommitted changes")
  [ "$cap_head" = "$cap_pin" ] || fails+=("capsules source is at $cap_head, the capsules pin is $cap_pin")
  [ -z "$(git -C "$CAPSULES_SRC" status --porcelain --untracked-files=no)" ] || fails+=("capsules source has uncommitted changes")
  mkdir -p "$LAB_DIR"
  python3 -B - "$LAB_DIR/build.json" "$mesh_head" "$mesh_pin" "$pr_pin" "$cap_head" \
    "$(sha256sum < "$MESH_LLM_BIN" | cut -d' ' -f1)" "$(sha256sum < "$CAPSULES_BIN" | cut -d' ' -f1)" \
    "$("$MESH_LLM_BIN" --version 2>&1 | head -1)" "${fails[@]+"${fails[@]}"}" << 'PY'
import datetime as dt, json, sys
path, mesh_head, mesh_pin, pr_pin, cap_head, mesh_sha, cap_sha, version, *fails = sys.argv[1:]
build = {
    "ok": not fails,
    "failures": fails,
    "checked_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "mesh_llm": {"source_head": mesh_head, "contains": {"mesh-llm": mesh_pin, "mesh-llm-pr-2620": pr_pin or None},
                 "binary_sha256": mesh_sha, "version": version},
    "capsules": {"source_head": cap_head, "binary_sha256": cap_sha},
    "relay": "off (--disable-iroh-relays) until Mesh-LLM/capsules-plugin#48 is closed",
}
json.dump(build, open(path, "w"), indent=2, sort_keys=True)
PY
  cat "$LAB_DIR/build.json"
  if [ ${#fails[@]} -gt 0 ]; then
    printf 'lab.sh: NOT the pinned build: %s\n' "${fails[@]}" >&2
    exit 1
  fi
  echo "check: the build matches versions.lock"
}

# --- tools ------------------------------------------------------------------------------------
cmd_tools() {
  local s
  for s in "$ROOT"/steps/[0-9][0-9]-*.setup.sh; do run "$s"; done
}

# --- start / stop / status --------------------------------------------------------------------
write_launcher() { # NAME EXTRA_ARGS...: the script tmux runs for one node
  local name=$1; shift
  local home launcher
  home=$(home_of "$name") launcher=$LAB_DIR/run/$name.sh
  local -a envs=(HOME="$home" XDG_DATA_HOME="$home/.local/share"
    MESH_LLM_NODE_KEY_PATH="$home/.mesh-llm/key" MESH_LLM_RUNTIME_ROOT="$home/.mesh-llm/runtime"
    MESH_LLM_NATIVE_RUNTIME_CACHE_DIR="$home/.mesh-llm/runtime-cache"
    CAPSULES_HOST_CONSOLE_URL="$(console "$name")")
  [ -n "${MESH_LLM_RUNTIME:-}" ] && envs+=(MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR="$MESH_LLM_RUNTIME")
  local -a args=(serve --config "$home/config.toml" --port "$(api_port "$name")" --console "$(console_port "$name")"
    --bind-port "$(bind_port "$name")" --headless --disable-iroh-relays --no-default-plugins --log-format json "$@")
  local body
  body=$(
    echo '#!/usr/bin/env bash'
    echo "# $name: started by lab/lab.sh. No relay, never --publish."
    printf 'echo $$ > %q\n' "$(pidfile "$name")"
    printf 'exec env -u CAPSULES_PEER_KEYS'
    printf ' %q' "${envs[@]}" "$MESH_LLM_BIN" "${args[@]}"
    printf ' < /dev/null >> %q 2>&1\n' "$(logfile "$name")"
  )
  local config
  config=$(printf 'version = 1\n\n[[plugin]]\nname = "capsules"\nenabled = true\ncommand = "%s"\nargs = []\n' "$CAPSULES_BIN")
  if [ "$DRY" = 1 ]; then
    echo "  > $home/config.toml:"; sed 's/^/    /' <<< "$config"
    echo "  > $launcher:"; sed 's/^/    /' <<< "$body"
  else
    mkdir -p "$home" && chmod 700 "$home"
    printf '%s\n' "$config" > "$home/config.toml"
    printf '%s\n' "$body" > "$launcher" && chmod 700 "$launcher"
  fi
}

start_node() { # NAME EXTRA_ARGS...
  local name=$1
  if [ "$DRY" = 0 ] && alive "$name"; then echo "$name already running (pid $(pid_of "$name"))"; return 0; fi
  local p
  for p in "$(api_port "$name")" "$(console_port "$name")" "$(bind_port "$name")"; do
    [ "$DRY" = 1 ] || port_free "$p" || die "port $p is in use: pick another LAB_PORT_BASE"
  done
  write_launcher "$@"
  if [ "$DRY" = 1 ] || tmux has-session -t loop-lab 2> /dev/null; then
    run tmux new-window -d -t loop-lab -n "$name" "$LAB_DIR/run/$name.sh"
  else
    run tmux new-session -d -s loop-lab -n "$name" "$LAB_DIR/run/$name.sh"
  fi
  echo "started $name: api $(api_port "$name") console $(console_port "$name") quic $(bind_port "$name")"
}

cmd_start() {
  need MESH_LLM_BIN; need CAPSULES_BIN; need LAB_MODEL
  [ "$DRY" = 1 ] || [ -f "$LAB_DIR/build.json" ] || die "run lab/lab.sh check first"
  run mkdir -p "$LAB_DIR/run" "$LAB_DIR/logs"
  run chmod 700 "$LAB_DIR/run"
  start_node lab-1 --gguf "$LAB_MODEL" --model "$MODEL_NAME"
  wait_for 600 "lab-1 has loaded the model" model_ready
  # The invite token goes from lab-1's status into a 0600 file: never a screen or an argument.
  if [ "$DRY" = 1 ]; then
    say curl -fsS "$(console lab-1)/api/status"; echo "  .token > $(token_file) (0600)"
  else
    (umask 077; curl -fsS "$(console lab-1)/api/status" | json_field token > "$(token_file).tmp")
    [ -s "$(token_file).tmp" ] || { rm -f "$(token_file).tmp"; die "lab-1 reported no invite token"; }
    mv "$(token_file).tmp" "$(token_file)"
  fi
  local n
  for n in lab-2 lab-3; do
    start_node "$n" --join-file "$(token_file)"
    wait_for 300 "$n can route to lab-1's model" routes_model "$n"
  done
  for n in "${NODES[@]}"; do
    if [ "$DRY" = 1 ]; then say curl -fsS "$(console "$n")/api/status"; echo "  publication_state must be private"; continue; fi
    local state
    state=$(curl -fsS "$(console "$n")/api/status" | json_field publication_state)
    if [ "$state" != private ]; then
      echo "lab.sh: $n reports publication_state=${state:-absent}, not private; stopping every node" >&2
      cmd_stop; exit 4
    fi
  done
  for n in "${NODES[@]}"; do wait_for 120 "$n's plugin knows its node id" knows_own_id "$n"; done
  cmd_status
}

cmd_stop() {
  local n p
  for n in "${NODES[@]}"; do
    if [ "$DRY" = 1 ]; then say kill -TERM "<pid in $(pidfile "$n")>"; continue; fi
    p=$(pid_of "$n") || continue
    kill -TERM "$p" 2> /dev/null || true
  done
  [ "$DRY" = 1 ] && { echo "  (wait up to 30s for the shutdown checkpoints, then kill -KILL what is left)"; return 0; }
  local _ any
  for _ in $(seq 60); do
    any=0
    for n in "${NODES[@]}"; do alive "$n" && any=1; done
    [ "$any" = 0 ] && break
    sleep 0.5
  done
  for n in "${NODES[@]}"; do
    if alive "$n"; then kill -KILL "$(pid_of "$n")" 2> /dev/null || true; fi
    rm -f "$(pidfile "$n")"
  done
  tmux kill-session -t loop-lab 2> /dev/null || true
  echo "stopped ${NODES[*]}"
}

cmd_status() {
  local n
  for n in "${NODES[@]}"; do
    if [ "$DRY" = 1 ]; then
      say curl -fsS "$(console "$n")/api/plugin-keys"; continue
    fi
    if alive "$n"; then
      echo "$n: up pid $(pid_of "$n") api $(api_port "$n") console $(console_port "$n") quic $(bind_port "$n")"
      echo "  node_id: $(node_id "$n" 2> /dev/null || echo '<not learned yet>')"
      echo "  plugin keys: $(curl -fsS "$(console "$n")/api/plugin-keys" 2> /dev/null | head -c 600 || echo '<no answer>')"
    else
      echo "$n: down"
    fi
  done
}

# --- exchange ---------------------------------------------------------------------------------
cmd_exchange() {
  local n target body
  target=$(node_id lab-1)
  body=$(printf '{"model":"%s","messages":[{"role":"user","content":"Reply with the word lab and nothing else."}],"max_tokens":8,"temperature":0}' "$MODEL_NAME")
  run mkdir -p "$LAB_DIR/exchange"
  for n in lab-2 lab-3; do
    run curl -fsS --max-time 120 -o "$LAB_DIR/exchange/$n.json" -D "$LAB_DIR/exchange/$n.headers.txt" \
      -H 'content-type: application/json' -H "x-mesh-target: $target" -d "$body" \
      "http://127.0.0.1:$(api_port "$n")/v1/chat/completions"
    [ "$DRY" = 1 ] || echo "$n -> lab-1: served by $(tr -d '\r' < "$LAB_DIR/exchange/$n.headers.txt" | awk -F': ' 'tolower($1) == "x-mesh-served-by" { print $2 }')"
  done
}

# --- join -------------------------------------------------------------------------------------
cmd_join() {
  local n key ctl
  for n in "${NODES[@]}"; do
    key=$(data_of "$n")/keys/node-key.pem
    ctl=$LAB_DIR/controllers/$n/controller-key.pem
    [ "$DRY" = 1 ] || [ -f "$key" ] || die "$n has no plugin key at $key yet: is it started?"
    run mkdir -p "$LAB_DIR/controllers/$n"
    run chmod 700 "$LAB_DIR/controllers" "$LAB_DIR/controllers/$n"
    if [ "$DRY" = 1 ] || [ ! -f "$ctl" ]; then
      run openssl genpkey -algorithm ed25519 -out "$ctl"
      run chmod 600 "$ctl"
    fi
    run "$ROOT/join.sh" --name "$n" --node-key "$key" --controller-key "$ctl" \
      --out "$LAB_DIR/join/$n" --home "$(home_of "$n")/join-book"
    # join.sh copied the plugin key's seed into its book profile; the join is done with it, and
    # the plugin's data dir keeps the only copy of the node key.
    run rm -f "$(home_of "$n")/join-book/node.seed"
  done
}

# --- entries / loop / pack --------------------------------------------------------------------
cmd_entries() {
  need LAB_OPERATOR
  local dry=(); [ "$DRY" = 1 ] && dry=(--dry-run)
  python3 -B "$ROOT/lab/pack.py" entries --lab-dir "$LAB_DIR" --operator "$LAB_OPERATOR" \
    --reporter "$REPORTER" "${dry[@]+"${dry[@]}"}"
  run python3 -B "$ROOT/scripts/check_nodes.py" "$ROOT/nodes"
}

cmd_loop() {
  # The reporter's console, never the default 3131: other nodes on the lab host may answer there.
  run env LOOP_CONSOLE="$(console "$REPORTER")" "$ROOT/loop.sh" --stage 1 --population live \
    --out "$ROOT/findings/$TODAY-live"
  [ "$DRY" = 1 ] || echo "$TODAY" > "$LAB_DIR/run/loop-date"
}

cmd_pack() {
  local day=$TODAY
  [ -s "$LAB_DIR/run/loop-date" ] && day=$(cat "$LAB_DIR/run/loop-date")
  local dry=(); [ "$DRY" = 1 ] && dry=(--dry-run)
  python3 -B "$ROOT/lab/pack.py" pack --lab-dir "$LAB_DIR" --date "$day" "${dry[@]+"${dry[@]}"}"
}

cmd_all() {
  cmd_check; cmd_start; cmd_exchange
  local wait=${LAB_CHECKPOINT_WAIT:-360}
  if [ "$DRY" = 1 ]; then echo "  (wait ${wait}s: each node cuts its first checkpoint 300 s after activity)"; else sleep "$wait"; fi
  cmd_join; cmd_entries; cmd_loop; cmd_pack
}

case "$cmd" in
  check | tools | start | status | exchange | join | entries | loop | pack | stop | all) "cmd_$cmd" ;;
  *) sed -n '6p' "$0" | sed 's/^#   //' >&2; exit 2 ;;
esac
