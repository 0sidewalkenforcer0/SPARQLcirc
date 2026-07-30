package npcs.circuit;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import org.eclipse.rdf4j.query.algebra.ArbitraryLengthPath;
import org.eclipse.rdf4j.query.algebra.Difference;
import org.eclipse.rdf4j.query.algebra.Distinct;
import org.eclipse.rdf4j.query.algebra.Filter;
import org.eclipse.rdf4j.query.algebra.Join;
import org.eclipse.rdf4j.query.algebra.LeftJoin;
import org.eclipse.rdf4j.query.algebra.Order;
import org.eclipse.rdf4j.query.algebra.Projection;
import org.eclipse.rdf4j.query.algebra.ProjectionElem;
import org.eclipse.rdf4j.query.algebra.QueryModelNode;
import org.eclipse.rdf4j.query.algebra.Slice;
import org.eclipse.rdf4j.query.algebra.StatementPattern;
import org.eclipse.rdf4j.query.algebra.TupleExpr;
import org.eclipse.rdf4j.query.algebra.Union;
import org.eclipse.rdf4j.query.algebra.Var;
import org.eclipse.rdf4j.query.algebra.ZeroLengthPath;
import org.eclipse.rdf4j.query.algebra.helpers.AbstractQueryModelVisitor;
import org.eclipse.rdf4j.query.algebra.helpers.StatementPatternCollector;
import org.eclipse.rdf4j.query.parser.ParsedQuery;
import org.eclipse.rdf4j.query.parser.sparql.SPARQLParser;

import npcs.rewrite.Reification;
import npcs.rewrite.QueryGuard;
import npcs.rewrite.Terms;

/**
 * Circuit rewriting (the paper's gamma) built on the NPCS rewriting: reified
 * triple patterns are NPCS's, but ProvProd/ProvAggSum/ProvDiff are replaced by
 * ⊗/⊕/⊖ gate constructors emitted as CONSTRUCT templates, so an unmodified
 * engine materializes a shared provenance circuit as RDF.
 *
 * A query is compiled to a <em>plan</em>: a list of CONSTRUCT queries whose
 * results are merged into one circuit (the paper's per-operator materialization).
 *   - BGP:      default multi-pass variable elimination (base/join/marginalize);
 *               FLAT mode retains 1 CONSTRUCT (⊗ per derivation -> ⊕ per answer)
 *   - UNION:    one plan per branch, all keyed by the projection W, so the
 *               branches feed one shared answer ⊕ (that shared ⊕ IS the union)
 *   - MINUS:    guarded DIFF — ⊕_{P1}; per right branch overlapping P1: ⊕_{P2} and
 *               ⊕_{P2} -> ⊕_{sub}; then ⊖(⊕_{P1},⊕_{sub}) -> answer. No shared variable
 *               ⇒ no-op. Composite operands (UNION/OPTIONAL/chained MINUS) are reduced to
 *               this form by {@link #normalize}.
 *   - OPTIONAL: one AND-branch CONSTRUCT over P1∪P2 + the DIFF plan (unguarded, via minusPlan pieces)
 *
 * Gate ids are content-addressed in SPARQL: IRI(prefix + SHA256(key)).
 * Vocabulary (urn:circuit:):  Times/Plus/Minus, in/feeds/minuend/subtrahend/answer.
 */
public class CircuitRewriter {

    private static final String PRE = "PREFIX c: <urn:circuit:>\n";
    private final Reification scheme;
    private final ConstructionMode constructionMode;
    private final String workspaceId;
    private String generatedPrefix = "__sc0_";

    public CircuitRewriter(Reification scheme) {
        this(scheme, ConstructionMode.FACTORED);
    }

    public CircuitRewriter(Reification scheme, ConstructionMode constructionMode) {
        this(scheme, constructionMode, java.util.UUID.randomUUID().toString());
    }

    /**
     * Create a rewriter with an explicit private workspace id. CircuitRun passes
     * a fresh id per invocation so concurrent factored plans cannot consume or
     * clean up one another's message rows. Gate ids do not depend on this id.
     */
    public CircuitRewriter(Reification scheme, ConstructionMode constructionMode, String workspaceId) {
        this.scheme = scheme;
        this.constructionMode = constructionMode;
        this.workspaceId = workspaceId;
    }

    /**
     * Compatibility view of {@link #constructionPlan(String)} for plan inspection.
     * Callers executing a factored plan must honor its feedback markers; the
     * production runner does so via {@link CircuitRun}.
     */
    public List<String> plan(String query) {
        return constructionPlan(query).queries();
    }

    /**
     * Build an executable construction plan. In FACTORED mode the BGP sub-parts of the query use
     * min-scope variable elimination: a pure BGP directly; and the BGP branches of UNION/OPTIONAL and
     * the P1/P2 marginals of MINUS/OPTIONAL via {@link FactoredBgpRewriter} (their content-addressed
     * gate IRIs are unchanged, so the ⊕/⊖/UNION operator plan connects to them exactly as in flat mode).
     * The composite plan is reported as effective=FACTORED when any sub-BGP was factored. FLAT mode (and
     * a query with no factorable sub-BGP) keeps the one-⊗-per-derivation operator plan.
     */
    public CircuitConstructionPlan constructionPlan(String query) {
        ParsedQuery pq = new SPARQLParser().parseQuery(query, null);
        QueryGuard.rejectDatasetsAndNamedGraphs(pq);
        TupleExpr te = pq.getTupleExpr();
        initializeGeneratedVariables(te);
        rejectSequenceModifiers(te);                 // LIMIT/OFFSET/ORDER BY don't apply to a circuit
        Projection projection = outerProjection(te);
        List<String> W = new ArrayList<>();
        for (ProjectionElem pe : projection.getProjectionElemList().getElements()) {
            if (!W.contains(pe.getName())) W.add(pe.getName());
        }
        TupleExpr body = normalize(projection.getArg());
        TupleExpr bgpCandidate = unwrapSetWrappers(body);
        // A FILTERed BGP keeps the flat plan: Def. 4.5's filter rule leaves the condition inside the
        // operand's group, and the factored plan has no single group for it (its passes exchange
        // materialized relations). Same answer gate either way, so this is a plan choice only.
        if (constructionMode == ConstructionMode.FACTORED && isPureBgp(bgpCandidate)
                && !Filters.of(bgpCandidate).isEmpty()) {
            return new CircuitConstructionPlan(branchPlan(body, W), constructionMode,
                    ConstructionMode.FLAT, "BGP carries a FILTER; the factored passes have no single "
                    + "group for the condition, so the flat plan is used");
        }
        if (constructionMode == ConstructionMode.FACTORED && isPureBgp(bgpCandidate)) {
            return FactoredBgpRewriter.build(scheme, generatedPrefix, workspaceId,
                    collect(bgpCandidate), W);
        }

        List<CircuitConstructionPlan.Step> steps = branchPlan(body, W);
        // Any factored sub-BGP contributes feedback steps (base/join/marginalize private messages);
        // their presence is exactly what makes the composite construction "factored" for this query.
        boolean anyFactored = false;
        for (CircuitConstructionPlan.Step s : steps) if (s.feedback()) { anyFactored = true; break; }
        ConstructionMode effective = anyFactored ? ConstructionMode.FACTORED : ConstructionMode.FLAT;
        String fallback = (constructionMode == ConstructionMode.FACTORED && effective == ConstructionMode.FLAT)
                ? "query body is not a pure BGP and no sub-BGP was factored; using the flat operator plan"
                : null;
        return new CircuitConstructionPlan(steps, constructionMode, effective, fallback);
    }

    public ConstructionMode constructionMode() { return constructionMode; }

