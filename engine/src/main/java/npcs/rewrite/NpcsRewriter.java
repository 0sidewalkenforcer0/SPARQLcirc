package npcs.rewrite;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import org.eclipse.rdf4j.query.algebra.ArbitraryLengthPath;
import org.eclipse.rdf4j.query.algebra.BindingSetAssignment;
import org.eclipse.rdf4j.query.algebra.Difference;
import org.eclipse.rdf4j.query.algebra.Extension;
import org.eclipse.rdf4j.query.algebra.Filter;
import org.eclipse.rdf4j.query.algebra.Join;
import org.eclipse.rdf4j.query.algebra.LeftJoin;
import org.eclipse.rdf4j.query.algebra.Projection;
import org.eclipse.rdf4j.query.algebra.Slice;
import org.eclipse.rdf4j.query.algebra.Service;
import org.eclipse.rdf4j.query.algebra.UnaryTupleOperator;
import org.eclipse.rdf4j.query.algebra.ZeroLengthPath;
import org.eclipse.rdf4j.query.algebra.ProjectionElem;
import org.eclipse.rdf4j.query.algebra.StatementPattern;
import org.eclipse.rdf4j.query.algebra.TupleExpr;
import org.eclipse.rdf4j.query.algebra.Union;
import org.eclipse.rdf4j.query.algebra.Var;
import org.eclipse.rdf4j.query.algebra.helpers.AbstractQueryModelVisitor;
import org.eclipse.rdf4j.query.algebra.helpers.StatementPatternCollector;
import org.eclipse.rdf4j.query.parser.ParsedQuery;
import org.eclipse.rdf4j.query.parser.sparql.SPARQLParser;

/**
 * NPCS query rewriter (clean-room, following Asma et al. WWW'24, Def 4.1/4.2/4.6).
 *
 * <p>Recursive rewriting {@code β} over the SPARQL algebra:
 * <ul>
 *   <li>BGP (conjunction)   — one {@code BIND(ProvProd(...))}, optimized single GROUP BY (Def 4.6)</li>
 *   <li>UNION               — {@code ProvSum} (Def 4.2 rule 4)</li>
 *   <li>MINUS / DIFF        — {@code ProvDiff} over an OPTIONAL join (rule 5)</li>
 *   <li>OPTIONAL            — {@code P1 OPTIONAL P2 ≡ (P1 AND P2) UNION (P1 DIFF P2)}</li>
 * </ul>
 * The provenance polynomial is emitted as {@code CONCAT(...)} expressions and
 * computed natively by the SPARQL endpoint.
 *
 * <p><b>Not thread-safe:</b> uses mutable gensym counters — use one instance per rewrite().
 */
public class NpcsRewriter {

    private final Reification scheme;
    private int provCounter = 0;   // ?fprovN — triple identifiers
    private int joinCounter = 0;   // ?fjoinN — per-BGP products
    private int unionCounter = 0;  // ?funionN — union sums
    private int diffCounter = 0;   // ?fdiffN — monus results
    private String generatedPrefix = "__npcs0_";
    private final Set<String> userVariableNames = new HashSet<>();
    private String provenanceOutputVariable = "finalprovennacevariable";

    public NpcsRewriter(Reification scheme) {
        this.scheme = scheme;
    }

    /** A rewritten graph-pattern fragment that BINDS/computes {@link #provVar}. */
    private static final class Frag {
        final String body;                    // graph pattern text
        final String provVar;                 // variable holding this fragment's provenance
        final LinkedHashSet<String> vars;     // in-scope (non-provenance) query variables
        Frag(String body, String provVar, LinkedHashSet<String> vars) {
            this.body = body; this.provVar = provVar; this.vars = vars;
        }
    }

