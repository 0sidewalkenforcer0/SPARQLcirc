package npcs.rewrite;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

import org.eclipse.rdf4j.query.algebra.StatementPattern;
import org.eclipse.rdf4j.query.algebra.helpers.StatementPatternCollector;
import org.eclipse.rdf4j.query.parser.ParsedQuery;
import org.eclipse.rdf4j.query.parser.sparql.SPARQLParser;
import org.junit.Test;

/** Public layout names and the asserted-plus-token default. */
public class ReificationTest {

    private static StatementPattern pattern() {
        ParsedQuery parsed = new SPARQLParser().parseQuery(
                "SELECT ?s ?o WHERE { ?s <urn:p> ?o }", null);
        return StatementPatternCollector.process(parsed.getTupleExpr()).get(0);
    }

    @Test
    public void ordinaryCliNamesSelectMixedLayouts() {
        assertSame(Reification.STANDARD_INLINE, Reification.fromName("Standard"));
        assertSame(Reification.SPARQL_STAR_INLINE, Reification.fromName("SPARQL_Star"));
        assertSame(Reification.SPARQL_STAR_ROW, Reification.fromName("SPARQL_Star_Row"));
        assertSame(Reification.NAMED_GRAPH_INLINE, Reification.fromName("NamedGraph"));

        assertSame(Reification.STANDARD, Reification.fromName("Standard_Pure"));
        assertSame(Reification.SPARQL_STAR, Reification.fromName("SPARQL_Star_Pure"));
        assertSame(Reification.NAMED_GRAPH, Reification.fromName("NamedGraph_Pure"));
    }

    @Test
    public void mixedLayoutsMatchTheAssertedPatternBeforeTheToken() {
        StatementPattern pattern = pattern();
        String base = "\t?s <urn:p> ?o . \n";

        String standard = Reification.fromName("Standard").reify(pattern, "token");
        assertTrue(standard.contains(base));
        assertTrue(standard.indexOf(base) < standard.indexOf(Reification.RDF_SUBJECT));

        String rdfStar = Reification.fromName("SPARQL_Star").reify(pattern, "token");
        assertTrue(rdfStar.contains(base));
        assertTrue(rdfStar.indexOf(base) < rdfStar.indexOf("<< ?s <urn:p> ?o >>"));

        String pure = Reification.fromName("SPARQL_Star_Pure").reify(pattern, "token");
        assertFalse(pure.contains(base));
        assertTrue(pure.contains("<< ?s <urn:p> ?o >>"));
    }

    @Test
    public void rowLayoutUsesRdfStarTypeOccurrenceSyntax() {
        StatementPattern pattern = pattern();
        String row = Reification.SPARQL_STAR_ROW.rowOccurrence(
                pattern.getSubjectVar(), "token", "rowtype");

        assertTrue(row.contains("?s <" + Reification.RDF_TYPE + "> ?rowtype"));
        assertTrue(row.contains("<< ?s <" + Reification.RDF_TYPE + "> ?rowtype >>"));
        assertTrue(row.contains("<" + Reification.OCCURRENCE_OF + "> ?token"));
        assertFalse(row.contains("rdf:reifies"));
        assertFalse(row.contains("<<("));
    }
}
