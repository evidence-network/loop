#!/usr/bin/env bash
# lab/goose.sh: the Goose lab driver (evidence-network/loop#349, under loop#315). It runs two of evidencebook's Goose
# nodes (G1, python/examples/goose at the goose-g1 pin in versions.lock) on the loop lab host, beside lab-1..4 and
# independent of them, so the loop's steps can judge a Goose ecosystem live. Run it on the lab host as the lab
# operator, from a clone of this repository, in this order:
#
#   lab/goose.sh [--dry-run] tools|init|entries|commitments|session|run
#
#   tools        install the Goose node (steps/goose.setup.sh) into $LOOP_TOOLS/goose
#   init         start each node once on its own persistent home, $LAB_DIR/goose/homes/<name> (mode 700), so it has
#                its keys; write its public key under $LAB_DIR/goose/keys/<name>/ and the two ids to
#                $LAB_DIR/goose/ids.json. Keys already there are kept: a node's producer key is its identity, the
#                key nodes/<node_id>.json lists. Private keys stay in the homes and are never printed or copied
#   entries      write nodes/<node_id>.json for each node, as the operator LAB_OPERATOR (the operator statement is
#                theirs: they open or approve the pull request), with ask_route nostr; an entry already there is kept
#   commitments  each node's own signed commitment and policy (loop_advertise), written exactly as returned to
#                nodes/commitments/<node_id>.json and policies/, then checked with scripts/check_commitment.py.
#                The harness signs nothing. Order: init, entries, commitments, then open the pull request
#   session      one run of the nodes: goose-2 seals a model call (step 01) and the two ask each other through the
#                mailbox their published commitments name (step 07), each answering by polling its own mailbox.
#                Writes $LAB_DIR/goose/work/<UTC stamp>/population/population.json. Pays nothing
#   run          session, then ./loop.sh --stage 1 --ecosystem goose --population live into
#                findings/goose/<UTC date>-live (LAB_OUT overrides), the steps without a Goose input NOT_CHECKED.
#                Needs the nodes listed in nodes/ at this clone's HEAD (step 03 reads them there)
#
# The relay is the lab's, in the session process: the commitments name wss://relay.loop-lab.example, which only a node
# on this host reaches, and a node answers only while a run polls it, so the commitments' PT1M answer window holds
# during a run only. The finding says so; no authenticating public relay is approved yet.
#
# run refuses to start when $LAB_DIR/run/LIVE_RUN exists or a lab.sh, loop.sh or live_daily.sh process runs, and holds
# LIVE_RUN while it runs (asg-lab-rebuild skips a host that has one). It touches no lab node, wallet or port of
# lab-1..4, and never Brattain or Shockley.
#
# Environment:
#   LAB_DIR        the lab directory (required); the Goose nodes live under $LAB_DIR/goose
#   LOOP_TOOLS     where the tools are (default ~/.cache/evidence-network-loop-live, the daily run's own)
#   LAB_OPERATOR   the GitHub handle of whoever runs the nodes (entries)
#   LAB_OUT        where run writes the findings (default findings/goose/<UTC date>-live)
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DRY=0
if [ "${1:-}" = "--dry-run" ]; then DRY=1; shift; fi
CMD=${1:-}
[ "$#" -eq 1 ] || { sed -n '5p' "$0" | sed 's/^#   //' >&2; exit 2; }

die() { echo "goose.sh: $*" >&2; exit 2; }
[ -n "${LAB_DIR:-}" ] || die "set LAB_DIR"
LAB_DIR=${LAB_DIR%/}
G=$LAB_DIR/goose
TOOLS=${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop-live}
export LOOP_TOOLS=$TOOLS
GOOSE_PY=$TOOLS/goose/venv/bin/python
export LOOP_GOOSE_SRC=$TOOLS/goose/src
export PYTHONDONTWRITEBYTECODE=1

# run COMMAND...: print the command under --dry-run, else run it.
run() {
  if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@" < /dev/null; fi
}
need_tools() { [ "$DRY" = 1 ] || [ -x "$GOOSE_PY" ] || die "no Goose tools in $TOOLS/goose; run lab/goose.sh tools"; }
need_ids() { [ "$DRY" = 1 ] || [ -f "$G/ids.json" ] || die "no $G/ids.json; run lab/goose.sh init"; }

cmd_tools() { run "$ROOT/steps/goose.setup.sh"; }

cmd_init() {
  need_tools
  if [ "$DRY" = 1 ]; then echo "+ goose_live.py init --homes $G/homes --keys $G/keys > $G/ids.json"; return; fi
  mkdir -p "$G" && chmod 700 "$G"
  mkdir -p "$G/homes" && chmod 700 "$G/homes"
  "$GOOSE_PY" -B "$ROOT/scripts/goose_live.py" init --homes "$G/homes" --keys "$G/keys" < /dev/null > "$G/ids.json.new" \
    && mv "$G/ids.json.new" "$G/ids.json"
  python3 -B -c 'import json,sys; [print(n, d["node_id"]) for n, d in sorted(json.load(open(sys.argv[1])).items())]' "$G/ids.json"
}