    /**
     * Normalize the algebra so every {@code Difference} (MINUS) has operands {@link
     * #branchPlan}/{@link #minusPlan} can build, by algebraically reducing composite MINUS
     * operands to the verified BGP/UNION-operand plan (all PQE-valid):
     *   (A∪B) MINUS P        ≡ (A MINUS P) ∪ (B MINUS P)
     *   (A OPT B) MINUS P    ≡ (Join(A,B) MINUS P) ∪ ((A DIFF B) MINUS P)  [B: DIFF, not MINUS]
     *   P MINUS (C OPT D)    ≡ P MINUS C                                 [P shares no D-only var]
     *   (A MINUS P) MINUS Q  ≡ A MINUS (P∪Q)                             [(A∖P)∖Q = A∖(P∪Q)]
     * Residuals left for minusPlan/collect to reject safely: right-nested MINUS
     * A MINUS (P MINUS Q) (introduces a join), and the two pathological OPTIONAL-as-MINUS-operand
     * shapes. (A bare cross-product OPTIONAL is NOT a residual — optionalPlan handles it correctly.)
     */
    private static TupleExpr normalize(TupleExpr node) {
        if (node instanceof Union) {
            Union u = (Union) node;
            return new Union(normalize(u.getLeftArg().clone()), normalize(u.getRightArg().clone()));
        }
        if (node instanceof LeftJoin) {
            LeftJoin lj = (LeftJoin) node;
            // The W3C algebra lifts a FILTER inside an OPTIONAL group to the LeftJoin condition.
            // When that condition only mentions variables the RIGHT operand binds, it is equivalent
            // to a filter on that operand — LeftJoin(A,B,f) = LeftJoin(A, σ_f(B)) — since the
            // matched case agrees pointwise and the unmatched case asks the same question of the same
            // filtered relation. We push it there, which is Def. 4.5's filter rule.
            // A condition that also mentions a LEFT-operand variable is the genuine filtered left
            // join, which the rewriting does not model: reject rather than emit a circuit for the
            // *unfiltered* query (wrong answers, no error).
            if (lj.getCondition() != null) {
                Set<String> used = Filters.conditionVars(lj.getCondition());
                used.removeAll(Filters.patternVars(lj.getRightArg()));
                if (!used.isEmpty()) {
                    throw new UnsupportedOperationException(
                            "Unsupported operator: filtered left join (OPTIONAL with a FILTER condition "
                          + "over both operands; here it also references " + used + ", which the OPTIONAL's "
                          + "own pattern does not bind) is outside the supported fragment. Refusing to emit a "
                          + "circuit for the unfiltered query; rewrite without the inner FILTER.");
                }
                return normalize(new LeftJoin(lj.getLeftArg().clone(),
                        new Filter(lj.getRightArg().clone(), lj.getCondition().clone())));
            }
            return new LeftJoin(normalize(lj.getLeftArg().clone()), normalize(lj.getRightArg().clone()));
        }
        if (node instanceof Difference) {
            Difference d = (Difference) node;
            TupleExpr nl = normalize(d.getLeftArg().clone());
            TupleExpr nr = normalize(d.getRightArg().clone());
            // OPTIONAL left operand: (A OPT B) MINUS P ≡ (Join(A,B) MINUS P) ∪ ((A DIFF B) MINUS P).
            // A OPT B's negative branch is UNGUARDED DIFF, so B needs DIFF, not MINUS. We realize the
            // second disjunct as A MINUS (B∪P) — equal to (A DIFF B) MINUS P ONLY when A,B share a
            // variable (then A DIFF B = A MINUS B, and (A∖B)∖P = A∖(B∪P)). Hence the guard below; the
            // no-shared-var case (cross-product OPTIONAL) falls through and is safely rejected.
            if (nl instanceof LeftJoin) {
                LeftJoin lj = (LeftJoin) nl;
                if (!intersect(varsOf(lj.getLeftArg()), varsOf(lj.getRightArg())).isEmpty()) {
                    return normalize(new Union(
                            new Difference(new Join(lj.getLeftArg().clone(), lj.getRightArg().clone()), nr.clone()),
                            new Difference(lj.getLeftArg().clone(), new Union(lj.getRightArg().clone(), nr.clone()))));
                }
            }
            // OPTIONAL right operand: P1 MINUS (C OPT D) ≡ P1 MINUS C when P1 shares no D-only
            // variable (the optional D-part washes out of the subtrahend: matched ⊕ unmatched = always).
            if (nr instanceof LeftJoin) {
                LeftJoin lj = (LeftJoin) nr;
                LinkedHashSet<String> dOnly = varsOf(lj.getRightArg());
                dOnly.removeAll(varsOf(lj.getLeftArg()));         // vars(D) \ vars(C)
                LinkedHashSet<String> shareD = varsOf(nl);
                shareD.retainAll(dOnly);
                if (shareD.isEmpty()) {
                    return normalize(new Difference(nl.clone(), lj.getLeftArg().clone()));
                }
            }
            if (nl instanceof Union) {          // (A∪B) MINUS P → (A MINUS P) ∪ (B MINUS P)
                Union u = (Union) nl;
                return normalize(new Union(
                        new Difference(u.getLeftArg().clone(), nr.clone()),
                        new Difference(u.getRightArg().clone(), nr.clone())));
            }
            if (nl instanceof Difference) {     // (A MINUS P) MINUS Q → A MINUS (P ∪ Q)  [(A∖P)∖Q=A∖(P∪Q)]
                Difference inner = (Difference) nl;
                return normalize(new Difference(inner.getLeftArg().clone(),
                        new Union(inner.getRightArg().clone(), nr.clone())));
            }
            return new Difference(nl, nr);
        }
        return node;   // BGP / Join / StatementPattern
    }

    /**
     * Plan a (sub)expression. Every answer gate is keyed by the projection {@code W},
     * so the branches of a UNION feed one <em>shared</em> answer ⊕ gate
     * (content-addressed by the binding) — that shared Plus IS the union. Recurses,
     * so UNION may nest and its branches may themselves be MINUS/OPTIONAL/BGP.
     *   - UNION:    branchPlan(left) ++ branchPlan(right)  (shared W-keyed answer ⊕)
     *   - MINUS:    minusPlan (guarded DIFF; UNION right operand -> per-branch subtrahend)
     *   - OPTIONAL: optionalPlan (AND-branch + unguarded DIFF)
     *   - BGP:      1 CONSTRUCT    (⊗ per derivation -> ⊕ per answer)
     */
    private List<CircuitConstructionPlan.Step> branchPlan(TupleExpr body, List<String> W) {
        // Look through the Distinct + inner Projection that a property-path `?` expansion wraps around
        // its Union (both are no-ops for our content-addressed, set-semantics answer gates).
        if (body instanceof Distinct)   return branchPlan(normalize(((Distinct) body).getArg()), W);
        if (body instanceof Projection) return branchPlan(normalize(((Projection) body).getArg()), W);
        if (body instanceof Union) {
            Union u = (Union) body;
            List<CircuitConstructionPlan.Step> plan = new ArrayList<>(branchPlan(u.getLeftArg(), W));
            plan.addAll(branchPlan(u.getRightArg(), W));
            return plan;
        }
        if (body instanceof Difference) {
            return minusPlan((Difference) body, W);      // handles the shared-var guard + UNION right operand
        }
        if (body instanceof LeftJoin) {
            return optionalPlan((LeftJoin) body, W);
        }
        if (body instanceof ZeroLengthPath) {                 // zero-length path, e.g. the ?-branch of :p?
            List<CircuitConstructionPlan.Step> plan = new ArrayList<>();
            plan.add(flatStep(zeroLengthPlan((ZeroLengthPath) body, W), "zerolen"));
            return plan;
        }
        return bgpSteps(collectBlock(body), W);               // pure BGP branch: factored in FACTORED mode
    }

    private CircuitConstructionPlan.Step flatStep(String query, String label) {
        return new CircuitConstructionPlan.Step(query, false, label);
    }

    /**
     * BGP sub-plan producing the answer ⊕ keyed by {@code W}. In FACTORED mode this is the same
     * min-scope variable elimination validated for whole-query pure BGPs (its answer gate is
     * byte-for-byte identical to the flat {@link #bgp} answer gate, so a factored branch and a flat
     * branch merge into one shared answer ⊕); in FLAT mode it is the single one-⊗-per-derivation
     * CONSTRUCT. This is where OPTIONAL's reconvergent P1∪P2 AND-branch gets compressed.
     */
    private List<CircuitConstructionPlan.Step> bgpSteps(Block block, List<String> W) {
        if (constructionMode == ConstructionMode.FACTORED && !block.isEmpty() && !block.isFiltered()) {
            return new ArrayList<>(FactoredBgpRewriter.build(
                    scheme, generatedPrefix, workspaceId, block.patterns, W).steps());
        }
        List<CircuitConstructionPlan.Step> plan = new ArrayList<>();
        plan.add(flatStep(bgp(block, W), "flat-bgp"));
        return plan;
    }

