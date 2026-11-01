#!/usr/bin/env bash
# Step 06 Settle needs no versions.lock component on the rehearsal population:
# the mock rail's adapter (population/rehearsal/rail/mock_rail.py) verifies
# Ed25519 with the openssl command (3.0 or later) from the runner's base image.
# On the live population the step only reads each node's capsules ledger, in
# the record format of the capsules pin: the flat capsules.jsonl, or for a node
# on the shelf the output of `capsules chain-lines` (the plugin's binary, on PATH
# or in LOOP_CAPSULES_BIN, else the lab's CAPSULES_BIN). This checks openssl is there; it installs nothing.
set -euo pipefail
command -v openssl >/dev/null || { echo "06-settle: openssl is missing" >&2; exit 1; }
v=$(openssl version | awk '{print $2}')
case "$v" in
  [3-9].*) ;;
  *) echo "06-settle: openssl $v has no Ed25519 pkeyutl -rawin; 3.0 or later is needed" >&2; exit 1 ;;
esac
echo "06-settle: openssl $v"
