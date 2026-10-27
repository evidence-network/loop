#!/usr/bin/env bash
# Step 03 Advertise needs no versions.lock component: it signs and verifies
# with the openssl command (Ed25519, 3.0 or later) and publishes with git,
# both from the runner's base image. This checks they are there; it installs
# nothing.
set -euo pipefail
command -v git >/dev/null || { echo "03-advertise: git is missing" >&2; exit 1; }
command -v openssl >/dev/null || { echo "03-advertise: openssl is missing" >&2; exit 1; }
v=$(openssl version | awk '{print $2}')
case "$v" in
  [3-9].*) ;;
  *) echo "03-advertise: openssl $v has no Ed25519 pkeyutl -rawin; 3.0 or later is needed" >&2; exit 1 ;;
esac
echo "03-advertise: openssl $v, $(git --version)"
