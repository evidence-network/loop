#!/usr/bin/env bash
# join.sh: join the evidence network with one command, on the pinned release.
#
#   ./join.sh --name NAME --node-key NODE.pem --controller-key CONTROLLER.pem --out DIR
#             [--controller-class operator_key] [--policy POLICY.json]
#             [--answers-within PT1H] [--home DIR]
#
# It does four things, in order, and writes what each produced into DIR:
#   1. binds the node key to its controller: a controller-binding/0 record
#      (same_controller_asserted, dual_signature/0) signed by both keys, and
#      the controller class, in binding.json and controller.json;
#   2. creates the node's evidence book (a capsulectl book profile), seals a
#      first record and cuts a checkpoint over it, in book.json;
#   3. writes the node's commitment, commitment.json (evidence-commitment/1,
#      steps/03-advertise.md), signed by the node key, with its signed share
#      policy in disclosure-policy.json, to open as a pull request to
#      nodes/commitments/ (see publish_commitment below). It staples one
#      history_card/1 answer per book the node serves, each given to a
#      stranger: a throwaway stranger profile asks the public requests
#      (checkpoints, then history_card/1 of each book), the node answers them
#      on the public path, and the requests and answers are kept in DIR/probe
#      for a verifier (the join's own book as probe/history_card.*, every
#      other book as probe/cards/<log id>.*);
#   4. probes the node as a stranger would: `capsulectl book doctor --probe`,
#      into doctor.json.
# join.json lists what was done, what is self-attested, unwitnessed,
# unchecked, unbound and unshared. join.sh checks nothing itself: the loop's
# step 12 (steps/12-join) verifies DIR.
#
# Needs: capsulectl and capsulectl-book at the versions.lock pins (run
# steps/12-join.setup.sh), openssl 3, python3. CAPSULECTL names the core
# binary and CAPSULECTL_PLUGIN_ROOTS the directory holding capsulectl-book;
# both default to the setup script's install directory. Keys are Ed25519
# PKCS#8 PEM. Profiles and the book live under --home (default
# ~/.local/share/evidence-network/NAME), never in your own capsulectl config.
#
# The books the node serves are found with the CLI: every profile that
# `capsulectl profile list` names under --home's config (XDG_CONFIG_HOME=
# HOME/config), except the stranger's, whose trusted keys include the node
# key; `capsulectl profile show` gives its log id, the card's stream, and
# its checkpoint key, which the card's entry names (checkpoint_key). Every
# card is in the mesh-llm community. The
# join's own book is always one. To serve another book, create its profile
# under the same --home, signed by the node key, with a checkpoint, before
# running join.sh.
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
tools="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/bin"
capsulectl="${CAPSULECTL:-$tools/capsulectl}"
export CAPSULECTL_PLUGIN_ROOTS="${CAPSULECTL_PLUGIN_ROOTS:-$(dirname "$capsulectl")}"

name="" node_key="" controller_key="" out="" home=""
controller_class="operator_key" policy="" answers_within="PT1H"
while [ $# -gt 0 ]; do
  case "$1" in
    --name) name="$2"; shift 2 ;;
    --node-key) node_key="$2"; shift 2 ;;
    --controller-key) controller_key="$2"; shift 2 ;;
    --controller-class) controller_class="$2"; shift 2 ;;
    --policy) policy="$2"; shift 2 ;;
    --answers-within) answers_within="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --home) home="$2"; shift 2 ;;
    -h|--help) sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "join.sh: unknown argument $1" >&2; exit 2 ;;
  esac
done
for v in name node_key controller_key out; do
  [ -n "${!v}" ] || { echo "join.sh: --${v//_/-} is required" >&2; exit 2; }
done
[[ "$name" =~ ^[a-z][a-z0-9-]*$ ]] || { echo "join.sh: --name is lowercase letters, digits and hyphens" >&2; exit 2; }
[ -x "$capsulectl" ] || { echo "join.sh: no capsulectl at $capsulectl (run steps/12-join.setup.sh)" >&2; exit 2; }
command -v openssl >/dev/null || { echo "join.sh: openssl is required" >&2; exit 2; }

