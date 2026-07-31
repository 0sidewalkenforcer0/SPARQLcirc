# SPARQLcirc paper — semantic claim inventory (VLDB'27 draft, 15pp)

Source: SPARQLcircPaper/Jingcheng_s_VLDB27.pdf. Semantics live in §3 (pp.2-3) and §4 (pp.3-6).
Each row is an atomic, checkable statement. IDs are used by CONFORMANCE.md.

## §3 Preliminaries — fragment boundary

| ID | p. | Claim |
|---|---|---|
| P1.1 | 3 | Path-free fragment = triple patterns + Join, Union, Proj, Filter, Diff. |
| P1.2 | 3 | Unfiltered `Opt` is *expressed as* `Union(Join(P1,P2), Diff(P1,P2))`. Formal results concern algebraic Diff only; **no separate rewriting is asserted for W3C MINUS' domain-disjointness condition**. |
| P1.3 | 3 | Excluded: aggregation, Extend (BIND), filtered left join. |
| P1.4 | 3 | Closure-free path grammar: `e ::= p | ^e | e1/e2 | e1|e2`, p∈I. Every such e denotes paths of positive length. |
| P1.5 | 3 | Closure atom `C=(α,e⋄,?y)`, ⋄∈{+,*}, closure-free e, source α∈T∪V, target ?y. Requires `?y ∉ dom(ρ)`; for variable source, `α ∈ dom(ρ)`. |
| P1.6 | 3 | Bound-source condition: bind-join order evaluates every variable-source closure atom after an operand binding its source. Order affects construction only, not the resulting mappings. |
| P1.7 | 3 | Excluded from paths: zero-or-one (`?`), negated property sets (`!`), closure atoms violating bound-source, **nested closure**. |
| P1.8 | 3 | Property paths use **set semantics**: each reachable pair once. |
| P1.9 | 2-3 | G_enc holds occurrence ids, probabilities, reification triples, intermediate-factor rows, circuit triples. Only occurrence ids of triples in G are random variables; all else deterministic. |
| P1.10 | 3 | Diff annotation: `[[Diff(P1,P2)]](μ) = [[P1]](μ) ⊖ ⊕_{ν∼μ} [[P2]](ν)` — subtrahend ranges over all *compatible* ν, not just ν=μ. |
| P1.11 | 3 | Boolean image: ∨, ∧, and material nonimplication `a ↛ b = a ∧ ¬b`. BProv = η([[P]]_Γ). |

## §4.1 Circuit structure

| ID | p. | Claim |
|---|---|---|
| P2.1 | 4 | Def 4.2. Gate kinds: leaf x∈X; ⊕-gate with finite child **set**; ⊗-gate with finite child **set**; ⊖-gate with minuend-child **set** pos(g) and **exactly one** subtrahend child neg(g). |
| P2.2 | 4 | Empty ⊕ = ⊥; empty ⊗ = ⊤. |
| P2.3 | 4 | Def 4.3 semantics: `[[⊖(C,d)]] = (∨_{c∈C}[[c]]) ∧ ¬[[d]]`. |
| P2.4 | 4 | Tseitin T(C): fresh y_g per gate, equivalence per gate; base literals weighted Pr/1−Pr, **both** aux literals weight 1; compile once, condition on y_r=⊤ per answer root without recompilation. |
| P2.5 | 4 | Child **sets** not bags: repeated derivations with identical provenance do not change the event. |
| P2.6 | 4 | Def 4.4/Lemma 4.5. Congruence = same kind + same child sets (for ⊖: same minuend set *and* same subtrahend). Merging congruent gates preserves every root's semantics. |

## §4.2 The rewriting γ

