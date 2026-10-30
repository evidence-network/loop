#!/usr/bin/env bash
# lab/lab.sh: bring up the three lab nodes on one host, join them, run stage 1 once, and pack
# what goes back into this repository. LAB-BRINGUP.md is the runbook: run the commands in its
# order, from a clone of this repository on the lab host.
#
#   lab/lab.sh [--dry-run] check|tools|start|status|exchange|join|entries|loop|deal|settle|ask|pack|stop|all
#
#   check     confirm the host build and the plugin against versions.lock; writes $LAB_DIR/build.json
#             on every outcome, and exits 1 naming each failure
#   tools     install every stage-1 step's pinned tools (each steps/<NN>-<name>.setup.sh): runs every
#             setup, records each one's exit code in build.json, and exits 1 if any failed
#   start     lab-1 makes the mesh and serves the model; lab-2 and lab-3 join it. No relay.
#   status    each node: pid, ports, node id, and the plugin keys its console lists
#   exchange  lab-2 and lab-3 each ask lab-1 for one chat completion, so every node's book has a record
#   join      ./join.sh for each node, with its plugin key and a new controller key
#   entries   write nodes/<node_id>.json, population/live/population.json (ids, data_dir, book_dir) and
#             reporter/reporter.json here
#   loop      ./loop.sh --stage 1 --population live, asking from the reporter node's console
#             (and the reporter node itself from a second node's console)
#   deal      steps/05-deal on population/live, into the same findings/<date>-live/: it reads the
#             halves the nodes in LAB_SHARE_AT_COMPLETION pushed after `exchange`. Starts no exchange
#             and pays nothing
#   settle    steps/06-settle on population/live, into the same findings/<date>-live/: it lists each
#             node's recorded settlements and is NOT_CHECKED while the lab has no rail. Pays nothing
#   ask       steps/07-ask on population/live, into the same findings/<date>-live/, asking from the reporter
#             node's console (and the reporter node itself from a second node's). Pays nothing
#   pack      one tarball of what goes back: $LAB_DIR/out/loop-lab-<date>.tgz
#   stop      SIGTERM every node and wait for its shutdown checkpoint
#   all       check, tools, start, exchange, wait for the first checkpoints, join, entries, loop, pack
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
#   LAB_SHARE_AT_COMPLETION  the nodes whose plugin is told to push its record of each completed
#             exchange to the other side (share_record_at_completion = counterparty), e.g. "lab-1 lab-2".
#             A node not named keeps the plugin's default, which is on at the pinned plugin unless the
#             node's config sets it off. Read by start: restart a node to change it
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
SESSION=${LAB_TMUX_SESSION:-loop-lab}
# Split once, on any whitespace, and match against the same list the check used.
SHARE=()
read -r -d '' -a SHARE <<< "${LAB_SHARE_AT_COMPLETION:-}" || true
for n in "${SHARE[@]+"${SHARE[@]}"}"; do
  [[ " ${NODES[*]} " == *" $n "* ]] || die "LAB_SHARE_AT_COMPLETION names $n, not a lab node"
done
shares() { local n; for n in "${SHARE[@]+"${SHARE[@]}"}"; do [ "$n" = "$1" ] && return 0; done; return 1; }
UP_SECONDS=${LAB_UP_SECONDS:-5}
STARTED=()
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
  local dead
  until "$@" > /dev/null 2>&1; do
    if dead=$(dead_node); then report_dead "$dead"; echo "lab.sh: stopped waiting: $what" >&2; return 1; fi
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
is_sha() { [[ $1 =~ ^[0-9a-f]{40}$ ]]; }
# resolve_pin TREE PIN: the commit PIN names in TREE. A full SHA is itself; a tag is resolved
# to its commit, after fetching the tree's tags if the tag is not there yet.
resolve_pin() {
  local tree=$1 ref=$2
  if is_sha "$ref"; then echo "$ref"; return 0; fi
  git -C "$tree" rev-parse -q --verify "refs/tags/$ref^{commit}" 2> /dev/null && return 0
  GIT_TERMINAL_PROMPT=0 git -C "$tree" fetch -q --tags 2> /dev/null || true
  git -C "$tree" rev-parse -q --verify "refs/tags/$ref^{commit}" 2> /dev/null
}
# head_of TREE: its HEAD commit; fails for an unset path or a path that is not a git tree.
head_of() { [ -n "$1" ] && git -C "$1" rev-parse -q --verify 'HEAD^{commit}' 2> /dev/null; }
# record SECTION: merge one command's outcome (JSON on stdin) into $LAB_DIR/build.json.
record() { mkdir -p "$LAB_DIR" && python3 -B "$ROOT/lab/pack.py" record "$1" --lab-dir "$LAB_DIR"; }