    public String rewrite(String queryStr) {
        ParsedQuery pq = new SPARQLParser().parseQuery(queryStr, null);
        QueryGuard.rejectDatasetsAndNamedGraphs(pq);
        TupleExpr te = pq.getTupleExpr();
        initializeGeneratedVariables(te);

        Projection proj = outerProjection(te);
        if (proj == null) {
            throw new IllegalArgumentException("Only SELECT queries are supported.");
        }
        // B1 guard: solution modifiers stack ABOVE the projection, so β (which runs below it) never
        // sees them. A Slice would otherwise be silently dropped — the provenance would be built for
        // the FULL result set, not the LIMIT/OFFSET window. Reject it loudly at the entry.
        for (TupleExpr cur = te; cur != proj && cur instanceof UnaryTupleOperator;
                cur = ((UnaryTupleOperator) cur).getArg()) {
            if (cur instanceof Slice) {
                throw new UnsupportedOperationException(
                    "Unsupported pattern (LIMIT/OFFSET changes the answer set but not the provenance): Slice");
            }
        }
        List<String> projVars = new ArrayList<>();
        for (ProjectionElem pe : proj.getProjectionElemList().getElements()) {
            if (!projVars.contains(pe.getName())) {
                projVars.add(pe.getName());
            }
        }

        Frag top = beta(proj.getArg());
        String outputVar = userVariableNames.contains("finalprovennacevariable")
                ? generated("finalprovennacevariable") : "finalprovennacevariable";
        provenanceOutputVariable = outputVar;

        // Def 4.2 rule 6: SELECT W (ProvAggSum(?z) AS ?final) WHERE β(body) GROUP BY W
        String p = String.join(" ", withQ(projVars));
        return "SELECT " + p + " (" + Prov.aggSum(top.provVar) + " AS ?" + outputVar + ") \n"
             + "WHERE { \n" + top.body + " }" + groupBy(p);
    }

    /** Actual output binding used for the provenance column (fresh if the query owns the legacy name). */
    public String provenanceOutputVariable() {
        return provenanceOutputVariable;
    }

    // ----------------------------------------------------------------- β dispatch

    private Frag beta(TupleExpr node) {
        if (node instanceof Union) {
            return betaUnion((Union) node);
        }
        if (node instanceof LeftJoin) {
            return betaOptional((LeftJoin) node);
        }
        if (node instanceof Difference) {
            return betaMinus((Difference) node);
        }
        if (isPureBgp(node)) {
            return betaBgp(node);
        }
        throw new UnsupportedOperationException(
            "Unsupported pattern (FILTER/BIND/subquery or nested non-BGP): "
            + node.getClass().getSimpleName());
    }

    /** Conjunctive block: reify every triple, one ProvProd over all statement ids. */
    private Frag betaBgp(TupleExpr node) {
        List<StatementPattern> sps = StatementPatternCollector.process(node);
        if (sps.isEmpty()) {
            throw new IllegalArgumentException("Empty basic graph pattern.");
        }
        StringBuilder body = new StringBuilder();
        List<String> provVars = new ArrayList<>();
        for (StatementPattern sp : sps) {
            String pv = generated("fprov" + (provCounter++));
            provVars.add(pv);
            body.append(scheme.reify(sp, pv));
        }
        String joinVar = generated("fjoin" + (joinCounter++));
        body.append("\tBIND (").append(Prov.prod(provVars)).append(" AS ?").append(joinVar).append(") . \n");
        return new Frag(body.toString(), joinVar, queryVars(node));
    }

    /** Def 4.2 rule 4: β(Q1) UNION β(Q2), both branches exposing the same prov var. */
    private Frag betaUnion(Union u) {
        return unionFrags(beta(u.getLeftArg()), beta(u.getRightArg()));
    }

    /** ⊕ of two already-rewritten fragments under one shared provenance var. */
    private Frag unionFrags(Frag l, Frag r) {
        String z = generated("funion" + (unionCounter++));
        LinkedHashSet<String> vars = new LinkedHashSet<>(l.vars);
        vars.addAll(r.vars);
        String body = "{\n" + seal(l, z) + "\n}\n UNION \n{\n" + seal(r, z) + "\n}\n";
        return new Frag(body, z, vars);
    }

    /** P1 OPTIONAL P2 ≡ (P1 AND P2) UNION (P1 DIFF P2)  (paper §4.2 footnote).
     *  The negative branch is DIFF (anti-join, NO shared-var guard) — not user MINUS. */
    private Frag betaOptional(LeftJoin lj) {
        if (lj.getCondition() != null) {
            throw new UnsupportedOperationException(
                    "Unsupported pattern: OPTIONAL with a FILTER condition");
        }
        Frag joinFrag = beta(new Join(lj.getLeftArg().clone(), lj.getRightArg().clone()));
        Frag diffFrag = diffCore(lj.getLeftArg().clone(), lj.getRightArg().clone());
        return unionFrags(joinFrag, diffFrag);
    }