home="${home:-$HOME/.local/share/evidence-network/$name}"
mkdir -p "$out/probe" "$home"
out="$(cd "$out" && pwd)" home="$(cd "$home" && pwd)"
# capsulectl reads its profiles from here: this join never touches yours.
export XDG_CONFIG_HOME="$home/config"
mkdir -p "$XDG_CONFIG_HOME"
cap() { "$capsulectl" "$@"; }
book_id="$name-book"

pubhex() { openssl pkey -in "$1" -pubout -outform DER | tail -c 32 | python3 -c 'import sys; print(sys.stdin.buffer.read().hex())'; }
# The 32-byte Ed25519 seed of a PKCS#8 key, as the hex seed file capsulectl reads.
seedfile() {
  umask 077
  openssl pkey -in "$1" -outform DER | tail -c 32 | python3 -c 'import sys; print(sys.stdin.buffer.read().hex())' >"$2"
}
# Ed25519 signature (hex) by PEM key $1 over the bytes of file $2.
sign() { openssl pkeyutl -sign -inkey "$1" -rawin -in "$2" | python3 -c 'import sys; print(sys.stdin.buffer.read().hex())'; }
# JCS (RFC 8785) of a JSON file with member $2 removed. The objects signed
# here hold only ASCII strings, arrays and objects, where sorted keys and no
# whitespace are the canonical form.
jcs_without() {
  python3 - "$1" "$2" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
d.pop(sys.argv[2], None)
sys.stdout.write(json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
EOF
}
for k in "$node_key" "$controller_key"; do
  openssl pkey -in "$k" -noout -text 2>/dev/null | grep -q ED25519 || { echo "join.sh: $k is not an Ed25519 private key" >&2; exit 2; }
done
node_pub="$(pubhex "$node_key")" controller_pub="$(pubhex "$controller_key")"
[ "$node_pub" != "$controller_pub" ] || { echo "join.sh: the controller key must not be the node key" >&2; exit 2; }
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
doctor_help="$(cap book doctor --help 2>&1)" || true
grep -q -- '--probe' <<<"$doctor_help" || {
  # capsulectl's own words first: a refused plugin (group-writable, wrong owner) reads like a
  # missing flag otherwise.
  echo "join.sh: 'capsulectl book doctor --help' did not offer --probe; it said:" >&2
  sed 's/^/  | /' <<<"$doctor_help" | tail -n 20 >&2
  echo "join.sh: if that is a refusal of capsulectl-book, fix it (chmod go-w on it and its directory);" \
       "if the flag is missing, run steps/12-join.setup.sh" >&2
  exit 2; }

# --- 1. Bind the node key to its controller (controller-binding/0) -----------
python3 - "$node_pub" "$controller_pub" "$now" "$out/binding.json" <<'EOF'
import datetime as dt, json, secrets, sys
node, controller, now, path = sys.argv[1:]
t = dt.datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
record = {
    "binding_version": "controller-binding/0",
    "claim": "same_controller_asserted",
    "method": "dual_signature/0",
    "keys": sorted([node, controller]),
    "issued_at": now,
    "not_before": now,
    "not_after": (t + dt.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "revocation": "revocation_statement/0",
    "nonce": secrets.token_hex(16),
    "sigs": [],
}
json.dump(record, open(path, "w"), indent=2, sort_keys=True)
EOF
jcs_without "$out/binding.json" sigs >"$home/binding.signing-input"
node_sig="$(sign "$node_key" "$home/binding.signing-input")"
controller_sig="$(sign "$controller_key" "$home/binding.signing-input")"
python3 - "$out" "$home/binding.signing-input" "$node_pub" "$node_sig" "$controller_pub" "$controller_sig" "$controller_class" <<'EOF'
import hashlib, json, sys
out, signing_input, node, node_sig, controller, controller_sig, cls = sys.argv[1:]
path = f"{out}/binding.json"
record = json.load(open(path))
by_key = {node: node_sig, controller: controller_sig}
record["sigs"] = [{"key_id": k, "sig": by_key[k]} for k in record["keys"]]
json.dump(record, open(path, "w"), indent=2, sort_keys=True)
digest = hashlib.sha256(open(signing_input, "rb").read()).hexdigest()
json.dump({"controller_class": cls, "controller_key": controller, "node_key": node, "binding_digest": digest},
          open(f"{out}/controller.json", "w"), indent=2, sort_keys=True)
EOF
echo "join: bound node key ${node_pub:0:16}… to its controller (class $controller_class)" >&2

# --- 2. Create the book ------------------------------------------------------
seedfile "$node_key" "$home/node.seed"
[ -f "$home/checkpoint.seed" ] || cap key generate --output "$home/checkpoint.seed" >/dev/null
checkpoint_pub="$(cap key show-public "$home/checkpoint.seed" | python3 -c 'import json,sys; print(json.load(sys.stdin)["public_key"])')"
cap profile create --name "$name" --type jsonl --jsonl-path "$home/book" --log-id "$book_id" \
  --operator "$name" --trusted-key "$node_pub" --signing-key-file "$home/node.seed" \
  --checkpoint-signing-key-file "$home/checkpoint.seed" --checkpoint-trusted-key "$checkpoint_pub" >/dev/null
cap store init --profile "$name" >/dev/null
python3 - "$name" "$now" >"$home/first-record.request.json" <<'EOF'
import json, sys
name, now = sys.argv[1:]
print(json.dumps({"spec_version": "capsule-seal-request/v1",
                  "capsule": {"ActionID": f"{name}-join", "ActionType": "fyi", "Operator": name,
                              "Developer": "evidence-network-join", "Timestamp": now},
                  "payload": {"joined": now}}))
EOF
cap seal --profile "$name" --request "$home/first-record.request.json" --output "$home/first-record.json" >"$home/seal.out"
cap cll append --profile "$name" --capsule "$home/first-record.json" >"$home/append.out"
cap cll checkpoint create --profile "$name" >"$home/checkpoint.out"
python3 - "$out/book.json" "$book_id" "$node_pub" "$checkpoint_pub" "$home" <<'EOF'
import json, sys
path, book_id, node, checkpoint, home = sys.argv[1:]
seal = json.load(open(f"{home}/seal.out"))
cp = json.load(open(f"{home}/checkpoint.out"))
json.dump({"book_id": book_id, "node_key": node, "checkpoint_key": checkpoint,
           "first_record": seal["capsule_id"], "checkpoint": cp["checkpoint"],
           "indexed_sequence": cp["indexed_sequence"], "checkpoint_statement": cp["statement"]},
          open(path, "w"), indent=2, sort_keys=True)
EOF
echo "join: created book $book_id with a checkpoint over its first record" >&2

# The share policy this node answers under, and advertises by digest.
if [ -n "$policy" ]; then
  cp "$policy" "$out/policy.json"
else
  # capsulectl-book's documented defaults, written out so they have a digest.
  printf '%s\n' '{"adjudications":"deliver_to_subjects","history_segments":"counterparties","record_at_completion":"counterparty"}' >"$out/policy.json"
fi

# --- 3. Publish the commitment ----------------------------------------------
# First the stranger's public requests, answered by the node: each book's
# history_card/1 answer is one of the commitment's staples, and the bytes stay
# in DIR/probe for a verifier. The stranger is a throwaway profile with keys made
# here: its seeds sit in probe/stranger/ on purpose, and are not secrets.
st="$out/probe/stranger"
mkdir -p "$st"
pub_only() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({"public_key": d["public_key"]}))'; }
cap key generate --output "$st/signing.seed" | pub_only >"$st/signing.json"
cap key generate --output "$st/checkpoint.seed" | pub_only >"$st/checkpoint.json"
key_of() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["public_key"])' "$1"; }
cap profile create --name "$name-stranger" --type jsonl --jsonl-path "$st/store" --log-id "$name-stranger" \
  --operator stranger --trusted-key "$(key_of "$st/signing.json")" --signing-key-file "$st/signing.seed" \
  --checkpoint-signing-key-file "$st/checkpoint.seed" --checkpoint-trusted-key "$(key_of "$st/checkpoint.json")" >/dev/null
