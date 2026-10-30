#!/usr/bin/env bash
# Install what step 07 (Ask) needs, exactly at its versions.lock pins: capsulectl and
# capsulectl-book from the evidencebook tag they pin (the asker's and the asked node's
# books, and the bundle verifier). steps/12-join.setup.sh builds both; this runs it.
#
#   steps/07-ask.setup.sh
set -euo pipefail
exec "$(cd "$(dirname "$0")" && pwd)/12-join.setup.sh"
