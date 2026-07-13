package npcs.circuit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
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
    public void constructionModeDefaultsToFactoredAndReportsFallbacks() {
        assertEquals(ConstructionMode.FACTORED, ConstructionMode.fromCli("FACTORED"));
        assertEquals(ConstructionMode.FLAT, ConstructionMode.fromCli("flat"));
        String bgp = "SELECT ?z WHERE { <urn:s> <urn:p> ?x . ?x <urn:p> ?z . }";
        CircuitRewriter defaultRewriter = new CircuitRewriter(Reification.STANDARD);
        CircuitConstructionPlan factored = defaultRewriter.constructionPlan(bgp);
        assertEquals(ConstructionMode.FACTORED, defaultRewriter.constructionMode());
        assertEquals(ConstructionMode.FACTORED, factored.effectiveMode());
        assertTrue(factored.requiresFeedback());
        assertTrue("base/join/marginalize/answer must be separate engine passes",
                factored.steps().size() > 1);
        for (CircuitConstructionPlan.Step step : factored.steps()) {
            new SPARQLParser().parseQuery(step.query(), null);
        }

        CircuitConstructionPlan flat = new CircuitRewriter(
                Reification.STANDARD, ConstructionMode.FLAT).constructionPlan(bgp);
        assertEquals(ConstructionMode.FLAT, flat.effectiveMode());
        assertFalse(flat.requiresFeedback());
        assertEquals(1, flat.steps().size());

        String union = "SELECT ?z WHERE { { <urn:s> <urn:p> ?z } UNION { <urn:s> <urn:q> ?z } }";
        CircuitConstructionPlan fallback = defaultRewriter.constructionPlan(union);
        assertEquals(ConstructionMode.FACTORED, fallback.requestedMode());
        assertEquals(ConstructionMode.FLAT, fallback.effectiveMode());
        assertNotNull(fallback.fallbackReason());
        assertTrue(fallback.fallbackReason().contains("not a pure BGP"));
    }

    @Test
    public void factoredWorkspaceIsCleanedWhenALaterStepFails() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:one", "urn:s", "urn:p", "urn:o");
            CircuitConstructionPlan valid = new CircuitRewriter(
                    Reification.STANDARD, ConstructionMode.FACTORED, "failing-session")
                    .constructionPlan("SELECT ?o WHERE { <urn:s> <urn:p> ?o }");
            List<CircuitConstructionPlan.Step> brokenSteps = new java.util.ArrayList<>();
            brokenSteps.add(valid.steps().get(0));                 // emits and feeds one private base relation
            brokenSteps.add(new CircuitConstructionPlan.Step(
                    "THIS IS NOT A SPARQL QUERY", false, "intentional failure"));
            CircuitConstructionPlan broken = new CircuitConstructionPlan(
                    brokenSteps, ConstructionMode.FACTORED, ConstructionMode.FACTORED, null);
            try {
                CircuitRun.executeConstructionPlan(con, broken, new LinkedHashModel(), false);
                fail("the deliberately malformed second step must fail");
            } catch (RuntimeException expected) {
                // The assertion below is the contract: finally must remove step one's rows.
            }
            assertNoFactoredMetadata(con);
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void factoredLayeredBgpMatchesFlatAndUsesFewerProducts() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            int width = 3;
            for (int a = 0; a < width; a++) {
                reify(con, "urn:r:e0_" + a, "urn:S", "urn:p", "urn:n1_" + a);
            }
            for (int level = 1; level < 4; level++) {
                for (int a = 0; a < width; a++) {
                    for (int b = 0; b < width; b++) {
                        reify(con, "urn:r:e" + level + "_" + a + "_" + b,
                                "urn:n" + level + "_" + a, "urn:p",
                                "urn:n" + (level + 1) + "_" + b);
                    }
                }
            }
            String query = "SELECT ?v4 WHERE { "
                    + "<urn:S> <urn:p> ?v1 . ?v1 <urn:p> ?v2 . "
                    + "?v2 <urn:p> ?v3 . ?v3 <urn:p> ?v4 . }";

            Model factored = executePlan(con, query, ConstructionMode.FACTORED, "session-one");
            Model repeated = executePlan(con, query, ConstructionMode.FACTORED, "session-two");
            Model flat = executePlan(con, query, ConstructionMode.FLAT);
            assertEquals("private session ids must not affect circuit gate identities", factored, repeated);
            Set<Resource> factoredRoots = answerRoots(factored);
            Set<Resource> flatRoots = answerRoots(flat);
            assertEquals(flatRoots, factoredRoots);
            assertEquals(width, factoredRoots.size());

            int flatProducts = gateCount(flat, "Times");
            int factoredProducts = gateCount(factored, "Times");
            assertEquals("flat construction spells out W^4 complete derivations", 81, flatProducts);
            assertEquals("three width-squared joins remain after early elimination", 27, factoredProducts);
            assertTrue(factoredProducts < flatProducts);

            List<String> tokens = new java.util.ArrayList<>();
            try (org.eclipse.rdf4j.repository.RepositoryResult<Statement> statements =
                    con.getStatements(null, con.getValueFactory().createIRI(RDF.NAMESPACE, "subject"), null)) {
                while (statements.hasNext()) tokens.add(statements.next().getSubject().stringValue());
            }
            Random random = new Random(0);
            for (int trial = 0; trial < 250; trial++) {
                Set<String> world = new HashSet<>();
                for (String token : tokens) if (random.nextBoolean()) world.add(token);
                for (Resource root : factoredRoots) {
                    assertEquals("flat and factored circuits differ in random world " + trial,
                            evaluate(flat, root, world, new HashMap<>()),
                            evaluate(factored, root, world, new HashMap<>()));
                }
            }

            assertNoFactoredMetadata(con);
        } finally {
            repo.shutDown();
        }
    }

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
        return executePlan(con, query, ConstructionMode.FACTORED);
    }

    private static Model executePlan(RepositoryConnection con, String query, ConstructionMode mode) {
        return executePlan(con, query, mode, "junit-" + mode.cliName());
    }

    private static Model executePlan(RepositoryConnection con, String query, ConstructionMode mode,
                                     String workspace) {
        Model circuit = new LinkedHashModel();
        CircuitConstructionPlan plan = new CircuitRewriter(
                Reification.STANDARD, mode, workspace).constructionPlan(query);
        CircuitRun.executeConstructionPlan(con, plan, circuit, false);
        return circuit;
    }

    private static Set<Resource> answerRoots(Model model) {
        IRI answer = SimpleValueFactory.getInstance().createIRI(C, "answer");
        return new LinkedHashSet<>(model.filter(null, answer, null).subjects());
    }

    private static int gateCount(Model model, String type) {
        IRI gateType = SimpleValueFactory.getInstance().createIRI(C, type);
        return model.filter(null, RDF.TYPE, gateType).subjects().size();
    }

    private static boolean evaluate(Model model, Resource node, Set<String> world,
                                    Map<Resource, Boolean> memo) {
        Boolean known = memo.get(node);
        if (known != null) return known;
        IRI times = SimpleValueFactory.getInstance().createIRI(C, "Times");
        IRI plus = SimpleValueFactory.getInstance().createIRI(C, "Plus");
        IRI in = SimpleValueFactory.getInstance().createIRI(C, "in");
        IRI feeds = SimpleValueFactory.getInstance().createIRI(C, "feeds");
        boolean value;
        if (model.contains(node, RDF.TYPE, times)) {
            value = true;
            for (Value child : model.filter(node, in, null).objects()) {
                value &= evaluate(model, (Resource) child, world, memo);
            }
        } else if (model.contains(node, RDF.TYPE, plus)) {
            value = false;
            for (Resource child : model.filter(null, feeds, node).subjects()) {
                value |= evaluate(model, child, world, memo);
            }
        } else {
            value = world.contains(node.stringValue());
        }
        memo.put(node, value);
        return value;
    }

    private static void assertNoFactoredMetadata(RepositoryConnection con) {
        try (org.eclipse.rdf4j.repository.RepositoryResult<Statement> statements =
                con.getStatements(null, null, null)) {
            while (statements.hasNext()) {
                assertFalse("private factored workspace leaked into the endpoint",
                        statements.next().getPredicate().stringValue().startsWith(FactoredBgpRewriter.META_NS));
            }
        }
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
