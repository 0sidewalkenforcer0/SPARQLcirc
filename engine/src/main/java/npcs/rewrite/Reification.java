package npcs.rewrite;

import org.eclipse.rdf4j.query.algebra.StatementPattern;

/**
 * A reification scheme = how a single triple pattern (s,p,o) is encoded so that
 * the statement itself is bound to a fresh provenance variable {@code ?fprovN}
 * (the triple identifier). This is the {@code Reify} function of Definition 4.2.
 *
 * The upper-level spm-semiring combination (⊗/⊕/⊖) is identical across schemes;
 * only this leaf encoding differs.
 */
public enum Reification {

    /** RDF standard reification: three triples on the statement node ?fprovN. */
    STANDARD {
        @Override
        public String reify(StatementPattern sp, String provVar) {
            String s = Terms.render(sp.getSubjectVar());
            String p = Terms.render(sp.getPredicateVar());
            String o = Terms.render(sp.getObjectVar());
            return "\t?" + provVar + " <" + RDF_SUBJECT + "> " + s + " . \n"
                 + "\t?" + provVar + " <" + RDF_PREDICATE + "> " + p + " . \n"
                 + "\t?" + provVar + " <" + RDF_OBJECT + "> " + o + " . \n";
        }
    },

    /** SPARQL-star: quoted triple << s p o >> occurrenceOf ?fprovN. */
    SPARQL_STAR {
        @Override
        public String reify(StatementPattern sp, String provVar) {
            String s = Terms.render(sp.getSubjectVar());
            String p = Terms.render(sp.getPredicateVar());
            String o = Terms.render(sp.getObjectVar());
            return "\t<< " + s + " " + p + " " + o + " >> <" + OCCURRENCE_OF + "> ?" + provVar + " . \n";
        }
    },

    /**
     * Wikidata's native statement reification (matches NPCS's "Wikidatareal"): a direct triple
     * {@code s wdt:P o} is encoded as {@code s p:P ?prov . ?prov ps:P o}, so the STATEMENT NODE is
     * the provenance token. The predicate must be a constant {@code wdt:} (prop/direct) IRI; the
     * data must be reified into the same p:/ps: form (see reference/wikidata/reify_wikidata.py).
     */
    WIKIDATA {
        @Override
        public String reify(StatementPattern sp, String provVar) {
            String s = Terms.render(sp.getSubjectVar());
            String o = Terms.render(sp.getObjectVar());
            org.eclipse.rdf4j.model.Value pv = sp.getPredicateVar().getValue();
            if (pv == null || !pv.stringValue().startsWith(WDT_DIRECT)) {
                throw new UnsupportedOperationException(
                    "Wikidata reification needs a constant wdt: (prop/direct) predicate; got: "
                    + (pv == null ? "a variable" : pv.stringValue()));
            }
            String local = pv.stringValue().substring(WDT_DIRECT.length());   // e.g. "P35"
            return "\t" + s + " <" + WD_PROP + local + "> ?" + provVar + " . \n"
                 + "\t?" + provVar + " <" + WD_STATEMENT + local + "> " + o + " . \n";
        }
    },

    /**
     * n-ary-relationship reification (matches SPARQLprov's "naryrel" + ProvSQL's granularity): the
     * provenance token is the SUBJECT (the row entity), not a reified triple. The data stays PLAIN
     * (no reification) -- every triple about a row shares that row's token, so provenance is PER-ROW.
     * Intended for relational-derived RDF (the TPC-H direct mapping) where a row's attributes are ONE
     * uncertain unit; write skeletons with one pattern per row so each row contributes one token.
     * Unlike the other schemes, the predicate may be a variable (the token is the subject regardless).
     */
    NARYREL {
        @Override
        public String reify(StatementPattern sp, String provVar) {
            String s = Terms.render(sp.getSubjectVar());
            String p = Terms.render(sp.getPredicateVar());
            String o = Terms.render(sp.getObjectVar());
            return "\t" + s + " " + p + " " + o + " . \n"
                 + "\tBIND(" + s + " AS ?" + provVar + ") \n";
        }
    };

    static final String RDF_SUBJECT   = "http://www.w3.org/1999/02/22-rdf-syntax-ns#subject";
    static final String RDF_PREDICATE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#predicate";
    static final String RDF_OBJECT    = "http://www.w3.org/1999/02/22-rdf-syntax-ns#object";
    // Placeholder predicate for the SPARQL-star occurrence link; must match the reified data's convention.
    static final String OCCURRENCE_OF = "http://example.org/occurrenceOf";
    // Wikidata statement-reification namespaces: wdt:P (direct) -> p:P (claim) + ps:P (statement value).
    static final String WDT_DIRECT   = "http://www.wikidata.org/prop/direct/";
    static final String WD_PROP      = "http://www.wikidata.org/prop/";
    static final String WD_STATEMENT = "http://www.wikidata.org/prop/statement/";

    /** Encode one triple pattern, binding the statement to {@code ?provVar}. */
    public abstract String reify(StatementPattern sp, String provVar);

    /** Parse the CLI scheme name used by the original NPCS ("Standard", "SPARQL_Star"). */
    public static Reification fromName(String name) {
        switch (name) {
            case "Standard":    return STANDARD;
            case "SPARQL_Star": return SPARQL_STAR;
            case "Wikidata":    return WIKIDATA;
            case "naryrel":     return NARYREL;
            default:
                throw new IllegalArgumentException(
                    "Unsupported reification scheme: " + name
                    + " (supported: Standard, SPARQL_Star, Wikidata, naryrel)");
        }
    }
}