cap store init --profile "$name-stranger" >/dev/null
printf '{"log_id":"%s","operator":"stranger"}\n' "$name-stranger" >"$st/profile.json"
# capsulectl's own output, without the local paths it names.
keep() { python3 -c 'import json,sys; d=json.load(sys.stdin); d.pop("output",None); json.dump(d,sys.stdout,sort_keys=True); print()'; }
ask() { # NAME REQUEST_JSON [PROFILE BOOK]: the stranger asks; the node answers on the public path (no --requester)
  local profile="${3:-$name}" responder="${4:-$book_id}"
  mkdir -p "$(dirname "$home/$1")" "$(dirname "$out/probe/$1")"
  printf '%s\n' "$2" >"$home/$1.request.json"
  cap book request --profile "$name-stranger" --request "$home/$1.request.json" --responder "$responder" \
    --output "$out/probe/$1.request.json" | keep >"$out/probe/$1.asked.json"
  cap book respond --profile "$profile" --policy "$out/policy.json" --request "$out/probe/$1.request.json" \
    --output "$out/probe/$1.answer.json" | keep >"$out/probe/$1.respond.json"
}
card_request='{"subject":{"checkpoints":null},"derivation":"history_card/1","coverage":{"min_freshness":1}}'
ask checkpoints '{"subject":{"checkpoints":null},"coverage":{"min_freshness":1}}'
ask history_card "$card_request"
# The books this node serves (see the header): "PROFILE<TAB>LOG_ID<TAB>CHECKPOINT_KEY" each, into books.tsv.
: >"$home/books.tsv"
for p in $(cap profile list | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)["profiles"]))'); do
  [ "$p" != "$name-stranger" ] || continue
  cap profile show "$p" | python3 -c '
