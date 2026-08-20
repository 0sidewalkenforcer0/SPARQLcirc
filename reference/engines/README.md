# Multi-engine study — running SPARQL_circ on stock SPARQL engines

Claim A ("an **unmodified** SPARQL engine builds the shared circuit") is only as strong as the number
of independent engines we demonstrate it on. The baselines each used two engines
(**SPARQLprov**: Virtuoso + Jena Fuseki · **NPCS**: GraphDB + Stardog); we run on those *and* on newer
high-performance engines. Because our circuit is **content-addressed**, the *same* query must produce
the *same* circuit on every engine — the `circuit_sha256` equality check below is the engine-agnostic
byte-identity result (E3), now across independent implementations.

## Engine matrix

| engine | role | writable? | RDF-star? | paths? | scale | reification |
|---|---|:--:|:--:|:--:|---|---|
| **GraphDB** | our primary; shared w/ NPCS | ✅ | ✅ | ✅ | 10⁹ | Standard / SPARQL\* |
| **Fuseki** (Jena) | shared w/ SPARQLprov | ✅ | ✅ | ✅ | ~10⁹ | Standard / SPARQL\* |
| **Oxigraph** (Rust) | independent lineage → byte-identity | ✅ | ✅ | ✅ | ~10⁸ | Standard / SPARQL\* |
| **QLever** | Wikidata-scale non-path | ⚠ update² | ✗ | ❌* | 10¹⁰ | Standard |
| **MillenniumDB** | property-path SOTA (WDBench) | ❌ read-only | ✗ | ❌* | 10⁹ | Standard |
| Virtuoso | baseline (SPARQLprov primary) | ✅ | ✗ | ✅ | 10¹⁰ | Standard |
| Stardog | baseline (NPCS) | ✅ | ✅ | ✅ | 10⁹ | Standard / SPARQL\* |

\* read-only engines run property paths only once the VALUES-inline loop lands (see below).

