#!/usr/bin/env bash
# Install what step 04 (Admit) needs, at its versions.lock pins: capsulectl and
# capsulectl-book, which steps/12-join.setup.sh builds from the evidencebook tag
# they pin. The buyer's book and the bundle check run on them. openssl 3 (the
# stand-ins' Ed25519 signatures) and python3 come from the host.
#
#   steps/04-admit.setup.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
command -v openssl >/dev/null || { echo "04-admit.setup: openssl is not installed" >&2; exit 1; }
"$ROOT/steps/12-join.setup.sh"
