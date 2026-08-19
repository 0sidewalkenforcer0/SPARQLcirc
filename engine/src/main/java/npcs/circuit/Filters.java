package npcs.circuit;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.StringJoiner;

import org.eclipse.rdf4j.query.algebra.And;
import org.eclipse.rdf4j.query.algebra.Bound;
import org.eclipse.rdf4j.query.algebra.Compare;
import org.eclipse.rdf4j.query.algebra.Datatype;
import org.eclipse.rdf4j.query.algebra.Filter;
import org.eclipse.rdf4j.query.algebra.FunctionCall;
import org.eclipse.rdf4j.query.algebra.IsBNode;
import org.eclipse.rdf4j.query.algebra.IsLiteral;
import org.eclipse.rdf4j.query.algebra.IsNumeric;
import org.eclipse.rdf4j.query.algebra.IsURI;
import org.eclipse.rdf4j.query.algebra.Lang;
import org.eclipse.rdf4j.query.algebra.LangMatches;
import org.eclipse.rdf4j.query.algebra.ListMemberOperator;
import org.eclipse.rdf4j.query.algebra.MathExpr;
import org.eclipse.rdf4j.query.algebra.Not;
import org.eclipse.rdf4j.query.algebra.Or;
import org.eclipse.rdf4j.query.algebra.QueryModelNode;
import org.eclipse.rdf4j.query.algebra.Regex;
import org.eclipse.rdf4j.query.algebra.SameTerm;
import org.eclipse.rdf4j.query.algebra.StatementPattern;
import org.eclipse.rdf4j.query.algebra.Str;
import org.eclipse.rdf4j.query.algebra.TupleExpr;
import org.eclipse.rdf4j.query.algebra.ValueConstant;
import org.eclipse.rdf4j.query.algebra.ValueExpr;
import org.eclipse.rdf4j.query.algebra.Var;
import org.eclipse.rdf4j.query.algebra.helpers.AbstractQueryModelVisitor;
import org.eclipse.rdf4j.query.algebra.helpers.StatementPatternCollector;

import npcs.rewrite.Terms;

/**
 * FILTER support for the circuit rewriting (Def. 4.5, clause 6: {@code g_{σ_φ(P)} = g_P} and
 * {@code γ(σ_φ(P)) = γ(P)}). A filter builds no gate and changes no gate identity; it only removes
 * the matches on which the operand's provenance is 0, which is the annotated algebra's
 * multiplication by 0. The rewriting therefore carries every FILTER of an operand into that
 * operand's reified group, exactly as {@code P̂} in Def. 4.5 leaves "everything else, filters
 * included, in place".
 *
 * <p>Two invariants keep this safe:
 * <ol>
 *   <li><b>Never silently drop a filter.</b> A condition this class cannot render back to SPARQL
 *       is rejected fail-fast, so no circuit is ever emitted for the <em>unfiltered</em> query.
 *       The supported subset is the SPARQL 1.1 operator/builtin core listed in
 *       {@link #render(ValueExpr)}; {@code EXISTS}/{@code NOT EXISTS} (which carry a pattern of
 *       their own, hence provenance of their own) fall outside it and are rejected.</li>
 *   <li><b>Never widen a filter's scope.</b> Hoisting a nested-group filter to the end of the
 *       enclosing group is equivalence-preserving only when the filter's variables are already
 *       bound by the subtree it wraps; otherwise the nested form evaluates it on an unbound
 *       variable and the flattened form does not. {@link #of(TupleExpr)} checks this and rejects
 *       the residual case.</li>
 * </ol>
 */
final class Filters {

    private Filters() {}

