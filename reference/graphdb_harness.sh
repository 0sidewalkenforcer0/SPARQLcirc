#!/usr/bin/env bash
# Run the flat engine-native circuit construction on a REAL deployed GraphDB:
# create a repo, load reified probabilistic data, POST our emitted CONSTRUCT, and
# get the shared provenance circuit back as N-Triples. Proves the method is
# engine-agnostic on a deployed triple store (not only in-memory RDF4J/rdflib).
#
# Prereqreq: GraphDB running on $GDB (start with, e.g.:
#   GDB_JAVA_OPTS="-Xmx2g -Dgraphdb.home=/tmp/gdbhome" \
#     <graphdb>/bin/graphdb -s   &   )
# Usage: ./graphdb_harness.sh <Standard|SPARQL_Star> data/drug.reified.ttl queries/drug3hop.sparql
set -euo pipefail
SCHEME="${1:-Standard}"; DATA="${2:-data/drug.reified.ttl}"; QUERY="${3:-queries/drug3hop.sparql}"
GDB="${GDB:-http://localhost:7200}"; REPO="${REPO:-prov}"
JAR="${JAR:-../engine/target/npcs-rewrite.jar}"
mkdir -p graphdb

cat > graphdb/${REPO}-config.ttl <<TTL
@prefix rep: <http://www.openrdf.org/config/repository#> .
@prefix sr:  <http://www.openrdf.org/config/repository/sail#> .
@prefix sail:<http://www.openrdf.org/config/sail#> .
@prefix graphdb: <http://www.ontotext.com/config/graphdb#> .
[] a rep:Repository ; rep:repositoryID "${REPO}" ; rdfs:label "${REPO}" ;
   rep:repositoryImpl [ rep:repositoryType "graphdb:SailRepository" ;
     sr:sailImpl [ sail:sailType "graphdb:Sail" ; graphdb:ruleset "empty" ] ] .
TTL

curl -s -X DELETE "$GDB/repositories/$REPO" >/dev/null 2>&1 || true
curl -s -f -X POST "$GDB/rest/repositories" -F "config=@graphdb/${REPO}-config.ttl" >/dev/null
curl -s -f -X POST "$GDB/repositories/$REPO/statements" -H 'Content-Type: text/turtle' --data-binary "@$DATA" >/dev/null
echo "loaded $(curl -s "$GDB/repositories/$REPO/size") triples into repo '$REPO'"

# extract our emitted CONSTRUCT (stderr = plan; stdout = circuit -> discard).
# NB: use explicit file redirection, not '2>&1 >/dev/null' (zsh MULTIOS tees both).
java -cp "$JAR" npcs.circuit.CircuitRun "$SCHEME" "$DATA" "$QUERY" >/dev/null 2>graphdb/plan.txt
awk '/^PREFIX c:/{p=1} /^# circuit triples/{p=0} p' graphdb/plan.txt > graphdb/construct.rq

curl -s -X POST "$GDB/repositories/$REPO" -H 'Content-Type: application/sparql-query' \
     -H 'Accept: application/n-triples' --data-binary @graphdb/construct.rq > graphdb/circuit.gdb.nt
echo "GraphDB emitted $(wc -l < graphdb/circuit.gdb.nt) circuit triples "\
"($(grep -c '<urn:circuit:Times>' graphdb/circuit.gdb.nt) Times, $(grep -c '<urn:circuit:Plus>' graphdb/circuit.gdb.nt) Plus)"
echo "-> compile + WMC with:  python3 -c \"...load graphdb/circuit.gdb.nt; compile_bdd...\""
