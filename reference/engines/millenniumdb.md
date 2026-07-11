# MillenniumDB (read-only · property-path SOTA on WDBench)

**Why:** MillenniumDB is purpose-built for **property-path** queries on Wikidata/WDBench and is the
current performance reference for exactly the recursive queries that are *our* contribution. Two roles:
1. **Non-path** circuit construction at WDBench/Wikidata scale (like QLever).
2. The **qualitative comparison** for paths: MillenniumDB computes path *answers* fast but no *provenance*;
   we compute the provenance circuit. It is the natural target for our path queries once read-only paths
   are supported.

## Capability boundary
Read-optimized (bulk import, then query) — same constraint as QLever:
- ✅ **Non-path** queries — read-only CONSTRUCTs.
- ❌ **Property paths** via the current iterative INSERT loop → refused with `CIRCUIT_READONLY=1`.
  This is the engine we most want the **VALUES-inline read-only path loop** for (see `README.md`); until
  then, run the bounded `P279+`/`P131+` paths on GraphDB/Fuseki and cite MillenniumDB for path *answer*
  performance.
- Use **Standard** reification.

## 1. Build + import (bulk, offline)
```bash
git clone https://github.com/MillenniumDB/MillenniumDB && cd MillenniumDB && ./build.sh
# import reified N-Triples into a MillenniumDB database
build/Release/bin/mdb-import --rdf wd.reified.nt /data/mdb-wd
```

## 2. Serve
```bash
build/Release/bin/mdb-server /data/mdb-wd --port 1234
#   sparql : http://localhost:1234/sparql   (verify your build exposes the SPARQL endpoint;
#                                             older builds speak MQL only)
```

## 3. Run the non-path circuit
```bash
cd reference
python3 engines/run_engine.py --engine millenniumdb --data wd.reified.ttl \
    --query-endpoint http://localhost:1234/sparql \
    --queries wikidata/WD-star.rq wikidata/WD-minus.rq wikidata/WD-opt.rq
# WD-path* auto-skipped (read-only).
```

## What to produce
- Non-path `build_ms` / gates / answers at WDBench scale.
- A one-line qualitative note: MillenniumDB returns path answers; we return the path **provenance
  circuit** (WMC-ready) — the capability neither it nor the other baselines have.
