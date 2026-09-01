#!/usr/bin/env bash
# Run the monotonic AND example for a chosen scheme.
set -euo pipefail
"$(dirname "$0")/run_query.sh" monotonic/and "${1:-Standard}"
