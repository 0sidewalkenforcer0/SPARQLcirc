package npcs.rewrite;

import java.util.List;

/**
 * The spm-semiring provenance operators of NPCS, encoded as SPARQL string
 * expressions exactly as in the paper's Definition 4.1 (and the original
 * ReifySparqlByte.jar output).
 *
 * The provenance polynomial is NOT computed in Java: each operator emits a
 * {@code CONCAT(...)} expression that the SPARQL endpoint evaluates natively.
 * The symbols used are:
 *   <ul>
 *     <li>{@code (⊗ ...)}  — product (AND / join)                        {@link #prod}</li>
 *     <li>{@code ⊕( ... )} — aggregate-sum over a GROUP (⊕ of duplicates) {@link #aggSum}</li>
 *     <li>{@code (⊖ x,⊕(..))} — monus of a minuend vs an aggregated subtrahend (DIFF/MINUS) {@link #diffAgg}</li>
 *   </ul>
 */
public final class Prov {

    private Prov() {}

    /** ProvAggSum(?x) = concat("⊕(", group_concat(?x), ")")  — Def 4.1. */
    public static String aggSum(String var) {
        return "CONCAT(\"⊕(\", GROUP_CONCAT(?" + var + "), \")\")";
    }

    /** ProvProd(?x1,...,?xn) = concat("(⊗", STR(?x1), ",", ..., STR(?xn), ",", ")")  — join. */
    public static String prod(List<String> vars) {
        StringBuilder sb = new StringBuilder("CONCAT(\"(⊗\"");
        for (String v : vars) {
            sb.append(", STR(?").append(v).append("), \",\"");
        }
        sb.append(", \")\")");
        return sb.toString();
    }

    /**
     * Monus of a grouped minuend against the aggregated subtrahend:
     * ProvDiff(?left, ProvAggSum(?rightAgg)) — used for DIFF / the OPTIONAL
     * subtrahend (Def 4.2 rule 5). The right operand is aggregated with
     * GROUP_CONCAT so that all compatible subtrahend derivations are summed.
     * An unmatched OPTIONAL leaves {@code rightAgg} unbound. Coalescing that
     * value inside the aggregate makes the empty subtrahend explicit instead
     * of leaving the complete provenance expression unbound on strict engines.
     */
    public static String diffAgg(String left, String rightAgg) {
        return "CONCAT(\"(⊖\", ?" + left + ", \",\", "
             + "GROUP_CONCAT(COALESCE(?" + rightAgg + ", \"\")), \",\", \")\")";
    }
}
