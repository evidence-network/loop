#!/usr/bin/env bash
# Install what step 10 (Judge) needs, exactly at its versions.lock pins: capsulectl and
# capsulectl-book from the evidencebook tag they pin (the scratch book the Results are sealed
# in, and the bundle verifier). steps/12-join.setup.sh builds both; this runs it.
#
#   steps/10-judge.setup.sh
set -euo pipefail
exec "$(cd "$(dirname "$0")" && pwd)/12-join.setup.sh"