cmd_check() {
  local mesh_pin pr_pin cap_pin
  mesh_pin=$(pin mesh-llm) pr_pin=$(pin mesh-llm-pr-2620) cap_pin=$(pin capsules)
  if [ "$DRY" = 1 ]; then
    need MESH_LLM_SRC; need MESH_LLM_BIN; need CAPSULES_SRC; need CAPSULES_BIN
    say git -C "$MESH_LLM_SRC" rev-parse HEAD
    say git -C "$MESH_LLM_SRC" merge-base --is-ancestor "$mesh_pin" HEAD
    [ -z "$pr_pin" ] || say git -C "$MESH_LLM_SRC" merge-base --is-ancestor "$pr_pin" HEAD
    say git -C "$MESH_LLM_SRC" status --porcelain --untracked-files=no
    say git -C "$CAPSULES_SRC" rev-parse HEAD
    if ! is_sha "$cap_pin"; then
      say git -C "$CAPSULES_SRC" rev-parse -q --verify "refs/tags/$cap_pin^{commit}"
      echo "  (git fetch --tags first if the tag is not in the tree; HEAD must be that commit)"
    fi
    say git -C "$CAPSULES_SRC" status --porcelain --untracked-files=no
    say sha256sum "$MESH_LLM_BIN" "$CAPSULES_BIN"
    say "$MESH_LLM_BIN" --version
    echo "  > $LAB_DIR/build.json (written on every outcome; exit 1 when a check fails)"
    return 0
  fi

  # Every outcome is recorded: a check that cannot run is a failure with its reason, never an abort.
  local fails=() v mesh_head="" cap_head="" cap_commit="" mesh_sha="" cap_sha="" version="" c rc name p
  for v in MESH_LLM_SRC MESH_LLM_BIN CAPSULES_SRC CAPSULES_BIN; do
    [ -n "${!v:-}" ] || fails+=("$v is not set")
  done
  [ -n "$mesh_pin" ] || fails+=("versions.lock has no mesh-llm pin")
  [ -n "$cap_pin" ] || fails+=("versions.lock has no capsules pin")

  if mesh_head=$(head_of "${MESH_LLM_SRC:-}"); then
    for name in mesh-llm mesh-llm-pr-2620; do
      p=$(pin "$name"); [ -n "$p" ] || continue
      if ! c=$(resolve_pin "$MESH_LLM_SRC" "$p"); then
        fails+=("the $name pin $p is not in the mesh-llm source"); continue
      fi
      rc=0; git -C "$MESH_LLM_SRC" merge-base --is-ancestor "$c" HEAD 2> /dev/null || rc=$?
      case $rc in
        0) ;;
        1) fails+=("mesh-llm source $mesh_head does not contain the $name pin $p") ;;
        *) fails+=("the $name pin $p is not in the mesh-llm source (git merge-base exited $rc)") ;;
      esac
    done
    [ -z "$(git -C "$MESH_LLM_SRC" status --porcelain --untracked-files=no)" ] || fails+=("mesh-llm source has uncommitted changes")
  else
    mesh_head=""; [ -z "${MESH_LLM_SRC:-}" ] || fails+=("mesh-llm source $MESH_LLM_SRC is not a git tree")
  fi

  if cap_head=$(head_of "${CAPSULES_SRC:-}"); then
    if [ -z "$cap_pin" ]; then :
    elif ! cap_commit=$(resolve_pin "$CAPSULES_SRC" "$cap_pin"); then
      cap_commit=""; fails+=("the capsules pin $cap_pin is not a tag in the capsules source, even after git fetch --tags")
    elif [ "$cap_head" != "$cap_commit" ]; then
      fails+=("capsules source is at $cap_head, the capsules pin $cap_pin is $cap_commit")
    fi
    [ -z "$(git -C "$CAPSULES_SRC" status --porcelain --untracked-files=no)" ] || fails+=("capsules source has uncommitted changes")
  else
    cap_head=""; [ -z "${CAPSULES_SRC:-}" ] || fails+=("capsules source $CAPSULES_SRC is not a git tree")
  fi

  if [ -n "${MESH_LLM_BIN:-}" ]; then
    if [ -f "$MESH_LLM_BIN" ] && [ -r "$MESH_LLM_BIN" ]; then
      mesh_sha=$(sha256sum < "$MESH_LLM_BIN" | cut -d' ' -f1)
      rc=0; version=$("$MESH_LLM_BIN" --version 2>&1) || rc=$?
      version=${version%%$'\n'*}
      [ "$rc" = 0 ] || fails+=("$MESH_LLM_BIN --version exited $rc")
    else
      fails+=("MESH_LLM_BIN $MESH_LLM_BIN is not a readable file")
    fi
  fi
  if [ -n "${CAPSULES_BIN:-}" ]; then
    if [ -f "$CAPSULES_BIN" ] && [ -r "$CAPSULES_BIN" ]; then
      cap_sha=$(sha256sum < "$CAPSULES_BIN" | cut -d' ' -f1)
    else
      fails+=("CAPSULES_BIN $CAPSULES_BIN is not a readable file")
    fi
  fi

  python3 -B - "$mesh_head" "$mesh_pin" "$pr_pin" "$cap_head" "$cap_pin" "$cap_commit" "$mesh_sha" "$cap_sha" \
    "$version" "${fails[@]+"${fails[@]}"}" << 'PY' | record check || die "cannot write $LAB_DIR/build.json"