    /**
     * Zero-length path {@code ?x <>? ?y} (the reflexive branch RDF4J emits for {@code :p?}, and the
     * zero-length part of {@code :p*}): the pair {@code (u,u)} for every term {@code u} in the graph,
     * with provenance {@code occ(u)} = ⊕ of the tokens of triples that mention {@code u} ("u occurs")
     * — the terms-in-graph reading. Both endpoints bind to {@code u}; a constant endpoint filters it.
     */
    private String zeroLengthPlan(ZeroLengthPath zlp, List<String> W) {
        if (scheme != Reification.STANDARD)
            throw new UnsupportedOperationException("Zero-length paths (:p?) currently support Standard reification only.");
        Var s = zlp.getSubjectVar(), o = zlp.getObjectVar();
        String u = qv("u"), tok = qv("tok"), times = qv("t");
        String ans = qv("ans"), anskey = qv("anskey");
        List<String> zv = new ArrayList<>();                   // projected endpoint vars (both bound to ?u)
        for (String w : W)
            if ((!s.hasValue() && w.equals(s.getName())) || (!o.hasValue() && w.equals(o.getName())))
                zv.add(w);
        StringBuilder rk = new StringBuilder("CONCAT(\"A\""), idk = new StringBuilder("CONCAT(\"A\"");
        for (String w : zv) {                                  // rk = readable label; idk = identity
            rk.append(", \"|").append(w).append("=\", STR(").append(u).append(")");
            idk.append(", ").append(termHash(w, u));
        }
        rk.append(")"); idk.append(")");
        StringBuilder q = new StringBuilder(PRE);
        q.append("CONSTRUCT {\n  ").append(times).append(" a c:Times ; c:in ").append(tok)
         .append(" ; c:feeds ").append(ans).append(" .\n  ").append(ans)
         .append(" a c:Plus ; c:answer ").append(anskey).append(" .\n");
        for (String w : zv) {
            String b = qv("b_" + w);
            q.append("  ").append(ans).append(" c:binding ").append(b).append(" . ").append(b)
             .append(" c:var \"").append(w).append("\" ; c:val ").append(u).append(" .\n");
        }
        q.append("}\nWHERE {\n")
         .append("  { ").append(tok).append(" <").append(RDF_S).append("> ").append(u)
         .append(" . } UNION { ").append(tok).append(" <").append(RDF_O).append("> ").append(u).append(" . }\n");
        if (s.hasValue()) q.append("  FILTER(").append(u).append(" = ").append(Terms.render(s)).append(")\n");
        if (o.hasValue()) q.append("  FILTER(").append(u).append(" = ").append(Terms.render(o)).append(")\n");
        q.append("  BIND(").append(rk).append(" AS ").append(anskey).append(")\n")
         .append("  BIND(IRI(CONCAT(\"urn:g:t:\", SHA256(CONCAT(\"T|\", SHA256(STR(")
         .append(tok).append(")))))) AS ").append(times).append(")\n")
         .append("  BIND(IRI(CONCAT(\"urn:g:a:\", SHA256(").append(idk).append("))) AS ").append(ans).append(")\n");
        for (String w : zv) {
            String b = qv("b_" + w);
            q.append("  BIND(IRI(CONCAT(STR(").append(ans).append("), \"#").append(w)
             .append("\")) AS ").append(b).append(")\n");
        }
        q.append("}\n");
        return q.toString();
    }

    // --------------------------- BGP ---------------------------
    private String bgp(Block block, List<String> W) {
        List<String> toks = new ArrayList<>();
        StringBuilder where = reify(block, "a", toks);
        String tkey = emitSortedProdKey(where, toks);   // canonical (order-independent) ⊗ key
        String times = qv("t"), ans = qv("ans"), anskey = qv("anskey");
        StringBuilder q = new StringBuilder(PRE);
        q.append("CONSTRUCT {\n  ").append(times).append(" a c:Times ;");
        for (String t : toks) q.append(" c:in ?").append(t).append(" ;");
        q.append(" c:feeds ").append(ans).append(" .\n  ").append(ans)
         .append(" a c:Plus ; c:answer ").append(anskey).append(" .\n").append(bindingCtor(W))
         .append("}\nWHERE {\n").append(where);
        q.append(bind(anskey, ansKey(W, setOf(W))));                 // readable label (display/debug)
        q.append(bindIri(times, "urn:g:t:", tkey));
        q.append(bindIri(ans, "urn:g:a:", idKey(W, "A")));           // collision-resistant identity
        q.append(bindingWhere(W));                                      // recoverable per-var RDF bindings
        q.append("}\n");
        return q.toString();
    }

    // --------------------------- MINUS ---------------------------
    /**
     * MINUS plan. The left operand is a BGP (normalize() has distributed any UNION out
     * of the left). The right operand may be a BGP or a UNION of BGPs: every right
     * branch that shares a variable with P1 contributes to the subtrahend ⊕_{sub}(V1) —
     * since ⊕_{sub} is content-addressed by V1, all branches feed one gate (μ is removed
     * iff it matches SOME branch). A branch sharing no variable with P1 is a no-op and
     * is skipped; if no branch overlaps, MINUS is a no-op (= P1).
     */
    private List<CircuitConstructionPlan.Step> minusPlan(Difference diff, List<String> W) {
        Block L = collectBlock(diff.getLeftArg());
        LinkedHashSet<String> V1 = vars(L.patterns);
        List<Block> removing = new ArrayList<>();
        for (Block Rb : unionBlocks(diff.getRightArg())) {
            if (!intersect(V1, vars(Rb.patterns)).isEmpty()) removing.add(Rb);   // overlap ⇒ can remove
        }
        List<CircuitConstructionPlan.Step> plan = new ArrayList<>();
        if (removing.isEmpty()) {                                      // no overlap ⇒ MINUS is a no-op
            plan.addAll(bgpSteps(L, W));                                // = P1 (factored in FACTORED mode)
            return plan;
        }
        String p1Tag = "P1@" + bgpFingerprint(L);
        String opFp = diffFingerprint(L, removing);
        // ⊕_{P1}(V1): the minuend marginal. Factored when possible (compresses reconvergent P1);
        // its content-addressed IRI is unchanged, so subFeeds/minusRoot connect regardless.
        plan.addAll(marginalPlus(L, "a", "urn:g:p1:", p1Tag, V1));
        for (Block Rb : removing) {
            LinkedHashSet<String> V2 = vars(Rb.patterns);
            String p2Tag = "P2@" + bgpFingerprint(Rb);
            plan.addAll(marginalPlus(Rb, "b", "urn:g:p2:", p2Tag, V2)); // ⊕_{P2}(V2)
            plan.add(flatStep(subFeeds(L, Rb, V1, V2, p2Tag, opFp), "sub")); // ⊕_{P2} -> ⊕_{sub}(V1)
        }
        plan.add(flatStep(minusRoot(L, V1, W, p1Tag, opFp), "minusRoot")); // ⊖(⊕_{P1}, ⊕_{sub}) -> answer
        return plan;
    }

    /** Flatten a UNION of BGPs into its branch pattern-lists (a plain BGP -> one branch). */
    private static List<List<StatementPattern>> unionBranches(TupleExpr node) {
        List<List<StatementPattern>> out = new ArrayList<>();
        if (node instanceof Union) {
            out.addAll(unionBranches(((Union) node).getLeftArg()));
            out.addAll(unionBranches(((Union) node).getRightArg()));
        } else {
            out.add(collect(node));    // asserts pure BGP (an OPTIONAL branch would throw)
        }
        return out;
    }

    /** {@link #unionBranches} keeping each branch's FILTERs with it (per-branch conditions differ). */
    private static List<Block> unionBlocks(TupleExpr node) {
        List<Block> out = new ArrayList<>();
        if (node instanceof Union) {
            out.addAll(unionBlocks(((Union) node).getLeftArg()));
            out.addAll(unionBlocks(((Union) node).getRightArg()));
        } else {
            out.add(collectBlock(node));
        }
        return out;
    }

    /** ⊗ per derivation feeding a ⊕ gate keyed by {@code groupVars}. */
    private String productPlus(Block block, String tokPrefix,
                               String plusPrefix, String groupTag, LinkedHashSet<String> groupVars) {
        List<String> toks = new ArrayList<>();
        StringBuilder where = reify(block, tokPrefix, toks);
        String tkey = emitSortedProdKey(where, toks);   // canonical (order-independent) ⊗ key
        String times = qv("t"), plus = qv("pg");
        StringBuilder q = new StringBuilder(PRE);
        q.append("CONSTRUCT {\n  ").append(times).append(" a c:Times ;");
        for (String t : toks) q.append(" c:in ?").append(t).append(" ;");
        q.append(" c:feeds ").append(plus).append(" .\n  ").append(plus).append(" a c:Plus .\n}\nWHERE {\n").append(where);
        q.append(bindIri(times, "urn:g:t:", tkey));
        q.append(bindIri(plus, plusPrefix, idKey(canonicalVars(groupVars), groupTag)));
        q.append("}\n");
        return q.toString();
    }

