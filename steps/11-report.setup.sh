#!/usr/bin/env bash
# Install what step 11 (Report) needs, exactly at its versions.lock pins: capsulectl and
# capsulectl-book from the evidencebook tag they pin (the report builder, its page check and the
# bundle verifier). steps/12-join.setup.sh builds both; this runs it.
#
#   steps/11-report.setup.sh
set -euo pipefail
exec "$(cd "$(dirname "$0")" && pwd)/12-join.setup.sh"
