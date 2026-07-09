# How SPARQL_circ works — a detailed walkthrough

This document explains the **mechanism** end to end, on one concrete running example, showing the
*real* artifact at every stage (query → rewritten CONSTRUCT → materialized circuit → probabilities).
Everything below is produced by the tools in this repo; nothing is hand-authored. Companion:
`TECHREPORT.md` (the reference), `reference/workflow.html` (the visual version).

---

## 0. The pipeline in one line

```
SELECT query
   │  γ rewrite  (client, ~ms)                         ← our contribution
   ▼
a CONSTRUCT query
   │  run on an UNMODIFIED SPARQL engine (RDF4J / GraphDB)
   ▼
a provenance CIRCUIT, returned as an RDF graph (⊕/⊗/⊖ gates, shared)
   │  compile (OBDD / d-DNNF) + weighted model count  (client)
   ▼
exact probability for every answer
```

The engine does nothing special — it just evaluates a normal CONSTRUCT. All the "provenance
intelligence" is in *what query we ask it to run*.

---

## 1. The running example

A drug-interaction KG. Each `interactsWith` (`iw`) edge is an extracted fact with a probability, and
each edge is given a **token** `p1…p8` (its identity):

```
p1 Aspirin  →iw→ Warfarin    Pr .92        p5 Ibuprofen →iw→ Metformin   .71
p2 Warfarin →iw→ Metformin   .87           p6 Warfarin  →iw→ Lisinopril  .65
p3 Metformin→iw→ Omeprazole  .85  ← shared p7 Lisinopril→iw→ Clopidogrel .60
p4 Aspirin  →iw→ Ibuprofen   .78           p8 Clopidogrel→iw→Aspirin     .55
```

The data is stored **reified** so a triple pattern can bind a token:

```
d:p1 rdf:subject d:Aspirin ; rdf:predicate d:iw ; rdf:object d:Warfarin .      (…p2…p8…)
```

The query — "which drugs does Aspirin reach in exactly three hops?":

```sparql
PREFIX d: <urn:d:>
SELECT ?z WHERE { d:Aspirin d:iw ?x . ?x d:iw ?y . ?y d:iw ?z . }
```

Two answers, and here is the whole point:
- **Clopidogrel** — one path `Aspirin→Warfarin→Lisinopril→Clopidogrel` = `p1 ⊗ p6 ⊗ p7`.
- **Omeprazole** — **two** paths that **share the edge `p3`**:
  `Aspirin→Warfarin→Metformin→Omeprazole` (`p1⊗p2⊗p3`) and
  `Aspirin→Ibuprofen→Metformin→Omeprazole` (`p4⊗p5⊗p3`).

**The trap.** If you computed Omeprazole's probability as if the two paths were independent, you'd get
`1−(1−.92·.87·.85)(1−.78·.71·.85) = 0.830814` — **wrong**, because both paths need `p3`, so they are
*correlated*. The correct value factors `p3` out: `P(p3)·P((p1∧p2)∨(p4∧p5)) = .85·.91094 = 0.774298`.
Getting this right is exactly what the *shared* circuit buys us.

---

## 2. Stage A–B: reify, and name every match (the NPCS skeleton `β`)

First we reuse NPCS's rewriting idea: turn each triple pattern into its reified form and bind the
matching statement's token to a fresh `?fprovN`. A join of the three patterns is a **product** `⊗` of
their tokens; grouping the results per answer is a **sum** `⊕`. NPCS emits this as a *string* built
with `CONCAT`. The real output of our `NpcsRewriter` on the query above:

```sparql
SELECT ?z (CONCAT("⊕(", GROUP_CONCAT(?fjoin0), ")") AS ?prov) WHERE {
  ?fprov0 rdf:subject d:Aspirin ; rdf:predicate d:iw ; rdf:object ?x .
  ?fprov1 rdf:subject ?x        ; rdf:predicate d:iw ; rdf:object ?y .
  ?fprov2 rdf:subject ?y        ; rdf:predicate d:iw ; rdf:object ?z .
  BIND(CONCAT("(⊗", STR(?fprov0), ",", STR(?fprov1), ",", STR(?fprov2), ",", ")") AS ?fjoin0)
} GROUP BY ?z
```

