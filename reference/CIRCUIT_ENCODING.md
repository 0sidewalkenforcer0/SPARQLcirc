# RDF circuit encoding

`CircuitRun` emits one native RDF representation for provenance circuits. The representation keeps
the circuit algebra unchanged while reducing RDF dictionary, serialization, transfer, and compiler
input costs.

The encoding has four parts:

1. Generated gate and private-row IRIs retain the first 128 bits of a SHA-256 content digest.
   Operator-specific prefixes and canonical keys are unchanged; RDF terms bound by the input query
   are never renamed. At one trillion independently distributed identifiers, the birthday-bound
   collision probability is approximately 1.5e-15 (about 2^-49).
2. Bound answer variables are stored directly as native RDF terms under reversible UTF-8-hex
   predicates. The `answerRoot` object declares the projected-variable schema; a declared variable
   without a direct binding is unbound. This also represents zero-column answers.
3. The generated CONSTRUCT omits `rdf:type` where `c:in`, `c:feeds`, `c:minuend`, `c:subtrahend`, or
   `answerRoot` determines the gate kind. Property-path reach Plus types remain present while the
   endpoint's fixpoint queries need them and are removed from the returned circuit. An empty Plus has
   no incoming edge, so its explicit type is retained as the zero anchor required by MINUS.
4. After construction, a non-answer Plus with one child is replaced by that child when it has no path
   or other metadata. A work queue finds the fixed point and rewrites all affected statements in one
   batch. Answer roots are not folded, so answer identity and term-aware bindings remain explicit.

These choices do not alter query operators, join or filter placement, factor-elimination order,
property-path recurrence, proof sharing, answer-set semantics, or WMC interpretation. Final
normalization is included in `construction_ms`; JVM startup and data loading remain outside it.

The reader in `reference/circuit_io.py` also accepts the earlier explicit-type and binding-node form
so that existing circuit artifacts remain usable.

Run the regression gates with:

```bash
mvn -q -f engine/pom.xml package
python3 reference/quick_verify.py
python3 reference/verify_g7_circuit_equiv.py
```
