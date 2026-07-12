# QLever (read-only · Wikidata-scale non-path)

**Why:** QLever indexes the *full* Wikidata (~10¹⁰ triples) on a single machine and answers SPARQL 1.1
fast. It is our **billion-scale non-path** datapoint — proof that the circuit CONSTRUCT plan runs on a
production-grade engine at the scale a VLDB reviewer expects.

## Capability boundary (read this first)
**Update (2026):** QLever added **SPARQL 1.1 Update** in ~2025 (full SPARQL 1.1 compliance; POST
`application/sparql-update`, `access-token` auth, `--persist-updates` for durability), so it is writable
*in principle*. **But its update path is slow (~10 ms/op) and OOM-prone at even ~8 M triples**
([ad-freiburg/qlever#2481](https://github.com/ad-freiburg/qlever/issues/2481)) — not a safe target for our
high-volume iterative path protocol at billion scale. So we run it **read-only by default** and treat it
as a fast read-optimized index (build offline, then query):
- ✅ **Non-path** queries (BGP / UNION / MINUS / OPTIONAL) — one-shot read-only CONSTRUCTs. Run these.
- ⚠️ **Property paths** — do **not** push them through raw bulk SPARQL UPDATE here (slow + #2481 OOM at
  scale). Use the **frontier-restricted / `VALUES`-inline** route (`README.md`, gap G1), which minimizes or
  eliminates writes — that is what makes billion-scale paths feasible on QLever, not the raw UPDATE
  endpoint. Until that route lands, `CircuitRun` refuses paths here with `CIRCUIT_READONLY=1` (exit 3).
- Use **Standard** reification (QLever's RDF-star support is limited). If you *do* enable updates, they need
  `--persist-updates` + an `access-token` (our `CircuitRun` does not send an auth token yet).

## 1. Build the index (offline, bulk)
```bash
# reify to N-Triples first:  python3 reference/watdiv/reify.py wikidata-truthy.nt > wd.reified.nt
docker run -it --rm -v /data/qlever:/index adfreiburg/qlever \
  IndexBuilderMain -f /index/wd.reified.nt -i /index/wd -s /index/wd.settings.json
```

## 2. Serve (query only)
```bash
docker run -d -p 7001:7001 -v /data/qlever:/index adfreiburg/qlever \
  ServerMain -i /index/wd -p 7001
#   query : http://localhost:7001     (no update endpoint)
```

## 3. Run the non-path circuit at scale
```bash
cd reference
python3 engines/run_engine.py --engine qlever --data wd.reified.ttl \
    --queries wikidata/WD-star.rq wikidata/WD-union.rq wikidata/WD-minus.rq wikidata/WD-opt.rq
# read-only path queries (WD-path*) are auto-skipped as 'skip-readonly-path'.

# or directly:
CIRCUIT_READONLY=1 \
  java -cp ../engine/target/npcs-rewrite.jar npcs.circuit.CircuitRun \
    Standard wd.reified.ttl wikidata/WD-star.rq http://localhost:7001
```
`CIRCUIT_READONLY=1` implies skip-load (the index is prebuilt) and blocks path queries.

## What to produce
Non-path `build_ms` / gates / answers on **full Wikidata** — the headline large-scale, stock-engine
construction number (E8). Note in the results which queries were skipped as read-only paths.
