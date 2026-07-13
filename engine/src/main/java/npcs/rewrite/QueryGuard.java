package npcs.rewrite;

import org.eclipse.rdf4j.query.algebra.StatementPattern;
import org.eclipse.rdf4j.query.algebra.helpers.AbstractQueryModelVisitor;
import org.eclipse.rdf4j.query.parser.ParsedQuery;

/** Shared fail-fast checks for query features whose graph semantics the rewriters cannot preserve. */
public final class QueryGuard {

    private QueryGuard() {}

    /**
     * Reject active/named datasets before a {@link StatementPattern} is rendered without its context.
     * Both rewriters currently target one default graph only.  Silently ignoring either a GRAPH context
     * or a FROM/FROM NAMED dataset clause would construct provenance for a different query.
     */
    public static void rejectDatasetsAndNamedGraphs(ParsedQuery query) {
        if (query.getDataset() != null) {
            throw new UnsupportedOperationException(
                "FROM/FROM NAMED dataset clauses are unsupported: the rewriters currently target "
                + "one default graph and cannot preserve an explicit query dataset.");
        }

        String[] context = {null};
        query.getTupleExpr().visit(new AbstractQueryModelVisitor<RuntimeException>() {
            @Override public void meet(StatementPattern sp) {
                if (context[0] == null && (sp.getContextVar() != null
                        || sp.getScope() == StatementPattern.Scope.NAMED_CONTEXTS)) {
                    context[0] = sp.getContextVar() == null ? "named context" : sp.getContextVar().toString();
                }
            }
        });
        if (context[0] != null) {
            throw new UnsupportedOperationException(
                "GRAPH/named-context patterns are unsupported (found " + context[0] + "): "
                + "the reification schemes currently encode only subject/predicate/object.");
        }
    }
}
