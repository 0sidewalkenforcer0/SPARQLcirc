# E7 — ProvSQL head-to-head harness

ProvSQL (Senellart et al.) is the closest baseline in spirit: it also builds a
**provenance circuit** and does exact probability by **knowledge compilation** — but
inside a **modified PostgreSQL** and over **relations**, not on an unmodified SPARQL
engine over RDF. E7 compares *time & space to the exact answer probability*, and
records the qualitative axis that is our actual advantage: **no engine modification**.

> Requires a PostgreSQL with the ProvSQL extension. Nothing here executes on this
> machine; this is the harness spec + a tested data loader.

## Setup

```sql
-- schema.sql
CREATE EXTENSION IF NOT EXISTS provsql;
CREATE TABLE triples (s text, p text, o text);
-- load rows (rdf_to_sql.py emits the INSERTs), then:
SELECT add_provenance('triples');                 -- one provenance gate per triple (= our leaf/token)
-- probabilities: give each triple a probability and register it
ALTER TABLE triples ADD COLUMN proba double precision;
-- UPDATE triples SET proba = ... ;   then per row: SELECT set_prob(provenance(...), proba)
```

`add_provenance` makes each base tuple a provenance variable — exactly our leaf token.
A query's result tuples then carry a provenance circuit ProvSQL builds as it evaluates.

## Relational mapping (RDF → relations)

Our reified KG becomes one `triples(s,p,o)` table (provenance per row = our token).
`rdf_to_sql.py` converts a reified `.ttl` (the `:tN rdf:subject/predicate/object` form)
into `INSERT`s. A SPARQL BGP is a **self-join of `triples`**; the operators map as:

| SPARQL | SQL over `triples` (ProvSQL tracks the circuit) |
|---|---|
| `?x :p ?y` (triple) | `SELECT s AS x, o AS y FROM triples WHERE p='p'` |
| BGP / AND (`⊗`) | join the per-pattern subqueries on shared vars (ProvSQL: ⊗) |
| projection (`⊕`) | `GROUP BY` the projected columns (ProvSQL: ⊕ over derivations) |
| UNION (`⊕`) | `... UNION ...` |
| OPTIONAL | `LEFT JOIN` |
| MINUS (`⊖`) | `EXCEPT` / `WHERE NOT EXISTS` (ProvSQL's m-semiring gives the monus) |

Example — the 2-hop join `?x :knows ?y . ?y :knows ?z` projecting `?z`:

```sql
SELECT t2.o AS z
FROM   triples t1, triples t2
WHERE  t1.p='knows' AND t2.p='knows' AND t1.o = t2.s
GROUP BY t2.o;
```

`bgp_to_sql()` in `rdf_to_sql.py` generates exactly this shape for path/star BGPs
(the E7 query set). UNION/OPTIONAL/MINUS use the templates above.

## Exact probability + what to measure

```sql
SELECT z, probability_evaluate(provenance(), 'weightmc') FROM ( <query> ) q;
-- or the d4-backed method, to match OUR compiler backend for a fair PQE comparison
```

Per query, record:

| metric | ProvSQL | SPARQL_circ (ours) |
|---|---|---|
| build (provenance circuit construction) | in-PostgreSQL query eval | engine runs our CONSTRUCT |
| compile + WMC (to exact probability) | `probability_evaluate` | `compile_bdd`/`d4` + WMC |
| total time to probability | sum | sum |
| peak memory | PG backend | client |
| **requires engine modification?** | **yes (PG extension)** | **no (stock SPARQL 1.1)** |

## Predicted result (before running)

- **PQE time comparable** when both use the same d4 backend — the circuits encode the
  same Boolean function, so compile+WMC is essentially the same work. **Do not claim we
  count faster.**
- **Construction:** ProvSQL builds inside a bespoke PG engine; we build on any stock
  triplestore. Timings are engine-dependent and not the point.
- **The win is deployability (axis A):** exact PQE, same tractability, with **no fork of
  the database** and **native RDF/SPARQL** (no relational remodeling). Frame E7 as "same
  exactness and tractability class, without touching the engine," not a speed race.

## Fair-comparison notes

- Use the **same probabilities** and the **same compiler** (d4) on both sides.
- Match query semantics exactly (bag vs set; ProvSQL default is bag — align with our set
  semantics or report both).
- Report ProvSQL's own circuit size next to ours for the same query (both are DAGs of
  ⊗/⊕/⊖ over the same tokens; sizes should be close — a sanity check that the mapping is
  faithful).