cmd_entries() {
  need_ids
  [ "$DRY" = 1 ] && { echo "+ write nodes/<node_id>.json for each node in $G/ids.json"; return; }
  [ -n "${LAB_OPERATOR:-}" ] || die "set LAB_OPERATOR to the GitHub handle of whoever runs the nodes"
  python3 -B - "$ROOT" "$G/ids.json" "$LAB_OPERATOR" "$LOOP_GOOSE_SRC" <<'PY'
import json, subprocess, sys
from pathlib import Path
root, ids, operator, src = sys.argv[1:]
sys.path.insert(0, root + "/scripts")
import check_nodes
rev = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
for name, d in sorted(json.load(open(ids)).items()):
    path = Path(root) / "nodes" / f"{d['node_id']}.json"
    entry = {
        "node_id": d["node_id"], "key_id": d["key_id"], "operator": operator, "consent": check_nodes.CONSENT,
        "mesh": "lab", "ask_route": "nostr",
        "disclosure": {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]},
        "host_build": f"goose-g1 {rev} (evidencebook python/examples/goose, run by lab/goose.sh on the loop lab host)",
        "plugin_version": f"loop_server.py of goose-g1 {rev}; the Goose node {name}",
    }
    if path.is_file():
        print(f"kept {path.name} ({name})")
        continue
    path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n")
    print(f"wrote {path.name} ({name})")
errs, _ = check_nodes.check_dir(Path(root) / "nodes")
if errs:
    sys.exit("nodes/ does not check:\n  " + "\n  ".join(errs))
PY
}

cmd_commitments() {
  need_tools; need_ids
  if [ "$DRY" = 1 ]; then echo "+ goose_live.py advertise --homes $G/homes --out nodes/commitments"; return; fi
  mkdir -p "$ROOT/nodes/commitments/policies"
  "$GOOSE_PY" -B "$ROOT/scripts/goose_live.py" advertise --homes "$G/homes" --out "$ROOT/nodes/commitments" < /dev/null
  python3 -B "$ROOT/scripts/check_commitment.py" "$ROOT/nodes/commitments"
}

cmd_session() {
  need_tools; need_ids
  work=$G/work/$(date -u +%Y%m%dT%H%M%SZ)
  if [ "$DRY" = 1 ]; then echo "+ goose_live.py session --homes $G/homes --keys $G/keys --published nodes/commitments --work $work"; return; fi
  mkdir -p "$work"
  "$GOOSE_PY" -B "$ROOT/scripts/goose_live.py" session --homes "$G/homes" --keys "$G/keys" \
    --published "$ROOT/nodes/commitments" --work "$work" < /dev/null 2>&1 | tee "$work/session.log" >&2
  [ "${PIPESTATUS[0]}" -eq 0 ] || die "the session failed; its log is $work/session.log"
  echo "$work/population"
}

cmd_run() {
  need_tools; need_ids
  lock=$LAB_DIR/run/LIVE_RUN
  if [ "$DRY" = 1 ]; then
    echo "+ refuse if $lock exists or lab.sh, loop.sh or live_daily.sh runs; take $lock"
    cmd_session
    echo "+ LOOP_GOOSE_POPULATION=<population> ./loop.sh --stage 1 --ecosystem goose --population live --out ${LAB_OUT:-findings/goose/<UTC date>-live}"
    return
  fi
  if pgrep -f "lab/lab.sh|[/ ]loop.sh|live_daily.sh" > /dev/null; then die "a lab.sh, loop.sh or live_daily.sh process runs"; fi
  mkdir -p "$LAB_DIR/run"
  ( set -o noclobber; echo "goose.sh $$ $(date -u +%FT%TZ)" > "$lock" ) 2> /dev/null || die "$lock exists: another run holds the lab"
  trap 'rm -f "$lock"' EXIT
  pop=$(cmd_session | tail -n 1)
  LOOP_GOOSE_POPULATION=$pop "$ROOT/loop.sh" --stage 1 --ecosystem goose --population live \
    --out "${LAB_OUT:-$ROOT/findings/goose/$(date -u +%F)-live}" < /dev/null
}

case "$CMD" in
  tools) cmd_tools ;;
  init) cmd_init ;;
  entries) cmd_entries ;;
  commitments) cmd_commitments ;;
  session) cmd_session ;;
  run) cmd_run ;;
  *) sed -n '5p' "$0" | sed 's/^#   //' >&2; exit 2 ;;
esac
