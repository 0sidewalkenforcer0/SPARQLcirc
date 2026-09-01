package npcs.rewrite;

import org.eclipse.rdf4j.model.Value;
import org.eclipse.rdf4j.query.algebra.StatementPattern;
import org.eclipse.rdf4j.query.algebra.Var;

/**
 * The physical encoding used to bind one matched triple to a provenance token.
 *
 * <p>The ordinary {@code Standard}, {@code SPARQL_Star}, and {@code NamedGraph}
 * CLI names use the mixed layout: the asserted triple is matched inline with
 * its token encoding. The corresponding {@code *_Pure} names retain the old
 * reification-only layout for reproducing earlier measurements.
 */
public enum Reification {

    /** Historical RDF standard statement-node lookup without the asserted triple. */
    STANDARD(Kind.STANDARD, false),

    /** Default CLI layout: asserted triple plus RDF standard statement-node lookup. */
    STANDARD_INLINE(Kind.STANDARD, true),

    /** Historical RDF-star occurrence lookup without the asserted triple. */
    SPARQL_STAR(Kind.SPARQL_STAR, false),

    /** Default CLI layout: asserted triple plus the RDF-star occurrence lookup used by NPCS. */
    SPARQL_STAR_INLINE(Kind.SPARQL_STAR, true),

    /**
     * TPC-H row layout: asserted attribute patterns plus one RDF-star occurrence lookup per
     * distinct row subject. Group-aware rewriters must call {@link #rowOccurrence}; invoking
     * {@link #reify} one triple at a time would incorrectly count every column as a separate row.
     */
    SPARQL_STAR_ROW(Kind.SPARQL_STAR_ROW, true),

    /** Wikidata's native p:/ps: statement encoding. */
    WIKIDATA(Kind.WIKIDATA, false),

    /** Per-row token: the data is already plain and the subject is the token. */
    NARYREL(Kind.NARYREL, false),

    /** Historical token-named graph lookup without an asserted default-graph triple. */
    NAMED_GRAPH(Kind.NAMED_GRAPH, false),

    /** Default CLI layout: asserted default-graph triple plus the token-named graph lookup. */
    NAMED_GRAPH_INLINE(Kind.NAMED_GRAPH, true);

    private enum Kind {
        STANDARD,
        SPARQL_STAR,
        SPARQL_STAR_ROW,
        WIKIDATA,
        NARYREL,
        NAMED_GRAPH
    }

    static final String RDF_SUBJECT   = "http://www.w3.org/1999/02/22-rdf-syntax-ns#subject";
    static final String RDF_PREDICATE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#predicate";
    static final String RDF_OBJECT    = "http://www.w3.org/1999/02/22-rdf-syntax-ns#object";
    static final String RDF_TYPE      = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type";
    static final String OCCURRENCE_OF = "http://example.org/occurrenceOf";
    static final String WDT_DIRECT    = "http://www.wikidata.org/prop/direct/";
    static final String WD_PROP       = "http://www.wikidata.org/prop/";
    static final String WD_STATEMENT  = "http://www.wikidata.org/prop/statement/";

    private final Kind kind;
    private final boolean includeAssertedTriple;

    Reification(Kind kind, boolean includeAssertedTriple) {
        this.kind = kind;
        this.includeAssertedTriple = includeAssertedTriple;
    }

    /**
     * Match one triple pattern and bind its token to {@code ?provVar}.
     *
     * <p>For the default mixed layouts the ordinary triple pattern is emitted
     * immediately before its reification pattern. Callers invoke this method in
     * the algebra node's own scope, so OPTIONAL, UNION, and MINUS keep their
     * original semantics.
     */
    public String reify(StatementPattern sp, String provVar) {
        String encoded;
        switch (kind) {
            case STANDARD:
                encoded = standard(sp, provVar);
                break;
            case SPARQL_STAR:
                encoded = sparqlStar(sp, provVar);
                break;
            case SPARQL_STAR_ROW:
                throw new UnsupportedOperationException(
                        "SPARQL_Star_Row requires BGP-aware per-subject rewriting");
            case WIKIDATA:
                encoded = wikidata(sp, provVar);
                break;
            case NARYREL:
                encoded = naryRelation(sp, provVar);
                break;
            case NAMED_GRAPH:
                encoded = namedGraph(sp, provVar);
                break;
            default:
                throw new AssertionError(kind);
        }
        return includeAssertedTriple ? asserted(sp) + encoded : encoded;
    }

    /** True for both mixed and historical-pure Standard variants. */
    public boolean isStandard() {
        return kind == Kind.STANDARD;
    }

    /** True for both mixed and historical-pure RDF-star variants. */
    public boolean isSparqlStar() {
        return kind == Kind.SPARQL_STAR || kind == Kind.SPARQL_STAR_ROW;
    }

