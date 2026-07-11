# Wikidata queries (large-scale, real-KG evaluation)

Matches the datasets SPARQLprov (942M dump, star/union/minus) and NPCS (WDBench, 2023 dump) used, so
our numbers are directly comparable — and adds **property paths** over Wikidata's transitive
hierarchies (`wdt:P279` subclass-of, `wdt:P131` located-in), which neither baseline can do.

**Data.** Use the Wikidata **truthy** dump (`wdt:` direct predicates) or the WDBench graph (matches
NPCS). Reify it (`reference/watdiv/reify.py` works on any N-Triples) and load into GraphDB.

**Prefixes.** `wd: <http://www.wikidata.org/entity/>`, `wdt: <http://www.wikidata.org/prop/direct/>`.

**Shapes.** BGP/star (`WD-star`), UNION (`WD-union`), MINUS (`WD-minus`), OPTIONAL (`WD-opt`) — the
non-monotone set SPARQLprov ran on Wikidata; plus PROPERTY PATHS `WD-path` (`Q7397 wdt:P279+ ?super`)
and `WD-path2` (`Q60 wdt:P131+ ?admin`) — single-source, **bounded reach**, so feasible under the
reachable-set-bounded loop. Paths run via `CircuitRun` (iterative); the rest via the one-shot flow.