    /** For each compatible (P1,P2) pair, ⊕_{P2}(μ2) feeds ⊕_{sub}(μ1). */
    private String subFeeds(Block L, Block R,
                            LinkedHashSet<String> V1, LinkedHashSet<String> V2,
                            String p2Tag, String opFp) {
        List<String> ta = new ArrayList<>(), tb = new ArrayList<>();
        StringBuilder where = reify(L, "a", ta);
        where.append(reify(R, "b", tb));                                 // natural join on shared vars
        String p2 = qv("p2"), sub = qv("sub");
        StringBuilder q = new StringBuilder(PRE);
        q.append("CONSTRUCT {\n  ").append(p2).append(" c:feeds ").append(sub).append(" .\n}\nWHERE {\n").append(where);
        q.append(bindIri(p2, "urn:g:p2:", idKey(canonicalVars(V2), p2Tag)));
        q.append(bindIri(sub, "urn:g:sub:", idKey(canonicalVars(V1), "SUB@" + opFp)));
        q.append("}\n");
        return q.toString();
    }

    /** ⊖(⊕_{P1}(μ1), ⊕_{sub}(μ1)) feeding the answer ⊕, keyed by the projection. */
    private String minusRoot(Block L, LinkedHashSet<String> V1, List<String> W,
                             String p1Tag, String opFp) {
        List<String> ta = new ArrayList<>();
        StringBuilder where = reify(L, "a", ta);
        String minus = qv("m"), p1 = qv("p1"), sub = qv("sub");
        String ans = qv("ans"), anskey = qv("anskey");
        StringBuilder q = new StringBuilder(PRE);
        q.append("CONSTRUCT {\n");
        q.append("  ").append(minus).append(" a c:Minus ; c:minuend ").append(p1)
         .append(" ; c:subtrahend ").append(sub).append(" ; c:feeds ").append(ans).append(" .\n");
        q.append("  ").append(sub).append(" a c:Plus .\n");
        q.append("  ").append(ans).append(" a c:Plus ; c:answer ").append(anskey).append(" .\n").append(bindingCtor(W))
         .append("}\nWHERE {\n").append(where);
        q.append(bindIri(p1, "urn:g:p1:", idKey(canonicalVars(V1), p1Tag)));
        q.append(bindIri(sub, "urn:g:sub:", idKey(canonicalVars(V1), "SUB@" + opFp)));
        q.append(bindIri(minus, "urn:g:m:", idKey(canonicalVars(V1), "M@" + opFp)));
        q.append(bind(anskey, ansKey(W, V1)));                        // readable label (W vars not in V1 -> NULL)
        q.append(bindIri(ans, "urn:g:a:", idKey(W, "A")));            // collision-resistant identity
        q.append(bindingWhere(W));
        q.append("}\n");
        return q.toString();
    }

    // --------------------------- OPTIONAL = (P1 AND P2) UNION (P1 DIFF P2) ---------------------------
    private List<CircuitConstructionPlan.Step> optionalPlan(LeftJoin lj, List<String> W) {
        Block L = collectBlock(lj.getLeftArg());
        Block R = collectBlock(lj.getRightArg());
        Block both = Block.concat(L, R);

        List<CircuitConstructionPlan.Step> plan = new ArrayList<>();
        plan.addAll(bgpSteps(both, W));                                  // AND-branch: ⊗ over P1∪P2 -> answer (factored)
        // DIFF-branch: P1-only answers with ⊖. UNLIKE MINUS this is UNGUARDED — OPTIONAL's negative
        // branch must subtract even when the operands share no variable. subFeeds reifies P1 and P2
        // in one WHERE, so disjoint operands cross-product and every P2 feeds every P1 subtrahend
        // (⊖(⊕_{P1}, ⊕ all P2)); a shared variable instead makes it a natural join. Guarding this on
        // shared variables (as MINUS does) would leave a bare P1 present even when P2 matches — wrong.
        LinkedHashSet<String> V1 = vars(L.patterns), V2 = vars(R.patterns);
        String p1Tag = "P1@" + bgpFingerprint(L);
        String p2Tag = "P2@" + bgpFingerprint(R);
        String opFp = diffFingerprint(L, Collections.singletonList(R));
        plan.addAll(marginalPlus(L, "a", "urn:g:p1:", p1Tag, V1));
        plan.addAll(marginalPlus(R, "b", "urn:g:p2:", p2Tag, V2));
        plan.add(flatStep(subFeeds(L, R, V1, V2, p2Tag, opFp), "sub"));
        plan.add(flatStep(minusRoot(L, V1, W, p1Tag, opFp), "minusRoot"));
        return plan;
    }

    /**
     * ⊕ keyed by {@code groupVars} (the P1/P2 marginal that feeds a ⊖). In FACTORED mode this is min-scope
     * variable elimination down to {@code groupVars}, then a sink ⊕ carrying the SAME content-addressed IRI
     * ({@code plusPrefix}+idKey(canonicalVars,groupTag)) as the flat gate, so subFeeds/minusRoot connect
     * unchanged and the ⊖ leaves (reified statement ids) are identical — the difference semantics are
     * preserved, only the minuend/subtrahend polynomial is factored. FLAT mode: one-⊗-per-derivation.
     */
    private List<CircuitConstructionPlan.Step> marginalPlus(Block block, String tokPrefix,
            String plusPrefix, String groupTag, LinkedHashSet<String> groupVars) {
        if (constructionMode == ConstructionMode.FACTORED && !block.isEmpty() && !block.isFiltered()) {
            return new ArrayList<>(FactoredBgpRewriter.buildMarginal(
                    scheme, generatedPrefix, workspaceId, block.patterns, canonicalVars(groupVars),
                    plusPrefix, groupTag).steps());
        }
        List<CircuitConstructionPlan.Step> plan = new ArrayList<>();
        plan.add(flatStep(productPlus(block, tokPrefix, plusPrefix, groupTag, groupVars), "marg-flat"));
        return plan;
    }

    // --------------------------- helpers ---------------------------
    /**
     * Reified group of a BGP operand: its patterns followed by its FILTERs. Appending the conditions
     * after the patterns is where Def. 4.5 puts them ("everything else, filters included, left in
     * place"): every variable a condition mentions is bound by this group ({@link Filters#of}
     * enforces that), so the position inside the group does not change its value.
     */
    private StringBuilder reify(Block block, String tokPrefix, List<String> toksOut) {
        StringBuilder w = reify(block.patterns, tokPrefix, toksOut);
        w.append(Filters.emit(block.filters));
        return w;
    }

    private StringBuilder reify(List<StatementPattern> sps, String tokPrefix, List<String> toksOut) {
        StringBuilder w = new StringBuilder();
        for (int i = 0; i < sps.size(); i++) {
            String t = generated(tokPrefix + i);
            toksOut.add(t);
            w.append(scheme.reify(sps.get(i), t));
        }
        return w;
    }

    /**
     * Emit (into {@code where}) the BINDs that hash each product child and sort
     * the hashes with a comparator network, and return the canonical product-key
     * expression {@code CONCAT("T","|",<sorted child hashes>)}.
     *
     * This makes a ⊗-gate's content address canonical, order-independent and
     * collision-resistant over its child MULTISET, so products that differ only
     * in derivation order (e.g. a self-join's two orderings) collapse to one gate.
     * Each child is first SHA256-hashed to fixed-width hex (delimiter-safe), the
     * hex strings are sorted (bubble/comparator network in pure SPARQL 1.1), and
     * concatenated; the caller hashes the result into the gate IRI. The fixed-width
     * preimage preserves child-hash boundaries and multiplicity, while SHA-256 is
     * collision-resistant rather than mathematically collision-free.
     */
    private String emitSortedProdKey(StringBuilder where, List<String> toks) {
        int k = toks.size();
        String[] cur = new String[k];
        int[] fresh = {0};
        for (int i = 0; i < k; i++) {
            String v = generated("srt" + (fresh[0]++));
            where.append("  BIND(SHA256(STR(?").append(toks.get(i)).append(")) AS ?").append(v).append(")\n");
            cur[i] = v;
        }
        // comparator network (bubble sort) over the child hashes
        for (int pass = 0; pass < k - 1; pass++) {
            for (int i = 0; i < k - 1 - pass; i++) {
                String a = "?" + cur[i], b = "?" + cur[i + 1];
                String lo = generated("srt" + (fresh[0]++));
                String hi = generated("srt" + (fresh[0]++));
                where.append("  BIND(IF(").append(a).append(" <= ").append(b).append(", ")
                     .append(a).append(", ").append(b).append(") AS ?").append(lo).append(")\n");
                where.append("  BIND(IF(").append(a).append(" <= ").append(b).append(", ")
                     .append(b).append(", ").append(a).append(") AS ?").append(hi).append(")\n");
                cur[i] = lo;
                cur[i + 1] = hi;
            }
        }
        StringBuilder key = new StringBuilder("CONCAT(\"T\"");
        for (int i = 0; i < k; i++) {
            key.append(", \"|\", ?").append(cur[i]);
        }
        key.append(")");
        return key.toString();
    }