import json, re, sys
p, node = sys.argv[1:]
d = json.load(sys.stdin)
log_id = d.get("LogID") or ""
checkpoint = ((d.get("Checkpoint") or {}).get("TrustedKeys") or [""])[0]
if node in (d.get("TrustedKeys") or []):
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", log_id):
        sys.exit("join.sh: book profile %s has log id %r, not a plain name" % (p, log_id))
    if not re.match(r"^[0-9a-f]{64}$", checkpoint):
        sys.exit("join.sh: book profile %s names no Ed25519 checkpoint key" % p)
    print("%s\t%s\t%s" % (p, log_id, checkpoint))' "$p" "$node_pub" >>"$home/books.tsv"
done
grep -q "^$name	$book_id	" "$home/books.tsv" || { echo "join.sh: the join's own book $book_id is not among the books found" >&2; exit 1; }
while IFS=$'\t' read -r -u 3 p stream _; do
  [ "$p" != "$name" ] || continue
  ask "cards/$stream" "$card_request" "$p" "$stream"
done 3<"$home/books.tsv"
echo "join: a stranger asked the node for checkpoints and the history_card/1 of each of its $(wc -l <"$home/books.tsv" | tr -d ' ') book(s)" >&2

# The commitment, in step 03's format (steps/03-advertise.md), signed with
# scripts/check_commitment.py's signer; step 12 verifies it with that checker. Its checkpoint member
# carries the book's checkpoint attestation whole: the book, its checkpoint
# key and the signed checkpoint statement. The share policy the node answers
# under (policy.json) is published signed by the node key, as
# disclosure-policy/0, in disclosure-policy.json beside the commitment.
# Publishing it is a pull request adding both to nodes/commitments/; join.sh
# never pushes.
publish_commitment() {
  python3 - "$root" "$out" "$node_key" "$book_id" "$node_pub" "$checkpoint_pub" "$answers_within" "$now" "$home/books.tsv" <<'EOF'
import base64, hashlib, json, sys
root, out, key, book_id, node, checkpoint_key, within, now, books = sys.argv[1:]
sys.path.insert(0, f"{root}/scripts")
import check_commitment as cc
b64 = lambda raw: base64.b64encode(raw).decode("ascii")
policy = cc.sign({"profile": cc.POLICY_PROFILE, "key_id": node,
                  "policy": json.load(open(f"{out}/policy.json"))}, key)
policy_bytes = (json.dumps(policy, indent=2, sort_keys=True) + "\n").encode()
open(f"{out}/disclosure-policy.json", "wb").write(policy_bytes)
book = json.load(open(f"{out}/book.json"))
attestation = (json.dumps({"book_id": book_id, "checkpoint_key": checkpoint_key,
                           "statement": book["checkpoint_statement"]}, indent=2, sort_keys=True) + "\n").encode()
cards = []
for line in open(books).read().splitlines():
    _, stream, checkpoint = line.split("\t")
    staple = open(f"{out}/probe/" + ("history_card" if stream == book_id else f"cards/{stream}") + ".answer.json",
                  "rb").read()
    if "artifact" not in json.loads(staple):
        sys.exit(f"join.sh: book {stream} did not answer history_card/1 with a card (does it have a checkpoint?)")
    cards.append({"stream": stream, "community": "mesh-llm", "derivation": "history_card/1",
                  "sha256": hashlib.sha256(staple).hexdigest(), "bytes": b64(staple),
                  "checkpoint_key": cc.b64url(cc.ED25519_SPKI + bytes.fromhex(checkpoint))})
commitment = cc.sign({
    "profile": cc.PROFILE_1,
    "node_id": node,
    "key_id": node,
    "spki": b64(cc.ED25519_SPKI + bytes.fromhex(node)),
    "issued_at": now,
    "ask": {"endpoint": f"book:{book_id}", "bindings": ["capsulectl-book/respond"]},
    "answers": {"subjects": ["checkpoints"], "derivations": ["history_card/1"]},
    "lcap": {"policy_digest": hashlib.sha256(policy_bytes).hexdigest(), "policy_uri": "disclosure-policy.json",
             "predicate_classes": ["recorded:self_attested"]},
    "checkpoint": {"sha256": hashlib.sha256(attestation).hexdigest(), "bytes": b64(attestation)},
    "human_gated": False,
    "answers_within": within,
    "history_cards": sorted(cards, key=lambda c: c["stream"]),
}, key)
open(f"{out}/commitment.json", "w").write(json.dumps(commitment, indent=2, sort_keys=True) + "\n")
EOF
}
publish_commitment
echo "join: wrote commitment.json and disclosure-policy.json; open a pull request adding them to nodes/commitments/" >&2