Run on the data it yields, per answer, a provenance **string**, e.g. for Omeprazole
`⊕( (⊗p1,p2,p3) (⊗p4,p5,p3) )`. This is where NPCS stops. The limitation we remove:

1. it's a **string per answer** — the shared `p3` is written twice and sharing *across* answers
   (`p1`) is invisible;
2. the string grows with the number of derivations (exponential on deep/cyclic data);
3. to get a **probability** you'd have to parse the string back into a factored form.

So we keep the reify-and-name skeleton but change *what the operators emit*.

---

## 3. Stage C: `γ` — emit **gates**, not text (a CONSTRUCT)

`γ` replaces `ProvProd/ProvAggSum/ProvDiff` with **gate constructors** and emits a `CONSTRUCT`. Here is
the *actual* query `CircuitRewriter` produces for the 3-hop BGP (verbatim):

```sparql
PREFIX c: <urn:circuit:>
CONSTRUCT {
  ?t a c:Times ; c:in ?a0 ; c:in ?a1 ; c:in ?a2 ; c:feeds ?ans .
  ?ans a c:Plus ; c:answer ?anskey .
}
WHERE {
  ?a0 rdf:subject d:Aspirin ; rdf:predicate d:iw ; rdf:object ?x .
  ?a1 rdf:subject ?x        ; rdf:predicate d:iw ; rdf:object ?y .
  ?a2 rdf:subject ?y        ; rdf:predicate d:iw ; rdf:object ?z .
  # --- content-addressed ⊗ id: hash each token, SORT the hashes, hash the sorted tuple ---
  BIND(SHA256(STR(?a0)) AS ?srt0)  BIND(SHA256(STR(?a1)) AS ?srt1)  BIND(SHA256(STR(?a2)) AS ?srt2)
  BIND(IF(?srt0 <= ?srt1, ?srt0, ?srt1) AS ?srt3)   # comparator
  BIND(IF(?srt0 <= ?srt1, ?srt1, ?srt0) AS ?srt4)   # network
  BIND(IF(?srt4 <= ?srt2, ?srt4, ?srt2) AS ?srt5)   # (bubble sort,
  BIND(IF(?srt4 <= ?srt2, ?srt2, ?srt4) AS ?srt6)   #  pure SPARQL 1.1)
  BIND(IF(?srt3 <= ?srt5, ?srt3, ?srt5) AS ?srt7)
  BIND(IF(?srt3 <= ?srt5, ?srt5, ?srt3) AS ?srt8)
  BIND(CONCAT("A", "|z=", STR(?z)) AS ?anskey)
  BIND(IRI(CONCAT("urn:g:t:", SHA256(CONCAT("T","|",?srt7,"|",?srt8,"|",?srt6)))) AS ?t)
  BIND(IRI(CONCAT("urn:g:a:", SHA256(?anskey))) AS ?ans)
}
```

Three mechanisms are doing the work here:

**(a) The template builds gates as RDF.** For each solution of the BGP, the `CONSTRUCT` emits a
`c:Times` node with a `c:in` edge to each matched token, feeding a `c:Plus` node that is the answer.
The leaves need no work — `?a0` is bound to the token IRI (`urn:d:p1`), so *the token is its own leaf
node*.

**(b) Content-addressed gate IRIs — this is how sharing happens.** Instead of a fresh id per gate,
each gate's IRI is a **hash of its meaning**: `IRI("urn:g:t:" + SHA256(key))` for a product,
`IRI("urn:g:a:" + SHA256(answerKey))` for an answer. Two products with the same children get the
**same** IRI; two derivations of the same answer compute the **same** `?ans`. When the engine writes
these triples, RDF set-semantics collapses the duplicates — **so sharing is produced by the engine,
automatically, with no bookkeeping on our side.**