    private static String ansKey(List<String> W, Set<String> bound) { return ansKey(W, bound, "A"); }

    /** CONCAT("tag", "|x=", STR(?x), "|y=", (bound?STR(?y):"NULL"), ...) — group/answer key. */
    private static String ansKey(List<String> vars, Set<String> bound, String tag) {
        StringBuilder sb = new StringBuilder("CONCAT(\"").append(tag).append("\"");
        for (String v : vars) {
            sb.append(", \"|").append(v).append("=\", ");
            // Runtime BOUND guard even for statically-"bound" vars: in a UNION arm that does not bind a
            // projected var (heterogeneous UNION), or a dynamically-unbound OPTIONAL var, raw STR(?v) is a
            // type error that leaves ?anskey UNBOUND -> the whole c:answer triple is dropped by CONSTRUCT.
            // c:answer is only a debug label, but its loss must not depend on runtime bindings.
            sb.append(bound.contains(v) ? "IF(BOUND(?" + v + "), STR(?" + v + "), \"NULL\")" : "\"NULL\"");
        }
        sb.append(")");
        return sb.toString();
    }

    private static String bind(String var, String expr) {
        return "  BIND(" + expr + " AS " + var + ")\n";
    }

    private static String bindIri(String var, String prefix, String keyExpr) {
        return "  BIND(IRI(CONCAT(\"" + prefix + "\", SHA256(" + keyExpr + "))) AS " + var + ")\n";
    }

