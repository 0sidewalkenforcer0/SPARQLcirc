#!/usr/bin/env bash
# NPCS-method demo on the OPTIONAL running example.
#
# Shows, end to end:  input query + reified data
#                  -> NPCS rewrite (a SELECT that CONCATs a provenance string per answer)
#                  -> executed result  (answer | provenance polynomial in ⊗/⊕/⊖).
#
# This is the NPCS method (string provenance, computed natively by the SPARQL engine) —
# NOT our circuit. Compare with the circuit output via:
#   java -cp target/npcs-rewrite.jar npcs.circuit.CircuitRun Standard \
#        examples/gallery/gallery.ttl examples/gallery/optional.sparql
set -e
cd "$(dirname "$0")/.."                 # -> engine/
JAR=target/npcs-rewrite.jar
[ -f "$JAR" ] || mvn -q package
DATA=examples/gallery/gallery.ttl
Q=examples/gallery/optional.sparql

echo "===== INPUT · query ====="
cat "$Q"
echo
echo "===== INPUT · data (reified; relevant triples) ====="
grep -E 'knows|city' "$DATA"
echo
echo "===== NPCS rewrite + result (Standard reification) ====="
java -cp "$JAR" npcs.RunExample Standard "$DATA" "$Q"