    /**
     * User-level SPARQL MINUS = DIFF behind a shared-variable guard. W3C MINUS removes
     * μ iff ∃ compatible μ' AND dom(μ)∩dom(μ')≠∅. A UNION may export a different domain
     * from each branch, so the guard is applied to each left/right branch pair rather
     * than once to the union of all syntactically mentioned variables.
     * (OPTIONAL's negative branch uses {@link #diffCore} directly and is NOT guarded.)
     */
    private Frag betaMinus(Difference d) {
        return guardedMinus(d.getLeftArg(), d.getRightArg());
    }

    private Frag guardedMinus(TupleExpr leftArg, TupleExpr rightArg) {
        if (leftArg instanceof Union) {
            Union union = (Union) leftArg;
            return unionFrags(
                    guardedMinus(union.getLeftArg(), rightArg.clone()),
                    guardedMinus(union.getRightArg(), rightArg.clone()));
        }

        Frag left = beta(leftArg);
        Frag subtrahend = null;
        for (TupleExpr branch : unionBranches(rightArg)) {
            LinkedHashSet<String> shared = new LinkedHashSet<>(left.vars);
            shared.retainAll(outputVars(branch));
            if (shared.isEmpty()) continue;

            TupleExpr renamed = branch.clone();
            renameNonShared(renamed, left.vars);
            Frag rewritten = beta(renamed);
            subtrahend = subtrahend == null ? rewritten : unionFrags(subtrahend, rewritten);
        }
        return subtrahend == null ? left : diffCore(left, subtrahend);
    }

    private static List<TupleExpr> unionBranches(TupleExpr node) {
        List<TupleExpr> out = new ArrayList<>();
        collectUnionBranches(node, out);
        return out;
    }

    private static void collectUnionBranches(TupleExpr node, List<TupleExpr> out) {
        if (node instanceof Union) {
            Union union = (Union) node;
            collectUnionBranches(union.getLeftArg(), out);
            collectUnionBranches(union.getRightArg(), out);
        } else {
            out.add(node);
        }
    }

    /**
     * DIFF (anti-join) provenance, Def 4.2 rule 5 (monus): keep P1's answers, subtract
     * the aggregated provenance of compatible P2 answers. P2's non-shared variables are
     * renamed fresh (ν) so the subtrahend only constrains the shared (join) variables.
     * Used by MINUS (guarded above) and by OPTIONAL (unguarded).
     */
    private Frag diffCore(TupleExpr leftArg, TupleExpr rightArg) {
        Frag left = beta(leftArg);
        TupleExpr right = rightArg.clone();
        renameNonShared(right, left.vars);   // ν
        return diffCore(left, beta(right));
    }

    private Frag diffCore(Frag left, Frag rght) {

        String zL = generated("fdl" + diffCounter);
        String zR = generated("fdr" + diffCounter);
        String zDiff = generated("fdiff" + (diffCounter++));

        String group = String.join(" ", withQ(left.vars)) + " ?" + zL;
        String body = "{ SELECT " + group + " (" + Prov.diffAgg(zL, zR) + " AS ?" + zDiff + ") \n"
                    + "WHERE { \n"
                    + seal(left, zL) + "\n OPTIONAL { \n" + seal(rght, zR) + "\n }\n"
                    + " }\n GROUP BY " + group + " }";
        return new Frag(body, zDiff, left.vars);   // in-scope = P1 variables
    }

    // ----------------------------------------------------------------- helpers

    /** Wrap a raw fragment into an aggregating sub-select exposing {@code outVar}. */
    private String seal(Frag f, String outVar) {
        String p = String.join(" ", withQ(f.vars));
        return "{ SELECT " + p + " (" + Prov.aggSum(f.provVar) + " AS ?" + outVar + ") \n"
             + "WHERE { \n" + f.body + " }" + groupBy(p) + " }";
    }

    private static String groupBy(String variables) {
        return variables.isEmpty() ? "" : "\n GROUP BY " + variables;
    }

