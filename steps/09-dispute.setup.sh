#!/usr/bin/env bash
# Step 09 Dispute needs no versions.lock component to install: its verifier
# (steps/09-dispute.d/aac.py) recomputes capsule ids and checks the capsules
# plugin's COSE_Sign1 envelopes with the standard library and the openssl
# command (3.0 or later) from the runner's base image. On the live population
# the step only reads each node's capsules ledger, in the record format of the
# capsules pin. This checks openssl is there; it installs nothing.
set -euo pipefail
command -v openssl >/dev/null || { echo "09-dispute: openssl is missing" >&2; exit 1; }
v=$(openssl version | awk '{print $2}')
case "$v" in
  [3-9].*) ;;
  *) echo "09-dispute: openssl $v has no Ed25519 pkeyutl -rawin; 3.0 or later is needed" >&2; exit 1 ;;
esac
echo "09-dispute: openssl $v"
