#!/usr/bin/env bash
# Make step 06's test fixtures once: a copy of the rehearsal population (the
# Alice and Bob TEST keys, the mock rail's TEST key) and, from them, the mock
# rail's files and step 06's inputs, written by the rail's own make_rail.py.
# These keys are test keys, committed on purpose, never used by a live node.
#   tests/fixtures/step06/make_fixtures.sh   (needs openssl, python3)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
REL=tests/fixtures/step06/rehearsal
rm -rf "$HERE/rehearsal"
mkdir -p "$HERE/rehearsal/keys" "$HERE/rehearsal/rail/keys"
for n in alice bob; do cp -r "$ROOT/population/rehearsal/keys/$n" "$HERE/rehearsal/keys/$n"; done
cp "$ROOT/population/rehearsal/rail/keys/rail-key.pem" "$ROOT/population/rehearsal/rail/keys/rail-key.pub.pem" \
  "$HERE/rehearsal/rail/keys/"
cat > "$HERE/rehearsal/population.json" <<JSON
{"population":"rehearsal","nodes":[{"name":"alice","key_dir":"$REL/keys/alice"},{"name":"bob","key_dir":"$REL/keys/bob"}],"rail":"mock"}
JSON
python3 -B "$ROOT/population/rehearsal/rail/make_rail.py" --population "$HERE/rehearsal"
