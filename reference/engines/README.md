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
| **QLever** | Wikidata-scale non-path | ❌ read-only | ✗ | ❌* | 10¹⁰ | Standard |
| **MillenniumDB** | property-path SOTA (WDBench) | ❌ read-only | ✗ | ❌* | 10⁹ | Standard |
| Virtuoso | baseline (SPARQLprov primary) | ✅ | ✗ | ✅ | 10¹⁰ | Standard |
| Stardog | baseline (NPCS) | ✅ | ✅ | ✅ | 10⁹ | Standard / SPARQL\* |

\* read-only engines run property paths only once the VALUES-inline loop lands (see below).

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
  On read-only engines (QLever, MillenniumDB) `CircuitRun` refuses path queries with a clear error (exit 3).
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

The update-endpoint convention differs per engine — GraphDB `/statements`, Fuseki/Oxigraph `/update`,
Virtuoso `/sparql`. `engines.json` records each; `run_engine.py` sets the env vars for you.

## Running it

1. Deploy an engine and **bulk-load the reified data** (per-engine doc). Use `reference/watdiv/reify.py`
   to reify any `.nt`. RDF-star engines can instead load the compact SPARQL\* form (`.ttls`).
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
  across all three per query. (Order-independent: hashes the sorted triple set.)
- **Scale (E8).** Run the **non-path** queries on **QLever** (or Virtuoso) at full-Wikidata scale, and the
  **bounded paths** (`P279+`/`P131+`) on a writable engine (GraphDB/Fuseki). See `../wikidata/`.

Per-engine setup + exact commands: [`fuseki.md`](fuseki.md) · [`oxigraph.md`](oxigraph.md) ·
[`qlever.md`](qlever.md) · [`millenniumdb.md`](millenniumdb.md).
