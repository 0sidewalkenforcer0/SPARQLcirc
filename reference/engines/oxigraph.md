# Oxigraph (writable · RDF-star · independent Rust implementation)

**Why:** Oxigraph is a **fully independent** SPARQL 1.1 implementation written in **Rust** — not the
RDF4J/Java lineage that GraphDB and Fuseki share. Matching `circuit_sha256` here is the strongest form
of the engine-agnostic byte-identity result (Claim A): the circuit is a property of the *rewrite*, not
of any one engine family. MIT-licensed, single binary → also the easiest artifact-evaluation target.

## 1. Install + start a writable server
```bash
cargo install oxigraph-server            # or: docker run -p 7878:7878 ghcr.io/oxigraph/oxigraph serve ...
oxigraph serve --location /data/oxi --bind 127.0.0.1:7878
#   query  : http://localhost:7878/query
#   update : http://localhost:7878/update
#   upload : http://localhost:7878/store   (Graph Store Protocol)
```

## 2. Bulk-load the reified data
```bash
oxigraph load --location /data/oxi --file data.reified.nt
# or live:  curl -X POST -H 'Content-Type: application/n-triples' \
#                --data-binary @data.reified.nt http://localhost:7878/store?default
```
RDF-star is supported → a `.ttls` + `--scheme SPARQL_Star` avoids the 3× reification blow-up.

## 3. Run circuit construction
```bash
cd reference
python3 engines/run_engine.py --engine oxigraph --data watdiv/slice.reified.ttl \
    --queries watdiv/S-star.rq watdiv/M-minus.rq watdiv/P-plus.rq

# or directly (Oxigraph uses /update):
CIRCUIT_UPDATE_ENDPOINT=http://localhost:7878/update CIRCUIT_SKIP_LOAD=1 \
  java -cp ../engine/target/npcs-rewrite.jar npcs.circuit.CircuitRun \
    Standard watdiv/slice.reified.ttl watdiv/M-minus.rq http://localhost:7878/query
```
Writable → property paths work.

## What to produce
The three-way **byte-identity** check: `circuit_sha256` for GraphDB == Fuseki == **Oxigraph** on the same
query set. Same bytes on a Java triplestore, a Java TDB2 store, and an independent Rust store ⇒ the
circuit is engine-agnostic.
