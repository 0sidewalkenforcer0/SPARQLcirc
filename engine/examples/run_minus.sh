#!/usr/bin/env bash
# Run the nonmonotonic MINUS example for a chosen scheme.
set -euo pipefail
"$(dirname "$0")/run_query.sh" nonmonotonic/minus "${1:-Standard}"
