#!/usr/bin/env bash
# Make step 05's test fixtures once.
#
# rehearsal/: Alice and Bob node TEST keys and one rehearsal controller TEST key
#   each (Ed25519, PKCS#8 and SPKI PEM), population.json in the agreed shape,
#   and step05/{settings,controllers}.json as steps/05-deal reads them.
# live/: two lab-shaped capsules plugin data dirs, under nodes/<name>/data, that
#   population.json names through $LAB_DIR (the tests set it to this directory).
#   lab-a holds one pushed half: the record-push bundle the capsules plugin
#   publishes as a test vector (capsules-plugin v0.1.5,
#   vectors/record_push_bundle_rust.json, Apache-2.0, checked against that tag's
#   vectors/SHA256SUMS), split into the plugin's three held stores, and its own
#   ledger carries a record citing that half as counterparty_half, sealed here
#   with the AAC Go reference (step 01's cross_language, at the aac-go pin).
#   lab-b holds nothing.
# These keys are test keys, committed on purpose, never used by a live node.
#   tests/fixtures/step05/make_fixtures.sh   (needs openssl, python3, curl, and
#   steps/01-seal.setup.sh run first)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REL=tests/fixtures/step05/rehearsal
VECTOR_URL=https://raw.githubusercontent.com/Mesh-LLM/capsules-plugin/v0.1.5/vectors/record_push_bundle_rust.json
VECTOR_SHA256=3896af5de49cfdcd24fc1ca0ff4e1c7c5b5e08ef406232767c2e7c36d91b10e1
CROSS="${LOOP_TOOLS:-$HOME/.cache/evidence-network-loop}/01-seal/bin/cross_language"
[ -x "$CROSS" ] || { echo "make_fixtures: $CROSS is missing; run steps/01-seal.setup.sh" >&2; exit 1; }
key() { # DIR STEM
  mkdir -p "$1"
  openssl genpkey -algorithm ed25519 -out "$1/$2.pem"
  openssl pkey -in "$1/$2.pem" -pubout -out "$1/$2.pub.pem"
}

rm -rf "$HERE/rehearsal" "$HERE/live"
for n in alice bob; do
  key "$HERE/rehearsal/keys/$n" node-key
  key "$HERE/rehearsal/keys/controller-$n" controller-key
done
cat > "$HERE/rehearsal/population.json" <<JSON
{"population":"rehearsal","nodes":[{"name":"alice","key_dir":"$REL/keys/alice"},{"name":"bob","key_dir":"$REL/keys/bob"}],"rail":"mock"}
JSON
mkdir -p "$HERE/rehearsal/step05"
cat > "$HERE/rehearsal/step05/settings.json" <<'JSON'
{"alice": {"share_record_at_completion": "counterparty"}, "bob": {"share_record_at_completion": "counterparty"}}
JSON
cat > "$HERE/rehearsal/step05/controllers.json" <<JSON
{"controllers": [
  {"id": "rehearsal-controller-alice", "key_dir": "$REL/keys/controller-alice", "nodes": ["alice"]},
  {"id": "rehearsal-controller-bob", "key_dir": "$REL/keys/controller-bob", "nodes": ["bob"]}
]}
JSON

mkdir -p "$HERE/live"
curl -sfL "$VECTOR_URL" -o "$HERE/live/record_push_bundle_rust.json"
echo "$VECTOR_SHA256  $HERE/live/record_push_bundle_rust.json" | sha256sum -c --quiet
for n in lab-a lab-b; do
  key "$HERE/live/nodes/$n/data/keys" node-key
  mkdir -p "$HERE/live/nodes/$n/data/ledger"
  : > "$HERE/live/nodes/$n/data/ledger/capsules.jsonl"
done
python3 -I - "$HERE/live" "$CROSS" <<'PY'
import base64, json, subprocess, sys
from pathlib import Path
live, cross = Path(sys.argv[1]), sys.argv[2]
bundle = json.loads((live / "record_push_bundle_rust.json").read_text())
half, cid = bundle["capsule"], bundle["capsule"]["capsule_id"]
ledger = live / "nodes" / "lab-a" / "data" / "ledger"
at = "2026-10-28T00:00:00.000Z"
def line(path, obj):
    with open(path, "a") as f:
        f.write(json.dumps(obj, separators=(",", ":")) + "\n")
line(ledger / "received-capsules.jsonl", half)
line(ledger / "received-provenance.jsonl",
     {"capsule_id": cid, "received_from": "lab-b", "via": "push", "received_at": at, "signature_ok": True})
line(ledger / "received-inclusion.jsonl",
     {"half_capsule_id": cid, "received_from": "lab-b", "via": "push", "received_at": at,
      "leaf_index": bundle["inclusion"]["leaf_index"], "mmr_size": bundle["checkpoint"]["mmr_size"],
      "checkpoint": bundle["checkpoint"], "inclusion_proof": bundle["inclusion"]["proof"]})
# lab-a's own record citing the half, in the shape the plugin's citing record takes.
der = base64.b64decode("".join(l for l in (ledger.parent / "keys" / "node-key.pub.pem").read_text().splitlines()
                               if not l.startswith("-----")))
body = {k: v for k, v in half.items() if k not in ("capsule_id", "signature", "key_id", "chain")}
body["action_id"] = f"mesh-poc/received-half-citation/{cid}"
body["operator"] = "lab-a"
body["references"] = [{"type": "capsule", "digest_alg": "SHA-256", "digest": cid,
                       "citation_purpose": "counterparty_half"}]
r = subprocess.run([cross, "seal"], input=json.dumps(body).encode(), capture_output=True, check=True)
citing = json.loads(r.stdout)
citing["key_id"] = der[12:].hex()
line(ledger / "capsules.jsonl", citing)
PY
cat > "$HERE/live/population.json" <<'JSON'
{"population":"live","nodes":[
  {"name":"lab-a","data_dir":"$LAB_DIR/nodes/lab-a/data","book_dir":null},
  {"name":"lab-b","data_dir":"$LAB_DIR/nodes/lab-b/data","book_dir":null},
  {"name":"lab-c","data_dir":null,"book_dir":null}],"rail":null}
JSON
