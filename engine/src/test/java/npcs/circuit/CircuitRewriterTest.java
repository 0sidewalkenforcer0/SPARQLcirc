package npcs.circuit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.eclipse.rdf4j.model.IRI;
import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.model.Resource;
import org.eclipse.rdf4j.model.Statement;
import org.eclipse.rdf4j.model.Value;
import org.eclipse.rdf4j.model.ValueFactory;
import org.eclipse.rdf4j.model.impl.LinkedHashModel;
import org.eclipse.rdf4j.model.impl.SimpleValueFactory;
import org.eclipse.rdf4j.model.vocabulary.RDF;
import org.eclipse.rdf4j.query.BindingSet;
import org.eclipse.rdf4j.query.GraphQueryResult;
import org.eclipse.rdf4j.query.QueryResults;
import org.eclipse.rdf4j.query.TupleQueryResult;
import org.eclipse.rdf4j.query.parser.sparql.SPARQLParser;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.sail.memory.MemoryStore;
import org.junit.Test;

import npcs.rewrite.NpcsRewriter;
import npcs.rewrite.Reification;

/** Offline regressions: no endpoint, files, probabilities, or network are required. */
public class CircuitRewriterTest {

    private static final String C = "urn:circuit:";

    @Test
    public void independentMinusAndOptionalOperatorsHaveIsolatedInternalRoots() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:a", "urn:x", "urn:a", "urn:v");
            reify(con, "urn:r:p", "urn:x", "urn:p", "urn:hit"); // P matches, Q does not

            String minus = "SELECT ?x WHERE { "
                    + "{ ?x <urn:a> <urn:v> MINUS { ?x <urn:p> ?p } } UNION "
                    + "{ ?x <urn:a> <urn:v> MINUS { ?x <urn:q> ?q } } }";
            Model minusCircuit = executePlan(con, minus);
            assertEquals("independent MINUS operators must not collapse", 2, minusRoots(minusCircuit).size());
            assertEquals("each MINUS must retain its own subtrahend", 2, subtrahends(minusCircuit).size());
            assertEquals("only the A MINUS P subtrahend is fed; A MINUS Q must remain live", 1,
                    fedSubtrahends(minusCircuit).size());

            String optional = "SELECT ?x WHERE { "
                    + "{ ?x <urn:a> <urn:v> OPTIONAL { ?x <urn:p> ?p } } UNION "
                    + "{ ?x <urn:a> <urn:v> OPTIONAL { ?x <urn:q> ?q } } }";
            Model optionalCircuit = executePlan(con, optional);
            assertEquals("independent OPTIONAL negative branches must not collapse",
                    2, minusRoots(optionalCircuit).size());

            String repeated = "SELECT ?x WHERE { "
                    + "{ ?x <urn:a> <urn:v> MINUS { ?x <urn:p> ?p } } UNION "
                    + "{ ?x <urn:a> <urn:v> MINUS { ?x <urn:p> ?p } } }";
            CircuitRewriter rw = new CircuitRewriter(Reification.STANDARD);
            assertEquals("the fingerprint and generated plan must be deterministic",
                    rw.plan(repeated), rw.plan(repeated));
            assertEquals("semantically identical DIFF operators should still hash-cons", 1,
                    minusRoots(executePlan(con, repeated)).size());
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void circuitGeneratedVariablesCannotCaptureUserVariables() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:1", "urn:x", "urn:p", "urn:y");
            reify(con, "urn:r:2", "urn:z", "urn:q", "urn:w");
            reify(con, "urn:r:3", "urn:underscore", "urn:r", "urn:k");
            String query = "SELECT ?_x ?a0 ?t ?ans ?srt0 WHERE { "
                    + "?_x <urn:r> <urn:k> . ?a0 <urn:p> ?t . ?ans <urn:q> ?srt0 . }";