# --- 4. Probe the node as a stranger ----------------------------------------
cap book doctor --profile "$name" --policy "$out/policy.json" \
  --controller-binding "$out/binding.json" --probe >"$out/doctor.json"
echo "join: probed the node as a stranger with capsulectl book doctor --probe" >&2

# --- What this join is, in the words of the doctor's list --------------------
python3 - "$out" "$("$capsulectl" --version 2>/dev/null | head -1)" "$("$CAPSULECTL_PLUGIN_ROOTS/capsulectl-book" --version 2>/dev/null | head -1)" <<'EOF'
import json, sys
out, version, book_version = sys.argv[1:]
c = json.load(open(f"{out}/controller.json"))
p = json.load(open(f"{out}/policy.json"))
off = sorted(k for k, v in p.items() if v == "off")
status = [
    {"state": "self_attested", "what": "every record in the book is sealed by the node key alone"},
    {"state": "unwitnessed", "what": "no witness receipt is held for the book's checkpoint: no witness endpoint is configured"},
    {"state": "unbound", "what": f"the controller binding {c['binding_digest']} is signed by both keys, but no released verifier checks controller-binding/0, so a verifier holds the key unbound"},
    {"state": "unshared", "what": "a stranger receives checkpoints and history_card/1 only; history segments go to counterparties, adjudications to their subjects" + (f"; off: {', '.join(off)}" if off else "")},
]
json.dump({"join": "evidence-network/join.sh", "capsulectl": version, "capsulectl_book": book_version,
           "doctor_probe": True,
           "did": ["bound the node key to a controller", "created the book", "wrote the commitment",
                   "probed the node as a stranger"],
           "status": status}, open(f"{out}/join.json", "w"), indent=2, sort_keys=True)
EOF
echo "join: done; what was done and what is not checked is in $out/join.json" >&2
