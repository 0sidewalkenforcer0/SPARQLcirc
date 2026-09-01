package npcs.circuit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.Set;

import npcs.rewrite.NpcsRewriter;
import npcs.rewrite.Reification;

import org.eclipse.rdf4j.model.IRI;
import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.model.Resource;
import org.eclipse.rdf4j.model.ValueFactory;
import org.eclipse.rdf4j.model.impl.LinkedHashModel;
import org.eclipse.rdf4j.model.vocabulary.RDF;
import org.eclipse.rdf4j.model.vocabulary.XMLSchema;
import org.eclipse.rdf4j.query.parser.sparql.SPARQLParser;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.sail.memory.MemoryStore;
import org.junit.Test;

/** TPC-H uses one RDF-star 1.1 occurrence per relational row, not per column. */
public class TpchRowReificationTest {

    private static final String QUERY =
            "SELECT ?order WHERE { "
          + "?order <http://example.org/o_cust> ?customer . "
          + "?order <http://example.org/o_orderdate> ?date . "
          + "?customer <http://example.org/c_mktsegment> \"BUILDING\" . "
          + "}";

    @Test
    public void npcsBindsOneOccurrenceForEachDistinctRowSubject() {
        String rewritten = new NpcsRewriter(Reification.SPARQL_STAR_ROW).rewrite(QUERY);

        assertEquals(2, occurrences(rewritten, "<http://example.org/occurrenceOf>"));
        assertEquals(4, occurrences(rewritten,
                "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"));
        assertTrue(rewritten.contains("?order <http://example.org/o_cust> ?customer"));
        assertTrue(rewritten.contains("?order <http://example.org/o_orderdate> ?date"));
        assertTrue(rewritten.contains(
                "?customer <http://example.org/c_mktsegment> \"BUILDING\""));
    }

    @Test
    public void flatCircuitHasTwoRowLeavesDespiteThreeAttributePatterns() {
        CircuitConstructionPlan plan = new CircuitRewriter(
                Reification.SPARQL_STAR_ROW, ConstructionMode.FLAT, "tpch-row-test")
                .constructionPlan(QUERY);

        assertEquals(1, plan.queries().size());
        String construct = plan.queries().get(0);
        assertEquals(2, occurrences(construct, "<http://example.org/occurrenceOf>"));
        assertEquals(2, occurrences(construct, " c:in ?"));
    }

    @Test
    public void factorisedCircuitBuildsOneBaseUnitPerRowSubject() {
        CircuitConstructionPlan plan = new CircuitRewriter(
                Reification.SPARQL_STAR_ROW, ConstructionMode.FACTORISED, "tpch-row-test")
                .constructionPlan(QUERY);

        assertEquals(ConstructionMode.FACTORISED, plan.requestedMode());
        assertEquals(ConstructionMode.FACTORISED, plan.effectiveMode());
        assertNull(plan.fallbackReason());
        assertTrue(plan.requiresFeedback());
        assertEquals(2, occurrences(
                plan.queries().get(0), "<http://example.org/occurrenceOf>"));
    }

    @Test
    public void filteredBoundTpchShapeExecutesEquivalentlyInBothModes() {
        String query = "SELECT ?order ?year WHERE { "
                + "?order <http://example.org/o_cust> ?customer . "
                + "?order <http://example.org/o_orderdate> ?date . "
                + "?customer <http://example.org/c_mktsegment> \"BUILDING\" . "
                + "FILTER(?date < \"1995-01-01\"^^<http://www.w3.org/2001/XMLSchema#date>) "
                + "BIND(YEAR(?date) AS ?year) }";

        Model flat = execute(query, ConstructionMode.FLAT);
        Model factorised = execute(query, ConstructionMode.FACTORISED);
        Set<Resource> flatRoots = CircuitTestSupport.answerRoots(flat);
        Set<Resource> factorisedRoots = CircuitTestSupport.answerRoots(factorised);
        assertEquals(1, flatRoots.size());
        assertEquals(1, factorisedRoots.size());
        assertEquals(
                CircuitTestSupport.bindingStrings(flat, flatRoots.iterator().next()),
                CircuitTestSupport.bindingStrings(
                        factorised, factorisedRoots.iterator().next()));

        Set<String> completeWorld = new LinkedHashSet<>(Arrays.asList(
                "http://example.org/Order/1", "http://example.org/Customer/1"));
        assertTrue(CircuitTestSupport.evaluate(factorised,
                factorisedRoots.iterator().next(), completeWorld, new HashMap<>()));
        assertFalse(CircuitTestSupport.evaluate(factorised,
                factorisedRoots.iterator().next(),
                new LinkedHashSet<>(Arrays.asList("http://example.org/Order/1")),
                new HashMap<>()));
    }

    @Test
    public void npcsRetainsTpchFiltersAndOutputBindings() {
        String query = "SELECT ?order ?year WHERE { "
                + "?order <http://example.org/o_cust> ?customer . "
                + "?order <http://example.org/o_orderdate> ?date . "
                + "?customer <http://example.org/c_mktsegment> \"BUILDING\" . "
                + "FILTER(?date < \"1995-01-01\"^^<http://www.w3.org/2001/XMLSchema#date>) "
                + "BIND(YEAR(?date) AS ?year) }";

        String rewritten = new NpcsRewriter(Reification.SPARQL_STAR_ROW).rewrite(query);
        assertEquals(2, occurrences(rewritten, "<http://example.org/occurrenceOf>"));
        assertTrue(rewritten.contains("FILTER((?date <"));
        assertTrue(rewritten.contains("BIND(YEAR(?date) AS ?year)"));
        new SPARQLParser().parseQuery(rewritten, null);
    }

    private static Model execute(String query, ConstructionMode mode) {
        Repository repository = new SailRepository(new MemoryStore());
        Model circuit = new LinkedHashModel();
        try (RepositoryConnection connection = repository.getConnection()) {
            ValueFactory vf = connection.getValueFactory();
            IRI occurrence = vf.createIRI("http://example.org/occurrenceOf");
            IRI customer = vf.createIRI("http://example.org/Customer/1");
            IRI order = vf.createIRI("http://example.org/Order/1");
            IRI customerType = vf.createIRI("http://example.org/Customer");
            IRI orderType = vf.createIRI("http://example.org/Order");
            connection.add(customer, RDF.TYPE, customerType);
            connection.add(vf.createTriple(customer, RDF.TYPE, customerType),
                    occurrence, customer);
            connection.add(customer, vf.createIRI("http://example.org/c_mktsegment"),
                    vf.createLiteral("BUILDING"));
            connection.add(order, RDF.TYPE, orderType);
            connection.add(vf.createTriple(order, RDF.TYPE, orderType), occurrence, order);
            connection.add(order, vf.createIRI("http://example.org/o_cust"), customer);
            connection.add(order, vf.createIRI("http://example.org/o_orderdate"),
                    vf.createLiteral("1994-03-17", XMLSchema.DATE));

            CircuitConstructionPlan plan = new CircuitRewriter(
                    Reification.SPARQL_STAR_ROW, mode, "tpch-row-execution")
                    .constructionPlan(query);
            assertEquals(mode, plan.effectiveMode());
            CircuitRun.executeConstructionPlan(connection, plan, circuit, false);
        } finally {
            repository.shutDown();
        }
        return circuit;
    }

    private static int occurrences(String text, String needle) {
        int count = 0;
        int offset = 0;
        while ((offset = text.indexOf(needle, offset)) >= 0) {
            count++;
            offset += needle.length();
        }
        return count;
    }
}
