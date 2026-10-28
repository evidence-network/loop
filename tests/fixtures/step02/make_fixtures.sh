#!/usr/bin/env bash
# Make step 02's test fixtures once: a rehearsal population with Alice and Bob
# TEST keys (Ed25519, PKCS#8 and SPKI PEM), in the agreed population.json shape.
# These keys are test keys, committed on purpose, never used by a live node.
#   tests/fixtures/step02/make_fixtures.sh   (needs openssl)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REL=tests/fixtures/step02/rehearsal
rm -rf "$HERE/rehearsal"
for n in alice bob; do
  d="$HERE/rehearsal/keys/$n"
  mkdir -p "$d"
  openssl genpkey -algorithm ed25519 -out "$d/node-key.pem"
  openssl pkey -in "$d/node-key.pem" -pubout -out "$d/node-key.pub.pem"
done
cat > "$HERE/rehearsal/population.json" <<JSON
{"population":"rehearsal","nodes":[{"name":"alice","key_dir":"$REL/keys/alice"},{"name":"bob","key_dir":"$REL/keys/bob"}],"rail":"mock"}
JSON