    /** True only for the RDF-star 1.1 per-row layout used by TPC-H. */
    public boolean isRowLevel() {
        return kind == Kind.SPARQL_STAR_ROW;
    }

    /** Match one asserted triple without creating a provenance leaf. */
    public String asserted(StatementPattern sp) {
        return "\t" + Terms.render(sp.getSubjectVar()) + " "
             + Terms.render(sp.getPredicateVar()) + " "
             + Terms.render(sp.getObjectVar()) + " . \n";
    }

    /** Match a row marker and bind its token using RDF-star 1.1 quoted-triple syntax. */
    public String rowOccurrence(Var subject, String provVar, String typeVar) {
        if (!isRowLevel()) {
            throw new UnsupportedOperationException(
                    "rowOccurrence is only valid for SPARQL_Star_Row");
        }
        String s = Terms.render(subject);
        return "\t" + s + " <" + RDF_TYPE + "> ?" + typeVar + " . \n"
             + "\t<< " + s + " <" + RDF_TYPE + "> ?" + typeVar + " >> <"
             + OCCURRENCE_OF + "> ?" + provVar + " . \n";
    }

    private static String standard(StatementPattern sp, String provVar) {
        String s = Terms.render(sp.getSubjectVar());
        String p = Terms.render(sp.getPredicateVar());
        String o = Terms.render(sp.getObjectVar());
        return "\t?" + provVar + " <" + RDF_SUBJECT + "> " + s + " . \n"
             + "\t?" + provVar + " <" + RDF_PREDICATE + "> " + p + " . \n"
             + "\t?" + provVar + " <" + RDF_OBJECT + "> " + o + " . \n";
    }

    private static String sparqlStar(StatementPattern sp, String provVar) {
        String s = Terms.render(sp.getSubjectVar());
        String p = Terms.render(sp.getPredicateVar());
        String o = Terms.render(sp.getObjectVar());
        return "\t<< " + s + " " + p + " " + o + " >> <" + OCCURRENCE_OF
             + "> ?" + provVar + " . \n";
    }

    private static String wikidata(StatementPattern sp, String provVar) {
        String s = Terms.render(sp.getSubjectVar());
        String o = Terms.render(sp.getObjectVar());
        Value predicate = sp.getPredicateVar().getValue();
        if (predicate == null || !predicate.stringValue().startsWith(WDT_DIRECT)) {
            throw new UnsupportedOperationException(
                    "Wikidata reification needs a constant wdt: (prop/direct) predicate; got: "
                    + (predicate == null ? "a variable" : predicate.stringValue()));
        }
        String local = predicate.stringValue().substring(WDT_DIRECT.length());
        return "\t" + s + " <" + WD_PROP + local + "> ?" + provVar + " . \n"
             + "\t?" + provVar + " <" + WD_STATEMENT + local + "> " + o + " . \n";
    }

    private static String naryRelation(StatementPattern sp, String provVar) {
        String s = Terms.render(sp.getSubjectVar());
        String p = Terms.render(sp.getPredicateVar());
        String o = Terms.render(sp.getObjectVar());
        return "\t" + s + " " + p + " " + o + " . \n"
             + "\tBIND(" + s + " AS ?" + provVar + ") \n";
    }

    private static String namedGraph(StatementPattern sp, String provVar) {
        String s = Terms.render(sp.getSubjectVar());
        String p = Terms.render(sp.getPredicateVar());
        String o = Terms.render(sp.getObjectVar());
        return "\tGRAPH ?" + provVar + " { " + s + " " + p + " " + o + " } \n";
    }

    /** Parse a public CLI name. Mixed layouts are the default; pure layouts are explicit. */
    public static Reification fromName(String name) {
        switch (name) {
            case "Standard":
                return STANDARD_INLINE;
            case "Standard_Pure":
            case "StandardPure":
                return STANDARD;
            case "SPARQL_Star":
                return SPARQL_STAR_INLINE;
            case "SPARQL_Star_Row":
            case "SPARQLStarRow":
            case "RDFStarRow":
                return SPARQL_STAR_ROW;
            case "SPARQL_Star_Pure":
            case "SPARQLStarPure":
                return SPARQL_STAR;
            case "Wikidata":
                return WIKIDATA;
            case "naryrel":
                return NARYREL;
            case "NamedGraph":
            case "namedgraph":
                return NAMED_GRAPH_INLINE;
            case "NamedGraph_Pure":
            case "namedgraph-pure":
                return NAMED_GRAPH;
            default:
                throw new IllegalArgumentException(
                        "Unsupported reification scheme: " + name
                        + " (supported: Standard, SPARQL_Star, SPARQL_Star_Row, Wikidata, "
                        + "naryrel, NamedGraph; "
                        + "append _Pure to Standard, SPARQL_Star, or NamedGraph for the historical layout)");
        }
    }
}