| ID | p. | Claim |
|---|---|---|
| P3.1 | 4 | Rewriting reads one provenance identifier per matched base triple. RDF-star `<<s p o>> occurrenceOf t`; standard reification and named graphs give the same logical construction. |
| P3.2 | 4 | **The rewriting creates no blank nodes.** Client applies injective skolemization sk: B_G→I into a fresh namespace before loading, stores sk⁻¹, applies sk⁻¹ to projected answer terms. |
| P3.3 | 4 | A gate IRI encodes its operator **and** a canonical key. Implementation maps key → SHA-256 digest, digest prefixed by operator kind. |
| P3.4 | 4 | Def 4.6 canonical encoding τ(v): term kind, lexical form, datatype, **lower-cased** language tag. τ(unbound) is a distinct token. UTF-8. |
| P3.5 | 4 | Component framing: one-byte kind tag, then **decimal byte length**, then colon, then component bytes. IRIs use absolute IRI strings. Literals encode lexical form, datatype IRI, language tag as **separate** length-prefixed components. |
| P3.6 | 4 | Variable names ordered lexicographically; child keys ordered by byte string; pattern tag = prefix serialization of the algebra syntax using the same framing. |
| P3.7 | 4 | `id⊗(c1..cn) = (⊗, sort(c1..cn))` — **no pattern tag**, sorted children. `id⊕^θ(v1..vk) = (⊕, θ, τ(v1)..τ(vk))` — pattern tag + binding tuple, **no children**. id⊖ analogous. |
| P3.8 | 4 | `reif(P)` replaces every triple pattern by its reified form and **preserves all filters**. |
| P3.9 | 4 | `reif(Diff(P1,P2)) = reif(P1)` (the subtrahend is dropped when Diff appears in a parent's WHERE). |
| P3.10 | 4 | v̄_P = lexicographically ordered tuple of variables **in scope at P**. |
| P3.11 | 5 | Def 4.7.1 Triple pattern: g_P = its provenance identifier; γ(P)=∅. |
| P3.12 | 5 | Def 4.7.3 Join: `g_P = id⊗(g_P1..g_Pn)`; one CONSTRUCT `{g_P a c:Times ; c:in g_P1,…,g_Pn}` WHERE `{{reif(P1,g_P1)}…{reif(Pn,g_Pn)}}`. |
| P3.13 | 5 | Def 4.7.4 Union: `g_P = id⊕^P(v̄_P)`; **one CONSTRUCT per branch**, each `{g_P a c:Plus ; c:in g_Pi}` WHERE `{reif(Pi,g_Pi)}`. |
| P3.14 | 5 | Def 4.7.5 Proj_W: `g_P = id⊕^P(W̄)` (keyed on the **projected** tuple); one CONSTRUCT `{g_P a c:Plus ; c:in g_P'}`. |
| P3.15 | 5 | Def 4.7.6 Diff: `g_P = id⊖^P(v̄_P1)`, `s_P = id⊕^{(P,sub)}(v̄_P1)` — both keyed on P1's variable tuple, subtrahend gate uses a **distinct** pattern tag `(P,sub)`. |
| P3.16 | 5 | Def 4.7.6 emits **two** CONSTRUCTs: (a) `{s_P a c:Plus ; c:in g_P2}` WHERE `{{reif(P1)}{reif(P2,g_P2)}}`; (b) `{g_P a c:Minus ; c:minuend g_P1 ; c:subtrahend s_P . s_P a c:Plus}` WHERE `{reif(P1,g_P1)}`. Note (b) unconditionally types s_P as c:Plus, so an empty subtrahend yields an empty ⊕ = ⊥. |
| P3.17 | 5 | Def 4.7.7 Filter: `g_P = g_P'`, `γ(P) = γ(P')`; reif(P) retains the filter. Filter contributes **no** query and **no** gate. |
| P3.18 | 5 | Query count: join 1, proj 1, diff 2, union 1 per branch, triple pattern 0, filter 0. |
| P3.19 | 5 | The outer projection gate is the root of each answer. |
| P3.20 | 5 | All non-path plan queries may run in any order or concurrently once required closure rows exist; the combined circuit is acyclic. |
| P3.21 | 5 | RDF vocabulary: `c:Times`, `c:Plus`, `c:Minus`, `c:in`, `c:minuend`, `c:subtrahend`. |

## §4.2 Factored basic patterns

| ID | p. | Claim |
|---|---|---|
| P4.1 | 5 | Applies to `Q = Proj_W(Join(t1..tm))`. One factor F_j per triple pattern, scope = vars of t_j, row for binding θ points to leaf gate x_{θ(t_j)}. |
| P4.2 | 5 | Eliminate z∈Z (non-output vars). A_z = current factors whose scope contains z. `U_z = (∪_{F∈A_z} scope(F)) \ {z}`. |
| P4.3 | 5 | `q_z(α,a) = ⊗{F(α∪{z↦a}) | F∈A_z}`; `h_z(α) = ⊕{q_z(α,a) | a∈A_z(α)}`; A_z(α) = values a for which **every** factor in A_z has a row. Absent row ⇒ no disjunct. |
| P4.4 | 5 | Variable selection heuristic: **minimum current scope**. |
| P4.5 | 5 | Before constructing a base factor: semi-join with every remaining pattern that contains a constant and shares a variable with that factor. |
| P4.6 | 5 | Intermediate factors materialized as RDF in a **private namespace**, one row node per binding tuple + gate id; one INSERT makes rows available to the next standard SPARQL query. |
| P4.7 | 5 | A **read-only endpoint uses the flat plan**. |
| P4.8 | 5 | Prop 4.9: factored and flat produce equivalent answer roots; factored creates O(m|G|^{w+1}) gates, w = max_z |U_z|. |

## §4.3 Recursive paths

| ID | p. | Claim |
|---|---|---|
| P5.1 | 5 | D_G = subjects and objects occurring in G. g⊥ = ⊕(∅), g⊤ = ⊗(∅). |
| P5.2 | 5 | `b^p_uv = ⊕{x_t | t=(u,p,v)∈G}`. |
| P5.3 | 5 | `b^{^e}_uv = b^e_vu` (inverse swaps endpoints, no new gate). |
| P5.4 | 5 | `b^{e1|e2}_uv = ⊕({b^{e1}_uv, b^{e2}_uv} \ {g⊥})`. |
| P5.5 | 5 | `b^{e1/e2}_uv = ⊕{ ⊗{b^{e1}_uw, b^{e2}_wv} : w∈D_G, b^{e1}_uw≠g⊥, b^{e2}_wv≠g⊥ }`. |
| P5.6 | 5 | Base gate keys contain the **syntax node of e** and the endpoint pair. |
| P5.7 | 6 | E_e = {(u,v) : b^e_uv ≠ g⊥}; V_s = s plus everything reachable from s in E_e; E_s = E_e ∩ (V_s×V_s); n = |V_s|. |
| P5.8 | 6 | `r_1(v) = ⊕{b^e_sv | (s,v)∈E_s}` (Eq. 5). |
| P5.9 | 6 | `a_{i,u,v} = ⊗({r_i(u), b^e_uv})`; `r_{i+1}(v) = ⊕({r_i(v)} ∪ {a_{i,u,v} | (u,v)∈E_s})` (Eq. 6). |
| P5.10 | 6 | Level gate keys contain e, s, i, and the endpoint ⇒ every edge points to a lower level (acyclic on cyclic data). |
| P5.11 | 6 | Root for `(s,e+,v)` is `r_n(v)`. For `e*`, root is `g⊤` when v=s, else r_n(v). |
| P5.12 | 6 | Per level the client issues **one** CONSTRUCT for the product gates and **one** for the union gates. |
| P5.13 | 6 | A variable source from an enclosing pattern invokes the same construction **per source binding**. |
| P5.14 | 6 | Def 4.7.2: for ⋄=+, endpoint v reachable ⇒ extension ρ∪{?y↦v} with g_C(ρ,v)=r_n(v). For ⋄=*, additionally ρ∪{?y↦s} with g_C(ρ,s)=g⊤. |
| P5.15 | 6 | The materialized relation reif(C,g_C) lets enclosing cases consume path roots exactly like triple-pattern gates. |
| P5.16 | 6 | **Stated implementation specialization**: the implementation evaluated in §5 specializes to e = p and materializes only predicate edges in E_s (never the all-pairs base relation). C_e for general closure-free e is the *semantic reference* construction. |
| P5.17 | 8 | **Stated implementation gap**: nested closures are not stratified; a closure inside the sub-path is **rejected**, not approximated. |

## §4.4 Correctness + PQE

| ID | p. | Claim |
|---|---|---|
| P6.1 | 6 | Thm 4.11: ν_W([[r_n(v)]]) = ⊤ ⟺ (s,v) ∈ Rel_{e+}(W). Level part acyclic, O(n(n+|E_s|)) gates. |
| P6.2 | 6 | Lemma 4.12: [[g_C(ρ,v)]] = BProv_C(μ). |
| P6.3 | 6 | Thm 4.13: [[root(μ)]] = BProv_Q(μ) whenever a root is produced; if no root, BProv_Q(μ)=⊥. Holds also when the factored plan replaces a basic-pattern subplan. |
| P6.4 | 6 | Cor 4.14: compiling a root's Boolean semantics + WMC returns the exact marginal probability under tuple independence. |
| P6.5 | 7,9 | Compilation targets: fixed-order OBDD (CUDD 3.0.0) and d-DNNF (d4v2). No approximation fallback: exact value or resource-limit failure. |
| P6.6 | 7 | Validation: agreement with exhaustive possible-world enumeration for provenance over ≤20 tuples, covering every query class incl. optional/minus and a P279+ path. |
