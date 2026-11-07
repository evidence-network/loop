#!/usr/bin/env bash
# Step 03 Advertise signs and verifies with the openssl command (Ed25519, 3.0
# or later) and publishes with git, both from the runner's base image; this
# checks they are there. It verifies the stapled history_card/1 with the
# capsule-cli versions.lock pins, which steps/capsule-cli.setup.sh builds.
set -euo pipefail
command -v git >/dev/null || { echo "03-advertise: git is missing" >&2; exit 1; }
command -v openssl >/dev/null || { echo "03-advertise: openssl is missing" >&2; exit 1; }
v=$(openssl version | awk '{print $2}')
case "$v" in
  [3-9].*) ;;
  *) echo "03-advertise: openssl $v has no Ed25519 pkeyutl -rawin; 3.0 or later is needed" >&2; exit 1 ;;
esac
echo "03-advertise: openssl $v, $(git --version)"
exec "$(cd "$(dirname "$0")" && pwd)/capsule-cli.setup.sh"
