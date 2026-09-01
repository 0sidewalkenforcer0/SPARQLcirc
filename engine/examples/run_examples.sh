#!/usr/bin/env bash
# Run every example query under both reification schemes and print provenance.
set -euo pipefail
cd "$(dirname "$0")/.."
JAR=target/npcs-rewrite.jar
[ -f "$JAR" ] || { echo "build first: mvn -q package"; exit 1; }

for scheme in Standard SPARQL_Star; do
  data="examples/data/example.standard.ttl"
  [ "$scheme" = "SPARQL_Star" ] && data="examples/data/example.star.ttls"
  for q in monotonic/and monotonic/union nonmonotonic/minus nonmonotonic/optional; do
    echo "==================== $scheme :: $q ===================="
    java -cp "$JAR" npcs.RunExample "$scheme" "$data" "examples/queries/$q.sparql"
    echo
  done
done