    /**
     * The rendered FILTER conditions of a BGP operand, sorted so the list is a canonical function of
     * the operand (conjunction is commutative, and the gate fingerprints hash this list).
     * Empty when the operand carries no filter.
     */
    static List<String> of(TupleExpr operand) {
        List<Filter> found = new ArrayList<>();
        operand.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override public void meet(Filter node) { found.add(node); super.meet(node); }
        });
        List<String> out = new ArrayList<>();
        for (Filter f : found) {
            Set<String> scope = patternVars(f.getArg());
            Set<String> used = conditionVars(f.getCondition());
            used.removeAll(scope);
            if (!used.isEmpty()) {
                throw new UnsupportedOperationException(
                    "Unsupported FILTER: condition references " + used + ", which its own group does not "
                  + "bind. Hoisting such a filter to the enclosing group would change its value (SPARQL "
                  + "evaluates it on an unbound variable inside the nested group). Move the FILTER into "
                  + "the group that binds those variables.");
            }
            out.add(render(f.getCondition()));
        }
        Collections.sort(out);
        return out;
    }

    /** SPARQL text for the conjunction of {@code conditions}, as lines to append to a WHERE group. */
    static String emit(List<String> conditions) {
        StringBuilder sb = new StringBuilder();
        for (String c : conditions) sb.append("\tFILTER(").append(c).append(") \n");
        return sb.toString();
    }

    /** Variables bound by the triple patterns of a subtree (the only binders in our fragment). */
    static Set<String> patternVars(TupleExpr te) {
        LinkedHashSet<String> out = new LinkedHashSet<>();
        for (StatementPattern sp : StatementPatternCollector.process(te)) {
            for (Var v : new Var[]{sp.getSubjectVar(), sp.getPredicateVar(), sp.getObjectVar()}) {
                if (v != null && !v.hasValue()) out.add(v.getName());
            }
        }
        return out;
    }

    /** Variables a condition mentions. Mutable: callers subtract the scope they are checking against. */
    static Set<String> conditionVars(ValueExpr condition) {
        LinkedHashSet<String> out = new LinkedHashSet<>();
        condition.visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override public void meet(Var node) { if (!node.hasValue()) out.add(node.getName()); }
        });
        return out;
    }

    /**
     * Render one condition back to SPARQL surface syntax. The supported subset is the SPARQL 1.1
     * core that needs no engine extension: the boolean connectives, the six comparisons, the four
     * arithmetic operators, and the term-inspection builtins. Anything else — notably
     * {@code EXISTS}/{@code NOT EXISTS}, aggregates, and extension functions — is rejected rather
     * than approximated.
     */
    static String render(ValueExpr e) {
        if (e instanceof Var)           return Terms.render((Var) e);
        if (e instanceof ValueConstant) return Terms.value(((ValueConstant) e).getValue());
        if (e instanceof Compare) {
            Compare c = (Compare) e;
            return "(" + render(c.getLeftArg()) + " " + c.getOperator().getSymbol() + " "
                       + render(c.getRightArg()) + ")";
        }
        if (e instanceof And) {
            And a = (And) e;
            return "(" + render(a.getLeftArg()) + " && " + render(a.getRightArg()) + ")";
        }
        if (e instanceof Or) {
            Or o = (Or) e;
            return "(" + render(o.getLeftArg()) + " || " + render(o.getRightArg()) + ")";
        }
        if (e instanceof Not)      return "(!" + render(((Not) e).getArg()) + ")";
        if (e instanceof MathExpr) {
            MathExpr m = (MathExpr) e;
            return "(" + render(m.getLeftArg()) + " " + m.getOperator().getSymbol() + " "
                       + render(m.getRightArg()) + ")";
        }
        if (e instanceof Bound)     return "BOUND(" + Terms.render(((Bound) e).getArg()) + ")";
        if (e instanceof Str)       return "STR(" + render(((Str) e).getArg()) + ")";
        if (e instanceof Lang)      return "LANG(" + render(((Lang) e).getArg()) + ")";
        if (e instanceof Datatype)  return "DATATYPE(" + render(((Datatype) e).getArg()) + ")";
        if (e instanceof IsURI)     return "isIRI(" + render(((IsURI) e).getArg()) + ")";
        if (e instanceof IsLiteral) return "isLiteral(" + render(((IsLiteral) e).getArg()) + ")";
        if (e instanceof IsBNode)   return "isBlank(" + render(((IsBNode) e).getArg()) + ")";
        if (e instanceof IsNumeric) return "isNumeric(" + render(((IsNumeric) e).getArg()) + ")";
        if (e instanceof SameTerm) {
            SameTerm s = (SameTerm) e;
            return "sameTerm(" + render(s.getLeftArg()) + ", " + render(s.getRightArg()) + ")";
        }
        if (e instanceof LangMatches) {
            LangMatches l = (LangMatches) e;
            return "langMatches(" + render(l.getLeftArg()) + ", " + render(l.getRightArg()) + ")";
        }
        if (e instanceof Regex) {
            Regex r = (Regex) e;
            String base = "REGEX(" + render(r.getArg()) + ", " + render(r.getPatternArg());
            return r.getFlagsArg() == null ? base + ")" : base + ", " + render(r.getFlagsArg()) + ")";
        }
        if (e instanceof ListMemberOperator) {
            List<ValueExpr> args = ((ListMemberOperator) e).getArguments();
            if (args.size() < 2) {
                throw new UnsupportedOperationException(
                    "Unsupported FILTER expression: IN requires a value and at least one candidate");
            }
            StringJoiner candidates = new StringJoiner(", ");
            for (int i = 1; i < args.size(); i++) candidates.add(render(args.get(i)));
            return "(" + render(args.get(0)) + " IN (" + candidates + "))";
        }
        if (e instanceof FunctionCall) return renderFunction((FunctionCall) e);
        throw new UnsupportedOperationException(
            "Unsupported FILTER expression: " + describe(e) + ". Supported = the SPARQL 1.1 core "
          + "(&& || ! , = != < <= > >= , + - * / , BOUND STR LANG DATATYPE isIRI isLiteral isBlank "
          + "isNumeric sameTerm langMatches REGEX IN STRDT CONCAT CONTAINS YEAR). "
          + "EXISTS/NOT EXISTS carry a pattern of their own "
          + "and are outside the fragment. Refusing to emit a circuit for the unfiltered query.");
    }

    /** Render the standard function calls used by SPARQLprov's TPC-H non-aggregate templates. */
    private static String renderFunction(FunctionCall function) {
        String uri = function.getURI();
        String name = standardFunctionName(uri);
        List<ValueExpr> args = function.getArgs();
        if (("STRDT".equals(name) || "CONTAINS".equals(name)) && args.size() != 2) {
            throw new UnsupportedOperationException(
                "Unsupported FILTER expression: " + name + " requires exactly two arguments");
        }
        if ("YEAR".equals(name) && args.size() != 1) {
            throw new UnsupportedOperationException(
                "Unsupported FILTER expression: YEAR requires exactly one argument");
        }
        if ("CONCAT".equals(name) && args.isEmpty()) {
            throw new UnsupportedOperationException(
                "Unsupported FILTER expression: CONCAT requires at least one argument");
        }

        StringJoiner rendered = new StringJoiner(", ");
        for (ValueExpr arg : args) rendered.add(render(arg));
        return name + "(" + rendered + ")";
    }

    private static String standardFunctionName(String uri) {
        String candidate = uri;
        String xpathNamespace = "http://www.w3.org/2005/xpath-functions#";
        if (candidate.startsWith(xpathNamespace)) {
            candidate = candidate.substring(xpathNamespace.length());
        }
        candidate = candidate.toUpperCase(Locale.ROOT);
        if ("YEAR-FROM-DATETIME".equals(candidate)) candidate = "YEAR";
        if ("STRDT".equals(candidate) || "CONCAT".equals(candidate)
                || "CONTAINS".equals(candidate) || "YEAR".equals(candidate)) {
            return candidate;
        }
        throw new UnsupportedOperationException(
            "Unsupported FILTER expression: function call " + uri
          + ". Only standard STRDT, CONCAT, CONTAINS, and YEAR are supported");
    }

    private static String describe(QueryModelNode node) {
        if (node instanceof FunctionCall) return "function call " + ((FunctionCall) node).getURI();
        return node.getClass().getSimpleName();
    }
}