import datetime as dt, json, sys
mesh_head, mesh_pin, pr_pin, cap_head, cap_pin, cap_commit, mesh_sha, cap_sha, version, *fails = sys.argv[1:]
print(json.dumps({
    "failures": fails,
    "checked_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "mesh_llm": {"source_head": mesh_head or None, "contains": {"mesh-llm": mesh_pin or None, "mesh-llm-pr-2620": pr_pin or None},
                 "binary_sha256": mesh_sha or None, "version": version or None},
    "capsules": {"source_head": cap_head or None, "pin": cap_pin or None, "pin_commit": cap_commit or None,
                 "binary_sha256": cap_sha or None},
    "relay": "off (--disable-iroh-relays) until Mesh-LLM/capsules-plugin#48 is closed",
}))
PY
  cat "$LAB_DIR/build.json"
  local all
  all=$(python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin).get("failures", [])))' < "$LAB_DIR/build.json")
  if [ -n "$all" ]; then
    printf 'lab.sh: NOT the pinned build:\n%s\n' "$all" >&2
    exit 1
  fi
  echo "check: the build matches versions.lock"
}

# --- tools ------------------------------------------------------------------------------------
# Each setup runs, pass or fail; the failures (setup and exit code) go into build.json, which
# then reads ok:false until a later `tools` run installs every step's tools.
cmd_tools() {
  local s rc results=() failed=0
  for s in "$ROOT"/steps/[0-9][0-9]-*.setup.sh; do
    [ -e "$s" ] || continue
    if [ "$DRY" = 1 ]; then say "$s"; continue; fi
    echo "== tools: $(basename "$s")"
    rc=0; "$s" || rc=$?
    [ "$rc" = 0 ] || { failed=$((failed + 1)); echo "lab.sh: tools: $(basename "$s") FAILED (exit $rc)" >&2; }
    results+=("$(basename "$s")" "$rc")
  done
  if [ "$DRY" = 1 ]; then echo "  > $LAB_DIR/build.json (each setup's exit code; a failed setup makes it ok:false)"; return 0; fi
  python3 -B - "${results[@]+"${results[@]}"}" << 'PY' | record tools || die "cannot write $LAB_DIR/build.json"
import datetime as dt, json, sys
args = sys.argv[1:]
setups = [{"name": n, "exit": int(rc)} for n, rc in zip(args[::2], args[1::2])]
print(json.dumps({
    "checked_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "setups": setups,
    "failures": [f"tools: steps/{s['name']} exited {s['exit']}" for s in setups if s["exit"]],
}))
PY
  if [ "$failed" -gt 0 ]; then
    echo "lab.sh: tools: $failed of $((${#results[@]} / 2)) setups failed; build.json reads ok:false until they pass" >&2
    exit 1
  fi
  echo "tools: every step's setup passed"
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
  # The plugin reads share_record_at_completion from this env var before its config
  # (capsules-plugin share_policy.rs), so a named node pushes whatever its config says.
  shares "$name" && envs+=(CAPSULES_SHARE_RECORD_AT_COMPLETION=counterparty)
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
  # The grant lets mesh-llm ask the plugin's admission hook at backend_selected (capsules-plugin INSTALL.md,
  # "Admit or refuse peers"); without it the plugin is never asked and seals no admission_decision (step 04).
  config=$(printf 'version = 1\n\n[[plugin]]\nname = "capsules"\nenabled = true\ncommand = "%s"\nargs = []\n' "$CAPSULES_BIN"
    printf '\n[plugin.openai_exchange_grant]\nendpoints = ["chat_completions", "completions", "responses"]\n'
    printf 'phases = ["backend_selected"]\nadmission = true\ndeadline_ms = 250\nmax_body_bytes = 1048576\n'
    printf 'max_queue_bytes = 4194304\nmax_in_flight = 32\nfailure_policy = "required"\n')
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
  run rm -f "$(pidfile "$name")"
  # tmux runs the launcher through bash, never by its bare path, with bash's own stderr in the
  # node's log: a bare path dies before its first line where the file cannot be executed (a
  # noexec mount, a lost mode bit), and the window closes with an empty log and nothing said.
  local cmd
  cmd=$(printf 'bash %q >> %q 2>&1' "$LAB_DIR/run/$name.sh" "$(logfile "$name")")
  if [ "$DRY" = 1 ] || tmux has-session -t "$SESSION" 2> /dev/null; then
    run tmux new-window -d -t "$SESSION" -n "$name" "$cmd"
  else
    run tmux new-session -d -s "$SESSION" -n "$name" "$cmd"
  fi
  STARTED+=("$name")
  still_up "$name" || die "$name exited at once"
  echo "started $name: api $(api_port "$name") console $(console_port "$name") quic $(bind_port "$name")"
}

