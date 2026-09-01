#!/usr/bin/env bash
# Run a single example query under a chosen reification scheme and print provenance.
set -euo pipefail
cd "$(dirname "$0")/.."
JAR=target/npcs-rewrite.jar
[ -f "$JAR" ] || { echo "build first: mvn -q package"; exit 1; }

if [ $# -lt 1 ]; then
  echo "Usage: $0 <query-name> [scheme]" >&2
  echo "Query name: monotonic/and | monotonic/union | nonmonotonic/minus | nonmonotonic/optional" >&2
  echo "Scheme: Standard (default) or SPARQL_Star" >&2
  exit 1
fi

query="$1"
scheme="${2:-Standard}"
data="examples/data/example.standard.ttl"
[ "$scheme" = "SPARQL_Star" ] && data="examples/data/example.star.ttls"

echo "==================== $scheme :: $query ===================="
java -cp "$JAR" npcs.RunExample "$scheme" "$data" "examples/queries/$query.sparql"
echo
