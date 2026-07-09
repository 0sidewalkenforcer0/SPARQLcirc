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
    };

    static final String RDF_SUBJECT   = "http://www.w3.org/1999/02/22-rdf-syntax-ns#subject";
    static final String RDF_PREDICATE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#predicate";
    static final String RDF_OBJECT    = "http://www.w3.org/1999/02/22-rdf-syntax-ns#object";
    // Placeholder predicate for the SPARQL-star occurrence link; must match the reified data's convention.
    static final String OCCURRENCE_OF = "http://example.org/occurrenceOf";

    /** Encode one triple pattern, binding the statement to {@code ?provVar}. */
    public abstract String reify(StatementPattern sp, String provVar);

    /** Parse the CLI scheme name used by the original NPCS ("Standard", "SPARQL_Star"). */
    public static Reification fromName(String name) {
        switch (name) {
            case "Standard":    return STANDARD;
            case "SPARQL_Star": return SPARQL_STAR;
            default:
                throw new IllegalArgumentException(
                    "Unsupported reification scheme: " + name
                    + " (supported: Standard, SPARQL_Star)");
        }
    }
}