    // ---- collision-resistant, term-type-aware gate IDENTITY key (fix for the STR-collision bug) ----
    /** Fixed-width SHA256 encoding of ONE binding that distinguishes bound/unbound, IRI vs literal vs
     *  blank node, and a literal's lexical + datatype + (lower-cased) language tag. Every part is hashed
     *  BEFORE concatenation, so the result carries no re-segmentable delimiter (same discipline
     *  emitSortedProdKey() uses for product children). SPARQL-1.1-only (BOUND/isIRI/isBlank/isLiteral/
     *  STR/DATATYPE/LANG/LCASE/SHA256) -> deterministic across engines. NOTE: collision-resistant modulo
     *  SHA256, not mathematically injective; the injective part is the delimiter-free serialization. */
    private static String termHash(String label, String term) {
        String enc =
            "IF(!BOUND(" + term + "), \"u\","
          + " IF(isIRI(" + term + "), CONCAT(\"i\", SHA256(STR(" + term + "))),"
          + " IF(isBlank(" + term + "), CONCAT(\"b\", SHA256(STR(" + term + "))),"
          + " IF(isLiteral(" + term + "), CONCAT(\"l\", SHA256(STR(" + term + ")),"
          + " SHA256(STR(DATATYPE(" + term + "))), SHA256(LCASE(LANG(" + term + ")))),"
          + " \"x\"))))";                                         // "x" = non-1.1 term (e.g. RDF-star quoted triple)
        return "SHA256(CONCAT(\"" + label + "=\", " + enc + "))";
    }
    /** Deterministic hex SHA-256 of a string, computed HERE at rewrite time (not in SPARQL). Used only
     *  for the per-path gate fingerprint (a compile-time constant), so it stays a plain hex token safe to
     *  embed in a CONSTRUCT literal. Deterministic => same path query stays byte-identical/idempotent. */
    private static String sha256hex(String s) {
        try {
            byte[] d = java.security.MessageDigest.getInstance("SHA-256")
                       .digest(s.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(64);
            for (byte b : d) sb.append(Character.forDigit((b >> 4) & 0xF, 16)).append(Character.forDigit(b & 0xF, 16));
            return sb.toString();
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new RuntimeException(e);                         // SHA-256 is guaranteed on every JVM
        }
    }

    /** Canonical fingerprint of one BGP operand (independent of the physical leaf-reification syntax). */
    private String bgpFingerprint(Block block) {
        return sha256hex(bgpSemanticKey(block));
    }

    /**
     * Fingerprint the semantic anti-join operator, not its traversal position.  Equivalent repeated
     * DIFFs therefore hash-cons, while a different right operand (the historical
     * {@code {A MINUS P} UNION {A MINUS Q}} collision) necessarily gets a different SUB/M identity.
     * Right UNION alternatives are sorted because Boolean union is associative/commutative/idempotent.
     */
    private String diffFingerprint(Block left, List<Block> rights) {
        List<String> rkeys = new ArrayList<>();
        for (Block right : rights) rkeys.add(bgpSemanticKey(right));
        Collections.sort(rkeys);
        StringBuilder key = new StringBuilder("DIFF|").append(part(bgpSemanticKey(left)));
        String previous = null;
        for (String rkey : rkeys) {
            if (!rkey.equals(previous)) key.append(part(rkey));       // UNION duplicate is Boolean-idempotent
            previous = rkey;
        }
        return sha256hex(key.toString());
    }

    /**
     * Semantic key of a BGP operand: its pattern key plus its FILTER conditions. The filters must be
     * in the key — two operands differing only by a filter denote different relations, so their ⊕/⊖
     * gates must not hash-cons (e.g. {@code A MINUS (P FILTER φ1)} and {@code A MINUS (P FILTER φ2)}
     * inside one query). The condition list is already canonically sorted by {@link Filters#of}.
     */
    private static String bgpSemanticKey(Block block) {
        StringBuilder out = new StringBuilder(bgpSemanticKey(block.patterns));
        for (String condition : block.filters) out.append("FLT").append(part(condition));
        return out.toString();
    }

    /** BGP conjunction is order/association independent, so sort its fully typed pattern keys. */
    private static String bgpSemanticKey(List<StatementPattern> patterns) {
        List<String> keys = new ArrayList<>();
        for (StatementPattern sp : patterns) {
            keys.add("SP" + part(varSemanticKey(sp.getSubjectVar()))
                    + part(varSemanticKey(sp.getPredicateVar()))
                    + part(varSemanticKey(sp.getObjectVar())));
        }
        Collections.sort(keys);
        StringBuilder out = new StringBuilder("BGP");
        for (String key : keys) out.append(part(key));
        return out.toString();
    }

    private static String varSemanticKey(Var v) {
        if (v == null) return "N";
        if (v.hasValue()) return "C" + part(Terms.value(v.getValue()));
        return (v.isAnonymous() ? "X" : "V") + part(v.getName());
    }

    private static String part(String value) {
        return value.length() + ":" + value;
    }

    private static List<String> canonicalVars(Set<String> vars) {
        List<String> out = new ArrayList<>(vars);
        Collections.sort(out);
        return out;
    }

    /** Initialize a deterministic namespace which cannot overlap any parsed query variable. */
    private void initializeGeneratedVariables(TupleExpr te) {
        Set<String> user = new HashSet<>(te.getBindingNames());
        te.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override public void meet(Var v) {
                if (v.getName() != null) user.add(v.getName());
            }
        });
        int n = 0;
        while (true) {
            String candidate = "__sc" + n + "_";
            boolean collision = false;
            for (String name : user) {
                if (name.startsWith(candidate)) { collision = true; break; }
            }
            if (!collision) {
                generatedPrefix = candidate;
                return;
            }
            n++;
        }
    }

    private String generated(String hint) { return generatedPrefix + hint; }
    private String qv(String hint) { return "?" + generated(hint); }

    /** Gate-IDENTITY key: CONCAT("tag", termHash(v1), termHash(v2), ...). Replaces raw-STR ansKey for
     *  every gate IRI; bound/unbound is decided at RUNTIME by BOUND() inside termHash. */
    private static String idKey(List<String> vars, String tag) {
        StringBuilder sb = new StringBuilder("CONCAT(\"").append(tag).append("\"");
        for (String v : vars) sb.append(", ").append(termHash(v, "?" + v));
        sb.append(")");
        return sb.toString();
    }
    /** CONSTRUCT triples recording each projected var's binding as recoverable RDF (preserves term
     *  type/datatype/lang). Unbound vars get a binding node with no c:val. `?ans` must already be bound. */
    private String bindingCtor(List<String> vars) {
        StringBuilder sb = new StringBuilder();
        for (String v : vars) {
            String b = qv("b_" + v);
            sb.append("  ").append(qv("ans")).append(" c:binding ").append(b).append(" . ").append(b)
              .append(" c:var \"").append(v).append("\" ; c:val ?").append(v).append(" .\n");
        }
        return sb.toString();
    }
    private String bindingWhere(List<String> vars) {
        StringBuilder sb = new StringBuilder();
        for (String v : vars) {
            String b = qv("b_" + v);
            sb.append("  BIND(IRI(CONCAT(STR(").append(qv("ans")).append("), \"#").append(v)
              .append("\")) AS ").append(b).append(")\n");
        }
        return sb.toString();
    }

    private static List<StatementPattern> collect(TupleExpr te) {
        assertPureBgp(te);                       // fail fast; don't silently drop out-of-fragment ops
        return StatementPatternCollector.process(te);
    }

    /**
     * A BGP operand as the rewriting consumes it: its triple patterns together with the FILTER
     * conditions scoping over them (Def. 4.5, clause 6). The filters build no gate and leave every
     * gate identity unchanged — they only remove the matches whose annotation is 0 — but they are
     * part of the operand, so they enter both the reified group and the operand's fingerprint.
     */
    private static final class Block {
        final List<StatementPattern> patterns;
        final List<String> filters;              // rendered, canonically sorted; empty if unfiltered

        Block(List<StatementPattern> patterns, List<String> filters) {
            this.patterns = patterns;
            this.filters = filters;
        }

        boolean isEmpty()     { return patterns.isEmpty(); }
        boolean isFiltered()  { return !filters.isEmpty(); }

        /** The operand of an OPTIONAL's AND-branch: the conjunction of both operands. */
        static Block concat(Block a, Block b) {
            List<StatementPattern> p = new ArrayList<>(a.patterns); p.addAll(b.patterns);
            List<String> f = new ArrayList<>(a.filters);
            for (String c : b.filters) if (!f.contains(c)) f.add(c);   // conjunction, idempotent
            Collections.sort(f);
            return new Block(p, f);
        }
    }

    private static Block collectBlock(TupleExpr te) {
        return new Block(collect(te), Filters.of(te));
    }

    /** DISTINCT and RDF4J's path-expansion Projection are set-semantic wrappers, not BGP operators. */
    private static TupleExpr unwrapSetWrappers(TupleExpr body) {
        TupleExpr current = body;
        while (current instanceof Distinct || current instanceof Projection) {
            current = current instanceof Distinct
                    ? ((Distinct) current).getArg()
                    : ((Projection) current).getArg();
        }
        return current;
    }

    private static boolean isPureBgp(TupleExpr body) {
        try {
            assertPureBgp(body);
            return true;
        } catch (UnsupportedOperationException unsupported) {
            return false;
        }
    }

    /**
     * Fail fast if a subtree we are about to treat as a BGP contains anything
     * outside the supported fragment — BIND/Extension, a subquery, a
     * nested UNION/OPTIONAL/MINUS operand, or a property path. Without this,
     * {@link StatementPatternCollector} would silently ignore such a node and we
     * would emit a circuit for the WRONG query (e.g. — as a
     * fixed past bug — compiling UNION as a join).
     *
     * <p>FILTER is inside the fragment (Def. 4.5, clause 6) and is therefore admitted here; the
     * condition itself is validated and rendered by {@link Filters}, which rejects anything it
     * cannot reproduce, so a filter is still never silently dropped. Note that {@code Filter} nodes
     * are transparent to {@link StatementPatternCollector}, so the pattern list is unaffected.
     * The NPCS string rewriter keeps its own, stricter guard: it has no filter rule.
     */
    private static void assertPureBgp(TupleExpr body) {
        String[] bad = {null};
        body.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override protected void meetNode(QueryModelNode node) {
                if (bad[0] != null) return;
                if (node instanceof StatementPattern) return;                 // leaf — fine
                if (node instanceof Join) { super.meetNode(node); return; }   // recurse into args
                if (node instanceof Filter) {
                    // σ builds no gate. Recurse into the pattern only: the CONDITION is validated and
                    // rendered by Filters, which knows which expressions it can reproduce (and which,
                    // like EXISTS, carry a pattern of their own).
                    ((Filter) node).getArg().visit(this);
                    return;
                }
                bad[0] = node.getClass().getSimpleName();                     // anything else: reject
            }
        });
        if (bad[0] != null) {
            throw new UnsupportedOperationException(
                "Unsupported operator in BGP position: " + bad[0] + ". Supported fragment = "
                + "BGP/AND, FILTER, UNION, OPTIONAL, MINUS (no BIND/subquery/property paths).");
        }
    }

    private static LinkedHashSet<String> vars(List<StatementPattern> sps) {
        LinkedHashSet<String> out = new LinkedHashSet<>();
        for (StatementPattern sp : sps) {
            add(out, sp.getSubjectVar()); add(out, sp.getPredicateVar()); add(out, sp.getObjectVar());
        }
        return out;
    }

    /** All real variables occurring in a (sub)expression's triple patterns (structure-agnostic). */
    private static LinkedHashSet<String> varsOf(TupleExpr te) {
        return vars(StatementPatternCollector.process(te));
    }

    private static void add(Set<String> s, Var v) {
        if (v != null && !v.hasValue() && !v.isAnonymous()) {
            s.add(v.getName());
        }
    }

    private static Set<String> intersect(Set<String> a, Set<String> b) {
        LinkedHashSet<String> s = new LinkedHashSet<>(a); s.retainAll(b); return s;
    }

    private static Set<String> setOf(List<String> l) { return new LinkedHashSet<>(l); }

    private static Projection outerProjection(TupleExpr te) {
        Projection[] found = new Projection[1];
        te.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override public void meet(Projection node) { if (found[0] == null) found[0] = node; }
        });
        if (found[0] == null) throw new IllegalArgumentException("Only SELECT queries supported.");
        return found[0];
    }

    /** Reject solution-sequence modifiers we cannot honor on a materialized circuit
     *  (LIMIT/OFFSET/ORDER BY). DISTINCT is allowed — an implicit no-op, since the circuit's
     *  content-addressed answer gates already give set semantics. */
    private static void rejectSequenceModifiers(TupleExpr te) {
        String[] bad = {null};
        te.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override protected void meetNode(QueryModelNode node) {
                if (bad[0] != null) return;
                if (node instanceof Slice) bad[0] = "LIMIT/OFFSET";
                else if (node instanceof Order) bad[0] = "ORDER BY";
                super.meetNode(node);
            }
        });
        if (bad[0] != null) {
            throw new UnsupportedOperationException("Unsupported solution modifier: " + bad[0]
                + ". SPARQL_circ computes the full set of answer probabilities; sequence modifiers "
                + "do not apply. (DISTINCT is implicit — answers are a set.)");
        }
    }

    // =========================== PROPERTY PATHS ===========================
    // Recursive provenance for SPARQL 1.1 arbitrary-length paths (:p+ / :p*), emitted by an
    // UNMODIFIED engine via a CLIENT-DRIVEN ITERATIVE protocol (CircuitRun drives the loop).
    // reach gates are keyed by (level, node) -- the level makes the emitted RDF an ACYCLIC DAG
    // even on cyclic data -- while composition Times gates are content-addressed by their sorted
    // child hashes (as elsewhere). First cut: BOUND source, single constant predicate, free
    // object (<A> :p+ ?y / <A> :p* ?y), Standard reification.
    // BOUNDARY: IRI frontier only. The client BFS reads each reached node via Value.stringValue() and
    // valuesClause() re-wraps it as <...>, so a blank-node or literal path node is coerced to an IRI and
    // a path continuing THROUGH such a node can be missed. All benchmark paths are IRI→IRI; general
    // (blank/literal) frontiers need skolemization or a typed VALUES and are NOT yet supported.
    private static final String RDF_S = "http://www.w3.org/1999/02/22-rdf-syntax-ns#subject";
    private static final String RDF_P = "http://www.w3.org/1999/02/22-rdf-syntax-ns#predicate";
    private static final String RDF_O = "http://www.w3.org/1999/02/22-rdf-syntax-ns#object";

    /** Parse an arbitrary-length path query; {@code null} if the query has no such path. */
    public PathQuery pathQuery(String query) {
        ParsedQuery pq = new SPARQLParser().parseQuery(query, null);
        QueryGuard.rejectDatasetsAndNamedGraphs(pq);
        TupleExpr te = pq.getTupleExpr();
        initializeGeneratedVariables(te);
        ArbitraryLengthPath[] found = {null};
        te.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override public void meet(ArbitraryLengthPath node) { if (found[0] == null) found[0] = node; }
        });
        if (found[0] == null) return null;                       // not a path query (let plan() handle it)
        rejectSequenceModifiers(te);                             // same fail-fast as plan(): LIMIT/OFFSET/ORDER BY
                                                                 // (a Slice can wrap the Projection above the path)
        if (scheme != Reification.STANDARD)
            throw new UnsupportedOperationException("Property paths currently support Standard reification only.");
        if (!(outerProjection(te).getArg() instanceof ArbitraryLengthPath))
            throw new UnsupportedOperationException("Property path must be the whole pattern for now (no join/union/minus with a path yet).");
        ArbitraryLengthPath alp = found[0];
        Var s = alp.getSubjectVar(), o = alp.getObjectVar();
        String subjName = s.getName(), objName = o.getName();
        if (subjName.equals(objName))
            throw new UnsupportedOperationException("Property path: identical subject/object variable not supported.");
        // Decompose the (compound) sub-path into BGP branches, endpoints substituted to ?u/?v so the
        // base relation is all-pairs. unionBranches -> collect asserts pure BGP, so a NESTED closure or
        // zero-length path inside the sub-path is rejected fail-fast.
        List<List<StatementPattern>> branches = new ArrayList<>();
        for (List<StatementPattern> br : unionBranches(alp.getPathExpression())) {
            List<StatementPattern> nb = new ArrayList<>();
            for (StatementPattern sp : br)
                nb.add(subst(sp, subjName, objName, generated("u"), generated("v")));
            branches.add(nb);
        }
        List<String> W = new ArrayList<>();
        for (ProjectionElem pe : outerProjection(te).getProjectionElemList().getElements())
            if (!W.contains(pe.getName())) W.add(pe.getName());
        boolean star = alp.getMinLength() == 0;
        // Materialize the all-pairs BASE relation (rlvl "base") here (reify needs the instance): one
        // CONSTRUCT per UNION alternative, each a ⊗ over the branch's reified patterns. reach^0 is then
        // seeded FROM the base (see PathQuery.init), restricted to the source when it is bound.
        List<String> branchWheres = new ArrayList<>();           // reified branch WHEREs (bind ?u,?v) — reused for the G1 BFS
        List<StringBuilder> whereFull = new ArrayList<>();        // + sort-network binds (drives the base ⊗ key)
        List<List<String>> tokss = new ArrayList<>();
        List<String> tkeys = new ArrayList<>();
        for (List<StatementPattern> br : branches) {
            List<String> toks = new ArrayList<>();
            StringBuilder where = reify(br, "a", toks);          // binds ?u,?v (+ intermediates) and the tokens
            branchWheres.add(where.toString());                  // clean reify WHERE (pre sort-network) = the path IDENTITY
            tkeys.add(emitSortedProdKey(where, toks));           // now `where` also carries the sort-network binds
            whereFull.add(where); tokss.add(toks);
        }
        // Per-path FINGERPRINT: a deterministic hash of the reified sub-path. Threaded into every reach/base
        // gate IRI AND matched via a c:rpath guard on every step/seed/project pattern, so two DIFFERENT path
        // queries on the SAME writable endpoint can never compose with (or collapse onto) each other's
        // persisted reach/base gates. Same query => same fp => byte-identical, idempotent re-runs.
        String fp = sha256hex(String.join("", branchWheres) + "star=" + star);   // star MUST be in the fp: :p* persists zero-length gates :p+ must not read
        String times = qv("t"), reach = qv("rg"), u = qv("u"), v = qv("v");
        List<String> baseC = new ArrayList<>();
        for (int i = 0; i < branches.size(); i++) {
            StringBuilder where = whereFull.get(i);
            StringBuilder q = new StringBuilder(PRE);
            q.append("CONSTRUCT {\n  ").append(PathQuery.reachHead("base", fp, generatedPrefix))
             .append(" .\n  ").append(times).append(" a c:Times ;");
            for (String t : tokss.get(i)) q.append(" c:in ?").append(t).append(" ;");
            q.append(" c:feeds ").append(reach).append(" .\n}\nWHERE {\n").append(where)
             .append(PathQuery.reachIri(u, v, "base", fp, generatedPrefix))
             .append(bindIri(times, "urn:g:t:", tkeys.get(i))).append("}\n");
            baseC.add(q.toString());
        }
        return new PathQuery(baseC, branchWheres, endOf(s, subjName), endOf(o, objName),
                star, W, fp, generatedPrefix);
    }

    private static PathQuery.End endOf(Var v, String name) {
        return v.hasValue() ? new PathQuery.End(false, Terms.render(v), name)
                            : new PathQuery.End(true, null, name);
    }
    // Substitute a path sub-pattern's endpoints (the ALP subject/object vars) with fresh ?u/?v so the
    // base relation is computed all-pairs; internal (intermediate) vars are kept.
    private static StatementPattern subst(StatementPattern sp, String subjName, String objName,
                                          String generatedSubject, String generatedObject) {
        return new StatementPattern(sv(sp.getSubjectVar(), subjName, objName, generatedSubject, generatedObject),
                                    (Var) sp.getPredicateVar().clone(),
                                    sv(sp.getObjectVar(), subjName, objName, generatedSubject, generatedObject));
    }
    private static Var sv(Var v, String subjName, String objName,
                          String generatedSubject, String generatedObject) {
        if (v == null) return null;
        if (v.getName().equals(subjName)) return new Var(generatedSubject);
        if (v.getName().equals(objName)) return new Var(generatedObject);
        return (Var) v.clone();
    }

    /**
     * An arbitrary-length path plan generating the iterative CONSTRUCTs. The (possibly compound)
     * sub-path `e` is materialized ONCE as an all-pairs base relation reach^0(u,v) — one branch per
     * UNION alternative, each a ⊗ over the branch's reified patterns (reusing reify/emitSortedProdKey).
     * `e+` is then the level-indexed closure reach^{k+1} = reach^k ⊕ (reach^k ∘ reach^0); `e*` adds the
     * zero-length pairs. reach gates are keyed by (level, from, to) — the level keeps the DAG acyclic on
     * cyclic data. All endpoint modes (a bound endpoint is filtered in FINAL). Standard reification.
     */
    public static final class PathQuery {
        static final class End {                                 // a path endpoint: variable or constant
            final boolean isVar; final String iri, var;
            End(boolean isVar, String iri, String var) { this.isVar = isVar; this.iri = iri; this.var = var; }
            String pat(String v) { return isVar ? v : iri; }     // SPARQL term to match this endpoint
        }
        private final List<String> baseConstructs;               // the all-pairs base relation (rlvl "base")
        private final List<String> branchWheres;                 // reified sub-path branches (bind ?u,?v), for the BFS
        private final End subj, obj; private final boolean star;
        private final List<String> W;
        private final String fp;                                 // per-path fingerprint (isolates reach/base gates)
        private final String gp;                                 // capture-avoiding generated-variable prefix
        private java.util.Set<String> reachable;                 // G1: if set (bound source), restrict base to ?u ∈ here
        PathQuery(List<String> baseConstructs, List<String> branchWheres, End subj, End obj,
                  boolean star, List<String> W, String fp, String generatedPrefix) {
            this.baseConstructs = baseConstructs; this.branchWheres = branchWheres;
            this.subj = subj; this.obj = obj; this.star = star; this.W = W; this.fp = fp;
            this.gp = generatedPrefix;
        }
        private static String pv(String gp, String hint) { return "?" + gp + hint; }
        private String v(String hint) { return pv(gp, hint); }
        /** The per-path fingerprint (for logging / cross-checking gate isolation). */
        public String fingerprint() { return fp; }
        /** G1: is the path source a bound constant? (only then can we restrict the base to a reachable subgraph). */
        public boolean boundSource() { return !subj.isVar; }
        public String sourceValue() { return subj.iri.replaceAll("^<|>$", ""); }   // bare source IRI
        public void setReachable(java.util.Set<String> r) { this.reachable = r; }
        /** Binding names consumed by CircuitRun; generated names must not be hard-coded by the client. */
        public String frontierValueBinding() { return gp + "v"; }
        public String nodeCountBinding() { return gp + "c"; }
        /** G1 BFS step: the sub-path targets ?v reachable in ONE hop from ?u ∈ frontier (read-only). */
        public String frontierStepQuery(java.util.Collection<String> frontier) {
            StringBuilder q = new StringBuilder(PRE).append("SELECT DISTINCT ").append(v("v"))
                    .append(" WHERE {\n").append(valuesClause(frontier));
            for (int i = 0; i < branchWheres.size(); i++)
                q.append(i == 0 ? "  { " : "  UNION { ").append(branchWheres.get(i)).append(" }\n");
            return q.append("}\n").toString();
        }
        private String valuesClause(java.util.Collection<String> nodes) {
            StringBuilder sb = new StringBuilder("  VALUES ").append(v("u")).append(" {");
            for (String n : nodes) sb.append(" <").append(n).append(">");
            return sb.append(" }\n").toString();
        }
        private static String injectValues(String construct, String vals) {   // insert VALUES right after WHERE {
            int i = construct.indexOf("WHERE {\n");
            return i < 0 ? construct : construct.substring(0, i + 8) + vals + construct.substring(i + 8);
        }
        // A BOUND source restricts reach^0 (and zero-length) to (source, .) -> single-source
        // O(|V_s|.|E_s|); a variable source keeps all pairs. The from-var in reach heads is ?u.
        private String sourceFilter() { return subj.isVar ? "" : "  FILTER(" + v("u") + " = " + subj.iri + ")\n"; }
        static String reachIri(String from, String to, String lvl, String fp, String gp) {  // reach gate IRI = f(path fp, level, from, to)
            return "  BIND(IRI(CONCAT(\"urn:g:r:\", SHA256(CONCAT(\"R|\", \"" + fp + "|\", SHA256(\"" + lvl + "\"), \"|\", "
                 + termHash("f", from) + ", " + termHash("t", to) + ")))) AS " + pv(gp, "rg") + ")\n";
        }
        static String reachHead(String lvl, String fp, String gp) {                          // gate carries its path fp for the c:rpath guard
            return pv(gp, "rg") + " a c:Plus ; c:rlvl \"" + lvl + "\" ; c:rpath \"" + fp
                    + "\" ; c:rfrom " + pv(gp, "u") + " ; c:rto " + pv(gp, "v");
        }
        private String rpathGuard() { return " ; c:rpath \"" + fp + "\""; }       // append to any reach/base match pattern
        static String comp2(String c0, String c1, String out, String gp) {  // content-addressed 2-child ⊗ gate
            String h0 = pv(gp, "h0"), h1 = pv(gp, "h1"), lo = pv(gp, "lo"), hi = pv(gp, "hi");
            return "  BIND(SHA256(STR(" + c0 + ")) AS " + h0 + ")\n  BIND(SHA256(STR(" + c1 + ")) AS " + h1 + ")\n"
                 + "  BIND(IF(" + h0 + " <= " + h1 + ", " + h0 + ", " + h1 + ") AS " + lo + ")\n"
                 + "  BIND(IF(" + h0 + " <= " + h1 + ", " + h1 + ", " + h0 + ") AS " + hi + ")\n"
                 + "  BIND(IRI(CONCAT(\"urn:g:t:\", SHA256(CONCAT(\"T|\", " + lo + ", \"|\", " + hi + ")))) AS " + out + ")\n";
        }
        String zeroLenConstruct() {                              // reach^0(u,u) = OR of tokens mentioning u
            String u = v("u"), vv = v("v"), z = v("z"), tg = v("tg"), rg = v("rg");
            return PRE + "CONSTRUCT {\n  " + reachHead("0", fp, gp) + " .\n  " + tg
                 + " a c:Times ; c:in " + z + " ; c:feeds " + rg + " .\n}\nWHERE {\n"
                 + "  { " + z + " <" + RDF_S + "> " + u + " . } UNION { " + z + " <" + RDF_O + "> " + u + " . }\n"
                 + "  BIND(" + u + " AS " + vv + ")\n" + sourceFilter() + reachIri(u, u, "0", fp, gp)
                 + "  BIND(IRI(CONCAT(\"urn:g:t:\", SHA256(CONCAT(\"T|\", SHA256(STR(" + z + ")))))) AS " + tg + ")\n}\n";
        }
        /** Distinct-node count over the reified data, to size the loop (rounds = N-1 simple-path bound). */
        public String nodeCountQuery() {
            String n = v("n"), t = v("t");
            return PRE + "SELECT (COUNT(DISTINCT " + n + ") AS " + v("c") + ") WHERE {\n"
                 + "  { " + t + " <" + RDF_S + "> " + n + " . } UNION { " + t + " <" + RDF_O + "> " + n + " . }\n}\n";
        }
        /** Materialize the base relation, then seed reach^0 from it (restricted to the source),
         *  plus the zero-length pairs for :p*. */
        public List<String> init() {
            List<String> out = new ArrayList<>();
            if (reachable != null && !reachable.isEmpty()) {      // G1 bound source: base only FROM reachable nodes
                String vals = valuesClause(reachable);            // (never materialize the all-pairs base = OOM)
                for (String b : baseConstructs) out.add(injectValues(b, vals));
            } else {
                out.addAll(baseConstructs);                       // variable source: all-pairs base (unchanged)
            }
            out.add(seedReach0());                                // reach^0 = base, restricted to the source
            if (star) out.add(zeroLenConstruct());                // + zero-length at the source (for :p*)
            return out;
        }
        // reach^0(u,v) fed by base(u,v); sourceFilter() makes it single-source when the source is bound.
        private String seedReach0() {
            String rb = v("rb"), rg = v("rg"), u = v("u"), vv = v("v");
            return PRE + "CONSTRUCT {\n  " + reachHead("0", fp, gp) + " .\n  " + rb + " c:feeds " + rg + " .\n}\nWHERE {\n"
                 + "  " + rb + " a c:Plus ; c:rlvl \"base\"" + rpathGuard() + " ; c:rfrom " + u + " ; c:rto " + vv + " .\n"
                 + sourceFilter() + reachIri(u, vv, "0", fp, gp) + "}\n";
        }
        /** reach^{k+1} from reach^k: (A) compose reach^k(u,w) ⊗ reach^0(w,v), (B) carry forward. */
        public List<String> step(int k) {
            String kL = "\"" + k + "\"", k1 = Integer.toString(k + 1);
            String rg = v("rg"), rk = v("rk"), rb = v("rb"), comp = v("comp");
            String u = v("u"), vv = v("v"), w = v("w");
            List<String> out = new ArrayList<>();
            out.add(PRE + "CONSTRUCT {\n  " + reachHead(k1, fp, gp) + " .\n"       // (A) reach^k(u,w) (*) base(w,v)
                  + "  " + comp + " a c:Times ; c:in " + rk + " ; c:in " + rb + " ; c:feeds " + rg + " .\n}\nWHERE {\n"
                  + "  " + rk + " a c:Plus ; c:rlvl " + kL + rpathGuard() + " ; c:rfrom " + u + " ; c:rto " + w + " .\n"
                  + "  " + rb + " a c:Plus ; c:rlvl \"base\"" + rpathGuard() + " ; c:rfrom " + w + " ; c:rto " + vv + " .\n"
                  + reachIri(u, vv, k1, fp, gp) + comp2(rk, rb, comp, gp) + "}\n");
            out.add(PRE + "CONSTRUCT {\n  " + reachHead(k1, fp, gp) + " .\n  " + rk + " c:feeds " + rg + " .\n}\nWHERE {\n"  // (B) carry
                  + "  " + rk + " a c:Plus ; c:rlvl " + kL + rpathGuard() + " ; c:rfrom " + u + " ; c:rto " + vv + " .\n"
                  + reachIri(u, vv, k1, fp, gp) + "}\n");
            return out;
        }
        /** Project reach^{lastLevel} to answer gates, filtering bound endpoints and keying by the free ones. */
        public List<String> projectAnswers(int lastLevel) {
            String fromPat = subj.pat(v("u")), toPat = obj.pat(v("v"));
            java.util.List<String[]> proj = new ArrayList<>();          // {var, term(?u|?v)}
            for (String w : W) {
                String term = subj.isVar && w.equals(subj.var) ? v("u")
                            : (obj.isVar && w.equals(obj.var) ? v("v") : null);
                if (term != null) proj.add(new String[]{w, term});
            }
            StringBuilder rk = new StringBuilder("CONCAT(\"A\""), idk = new StringBuilder("CONCAT(\"A\"");
            StringBuilder ctor = new StringBuilder(), binds = new StringBuilder();
            String ans = v("ans"), anskey = v("anskey"), rg = v("rg");
            for (String[] p : proj) {
                rk.append(", \"|").append(p[0]).append("=\", STR(").append(p[1]).append(")");
                idk.append(", ").append(termHash(p[0], p[1]));
                String b = v("b_" + p[0]);
                ctor.append("  ").append(ans).append(" c:binding ").append(b).append(" . ").append(b)
                    .append(" c:var \"").append(p[0]).append("\" ; c:val ").append(p[1]).append(" .\n");
                binds.append("  BIND(IRI(CONCAT(STR(").append(ans).append("), \"#").append(p[0])
                    .append("\")) AS ").append(b).append(")\n");
            }
            rk.append(")"); idk.append(")");
            String q = PRE + "CONSTRUCT {\n  " + rg + " c:feeds " + ans + " .\n  " + ans
                     + " a c:Plus ; c:answer " + anskey + " .\n" + ctor + "}\nWHERE {\n"
                     + "  " + rg + " a c:Plus ; c:rlvl \"" + lastLevel + "\"" + rpathGuard()
                     + " ; c:rfrom " + fromPat + " ; c:rto " + toPat + " .\n"
                     + "  BIND(" + rk + " AS " + anskey + ")\n"
                     + "  BIND(IRI(CONCAT(\"urn:g:a:\", SHA256(" + idk + "))) AS " + ans + ")\n" + binds + "}\n";
            return java.util.Collections.singletonList(q);
        }
    }
}
