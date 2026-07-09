package npcs.circuit;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import org.eclipse.rdf4j.query.algebra.Difference;
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
import org.eclipse.rdf4j.query.algebra.helpers.AbstractQueryModelVisitor;
import org.eclipse.rdf4j.query.algebra.helpers.StatementPatternCollector;
import org.eclipse.rdf4j.query.parser.ParsedQuery;
import org.eclipse.rdf4j.query.parser.sparql.SPARQLParser;

import npcs.rewrite.Reification;

/**
 * Circuit rewriting (the paper's gamma) built on the NPCS rewriting: reified
 * triple patterns are NPCS's, but ProvProd/ProvAggSum/ProvDiff are replaced by
 * ⊗/⊕/⊖ gate constructors emitted as CONSTRUCT templates, so an unmodified
 * engine materializes a shared provenance circuit as RDF.
 *
 * A query is compiled to a <em>plan</em>: a list of CONSTRUCT queries whose
 * results are merged into one circuit (the paper's per-operator materialization).
 *   - BGP:      1 CONSTRUCT  (⊗ per derivation -> ⊕ per answer)
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

    public CircuitRewriter(Reification scheme) {
        this.scheme = scheme;
    }

    public List<String> plan(String query) {
        ParsedQuery pq = new SPARQLParser().parseQuery(query, null);
        TupleExpr te = pq.getTupleExpr();
        rejectSequenceModifiers(te);                 // LIMIT/OFFSET/ORDER BY don't apply to a circuit
        Projection projection = outerProjection(te);
        List<String> W = new ArrayList<>();
        for (ProjectionElem pe : projection.getProjectionElemList().getElements()) {
            if (!W.contains(pe.getName())) W.add(pe.getName());
        }
        return branchPlan(normalize(projection.getArg()), W);
    }

    /**
     * Normalize the algebra so every {@code Difference} (MINUS) has operands {@link
     * #branchPlan}/{@link #minusPlan} can build, by algebraically reducing composite MINUS
     * operands to the verified BGP/UNION-operand plan (all PQE-valid):
     *   (A∪B) MINUS P        ≡ (A MINUS P) ∪ (B MINUS P)
     *   (A OPT B) MINUS P    ≡ (Join(A,B) MINUS P) ∪ (A MINUS (B∪P))     [A,B share a var]
     *   P MINUS (C OPT D)    ≡ P MINUS C                                 [P shares no D-only var]
     *   (A MINUS P) MINUS Q  ≡ A MINUS (P∪Q)                             [(A∖P)∖Q = A∖(P∪Q)]
     * Residuals left for minusPlan/collect to reject safely: right-nested MINUS
     * A MINUS (P MINUS Q) (introduces a join), and the two pathological OPTIONAL shapes.
     */
    private static TupleExpr normalize(TupleExpr node) {
        if (node instanceof Union) {
            Union u = (Union) node;
            return new Union(normalize(u.getLeftArg().clone()), normalize(u.getRightArg().clone()));
        }
        if (node instanceof LeftJoin) {
            LeftJoin lj = (LeftJoin) node;
            return new LeftJoin(normalize(lj.getLeftArg().clone()), normalize(lj.getRightArg().clone()));
        }
        if (node instanceof Difference) {
            Difference d = (Difference) node;
            TupleExpr nl = normalize(d.getLeftArg().clone());
            TupleExpr nr = normalize(d.getRightArg().clone());
            // OPTIONAL left operand: (A OPT B) MINUS P ≡ (Join(A,B) MINUS P) ∪ (A MINUS (B ∪ P)).
            // Valid when A,B share a variable (then A DIFF B = A MINUS B, and (A∖B)∖P = A∖(B∪P)).
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
    private List<String> branchPlan(TupleExpr body, List<String> W) {
        if (body instanceof Union) {
            Union u = (Union) body;
            List<String> plan = new ArrayList<>(branchPlan(u.getLeftArg(), W));
            plan.addAll(branchPlan(u.getRightArg(), W));
            return plan;
        }
        if (body instanceof Difference) {
            return minusPlan((Difference) body, W);      // handles the shared-var guard + UNION right operand
        }
        if (body instanceof LeftJoin) {
            return optionalPlan((LeftJoin) body, W);
        }
        List<String> plan = new ArrayList<>();
        plan.add(bgp(collect(body), W));
        return plan;
    }

    // --------------------------- BGP ---------------------------
    private String bgp(List<StatementPattern> sps, List<String> W) {
        List<String> toks = new ArrayList<>();
        StringBuilder where = reify(sps, "a", toks);
        String tkey = emitSortedProdKey(where, toks);   // canonical (order-independent) ⊗ key
        StringBuilder q = new StringBuilder(PRE);
        q.append("CONSTRUCT {\n  ?t a c:Times ;");
        for (String t : toks) q.append(" c:in ?").append(t).append(" ;");
        q.append(" c:feeds ?ans .\n  ?ans a c:Plus ; c:answer ?anskey .\n}\nWHERE {\n").append(where);
        q.append(bind("?anskey", ansKey(W, setOf(W))));                 // literal key
        q.append(bindIri("?t", "urn:g:t:", tkey));
        q.append(bindIri("?ans", "urn:g:a:", "?anskey"));
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
    private List<String> minusPlan(Difference diff, List<String> W) {
        List<StatementPattern> L = collect(diff.getLeftArg());
        LinkedHashSet<String> V1 = vars(L);
        List<List<StatementPattern>> removing = new ArrayList<>();
        for (List<StatementPattern> Rb : unionBranches(diff.getRightArg())) {
            if (!intersect(V1, vars(Rb)).isEmpty()) removing.add(Rb);   // overlap ⇒ can remove
        }
        List<String> plan = new ArrayList<>();
        if (removing.isEmpty()) {                                      // no overlap ⇒ MINUS is a no-op
            plan.add(bgp(L, W));
            return plan;
        }
        plan.add(productPlus(L, "a", "urn:g:p1:", "P1", V1));          // ⊗ -> ⊕_{P1}(V1)
        for (List<StatementPattern> Rb : removing) {
            LinkedHashSet<String> V2 = vars(Rb);
            plan.add(productPlus(Rb, "b", "urn:g:p2:", "P2", V2));     // ⊗ -> ⊕_{P2}(V2)
            plan.add(subFeeds(L, Rb, V1, V2));                        // ⊕_{P2} -> ⊕_{sub}(V1)
        }
        plan.add(minusRoot(L, V1, W));                                 // ⊖(⊕_{P1}, ⊕_{sub}) -> answer
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

    /** ⊗ per derivation feeding a ⊕ gate keyed by {@code groupVars}. */
    private String productPlus(List<StatementPattern> sps, String tokPrefix,
                               String plusPrefix, String groupTag, LinkedHashSet<String> groupVars) {
        List<String> toks = new ArrayList<>();
        StringBuilder where = reify(sps, tokPrefix, toks);
        String tkey = emitSortedProdKey(where, toks);   // canonical (order-independent) ⊗ key
        StringBuilder q = new StringBuilder(PRE);
        q.append("CONSTRUCT {\n  ?t a c:Times ;");
        for (String t : toks) q.append(" c:in ?").append(t).append(" ;");
        q.append(" c:feeds ?pg .\n  ?pg a c:Plus .\n}\nWHERE {\n").append(where);
        q.append(bindIri("?t", "urn:g:t:", tkey));
        q.append(bindIri("?pg", plusPrefix, ansKey(new ArrayList<>(groupVars), groupVars, groupTag)));
        q.append("}\n");
        return q.toString();
    }

    /** For each compatible (P1,P2) pair, ⊕_{P2}(μ2) feeds ⊕_{sub}(μ1). */
    private String subFeeds(List<StatementPattern> L, List<StatementPattern> R,
                            LinkedHashSet<String> V1, LinkedHashSet<String> V2) {
        List<String> ta = new ArrayList<>(), tb = new ArrayList<>();
        StringBuilder where = reify(L, "a", ta);
        where.append(reify(R, "b", tb));                                 // natural join on shared vars
        StringBuilder q = new StringBuilder(PRE);
        q.append("CONSTRUCT {\n  ?p2 c:feeds ?sub .\n}\nWHERE {\n").append(where);
        q.append(bindIri("?p2", "urn:g:p2:", ansKey(new ArrayList<>(V2), V2, "P2")));
        q.append(bindIri("?sub", "urn:g:sub:", ansKey(new ArrayList<>(V1), V1, "SUB")));
        q.append("}\n");
        return q.toString();
    }

    /** ⊖(⊕_{P1}(μ1), ⊕_{sub}(μ1)) feeding the answer ⊕, keyed by the projection. */
    private String minusRoot(List<StatementPattern> L, LinkedHashSet<String> V1, List<String> W) {
        List<String> ta = new ArrayList<>();
        StringBuilder where = reify(L, "a", ta);
        StringBuilder q = new StringBuilder(PRE);
        q.append("CONSTRUCT {\n");
        q.append("  ?m a c:Minus ; c:minuend ?p1 ; c:subtrahend ?sub ; c:feeds ?ans .\n");
        q.append("  ?sub a c:Plus .\n");
        q.append("  ?ans a c:Plus ; c:answer ?anskey .\n}\nWHERE {\n").append(where);
        q.append(bindIri("?p1", "urn:g:p1:", ansKey(new ArrayList<>(V1), V1, "P1")));
        q.append(bindIri("?sub", "urn:g:sub:", ansKey(new ArrayList<>(V1), V1, "SUB")));
        q.append(bindIri("?m", "urn:g:m:", ansKey(new ArrayList<>(V1), V1, "M")));
        q.append(bind("?anskey", ansKey(W, V1)));                        // W vars not in V1 -> NULL
        q.append(bindIri("?ans", "urn:g:a:", "?anskey"));
        q.append("}\n");
        return q.toString();
    }

    // --------------------------- OPTIONAL = (P1 AND P2) UNION (P1 DIFF P2) ---------------------------
    private List<String> optionalPlan(LeftJoin lj, List<String> W) {
        List<StatementPattern> L = collect(lj.getLeftArg());
        List<StatementPattern> R = collect(lj.getRightArg());
        List<StatementPattern> both = new ArrayList<>(L); both.addAll(R);

        List<String> plan = new ArrayList<>();
        plan.add(bgp(both, W));                                          // AND-branch: ⊗ over P1∪P2 -> answer
        // DIFF-branch (same as MINUS): P1-only answers with ⊖
        LinkedHashSet<String> V1 = vars(L), V2 = vars(R);
        Set<String> S = intersect(V1, V2);
        plan.add(productPlus(L, "a", "urn:g:p1:", "P1", V1));
        plan.add(productPlus(R, "b", "urn:g:p2:", "P2", V2));
        if (!S.isEmpty()) plan.add(subFeeds(L, R, V1, V2));
        plan.add(minusRoot(L, V1, W));
        return plan;
    }

    // --------------------------- helpers ---------------------------
    private StringBuilder reify(List<StatementPattern> sps, String tokPrefix, List<String> toksOut) {
        StringBuilder w = new StringBuilder();
        for (int i = 0; i < sps.size(); i++) {
            String t = tokPrefix + i;
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
     * This makes a ⊗-gate's content address a canonical, collision-free function
     * of its child MULTISET (order-independent), so products that differ only in
     * derivation order (e.g. a self-join's two orderings) collapse to one gate.
     * Each child is first SHA256-hashed to fixed-width hex (delimiter-safe), the
     * hex strings are sorted (bubble/comparator network in pure SPARQL 1.1), and
     * concatenated; the caller hashes the result into the gate IRI. This is the
     * SPARQL realization of the sorted-child-id canonicalization used in the
     * reference implementation, closing the multiset-hash collision concern.
     */
    private static String emitSortedProdKey(StringBuilder where, List<String> toks) {
        int k = toks.size();
        String[] cur = new String[k];
        int[] fresh = {0};
        for (int i = 0; i < k; i++) {
            String v = "srt" + (fresh[0]++);
            where.append("  BIND(SHA256(STR(?").append(toks.get(i)).append(")) AS ?").append(v).append(")\n");
            cur[i] = v;
        }
        // comparator network (bubble sort) over the child hashes
        for (int pass = 0; pass < k - 1; pass++) {
            for (int i = 0; i < k - 1 - pass; i++) {
                String a = "?" + cur[i], b = "?" + cur[i + 1];
                String lo = "srt" + (fresh[0]++), hi = "srt" + (fresh[0]++);
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
            sb.append(bound.contains(v) ? "STR(?" + v + ")" : "\"NULL\"");
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

    private static List<StatementPattern> collect(TupleExpr te) {
        assertPureBgp(te);                       // fail fast; don't silently drop out-of-fragment ops
        return StatementPatternCollector.process(te);
    }

    /**
     * Fail fast if a subtree we are about to treat as a BGP contains anything
     * outside the supported fragment — FILTER, BIND/Extension, a subquery, a
     * nested UNION/OPTIONAL/MINUS operand, or a property path. Without this,
     * {@link StatementPatternCollector} would silently ignore such a node and we
     * would emit a circuit for the WRONG query (e.g. dropping a FILTER, or — as a
     * fixed past bug — compiling UNION as a join). Mirrors NpcsRewriter's guard.
     */
    private static void assertPureBgp(TupleExpr body) {
        String[] bad = {null};
        body.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override protected void meetNode(QueryModelNode node) {
                if (bad[0] != null) return;
                if (node instanceof StatementPattern) return;               // leaf — fine
                if (node instanceof Join) { super.meetNode(node); return; } // recurse into args
                bad[0] = node.getClass().getSimpleName();                   // anything else: reject
            }
        });
        if (bad[0] != null) {
            throw new UnsupportedOperationException(
                "Unsupported operator in BGP position: " + bad[0] + ". Supported fragment = "
                + "BGP/AND, UNION, OPTIONAL, MINUS (no FILTER/BIND/subquery/property paths).");
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
        if (v != null && !v.hasValue() && !v.isAnonymous() && !v.getName().startsWith("_")) {
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
}