    private static Projection outerProjection(TupleExpr te) {
        Projection[] found = new Projection[1];
        te.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override public void meet(Projection node) {
                if (found[0] == null) {
                    found[0] = node;   // outermost only; do not recurse
                }
            }
        });
        return found[0];
    }

    /** Real (named, non-constant, non-anonymous) query variables of a subtree, in order. */
    private LinkedHashSet<String> queryVars(TupleExpr node) {
        LinkedHashSet<String> vars = new LinkedHashSet<>();
        node.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override public void meet(StatementPattern sp) {
                add(sp.getSubjectVar()); add(sp.getPredicateVar()); add(sp.getObjectVar());
            }
            private void add(Var v) {
                if (v != null && !v.hasValue() && !v.isAnonymous()
                        && !v.getName().startsWith(generatedPrefix)) {
                    vars.add(v.getName());
                }
            }
        });
        return vars;
    }

    /** Variables actually exported by an algebra subtree, excluding generated internals. */
    private LinkedHashSet<String> outputVars(TupleExpr node) {
        LinkedHashSet<String> vars = new LinkedHashSet<>();
        for (String name : node.getBindingNames()) {
            if (name != null && !name.startsWith(generatedPrefix)) vars.add(name);
        }
        return vars;
    }

    /** ν: rename variables of {@code node} that are not shared with {@code shared}. */
    private void renameNonShared(TupleExpr node, LinkedHashSet<String> shared) {
        java.util.Map<String, String> renamed = new java.util.LinkedHashMap<>();
        node.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override public void meet(Var v) {
                if (!v.hasValue() && !v.isAnonymous()
                        && !v.getName().startsWith(generatedPrefix) && !shared.contains(v.getName())) {
                    String old = v.getName();
                    v.setName(renamed.computeIfAbsent(old,
                            name -> generated(name + "_nu" + diffCounter)));
                }
            }
        });
    }

    /**
     * Put every internal variable below a query-specific prefix which no user variable can start with.
     * Keeping one prefix for the full rewrite makes repeated occurrences deterministic while making
     * capture impossible even for adversarial names such as ?fprov0, ?fjoin0, or a generated ν name.
     */
    private void initializeGeneratedVariables(TupleExpr te) {
        provCounter = joinCounter = unionCounter = diffCounter = 0;
        userVariableNames.clear();
        userVariableNames.addAll(te.getBindingNames());
        te.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override public void meet(Var v) {
                if (v.getName() != null) userVariableNames.add(v.getName());
            }
        });
        int n = 0;
        while (true) {
            String candidate = "__npcs" + n + "_";
            boolean collision = false;
            for (String user : userVariableNames) {
                if (user.startsWith(candidate)) { collision = true; break; }
            }
            if (!collision) {
                generatedPrefix = candidate;
                return;
            }
            n++;
        }
    }

    private String generated(String hint) {
        return generatedPrefix + hint;
    }

    private static boolean isPureBgp(TupleExpr node) {
        boolean[] impure = {false};
        node.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override public void meet(Union n)      { impure[0] = true; }
            @Override public void meet(LeftJoin n)   { impure[0] = true; }
            @Override public void meet(Difference n) { impure[0] = true; }
            @Override public void meet(Filter n)     { impure[0] = true; }
            @Override public void meet(Extension n)  { impure[0] = true; }
            // B1 guard: these would otherwise be silently flattened by StatementPatternCollector
            // into a plain BGP — property paths (p+/p*/p?) collapse to a single hop, LIMIT/OFFSET is
            // dropped, and a nested subquery loses its own scope. Reject loudly instead.
            @Override public void meet(ArbitraryLengthPath n) { impure[0] = true; }  // p+ / p*
            @Override public void meet(ZeroLengthPath n)      { impure[0] = true; }  // p? / zero-length
            @Override public void meet(Slice n)               { impure[0] = true; }  // LIMIT / OFFSET
            @Override public void meet(Projection n)          { impure[0] = true; }  // nested subquery
            @Override public void meet(BindingSetAssignment n){ impure[0] = true; }  // VALUES
            @Override public void meet(Service n)             { impure[0] = true; }  // SERVICE
        });
        return !impure[0];
    }

    private static List<String> withQ(Iterable<String> names) {
        List<String> out = new ArrayList<>();
        for (String n : names) {
            out.add("?" + n);
        }
        return out;
    }
}