² QLever gained **SPARQL 1.1 Update** (~2025), so it is writable in principle — but its update path is
slow (~10 ms/op) and OOM-prone at even ~8 M triples ([ad-freiburg/qlever#2481](https://github.com/ad-freiburg/qlever/issues/2481)),
so we **default it read-only** and route paths through the frontier-restricted / `VALUES`-inline loop, not
raw bulk UPDATE. (Updates also need `--persist-updates` + an `access-token`.)

**Reification schemes** (the `--scheme` / first `CircuitRun` arg): `Standard` (plain rdf:subject/…triples,
loadable anywhere), `SPARQL_Star` (compact, needs an RDF-star engine — avoids the 3× blow-up), and
`Wikidata` (the server-added NPCS-matching Wikidata statement model — also plain triples). **For Wikidata
data use `--scheme Wikidata`**: it matches NPCS's reification *and*, being plain-triple, loads on the
read-only engines (QLever/MillenniumDB) that have no RDF-star support.

## The one constraint: writable vs read-only

- **Non-path** queries (BGP / UNION / MINUS / OPTIONAL) are a **read-only** multi-CONSTRUCT plan → run on
  **any** SPARQL 1.1 engine, including read-only ones, at full scale.
- **Property paths** use a **client-driven iterative fixpoint** that **INSERTs each round's reach gates
  back** so the next CONSTRUCT can match them → needs a **writable** endpoint.
  On read-only engines (MillenniumDB, and QLever by default²) `CircuitRun` refuses path queries with a clear error (exit 3).
  - **Planned read-only path route** (not yet implemented): instead of INSERTing each round's gates,
    **inline** the prior round's reach gates via a `VALUES` block in the next CONSTRUCT. For our
    *bounded-reach* path queries (Wikidata `P279+`/`P131+`, small |V_s|) that is cheap and would unlock
    QLever/MillenniumDB — the latter being the natural target since it is *built* for path queries.

## How `CircuitRun` selects an engine (the interface)

`CircuitRun <Standard|SPARQL_Star> <dataFile> <queryFile> <queryEndpointURL>` plus env vars:

| env var | meaning | default |
|---|---|---|
| `CIRCUIT_UPDATE_ENDPOINT` | SPARQL UPDATE URL | `<endpoint>/statements` (GraphDB/RDF4J) |
| `CIRCUIT_SKIP_LOAD=1` | data already bulk-loaded (don't INSERT the file) | off (loads via `con.add`) |
| `CIRCUIT_READONLY=1` | engine has no UPDATE (query-only); implies skip-load; refuses paths | off |
| `CIRCUIT_CLEANUP=1` | remove emitted gates after a run; **scratch endpoint only** | off |

`CIRCUIT_CLEANUP=1` is unsafe on a shared or long-lived content-addressed circuit store: another query
may rely on an identical Times/answer gate, and concurrent path runs may still need persisted reach state.
Use per-run named graphs or reference counting for a persistent multi-circuit store; do not enable this
flag there.

The update-endpoint convention differs per engine — GraphDB `/statements`, Fuseki/Oxigraph `/update`,
Virtuoso `/sparql`. `engines.json` records each; `run_engine.py` sets the env vars for you.

## Running it

1. Deploy an engine and **bulk-load the mixed data** (per-engine doc). Use
   `reference/watdiv/reify.py` to retain each asserted triple and add its token
   record. RDF-star engines can use `--star` for the compact token encoding.
2. Edit the endpoints in `engines.json` (or pass `--query-endpoint`/`--update-endpoint`).
3. Run:
   ```bash
   cd reference
   python3 engines/run_engine.py --engine fuseki --data watdiv/slice.reified.ttl \
       --queries watdiv/S-star.rq watdiv/M-minus.rq watdiv/P-plus.rq
   ```
   → `engines/results_fuseki.csv` with `build_ms`, gate counts, answers, and `circuit_sha256`.

## The two headline cross-engine results to produce

- **Byte-identity (Claim A).** Run the *same* WatDiv slice + query set on **GraphDB + Fuseki + Oxigraph**
  (three independent codebases, one of them non-Java). The `circuit_sha256` column must be **identical**
  across all three per query. (Order-independent: hashes the sorted triple set.) Measured results:
  [`RESULTS.md`](RESULTS.md) at 10 M, [`../paper/rq3/BYTEID_100M_SUMMARY.md`](../paper/rq3/BYTEID_100M_SUMMARY.md) at 100 M.
- **Scale (E8).** Run the **non-path** queries on **QLever** (or Virtuoso) at full-Wikidata scale, and the
  **bounded paths** (`P279+`/`P131+`) on a writable engine (GraphDB/Fuseki). See `../wikidata/`.

---

# Per-engine setup

Every engine below is driven the same way: bulk-load mixed data (`python3 reference/watdiv/reify.py
in.nt data.mixed.nt`), then either `engines/run_engine.py --engine <name>` (reads `engines.json`)
or `CircuitRun` directly with the env vars above. Only the endpoint conventions and the
writable/read-only boundary differ.

The ordinary Java scheme names expect mixed data. Historical token-only stores
must instead be queried with `Standard_Pure` or `SPARQL_Star_Pure`.

## Fuseki (Apache Jena) — writable · RDF-star · SPARQLprov's engine

One of the two engines **SPARQLprov** ran on (apples-to-apples), and a second Java-but-independent
codebase for the byte-identity check.

```bash
cd pilot/tools/apache-jena-fuseki-5.2.0        # or download Fuseki 5.x
./fuseki-server --update --loc=/data/fuseki-tdb2 /ds
#   query http://localhost:3030/ds/query · update .../update · upload .../data (GSP)

./tdb2.tdbloader --loc=/data/fuseki-tdb2 data.reified.nt     # offline bulk load
# small data can be POSTed live to .../data instead

cd reference
python3 engines/run_engine.py --engine fuseki --data watdiv/slice.reified.ttl \
    --queries watdiv/S-star.rq watdiv/M-minus.rq watdiv/P-plus.rq
# or directly (note Fuseki's /update convention):
CIRCUIT_UPDATE_ENDPOINT=http://localhost:3030/ds/update CIRCUIT_SKIP_LOAD=1 \
  java -cp ../engine/target/npcs-rewrite.jar npcs.circuit.CircuitRun \
    Standard watdiv/slice.reified.ttl watdiv/P-plus.rq http://localhost:3030/ds/query
```

Writable, so property paths run here. An RDF-star `.ttls` + `--scheme SPARQL_Star` avoids the 3×
reification blow-up.

## Oxigraph — writable · RDF-star · independent Rust implementation

A **fully independent** SPARQL 1.1 implementation in **Rust**, not the RDF4J/Java lineage GraphDB and
Fuseki share, so matching `circuit_sha256` here is the strongest form of the byte-identity result: the
circuit is a property of the *rewrite*, not of one engine family. MIT-licensed single binary, also the
easiest artifact-evaluation target.

```bash
cargo install oxigraph-server        # or ghcr.io/oxigraph/oxigraph
oxigraph serve --location /data/oxi --bind 127.0.0.1:7878
#   query http://localhost:7878/query · update .../update · upload .../store (GSP)

oxigraph load --location /data/oxi --file data.reified.nt

cd reference
python3 engines/run_engine.py --engine oxigraph --data watdiv/slice.reified.ttl \
    --queries watdiv/S-star.rq watdiv/M-minus.rq watdiv/P-plus.rq
```

Writable → property paths work. RDF-star supported.

## QLever — read-only by default · Wikidata-scale non-path

QLever indexes the *full* Wikidata (~10¹⁰ triples) on one machine, so it is the **billion-scale
non-path** datapoint: proof the CONSTRUCT plan runs on a production-grade engine at reviewer-expected
scale.

**Capability boundary.** QLever gained SPARQL 1.1 Update in ~2025, but the update path is slow
(~10 ms/op) and OOM-prone at even ~8 M triples
([ad-freiburg/qlever#2481](https://github.com/ad-freiburg/qlever/issues/2481)), so we run it read-only
and treat it as a fast read-optimized index. Non-path queries are one-shot read-only CONSTRUCTs; paths
must go through the frontier-restricted / `VALUES`-inline route rather than raw bulk UPDATE. Use
**Standard** reification (RDF-star support is limited); enabling updates additionally needs
`--persist-updates` and an `access-token` that `CircuitRun` does not send yet.

```bash
docker run -it --rm -v /data/qlever:/index adfreiburg/qlever \
  IndexBuilderMain -f /index/wd.reified.nt -i /index/wd -s /index/wd.settings.json
docker run -d -p 7001:7001 -v /data/qlever:/index adfreiburg/qlever ServerMain -i /index/wd -p 7001

cd reference
python3 engines/run_engine.py --engine qlever --data wd.reified.ttl \
    --queries wikidata/WD-star.rq wikidata/WD-union.rq wikidata/WD-minus.rq wikidata/WD-opt.rq
# WD-path* are auto-skipped as 'skip-readonly-path'; CIRCUIT_READONLY=1 implies skip-load.
```

## MillenniumDB — read-only · property-path SOTA on WDBench

Purpose-built for **property-path** queries on Wikidata/WDBench and the current performance reference
for exactly the recursive queries that are our contribution. Two roles: non-path circuit construction at
WDBench scale (like QLever), and the qualitative path comparison — MillenniumDB computes path *answers*
fast but no *provenance*; we compute the provenance circuit.

```bash
git clone https://github.com/MillenniumDB/MillenniumDB && cd MillenniumDB && ./build.sh
build/Release/bin/mdb-import --rdf wd.reified.nt /data/mdb-wd
build/Release/bin/mdb-server /data/mdb-wd --port 1234
#   sparql http://localhost:1234/sparql  (older builds speak MQL only — verify your build)

cd reference
python3 engines/run_engine.py --engine millenniumdb --data wd.reified.ttl \
    --query-endpoint http://localhost:1234/sparql \
    --queries wikidata/WD-star.rq wikidata/WD-minus.rq wikidata/WD-opt.rq
```

Read-only, Standard reification, paths refused with `CIRCUIT_READONLY=1` (exit 3). This is the engine we
most want the VALUES-inline read-only path loop for; until then run bounded `P279+`/`P131+` on
GraphDB/Fuseki and cite MillenniumDB for path *answer* performance.