**(c) The comparator network makes the ⊗ id canonical.** A product's id must depend on its child
*multiset*, not their textual order (otherwise `⊗(p1,p2,p3)` and a re-ordered derivation would look
like different gates). So we SHA256 each token to fixed-width hex, **sort the hashes with a
comparator (bubble-sort) network written as `BIND(IF(?a<=?b,…))`**, and hash the sorted tuple. Fixed
width makes the delimiter safe → the id is a collision-free function of the multiset. (This closes a
real hole in NPCS's naive string concatenation.)

**(d) The answer key** `"A|z=<value>"` encodes the projected binding; it is what makes the answer ⊕
content-addressed and, later, lets us recover the SELECT row.

---

## 4. Stage D: the engine materializes the circuit

Running that CONSTRUCT on an unmodified engine returns the circuit as **19 N-Triples** (hashes
abbreviated to 4 hex; the two answers are `e357…`=Omeprazole, `8c73…`=Clopidogrel):

```
g:t:a977…  a c:Times ; c:in d:p1 ; c:in d:p2 ; c:in d:p3 ; c:feeds g:a:e357…     # ⊗(p1,p2,p3)
g:t:73f8…  a c:Times ; c:in d:p4 ; c:in d:p5 ; c:in d:p3 ; c:feeds g:a:e357…     # ⊗(p4,p5,p3)
g:t:7124…  a c:Times ; c:in d:p1 ; c:in d:p6 ; c:in d:p7 ; c:feeds g:a:8c73…     # ⊗(p1,p6,p7)
g:a:e357…  a c:Plus  ; c:answer "A|z=urn:d:Omeprazole"
g:a:8c73…  a c:Plus  ; c:answer "A|z=urn:d:Clopidogrel"
```

As a DAG:

```
     Omeprazole (⊕ e357…)                 Clopidogrel (⊕ 8c73…)
        /          \                             |
   ⊗(p1,p2,p3)   ⊗(p4,p5,p3)                ⊗(p1,p6,p7)
    a977…          73f8…                        7124…
    | | \          / | |                        / | \
   p1 p2  p3 ----- p3 p5 p4                    p1 p6 p7
        └─────shared─────┘   └── p1 shared with ⊗(p1,p6,p7) ──┘
```

Two things to notice, both *emergent* from content-addressing:
- Both derivations of Omeprazole (`a977…` and `73f8…`) computed the same `?ans` IRI (both hash
  `"A|z=urn:d:Omeprazole"`), so they **feed the same ⊕ node** — the ⊕ *is* the sum of the two paths.
- The token `p3` is a single node referenced by both `a977…` and `73f8…`; `p1` is referenced by
  `a977…` and `7124…`. **The shared leaves are stored once** — it's a DAG, not a tree. This is the
  structure that makes the probability both correct and compact.

---

## 5. Stage E: compile + weighted model count → the probability

The client reads the circuit and reads it as a Boolean function of the tokens (⊗=∧, ⊕=∨):
```
Omeprazole  ⟺  (p1∧p2∧p3) ∨ (p4∧p5∧p3)
Clopidogrel ⟺  p1∧p6∧p7
```
It compiles each to a d-DNNF-family form (an OBDD / SDD / d-DNNF) and runs **weighted model
counting** — linear in the compiled size. Because `p3` is one shared leaf, the count handles the
correlation instead of double-counting it:

```
Omeprazole  = P(p3)·P((p1∧p2)∨(p4∧p5)) = .85 · .91094 = 0.774298   ✓  (not the naive 0.830814)
Clopidogrel = P(p1)·P(p6)·P(p7)          = .92·.65·.60  = 0.358800   ✓
```
(These match possible-world enumeration to float precision — `reference/verify_engine_native.py`.)

**Post-processing back to a SELECT answer.** The `CONSTRUCT` output is a graph, but the SELECT table
is recovered by reading the `c:answer` literals: parse `"A|z=urn:d:Omeprazole"` → `{?z = Omeprazole}`,
attach the WMC of the sub-circuit rooted at that ⊕. Result:

| ?z | probability |
|---|---|
| Omeprazole | 0.774298 |
| Clopidogrel | 0.358800 |

That is the ordinary SELECT result table, now with an exact probability column.

---

## 6. Beyond BGP: the other operators

The same idea — emit gates, content-address them — handles the whole fragment. The only new gate is
`⊖` (Minus / monus).

**UNION** `{A} UNION {B}`. Each branch is its own CONSTRUCT, but both compute the *same* answer-gate id
`hash("A|y=…")`, so the ⊗ from branch A and the ⊗ from branch B **feed the same ⊕** — content-addressing
turns "two branches" into "one ⊕" with no coordination.

**MINUS** `P1 MINUS P2`. Real gate structure for `:Alice :knows ?y MINUS { :Alice :blocks ?y }` (Alice
knows Bob=`t1`, Carol=`t2`; Alice blocks Carol=`t5`):
```
Bob   (⊕ a:3961…) ← ⊖ m:1ec0…( minuend = ⊕⊗t1 ,  subtrahend = ∅ )          # Bob not blocked ⇒ kept
Carol (⊕ a:5c88…) ← ⊖ m:dc32…( minuend = ⊕⊗t2 ,  subtrahend = ⊕⊗t5 )       # present iff t2 ∧ ¬t5
```
built by a 4-part plan: `⊕_{P1}` (knows), `⊕_{P2}` (blocks), a CONSTRUCT feeding compatible `⊕_{P2}`
into `⊕_{sub}`, and `⊖(⊕_{P1}, ⊕_{sub})` → answer. **Guard:** MINUS only subtracts when the two
operands share a variable (else it is a no-op) — the W3C domain-intersection rule; see
`TECHREPORT.md` §5 for why DIFF ≠ MINUS.

**OPTIONAL** `A OPTIONAL B ≡ (A AND B) ∪ (A DIFF B)`: one AND-branch (a join) plus the DIFF plan
(the MINUS internals *without* the guard). So each answer is a `⊕` of a matched `⊗` term and/or an
unmatched `⊖` term.

**Composite operands** (a UNION/OPTIONAL/chained MINUS *inside* a MINUS) are reduced algebraically to
the BGP/UNION-operand forms above by a `normalize()` pass before construction — e.g.
`(A∪B) MINUS P ≡ (A MINUS P)∪(B MINUS P)`, `(A MINUS P) MINUS Q ≡ A MINUS (P∪Q)`,
`(A OPT B) MINUS P ≡ (Join(A,B) MINUS P)∪((A DIFF B) MINUS P)` — note **DIFF, not MINUS, on B**, because
`A OPT B`'s negative branch is unguarded; the code realizes this as `A MINUS (B∪P)` only when A,B share a
variable (where `A DIFF B = A MINUS B`) and rejects the cross-product case otherwise. All are
provenance-correct; the identities and the DIFF-vs-MINUS subtlety are in `TECHREPORT.md` §5.

Every operator shape is checked as **circuit-WMC == possible-world-enumeration** in
`reference/verify_gallery.py` (12 shapes; the non-monotone ones cross-checked against rdflib's own
W3C evaluation).

---

## 7. Why this design (the two decisions that make it work)

**Content-addressing = automatic sharing.** Because a gate's identity *is* the hash of its meaning,
identical sub-computations produced anywhere in the query collapse to one node under RDF
set-semantics — no global hash table, no coordination, engine-side. The circuit is therefore a
**DAG that stays polynomial** where the equivalent string blows up with the number of derivations
(exponential on deep/cyclic data). That sharing is simultaneously (i) the compactness win and
(ii) what makes the probability *correct* (a shared token counted once) and *feasible* (WMC linear
in the compiled DAG).

**Unmodified engine.** The rewritten query uses only standard SPARQL 1.1 — basic patterns, `UNION`,
`OPTIONAL`, aggregation, `BIND`, and the built-in `SHA256`. Nothing engine-specific. We verified the
**byte-identical** circuit comes back from in-memory RDF4J and from a deployed GraphDB — so the method
is portable to any SPARQL 1.1 store, with no plugin, UDF, patch, or fork (contrast ProvSQL, which
modifies PostgreSQL).

---

## 8. End-to-end recap

```
SELECT ?z {Aspirin→?x→?y→?z}                      the question
  └ γ: reify + emit ⊗/⊕ gate constructors  →  one CONSTRUCT (standard SPARQL 1.1)
  └ engine runs it (RDF4J / GraphDB)        →  19-triple circuit; p1,p3 shared; 2 answer ⊕
  └ client compiles (OBDD/d-DNNF) + WMC      →  Omeprazole 0.774298, Clopidogrel 0.358800
  └ read c:answer literals                   →  the SELECT table + probability column
```

The engine never knew it was computing provenance. It ran one ordinary CONSTRUCT; the shared
circuit — and thus exact probabilistic query evaluation, including non-monotone OPTIONAL/MINUS — fell
out of *what we asked it to build*.
