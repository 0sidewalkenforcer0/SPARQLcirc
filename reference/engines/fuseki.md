# Apache Jena Fuseki (writable · RDF-star · SPARQLprov's engine)

**Why:** one of the two engines **SPARQLprov** ran on → apples-to-apples; and a second Java-but-independent
codebase for the byte-identity check. Already downloaded at `pilot/tools/apache-jena-fuseki-5.2.0`.

## 1. Start a writable, persistent dataset
```bash
cd pilot/tools/apache-jena-fuseki-5.2.0           # or download Fuseki 5.x
# TDB2-backed, updatable dataset named /ds
./fuseki-server --update --loc=/data/fuseki-tdb2 /ds
#   query  : http://localhost:3030/ds/query
#   update : http://localhost:3030/ds/update
#   upload : http://localhost:3030/ds/data   (Graph Store Protocol)
```

## 2. Bulk-load the reified data (offline is fastest at scale)
```bash
# reify first if needed:  python3 reference/watdiv/reify.py in.nt > data.reified.nt
./tdb2.tdbloader --loc=/data/fuseki-tdb2 data.reified.nt
# small data can instead be POSTed live:
#   curl -X POST -H 'Content-Type: application/n-triples' --data-binary @data.reified.nt \
#        http://localhost:3030/ds/data
```
RDF-star alternative (compact, no reification blow-up): load a `.ttls` and use `--scheme SPARQL_Star`.

## 3. Run circuit construction
```bash
cd reference
# via the driver (sets the env vars from engines.json):
python3 engines/run_engine.py --engine fuseki --data watdiv/slice.reified.ttl \
    --queries watdiv/S-star.rq watdiv/M-minus.rq watdiv/P-plus.rq

# or CircuitRun directly (note the Fuseki /update convention):
CIRCUIT_UPDATE_ENDPOINT=http://localhost:3030/ds/update CIRCUIT_SKIP_LOAD=1 \
  java -cp ../engine/target/npcs-rewrite.jar npcs.circuit.CircuitRun \
    Standard watdiv/slice.reified.ttl watdiv/P-plus.rq http://localhost:3030/ds/query
```
Fuseki is **writable**, so property-path queries (`P-*`) work here. Drop `CIRCUIT_SKIP_LOAD` only if you
want CircuitRun to INSERT a small data file itself.

## What to produce
- The **byte-identity** row: same queries as on GraphDB + Oxigraph → `circuit_sha256` must match all three.
- Optionally the SPARQLprov apples-to-apples note: same engine, but we emit a shared circuit + PQE, not
  per-answer strings.
