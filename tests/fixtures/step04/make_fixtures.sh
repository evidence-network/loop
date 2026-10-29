#!/usr/bin/env bash
# Make step 04's test fixtures once.
#
# rehearsal/: Alice and Bob node TEST keys (Ed25519, PKCS#8 and SPKI PEM),
#   population.json in the agreed shape, and step04/ copied from
#   population/rehearsal/step04 (the admission decisions).
# live/: two lab-shaped capsules plugin data dirs, under nodes/<name>/data, that
#   population.json names through $LAB_DIR (the tests set it to this directory):
#   lab-a's ledger holds two records with no admission decision and a GO and a NO
#   admission_decision record in the plugin's shape, lab-b's is empty; lab-c has no data_dir.
# These keys are test keys, committed on purpose, never used by a live node.
#   tests/fixtures/step04/make_fixtures.sh   (needs openssl)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
REL=tests/fixtures/step04/rehearsal
key() { # DIR STEM
  mkdir -p "$1"
  openssl genpkey -algorithm ed25519 -out "$1/$2.pem"
  openssl pkey -in "$1/$2.pem" -pubout -out "$1/$2.pub.pem"
}

rm -rf "$HERE/rehearsal" "$HERE/live"
for n in alice bob; do key "$HERE/rehearsal/keys/$n" node-key; done
cat > "$HERE/rehearsal/population.json" <<JSON
{"population":"rehearsal","nodes":[{"name":"alice","key_dir":"$REL/keys/alice"},{"name":"bob","key_dir":"$REL/keys/bob"}],"rail":"mock"}
JSON
mkdir -p "$HERE/rehearsal/step04"
cp -R "$ROOT/population/rehearsal/step04/scenarios" "$HERE/rehearsal/step04/"

for n in lab-a lab-b; do
  mkdir -p "$HERE/live/nodes/$n/data/ledger" "$HERE/live/nodes/$n/data/keys"
  : > "$HERE/live/nodes/$n/data/ledger/capsules.jsonl"
done
printf '%s\n' '{"capsule_id":"00","action_type":"fyi","admission_policy":{"decision":"allow"}}' \
  '{"capsule_id":"01","action_type":"fyi"}' \
  '{"capsule_id":"02","action_type":"fyi","model_attestation":{"compute_attestation":{"admission_decision":{"peer_commitment":{"alg":"SHA-256","digest":"abababababababababababababababababababababababababababababababab"},"verdict":"GO","source":"default","reads":[{"kind":"operator_policy","policy":"basic/1","digest":{"alg":"SHA-256","digest":"cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"}}],"request_id":"r2","attempt":1,"model":"m"}}}}' \
  '{"capsule_id":"03","action_type":"fyi","model_attestation":{"compute_attestation":{"admission_decision":{"peer_commitment":{"alg":"SHA-256","digest":"abababababababababababababababababababababababababababababababab"},"verdict":"NO","source":"deny_list","reads":[{"kind":"operator_policy","policy":"basic/1","digest":{"alg":"SHA-256","digest":"cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"}}],"request_id":"r3","attempt":1,"model":"m"}}}}' > "$HERE/live/nodes/lab-a/data/ledger/capsules.jsonl"
cat > "$HERE/live/population.json" <<'JSON'
{"population":"live","nodes":[
  {"name":"lab-a","data_dir":"$LAB_DIR/nodes/lab-a/data","book_dir":null},
  {"name":"lab-b","data_dir":"$LAB_DIR/nodes/lab-b/data","book_dir":null},
  {"name":"lab-c","data_dir":null,"book_dir":null}],"rail":null}
JSON
