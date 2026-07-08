#!/usr/bin/env bash
# Launch the AND example under JDWP, suspended, so a debugger can attach.
# Usage: examples/debug_and.sh [Standard|SPARQL_Star]
# Then in VS Code pick the "Attach to RunExample :5005" launch config.
set -euo pipefail
cd "$(dirname "$0")/.."
JAR=target/npcs-rewrite.jar
[ -f "$JAR" ] || { echo "build first: mvn -q package"; exit 1; }

scheme="${1:-Standard}"
data="examples/data/example.standard.ttl"
[ "$scheme" = "SPARQL_Star" ] && data="examples/data/example.star.ttls"

echo ">>> JDWP listening on :5005 (suspend=y). Attach the debugger to start execution."
exec java \
  -agentlib:jdwp=transport=dt_socket,server=y,suspend=y,address=localhost:5005 \
  -cp "$JAR" npcs.RunExample "$scheme" "$data" examples/queries/monotonic/and.sparql
