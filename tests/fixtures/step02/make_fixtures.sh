#!/usr/bin/env bash
# Make step 02's test fixtures once: a rehearsal population with Alice and Bob
# TEST keys (Ed25519, PKCS#8 and SPKI PEM), in the agreed population.json shape,
# and a rehearsal TEST witness (its key, and witnesses.json, the directory a
# verifier trusts it under), plus a second witness key the directory does not list.
# These keys are test keys, committed on purpose, never used by a live node.
#   tests/fixtures/step02/make_fixtures.sh   (needs openssl, python3)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REL=tests/fixtures/step02/rehearsal
rm -rf "$HERE/rehearsal"
key() { # DIR STEM
  mkdir -p "$1"
  openssl genpkey -algorithm ed25519 -out "$1/$2.pem"
  openssl pkey -in "$1/$2.pem" -pubout -out "$1/$2.pub.pem"
}
for n in alice bob; do key "$HERE/rehearsal/keys/$n" node-key; done
key "$HERE/rehearsal/keys/witness" witness-key
key "$HERE/rehearsal/keys/unlisted-witness" witness-key
python3 - "$HERE/rehearsal/keys/witness/witness-key.pub.pem" >"$HERE/rehearsal/witnesses.json" <<'PY'
import base64, json, sys
der = base64.b64decode("".join(l for l in open(sys.argv[1]).read().splitlines() if not l.startswith("-----")))
print(json.dumps({"witnesses": [{"name": "rehearsal-witness", "endpoint": "https://witness.rehearsal.invalid",
                                 "key_ids": [der[-32:].hex()]}]}, indent=2))
PY
cat > "$HERE/rehearsal/population.json" <<JSON
{"population":"rehearsal","nodes":[{"name":"alice","key_dir":"$REL/keys/alice"},{"name":"bob","key_dir":"$REL/keys/bob"}],"rail":"mock",
 "witness":{"name":"rehearsal-witness","url":"https://witness.rehearsal.invalid","key_dir":"$REL/keys/witness","directory":"$REL/witnesses.json"}}
JSON