            CircuitRewriter rw = new CircuitRewriter(Reification.STANDARD);
            List<String> plan = rw.plan(query);
            for (String construct : plan) {
                new SPARQLParser().parseQuery(construct, null);       // also catches malformed generated names
            }
            Model circuit = executePlan(con, query);
            Map<String, Value> bindings = recoveredBindings(circuit);
            assertEquals(iri(con, "urn:underscore"), bindings.get("_x"));
            assertEquals(iri(con, "urn:x"), bindings.get("a0"));
            assertEquals(iri(con, "urn:y"), bindings.get("t"));
            assertEquals(iri(con, "urn:z"), bindings.get("ans"));
            assertEquals(iri(con, "urn:w"), bindings.get("srt0"));
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void npcsProvenanceVariableCannotCaptureUserFprov0() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:1", "urn:x", "urn:p", "urn:o");
            reify(con, "urn:r:2", "urn:underscore", "urn:q", "urn:w");
            reify(con, "urn:r:3", "urn:legacy", "urn:r", "urn:k");
            NpcsRewriter rw = new NpcsRewriter(Reification.STANDARD);
            String rewritten = rw.rewrite(
                    "SELECT ?fprov0 ?_x ?finalprovennacevariable WHERE { "
                    + "?fprov0 <urn:p> <urn:o> . ?_x <urn:q> <urn:w> . "
                    + "?finalprovennacevariable <urn:r> <urn:k> . }");
            new SPARQLParser().parseQuery(rewritten, null);
            assertTrue("the user variable must remain projected", rewritten.contains("SELECT ?fprov0 "));
            assertFalse("the token variable must be fresh, not the user's ?fprov0",
                    rewritten.contains("?fprov0 <http://www.w3.org/1999/02/22-rdf-syntax-ns#subject>"));
            try (TupleQueryResult result = con.prepareTupleQuery(rewritten).evaluate()) {
                assertTrue(result.hasNext());
                BindingSet row = result.next();
                assertEquals(iri(con, "urn:x"), row.getValue("fprov0"));
                assertEquals(iri(con, "urn:underscore"), row.getValue("_x"));
                assertEquals(iri(con, "urn:legacy"), row.getValue("finalprovennacevariable"));
                assertFalse("the provenance alias must avoid the user's legacy output name",
                        "finalprovennacevariable".equals(rw.provenanceOutputVariable()));
                assertNotNull(row.getValue(rw.provenanceOutputVariable()));
                assertFalse(result.hasNext());
            }
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void graphAndDatasetClausesFailFastInBothRewriters() {
        String graph = "SELECT ?s WHERE { GRAPH <urn:g> { ?s <urn:p> ?o } }";
        String from = "SELECT ?s FROM <urn:g> WHERE { ?s <urn:p> ?o }";
        String fromNamed = "SELECT ?s FROM NAMED <urn:g> WHERE { ?s <urn:p> ?o }";

        assertRejected(() -> new CircuitRewriter(Reification.STANDARD).plan(graph), "GRAPH");
        assertRejected(() -> new CircuitRewriter(Reification.STANDARD).plan(from), "FROM");
        assertRejected(() -> new CircuitRewriter(Reification.STANDARD).plan(fromNamed), "FROM");
        assertRejected(() -> new NpcsRewriter(Reification.STANDARD).rewrite(graph), "GRAPH");
        assertRejected(() -> new NpcsRewriter(Reification.STANDARD).rewrite(from), "FROM");
        assertRejected(() -> new NpcsRewriter(Reification.STANDARD).rewrite(fromNamed), "FROM");

        String pathFrom = "SELECT ?o FROM <urn:g> WHERE { <urn:s> <urn:p>+ ?o }";
        assertRejected(() -> new CircuitRewriter(Reification.STANDARD).pathQuery(pathFrom), "FROM");
    }

    private static Model executePlan(RepositoryConnection con, String query) {
        Model circuit = new LinkedHashModel();
        List<String> plan = new CircuitRewriter(Reification.STANDARD).plan(query);
        for (String construct : plan) {
            try (GraphQueryResult result = con.prepareGraphQuery(construct).evaluate()) {
                circuit.addAll(QueryResults.asModel(result));
            }
        }
        return circuit;
    }

    private static Set<Resource> minusRoots(Model model) {
        IRI minus = SimpleValueFactory.getInstance().createIRI(C, "Minus");
        return new HashSet<>(model.filter(null, RDF.TYPE, minus).subjects());
    }

    private static Set<Value> subtrahends(Model model) {
        IRI sub = SimpleValueFactory.getInstance().createIRI(C, "subtrahend");
        Set<Value> out = new HashSet<>();
        for (Statement st : model.filter(null, sub, null)) out.add(st.getObject());
        return out;
    }

    private static Set<Value> fedSubtrahends(Model model) {
        IRI feeds = SimpleValueFactory.getInstance().createIRI(C, "feeds");
        Set<Value> subtrahends = subtrahends(model);
        Set<Value> out = new HashSet<>();
        for (Statement st : model.filter(null, feeds, null)) {
            if (subtrahends.contains(st.getObject())) out.add(st.getObject());
        }
        return out;
    }

    private static Map<String, Value> recoveredBindings(Model model) {
        IRI var = SimpleValueFactory.getInstance().createIRI(C, "var");
        IRI val = SimpleValueFactory.getInstance().createIRI(C, "val");
        Map<String, Value> out = new HashMap<>();
        for (Statement st : model.filter(null, var, null)) {
            Value value = model.filter(st.getSubject(), val, null).objects().stream().findFirst().orElse(null);
            out.put(st.getObject().stringValue(), value);
        }
        return out;
    }

    private static void reify(RepositoryConnection con, String token, String s, String p, String o) {
        ValueFactory vf = con.getValueFactory();
        IRI t = vf.createIRI(token);
        con.add(t, vf.createIRI(RDF.NAMESPACE, "subject"), vf.createIRI(s));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "predicate"), vf.createIRI(p));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "object"), vf.createIRI(o));
    }

    private static IRI iri(RepositoryConnection con, String value) {
        return con.getValueFactory().createIRI(value);
    }

    private static void assertRejected(Runnable action, String messagePart) {
        try {
            action.run();
            fail("expected unsupported query to be rejected");
        } catch (UnsupportedOperationException expected) {
            assertTrue("unexpected rejection message: " + expected.getMessage(),
                    expected.getMessage().contains(messagePart));
        }
    }
}