# A node that exits within a few seconds of its launch is reported then, with its log tail, not
# when a wait_for runs out; wait_for calls dead_node too, so a node that dies later stops the wait.
still_up() { # NAME
  [ "$DRY" = 1 ] && { echo "  (within ${UP_SECONDS}s: $1 is still running, or stop with its log tail)"; return 0; }
  local _
  for _ in $(seq "$UP_SECONDS"); do
    sleep 1
    [ -s "$(pidfile "$1")" ] && ! alive "$1" && break
  done
  alive "$1" && return 0
  report_dead "$1"; return 1
}
report_dead() { # NAME
  echo "lab.sh: $1 is not running; the last lines of $(logfile "$1"):" >&2
  tail -n 20 "$(logfile "$1")" 2> /dev/null | sed 's/^/  | /' >&2 || true
}
dead_node() { # prints the first started node that is not running
  local n
  for n in "${STARTED[@]}"; do alive "$n" || { echo "$n"; return 0; }; done
  return 1
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
  tmux kill-session -t "$SESSION" 2> /dev/null || true
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

# The reporter's console, never the default 3131: other nodes on the lab host may answer there.
# A node can't be asked through its own console, so steps 12 and 07 ask the reporter node through a
# second node's console: lab-1's, or lab-2's when lab-1 is the reporter.
# LAB_DIR: population/live names each node's data_dir and book_dir from it.
# LOOP_SELF_NODE is the reporter's name in population/live, its operator's label (lab/pack.py
# host_name maps a label back to its lab-N directory the same way).
ask_env() {
  second=lab-1
  [ "$REPORTER" = lab-1 ] && second=lab-2
  self=$(python3 -c 'import json, sys
for n in json.load(open(sys.argv[1]))["nodes"]:
    if f"/nodes/{sys.argv[2]}/" in (n.get("data_dir") or "") or n["name"] == sys.argv[2]:
        print(n["name"]); break' "$ROOT/population/live/population.json" "$REPORTER")
  [ -n "$self" ] || die "the reporter $REPORTER is not a node in population/live (run lab/lab.sh entries)"
  # Each node's own console, for step 12 to read that node's doctor: NAME=URL per population node, by the
  # lab-N directory its data_dir names (the name is the operator's label).
  node_consoles=$(python3 -c 'import json, re, sys
base, pop = int(sys.argv[1]), sys.argv[2]
out = []
for n in json.load(open(pop))["nodes"]:
    m = re.search(r"/nodes/lab-([0-9])/", n.get("data_dir") or "")
    if m:
        out.append(n["name"] + "=http://127.0.0.1:" + str(base + 10 + int(m.group(1))))
print(",".join(out))' "$BASE" "$ROOT/population/live/population.json")
}

cmd_loop() {
  local second self node_consoles
  ask_env
  run env LOOP_CONSOLE="$(console "$REPORTER")" LOOP_SELF_NODE="$self" LOOP_SECOND_CONSOLE="$(console "$second")" \
    LAB_DIR="$LAB_DIR" LOOP_NODE_CONSOLES="$node_consoles" "$ROOT/loop.sh" --stage 1 --population live \
    --out "$ROOT/findings/$TODAY-live"
  [ "$DRY" = 1 ] || echo "$TODAY" > "$LAB_DIR/run/loop-date"
}

cmd_deal() {
  # Step 05 is not in loop.sh's stage 1, so it runs on its own, into the day's live findings
  # beside stage 1's, and the finding contract is checked over the whole directory.
  local out=$ROOT/findings/$TODAY-live
  run env LAB_DIR="$LAB_DIR" "$ROOT/steps/05-deal" --population "$ROOT/population/live" --out "$out/05-deal"
  run python3 -B "$ROOT/steps/finding.py" check "$out"
  [ "$DRY" = 1 ] || echo "$TODAY" > "$LAB_DIR/run/loop-date"
}

cmd_settle() {
  local out=$ROOT/findings/$TODAY-live
  run env LAB_DIR="$LAB_DIR" "$ROOT/steps/06-settle" --population "$ROOT/population/live" --out "$out/06-settle"
  run python3 -B "$ROOT/steps/finding.py" check "$out"
  [ "$DRY" = 1 ] || echo "$TODAY" > "$LAB_DIR/run/loop-date"
}

cmd_ask() {
  # Step 07 is not in loop.sh's stage 1 either. It asks as step 12 does (the reporter's console, and a second
  # node's console for the reporter's own node), so it takes the same environment as cmd_loop.
  local out=$ROOT/findings/$TODAY-live second self node_consoles
  ask_env
  run env LOOP_CONSOLE="$(console "$REPORTER")" LOOP_SELF_NODE="$self" LOOP_SECOND_CONSOLE="$(console "$second")" \
    LAB_DIR="$LAB_DIR" "$ROOT/steps/07-ask" --population "$ROOT/population/live" --out "$out/07-ask"
  run python3 -B "$ROOT/steps/finding.py" check "$out"
  [ "$DRY" = 1 ] || echo "$TODAY" > "$LAB_DIR/run/loop-date"
}

cmd_pack() {
  local day=$TODAY
  [ -s "$LAB_DIR/run/loop-date" ] && day=$(cat "$LAB_DIR/run/loop-date")
  local dry=(); [ "$DRY" = 1 ] && dry=(--dry-run)
  python3 -B "$ROOT/lab/pack.py" pack --lab-dir "$LAB_DIR" --date "$day" "${dry[@]+"${dry[@]}"}"
}

cmd_all() {
  cmd_check; cmd_tools; cmd_start; cmd_exchange
  local wait=${LAB_CHECKPOINT_WAIT:-360}
  if [ "$DRY" = 1 ]; then echo "  (wait ${wait}s: each node cuts its first checkpoint 300 s after activity)"; else sleep "$wait"; fi
  cmd_join; cmd_entries; cmd_loop; cmd_pack
}

case "$cmd" in
  check | tools | start | status | exchange | join | entries | loop | deal | settle | ask | pack | stop | all) "cmd_$cmd" ;;
  *) sed -n '6p' "$0" | sed 's/^#   //' >&2; exit 2 ;;
esac
