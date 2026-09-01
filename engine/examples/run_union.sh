#!/usr/bin/env bash
# Run the monotonic UNION example for a chosen scheme.
set -euo pipefail
"$(dirname "$0")/run_query.sh" monotonic/union "${1:-Standard}"
