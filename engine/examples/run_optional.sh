#!/usr/bin/env bash
# Run the nonmonotonic OPTIONAL example for a chosen scheme.
set -euo pipefail
"$(dirname "$0")/run_query.sh" nonmonotonic/optional "${1:-Standard}"
