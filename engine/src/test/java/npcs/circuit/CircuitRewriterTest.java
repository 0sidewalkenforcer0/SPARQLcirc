package npcs.circuit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

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

        // UNION of pure BGPs: each branch is now factored (min-scope variable elimination), so the
        // composite construction is factored — not the old whole-query flat fallback.
        String union = "SELECT ?z WHERE { { <urn:s> <urn:p> ?z } UNION { <urn:s> <urn:q> ?z } }";
        CircuitConstructionPlan unionPlan = defaultRewriter.constructionPlan(union);
        assertEquals(ConstructionMode.FACTORED, unionPlan.requestedMode());
        assertEquals(ConstructionMode.FACTORED, unionPlan.effectiveMode());
        assertTrue(unionPlan.requiresFeedback());
        assertNull(unionPlan.fallbackReason());
        for (CircuitConstructionPlan.Step step : unionPlan.steps()) {
            new SPARQLParser().parseQuery(step.query(), null);     // every emitted step is valid SPARQL
        }
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
            assertEquals("private session ids must not affect circuit gate identities",
                    canonicalStatements(factored), canonicalStatements(repeated));
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
    public void concurrentFactoredSessionsAreIsolatedAndCleaned() throws Exception {
        Repository repo = new SailRepository(new MemoryStore());
        ExecutorService workers = Executors.newFixedThreadPool(2);
        try {
            try (RepositoryConnection setup = repo.getConnection()) {
                reify(setup, "urn:r:left-a", "urn:s", "urn:p", "urn:a");
                reify(setup, "urn:r:left-b", "urn:s", "urn:p", "urn:b");
                reify(setup, "urn:r:right-a", "urn:a", "urn:q", "urn:x");
                reify(setup, "urn:r:right-b", "urn:b", "urn:q", "urn:y");
            }
            String query = "SELECT ?z WHERE { <urn:s> <urn:p> ?mid . ?mid <urn:q> ?z . }";
            CountDownLatch start = new CountDownLatch(1);
            Future<Model> first = workers.submit(() -> {
                try (RepositoryConnection con = repo.getConnection()) {
                    start.await();
                    return executePlan(con, query, ConstructionMode.FACTORED, "concurrent-one");
                }
            });
            Future<Model> second = workers.submit(() -> {
                try (RepositoryConnection con = repo.getConnection()) {
                    start.await();
                    return executePlan(con, query, ConstructionMode.FACTORED, "concurrent-two");
                }
            });
            start.countDown();
            Model firstCircuit = first.get(30, TimeUnit.SECONDS);
            Model secondCircuit = second.get(30, TimeUnit.SECONDS);
            assertEquals("session-local feedback must produce identical public circuits",
                    canonicalStatements(firstCircuit), canonicalStatements(secondCircuit));
            assertEquals(2, answerRoots(firstCircuit).size());
            try (RepositoryConnection audit = repo.getConnection()) {
                assertNoFactoredMetadata(audit);
            }
        } finally {
            workers.shutdownNow();
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

    /**
     * The answer ⊕ carries Def. 4.6's pattern tag θ, so it isolates queries without breaking the
     * convergence the rewriting depends on.
     *
     * <p>Two properties, and they pull in opposite directions:
     * <ul>
     *   <li><b>Isolation across queries.</b> Two queries denoting different events must not mint the
     *       same root. Before θ, {@code SELECT ?z WHERE {&lt;s&gt; &lt;p&gt; ?z}} and
     *       {@code SELECT ?z WHERE {?z &lt;p&gt; &lt;o&gt;}} produced byte-identical answer gates for a
     *       shared binding, and merging their circuits OR-ed the two functions together.</li>
     *   <li><b>Convergence inside one query.</b> θ is one value per query, so UNION branches, a MINUS
     *       root and a factored BGP still land on ONE shared answer ⊕ — which is what makes the
     *       shared circuit shared.</li>
     * </ul>
     */
    @Test
    public void answerGatesAreIsolatedPerQueryButSharedAcrossBranchesOfOne() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:1", "urn:s", "urn:p", "urn:m");
            reify(con, "urn:r:2", "urn:m", "urn:p", "urn:o");

            for (ConstructionMode mode : ConstructionMode.values()) {
                // ?z = urn:m is an answer to both, via different events (r1 vs r2).
                Set<Resource> forward = answerRoots(executePlan(con,
                        "SELECT ?z WHERE { <urn:s> <urn:p> ?z }", mode));
                Set<Resource> backward = answerRoots(executePlan(con,
                        "SELECT ?z WHERE { ?z <urn:p> <urn:o> }", mode));
                assertEquals(mode + ": one answer each", 1, forward.size());
                assertEquals(mode + ": one answer each", 1, backward.size());
                assertTrue(mode + ": distinct queries must not share an answer root",
                        java.util.Collections.disjoint(forward, backward));

                // Same query, two UNION branches, one binding: exactly ONE root, not two.
                Set<Resource> union = answerRoots(executePlan(con,
                        "SELECT ?z WHERE { { <urn:s> <urn:p> ?z } UNION { ?z <urn:p> <urn:o> } }", mode));
                assertEquals(mode + ": the UNION branches must converge on one shared answer ⊕",
                        1, union.size());
                assertTrue(mode + ": and that root belongs to the UNION query, not to either branch "
                                + "query taken on its own",
                        java.util.Collections.disjoint(union, forward)
                                && java.util.Collections.disjoint(union, backward));
            }

            // Flat and factored are two plans for the SAME query: their answer roots must agree
            // byte for byte, or a factored branch could never merge with a flat one.
            String query = "SELECT ?z WHERE { <urn:s> <urn:p> ?y . ?y <urn:p> ?z }";
            assertEquals("θ is plan-independent",
                    answerRoots(executePlan(con, query, ConstructionMode.FLAT)),
                    answerRoots(executePlan(con, query, ConstructionMode.FACTORED)));
        } finally {
            repo.shutDown();
        }
    }

    /**
     * A SPARQL subquery parses to the same {@code Projection} node RDF4J wraps around a property-path
     * {@code ?} expansion, but unlike that wrapper it can restrict scope. Looking through one that
     * does was the single place the rewriting answered a DIFFERENT query instead of failing fast:
     * {@code SELECT ?y WHERE {{ SELECT ?x WHERE { ?x :p ?y }}}} has no solution binding {@code ?y}
     * (rdflib: 0 rows), yet the stripped body bound it and a probability came out.
     */
    @Test
    public void aSubqueryThatProjectsAwayAnOuterVariableIsRejected() {
        assertRejected(() -> new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT, "junit-sub")
                        .constructionPlan("SELECT ?y WHERE { { SELECT ?x WHERE { ?x <urn:p> ?y } } }"),
                "projects away");
        assertRejected(() -> new CircuitRewriter(Reification.STANDARD, ConstructionMode.FACTORED, "junit-sub")
                        .constructionPlan("SELECT ?y WHERE { { SELECT ?x WHERE { ?x <urn:p> ?y } } }"),
                "projects away");
        // A scope-PRESERVING subquery stays transparent, and so does the Distinct+Projection that
        // RDF4J wraps around a `:p?` path expansion -- rejecting those would be over-correction.
        assertNotNull(new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT, "junit-sub")
                .constructionPlan("SELECT ?y WHERE { { SELECT ?y WHERE { ?x <urn:p> ?y } } }"));
        assertNotNull(new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT, "junit-sub")
                .constructionPlan("SELECT ?x ?y WHERE { ?x <urn:p>? ?y }"));
    }

    /**
     * A UNION may now be a JOIN operand: {@code normalize} distributes
     * {@code Join(A∪B, Z) ≡ (A⋈Z) ∪ (B⋈Z)}, which leaves every operand a BGP, the only thing the
     * inline {@code reif} of a join can consume. Before, {@code assertPureBgp} rejected it.
     *
     * <p>Checked against the answer's Boolean function over all 2^4 worlds, not just against
     * "it plans": {@code ?x=s} holds exactly when {@code (t1 ∨ t3) ∧ t4}.
     */
    @Test
    public void aUnionMayBeAJoinOperandAndDenotesTheRightEvent() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:t1", "urn:s", "urn:p", "urn:b");
            reify(con, "urn:r:t2", "urn:b", "urn:q", "urn:c");
            reify(con, "urn:r:t3", "urn:s", "urn:r", "urn:e");
            reify(con, "urn:r:t4", "urn:s", "urn:t", "urn:f");
            String query = "SELECT ?x WHERE { { { ?x <urn:p> ?y } UNION { ?x <urn:r> ?w } } "
                         + "?x <urn:t> ?v }";
            String[] tokens = {"urn:r:t1", "urn:r:t2", "urn:r:t3", "urn:r:t4"};

            for (ConstructionMode mode : ConstructionMode.values()) {
                Model circuit = executePlan(con, query, mode);
                Set<Resource> roots = answerRoots(circuit);
                assertEquals(mode + ": one answer", 1, roots.size());
                Resource root = roots.iterator().next();
                for (int bits = 0; bits < 16; bits++) {
                    Set<String> world = new LinkedHashSet<>();
                    for (int i = 0; i < 4; i++) if ((bits & (1 << i)) != 0) world.add(tokens[i]);
                    boolean expected = (world.contains("urn:r:t1") || world.contains("urn:r:t3"))
                            && world.contains("urn:r:t4");
                    assertEquals(mode + ": world " + world, expected,
                            evaluate(circuit, root, world, new HashMap<>()));
                }
            }
        } finally {
            repo.shutDown();
        }
    }

    /**
     * OPTIONAL is no longer planned by a dedicated planner: normalize expands
     * {@code A OPT B ≡ Join(A,B) ∪ (A DIFF B)} — §3's own definition — so a UNION may now sit on
     * either side of it. The expansion has to keep the two difference operators apart, which is what
     * this pins down on domain-DISJOINT operands, where they disagree:
     * <ul>
     *   <li>OPTIONAL's negative branch is the UNGUARDED anti-join. Its {@code ?w}-unbound answer must
     *       denote {@code t1 ∧ ¬t2}, so it is FALSE in the world where the optional matched.
     *       A guarded difference would leave it unconditionally true — a bare left answer standing
     *       next to the matched one.</li>
     *   <li>User MINUS keeps the W3C guard, so on disjoint operands it removes nothing.</li>
     * </ul>
     */
    @Test
    public void optionalKeepsTheUnguardedDifferenceAndMinusKeepsTheGuard() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:t1", "urn:s", "urn:p", "urn:b");
            reify(con, "urn:r:t2", "urn:z", "urn:r", "urn:e");
            Set<String> bothPresent = new LinkedHashSet<>(java.util.Arrays.asList("urn:r:t1", "urn:r:t2"));

            for (ConstructionMode mode : ConstructionMode.values()) {
                // disjoint operands: ?x from the left, ?u/?w from the optional
                Model optional = executePlan(con,
                        "SELECT ?x ?w WHERE { ?x <urn:p> ?y OPTIONAL { ?u <urn:r> ?w } }", mode);
                Set<Resource> roots = answerRoots(optional);
                assertEquals(mode + ": a matched answer and a ?w-unbound one", 2, roots.size());
                int trueWhenOptionalMatched = 0;
                for (Resource root : roots) {
                    if (evaluate(optional, root, bothPresent, new HashMap<>())) trueWhenOptionalMatched++;
                }
                assertEquals(mode + ": with the optional matched only the extended answer may hold; "
                        + "an unguarded difference is what removes the other one", 1, trueWhenOptionalMatched);

                Model minus = executePlan(con,
                        "SELECT ?x WHERE { ?x <urn:p> ?y MINUS { ?u <urn:r> ?w } }", mode);
                Set<Resource> minusRoots = answerRoots(minus);
                assertEquals(mode + ": MINUS on disjoint operands is a no-op", 1, minusRoots.size());
                assertTrue(mode + ": and its answer survives, guard intact",
                        evaluate(minus, minusRoots.iterator().next(), bothPresent, new HashMap<>()));
            }
        } finally {
            repo.shutDown();
        }
    }

    /** A UNION on either side of an OPTIONAL, unlocked by the same expansion. */
    @Test
    public void aUnionMayBeAnOptionalOperand() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:t1", "urn:s", "urn:p", "urn:b");
            reify(con, "urn:r:t3", "urn:s", "urn:r", "urn:e");
            reify(con, "urn:r:t4", "urn:s", "urn:t", "urn:f");
            for (ConstructionMode mode : ConstructionMode.values()) {
                assertFalse(mode + ": UNION as the OPTIONAL's left operand", answerRoots(executePlan(con,
                        "SELECT ?x WHERE { { { ?x <urn:p> ?y } UNION { ?x <urn:r> ?w } } "
                      + "OPTIONAL { ?x <urn:t> ?v } }", mode)).isEmpty());
                assertFalse(mode + ": UNION as the OPTIONAL's right operand", answerRoots(executePlan(con,
                        "SELECT ?x WHERE { ?x <urn:p> ?y OPTIONAL { { ?x <urn:r> ?w } "
                      + "UNION { ?x <urn:t> ?v } } }", mode)).isEmpty());
            }
        } finally {
            repo.shutDown();
        }
    }

    /**
     * A composite JOIN operand. Def. 4.7 clause 3 allows any subpattern there, but the rewriting
     * reifies operands inline, which only a BGP supports. A MINUS operand is now materialized as a
     * private {@code urn:sc:} row relation carrying its binding columns and its ⊖ gate — clause 2's
     * recipe for an operand that cannot be reified — and the join reads that relation, contributing
     * the ⊖ gate as a single ⊗ child.
     *
     * <p>Checked over all 2^3 worlds: {@code { {A MINUS C} . D }} must denote {@code t1 ∧ ¬t2 ∧ t3}.
     * The materialized route also needs a writable endpoint, so the plan must declare feedback.
     */
    @Test
    public void aMinusMayBeAJoinOperandAndDenotesTheRightEvent() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:t1", "urn:s", "urn:p", "urn:b");
            reify(con, "urn:r:t2", "urn:s", "urn:r", "urn:e");
            reify(con, "urn:r:t3", "urn:s", "urn:t", "urn:f");
            String[] tokens = {"urn:r:t1", "urn:r:t2", "urn:r:t3"};
            String query = "SELECT ?x WHERE { { ?x <urn:p> ?y MINUS { ?x <urn:r> ?w } } ?x <urn:t> ?v }";

            assertTrue("a materialized operand needs a writable endpoint, so the plan must say so",
                    new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT, "junit-operand")
                            .constructionPlan(query).requiresFeedback());

            for (ConstructionMode mode : ConstructionMode.values()) {
                Model circuit = executePlan(con, query, mode);
                Set<Resource> roots = answerRoots(circuit);
                assertEquals(mode + ": one answer", 1, roots.size());
                Resource root = roots.iterator().next();
                for (int bits = 0; bits < 8; bits++) {
                    Set<String> world = new LinkedHashSet<>();
                    for (int i = 0; i < 3; i++) if ((bits & (1 << i)) != 0) world.add(tokens[i]);
                    boolean expected = world.contains("urn:r:t1") && !world.contains("urn:r:t2")
                            && world.contains("urn:r:t3");
                    assertEquals(mode + ": world " + world, expected,
                            evaluate(circuit, root, world, new HashMap<>()));
                }
            }
        } finally {
            repo.shutDown();
        }
    }

    /**
     * Two OPTIONALs — {@code LeftJoin(LeftJoin(A,B),C)} — the shape real queries hit most often and the
     * one that could not be reached by reordering. normalize expands both into
     * {@code Union(Join, Diff)} and distributes, leaving four branches of which one,
     * {@code Join(Diff(A,B),C)}, is a join over a materialized operand.
     *
     * <p>The all-unbound answer is the discriminating one: it must denote {@code t1 ∧ ¬t2 ∧ ¬t3}.
     */
    @Test
    public void twoOptionalsPlanAndTheUnmatchedAnswerIsDoublyNegated() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:t1", "urn:s", "urn:p", "urn:b");
            reify(con, "urn:r:t2", "urn:s", "urn:r", "urn:e");
            reify(con, "urn:r:t3", "urn:s", "urn:t", "urn:f");
            String[] tokens = {"urn:r:t1", "urn:r:t2", "urn:r:t3"};
            String query = "SELECT ?x ?w ?v WHERE { ?x <urn:p> ?y "
                         + "OPTIONAL { ?x <urn:r> ?w } OPTIONAL { ?x <urn:t> ?v } }";

            for (ConstructionMode mode : ConstructionMode.values()) {
                Model circuit = executePlan(con, query, mode);
                Set<Resource> roots = answerRoots(circuit);
                assertEquals(mode + ": ?w and ?v each bound or not", 4, roots.size());
                // the answer where neither optional matched: no c:val on either optional variable
                Resource unmatched = null;
                for (Resource root : roots) {
                    int bound = 0;
                    for (Value b : circuit.filter(root, SimpleValueFactory.getInstance()
                            .createIRI(C, "binding"), null).objects()) {
                        if (!circuit.filter((Resource) b, SimpleValueFactory.getInstance()
                                .createIRI(C, "val"), null).isEmpty()) bound++;
                    }
                    if (bound == 1) unmatched = root;                 // only ?x carries a value
                }
                assertNotNull(mode + ": the neither-matched answer must exist", unmatched);
                for (int bits = 0; bits < 8; bits++) {
                    Set<String> world = new LinkedHashSet<>();
                    for (int i = 0; i < 3; i++) if ((bits & (1 << i)) != 0) world.add(tokens[i]);
                    boolean expected = world.contains("urn:r:t1") && !world.contains("urn:r:t2")
                            && !world.contains("urn:r:t3");
                    assertEquals(mode + ": world " + world, expected,
                            evaluate(circuit, unmatched, world, new HashMap<>()));
                }
            }
        } finally {
            repo.shutDown();
        }
    }

    /**
     * The composition matrix: every way the supported operators nest, which is what Thm. 4.13 claims
     * and what the engine now builds. Each shape is listed with the algebra it parses to, because the
     * W3C group translation folds left to right and the position of an operator inside its group
     * decides that algebra — {@code { A OPTIONAL{B} . C }} is {@code Join(LeftJoin(A,B), C)} while
     * {@code { A . C OPTIONAL{B} }} is {@code LeftJoin(Join(A,C), B)}. Only the second used to build.
     *
     * <p>This asserts coverage. The Boolean functions are checked against possible-world enumeration
     * by the focused tests above and, for the whole matrix, against the Python reference.
     */
    @Test
    public void everyCompositionOfTheSupportedOperatorsBuilds() {
        String[][] shapes = {
            {"Join(Union,·)",      "SELECT ?x WHERE { { { ?x <urn:p> ?y } UNION { ?x <urn:r> ?w } } ?x <urn:t> ?v }"},
            {"Join(LeftJoin,·)",   "SELECT ?x WHERE { { ?x <urn:p> ?y OPTIONAL { ?x <urn:r> ?w } } ?x <urn:t> ?v }"},
            {"Join(Diff,·)",       "SELECT ?x WHERE { { ?x <urn:p> ?y MINUS { ?x <urn:r> ?w } } ?x <urn:t> ?v }"},
            {"Union(Diff,·)",      "SELECT ?x WHERE { { ?x <urn:p> ?y MINUS { ?x <urn:r> ?w } } UNION { ?x <urn:t> ?v } }"},
            {"Union(LeftJoin,·)",  "SELECT ?x WHERE { { ?x <urn:p> ?y OPTIONAL { ?x <urn:r> ?w } } UNION { ?x <urn:t> ?v } }"},
            {"Diff(Union,·)",      "SELECT ?x WHERE { { { ?x <urn:p> ?y } UNION { ?x <urn:r> ?w } } MINUS { ?x <urn:t> ?v } }"},
            {"Diff(·,Union)",      "SELECT ?x WHERE { ?x <urn:p> ?y MINUS { { ?x <urn:r> ?w } UNION { ?x <urn:t> ?v } } }"},
            {"Diff(LeftJoin,·)",   "SELECT ?x WHERE { { ?x <urn:p> ?y OPTIONAL { ?x <urn:r> ?w } } MINUS { ?x <urn:t> ?v } }"},
            {"Diff(·,LeftJoin)",   "SELECT ?x WHERE { ?x <urn:p> ?y MINUS { ?x <urn:r> ?w OPTIONAL { ?x <urn:t> ?v } } }"},
            {"Diff(Diff,·)",       "SELECT ?x WHERE { { ?x <urn:p> ?y MINUS { ?x <urn:r> ?w } } MINUS { ?x <urn:t> ?v } }"},
            {"Diff(·,Diff)",       "SELECT ?x WHERE { ?x <urn:p> ?y MINUS { ?x <urn:r> ?w MINUS { ?x <urn:t> ?v } } }"},
            {"LeftJoin(Union,·)",  "SELECT ?x WHERE { { { ?x <urn:p> ?y } UNION { ?x <urn:r> ?w } } OPTIONAL { ?x <urn:t> ?v } }"},
            {"LeftJoin(·,Union)",  "SELECT ?x WHERE { ?x <urn:p> ?y OPTIONAL { { ?x <urn:r> ?w } UNION { ?x <urn:t> ?v } } }"},
            {"LeftJoin(LeftJoin,·)","SELECT ?x WHERE { ?x <urn:p> ?y OPTIONAL { ?x <urn:r> ?w } OPTIONAL { ?x <urn:t> ?v } }"},
            {"LeftJoin(·,LeftJoin)","SELECT ?x WHERE { ?x <urn:p> ?y OPTIONAL { ?x <urn:r> ?w OPTIONAL { ?x <urn:t> ?v } } }"},
            {"LeftJoin(Diff,·)",   "SELECT ?x WHERE { { ?x <urn:p> ?y MINUS { ?x <urn:r> ?w } } OPTIONAL { ?x <urn:t> ?v } }"},
            {"LeftJoin(·,Diff)",   "SELECT ?x WHERE { ?x <urn:p> ?y OPTIONAL { ?x <urn:r> ?w MINUS { ?x <urn:t> ?v } } }"},
            {"OPTIONAL mid-group", "SELECT ?x WHERE { ?x <urn:p> ?y OPTIONAL { ?x <urn:r> ?w } ?x <urn:t> ?v }"},
            {"MINUS mid-group",    "SELECT ?x WHERE { ?x <urn:p> ?y MINUS { ?x <urn:r> ?w } ?x <urn:t> ?v }"},
            {"UNION mid-group",    "SELECT ?x WHERE { ?x <urn:p> ?y { { ?x <urn:r> ?w } UNION { ?x <urn:t> ?v } } }"},
            {"MINUS then OPTIONAL","SELECT ?x WHERE { ?x <urn:p> ?y MINUS { ?x <urn:r> ?w } OPTIONAL { ?x <urn:t> ?v } }"},
            {"OPTIONAL then MINUS","SELECT ?x WHERE { ?x <urn:p> ?y OPTIONAL { ?x <urn:r> ?w } MINUS { ?x <urn:t> ?v } }"},
            {"two MINUSes",        "SELECT ?x WHERE { ?x <urn:p> ?y MINUS { ?x <urn:r> ?w } MINUS { ?x <urn:t> ?v } }"},
            {"nested UNION",       "SELECT ?x WHERE { { ?x <urn:p> ?y } UNION { ?x <urn:r> ?w } UNION { ?x <urn:t> ?v } }"},
            // Three levels deep: the two-level matrix above missed that a JOIN can itself be an
            // operand (Join(Diff,·) appears as a join operand once a third OPTIONAL nests), which
            // planOperand handled only for a Difference.
            {"three OPTIONALs",    "SELECT ?x WHERE { ?x <urn:p> ?y OPTIONAL { ?x <urn:r> ?w } "
                                 + "OPTIONAL { ?x <urn:t> ?v } OPTIONAL { ?x <urn:u> ?z } }"},
            {"two OPT then MINUS", "SELECT ?x WHERE { ?x <urn:p> ?y OPTIONAL { ?x <urn:r> ?w } "
                                 + "OPTIONAL { ?x <urn:t> ?v } MINUS { ?x <urn:u> ?z } }"},
            {"MINUS of two OPTs",  "SELECT ?x WHERE { { ?x <urn:p> ?y OPTIONAL { ?x <urn:r> ?w } } "
                                 + "MINUS { ?x <urn:t> ?v OPTIONAL { ?x <urn:u> ?z } } }"},
        };
        for (String[] shape : shapes) {
            for (ConstructionMode mode : ConstructionMode.values()) {
                try {
                    CircuitConstructionPlan plan = new CircuitRewriter(
                            Reification.STANDARD, mode, "junit-matrix").constructionPlan(shape[1]);
                    assertFalse(shape[0] + " (" + mode + ") produced no steps", plan.steps().isEmpty());
                    for (CircuitConstructionPlan.Step step : plan.steps()) {
                        new SPARQLParser().parseQuery(step.query(), null);   // every step is valid SPARQL
                    }
                } catch (RuntimeException failure) {
                    throw new AssertionError(shape[0] + " (" + mode + ") no longer builds: "
                            + failure.getMessage(), failure);
                }
            }
        }
    }

    /**
     * Thm. 4.13 claims every composition of the supported operators. This ENUMERATES them instead of
     * listing a few by hand, which is the only way the claim can be checked: the hand-written matrix
     * above missed that three nested OPTIONALs put a JOIN in operand position, and missed FILTER in
     * operand position entirely. Both gaps were found by accident, not by the matrix.
     *
     * <p>Two sweeps: every binary-operator tree up to four operators, and every tree up to three
     * constructors once FILTER is added as a unary one. The condition is on {@code ?x}, which every
     * leaf binds, so it is in scope wherever it lands.
     */
    @Test
    public void everyCompositionUpToThreeConstructorsBuilds() {
        List<String> shapes = new java.util.ArrayList<>();
        for (int n = 1; n <= 4; n++) shapes.addAll(compositions(n, false));
        for (int n = 1; n <= 3; n++) shapes.addAll(compositions(n, true));
        assertTrue("the generator must produce a real sweep", shapes.size() > 4000);
        List<String> rejected = new java.util.ArrayList<>();
        for (String shape : shapes) {
            String query = "SELECT ?x WHERE " + materialize(shape);
            for (ConstructionMode mode : ConstructionMode.values()) {
                try {
                    new CircuitRewriter(Reification.STANDARD, mode, "junit-sweep").constructionPlan(query);
                } catch (RuntimeException failure) {
                    if (rejected.size() < 6) rejected.add(mode + ": " + query + "\n      " + failure.getMessage());
                }
            }
        }
        assertTrue(shapes.size() + " shapes swept, " + rejected.size() + " rejected:\n   "
                + String.join("\n   ", rejected), rejected.isEmpty());
    }

    /**
     * The same sweep, deeper. 86,016 binary-operator trees is too slow for every build, so it is
     * opt-in: {@code mvn test -Dsparqlcirc.deepSweep=true}. Run it after any change to normalize or
     * to the operand machinery — the three-constructor sweep would not have caught the depth-3 gap.
     */
    @Test
    public void deepCompositionSweep() {
        if (!Boolean.getBoolean("sparqlcirc.deepSweep")) return;      // opt-in
        int planned = 0;
        for (int n = 1; n <= 5; n++) {
            for (String shape : compositions(n, false)) {
                String query = "SELECT ?x WHERE " + materialize(shape);
                for (ConstructionMode mode : ConstructionMode.values()) {
                    try {
                        new CircuitRewriter(Reification.STANDARD, mode, "junit-deep")
                                .constructionPlan(query);
                        planned++;
                    } catch (RuntimeException failure) {
                        throw new AssertionError(mode + " rejected " + query + ": "
                                + failure.getMessage(), failure);
                    }
                }
            }
        }
        assertEquals("46,948 trees (4+32+320+3584+43008) x 2 construction modes", 93896, planned);
    }

    /** Every group expression with exactly {@code n} constructors; FILTER counts as a unary one. */
    private static List<String> compositions(int n, boolean withFilter) {
        List<String> out = new java.util.ArrayList<>();
        if (n == 0) { out.add("LEAF"); return out; }
        for (int left = 0; left < n; left++) {
            for (String a : compositions(left, withFilter)) {
                for (String b : compositions(n - 1 - left, withFilter)) {
                    out.add("{" + a + " " + b + "}");
                    out.add("{{" + a + "} UNION {" + b + "}}");
                    out.add("{" + a + " MINUS {" + b + "}}");
                    out.add("{" + a + " OPTIONAL {" + b + "}}");
                }
            }
        }
        if (withFilter) {
            for (String a : compositions(n - 1, true)) out.add("{" + a + " FILTER(?x != <urn:zz>)}");
        }
        return out;
    }

    /** Give each LEAF placeholder a distinct triple pattern sharing {@code ?x}. */
    private static String materialize(String shape) {
        StringBuilder out = new StringBuilder();
        int leaf = 0;
        for (int i = 0; i < shape.length(); ) {
            if (shape.startsWith("LEAF", i)) {
                out.append("?x <urn:p").append(leaf).append("> ?v").append(leaf++).append(" .");
                i += 4;
            } else {
                out.append(shape.charAt(i++));
            }
        }
        return out.toString();
    }

    /**
     * A closure atom as a join operand — Def. 4.7 clause 2 finally used for what it describes. The
     * atom's level-indexed fixpoint runs as its own plan step, publishes {@code reif(C, g_C)} as a
     * private row relation, and the join reads it like a triple pattern's gate. Before, a property
     * path had to BE the whole query pattern.
     *
     * <p>Over all 2^3 worlds: {@code { <a> :p+ ?y . ?y :q ?z }} has one answer, {@code ?y=c ?z=z},
     * and it holds exactly when the whole chain does — {@code t1 ∧ t2 ∧ t3}.
     */
    @Test
    public void aClosureAtomMayBeAJoinOperandAndDenotesTheRightEvent() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:t1", "urn:a", "urn:p", "urn:b");
            reify(con, "urn:r:t2", "urn:b", "urn:p", "urn:c");
            reify(con, "urn:r:t3", "urn:c", "urn:q", "urn:z");
            String[] tokens = {"urn:r:t1", "urn:r:t2", "urn:r:t3"};
            String query = "SELECT ?y ?z WHERE { <urn:a> <urn:p>+ ?y . ?y <urn:q> ?z }";

            CircuitConstructionPlan plan = new CircuitRewriter(
                    Reification.STANDARD, ConstructionMode.FLAT, "junit-path").constructionPlan(query);
            boolean hasPathStep = false;
            for (CircuitConstructionPlan.Step step : plan.steps()) {
                if (step.path() != null) hasPathStep = true;
            }
            assertTrue("the atom must be planned as its own iterative step", hasPathStep);
            assertTrue("a materialized atom needs a writable endpoint", plan.requiresFeedback());

            Model circuit = executePlan(con, query, ConstructionMode.FLAT);
            Set<Resource> roots = answerRoots(circuit);
            assertEquals("one answer: ?y=c ?z=z", 1, roots.size());
            Resource root = roots.iterator().next();
            for (int bits = 0; bits < 8; bits++) {
                Set<String> world = new LinkedHashSet<>();
                for (int i = 0; i < 3; i++) if ((bits & (1 << i)) != 0) world.add(tokens[i]);
                assertEquals("world " + world, world.size() == 3,
                        evaluate(circuit, root, world, new HashMap<>()));
            }
            assertNoFactoredMetadata(con);          // the atom's private rows are cleaned up
        } finally {
            repo.shutDown();
        }
    }

    /** The other compositions a closure atom can appear in; probabilities are checked by
     *  {@code reference/verify_composition.py} against an rdflib possible-world oracle. */
    @Test
    public void closureAtomsComposeWithEveryOperator() {
        String[][] shapes = {
            {"UNION branch",  "SELECT ?y WHERE { { <urn:a> <urn:p>+ ?y } UNION { <urn:a> <urn:q> ?y } }"},
            {"MINUS minuend", "SELECT ?y WHERE { <urn:a> <urn:p>+ ?y MINUS { ?y <urn:q> ?z } }"},
            {"MINUS subtrahend", "SELECT ?y WHERE { ?y <urn:q> ?z MINUS { <urn:a> <urn:p>+ ?y } }"},
            {"OPTIONAL",      "SELECT ?y ?z WHERE { <urn:a> <urn:p>+ ?y OPTIONAL { ?y <urn:q> ?z } }"},
            {"FILTER",        "SELECT ?y WHERE { <urn:a> <urn:p>+ ?y FILTER(?y != <urn:zz>) }"},
            {"variable source", "SELECT ?x ?y WHERE { ?x <urn:p>+ ?y . ?x <urn:q> ?w }"},
            {"zero-or-more",  "SELECT ?y ?z WHERE { <urn:a> <urn:p>* ?y . ?y <urn:q> ?z }"},
            {"two atoms",     "SELECT ?y WHERE { <urn:a> <urn:p>+ ?y . <urn:a> <urn:r>+ ?y }"},
        };
        for (String[] shape : shapes) {
            for (ConstructionMode mode : ConstructionMode.values()) {
                try {
                    new CircuitRewriter(Reification.STANDARD, mode, "junit-path")
                            .constructionPlan(shape[1]);
                } catch (RuntimeException failure) {
                    throw new AssertionError(shape[0] + " (" + mode + "): " + failure.getMessage(),
                            failure);
                }
            }
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

    @Test
    public void npcsRewriterRejectsSilentlyMiscompiledPatterns() {
        // B1 guard: these used to slip past isPureBgp and get flattened into a plain BGP by
        // StatementPatternCollector — silently producing a WRONG circuit (property paths collapsed to a
        // single hop, LIMIT/OFFSET dropped, a nested subquery losing its own scope). They must now be
        // rejected loudly instead of miscompiled.
        assertRejected(() -> new NpcsRewriter(Reification.STANDARD).rewrite(
                "SELECT ?o WHERE { <urn:s> <urn:p>+ ?o }"), "Unsupported pattern");        // p+ path
        assertRejected(() -> new NpcsRewriter(Reification.STANDARD).rewrite(
                "SELECT ?o WHERE { <urn:s> <urn:p>* ?o }"), "Unsupported pattern");        // p* path
        assertRejected(() -> new NpcsRewriter(Reification.STANDARD).rewrite(
                "SELECT ?o WHERE { <urn:s> <urn:p> ?o } LIMIT 5"), "Unsupported pattern"); // LIMIT/OFFSET
        assertRejected(() -> new NpcsRewriter(Reification.STANDARD).rewrite(
                "SELECT ?o WHERE { { SELECT ?o WHERE { <urn:s> <urn:p> ?o } } }"), "Unsupported pattern"); // subquery
    }

    @Test
    public void circuitRewriterRejectsFilteredLeftJoin() {
        // W3C filtered left join: RDF4J attaches the FILTER as the LeftJoin condition. The rewriting
        // does not model it; dropping it would silently emit a circuit for the *unfiltered* OPTIONAL
        // (wrong answers, no error). It must be rejected loudly, like FILTER-in-BGP (assertPureBgp).
        assertRejected(() -> new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT, "junit-flj")
                .constructionPlan("SELECT ?x ?y WHERE { ?x <urn:a> ?age OPTIONAL { ?x <urn:b> ?y . FILTER(?y > ?age) } }"),
                "filtered left join");
        // The plain (condition-free) OPTIONAL must still compile.
        new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT, "junit-flj-ok")
                .constructionPlan("SELECT ?x ?y WHERE { ?x <urn:a> ?age OPTIONAL { ?x <urn:b> ?y } }");
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

    /**
     * A circuit as its canonical N-Triples line set — the same thing the cross-engine byte-identity
     * comparison uses, and the right way to compare two circuits here.
     *
     * <p>{@code Model.equals} is NOT: a circuit mixes terms owned by the store ({@code MemIRI},
     * {@code MemLiteral}) with terms the CONSTRUCT mints ({@code SimpleIRI}, {@code SimpleLiteral}),
     * which class a given term comes back as depends on timing, and {@code LinkedHashModel}'s indexed
     * {@code contains} can then miss a statement that is equal as RDF. Measured over 60 concurrent
     * runs of the test below: {@code Model.equals} reported unequal twice while the sorted triple
     * text differed zero times. Comparing models directly made this a ~1-in-10 flake with no
     * underlying defect.
     */
    private static Set<String> canonicalStatements(Model model) {
        Set<String> out = new java.util.TreeSet<>();
        for (Statement st : model) {
            out.add(org.eclipse.rdf4j.rio.ntriples.NTriplesUtil.toNTriplesString(st.getSubject()) + " "
                  + org.eclipse.rdf4j.rio.ntriples.NTriplesUtil.toNTriplesString(st.getPredicate()) + " "
                  + org.eclipse.rdf4j.rio.ntriples.NTriplesUtil.toNTriplesString(st.getObject()) + " .");
        }
        return out;
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
        SimpleValueFactory vf = SimpleValueFactory.getInstance();
        IRI times = vf.createIRI(C, "Times");
        IRI plus = vf.createIRI(C, "Plus");
        IRI minus = vf.createIRI(C, "Minus");
        IRI in = vf.createIRI(C, "in");
        IRI feeds = vf.createIRI(C, "feeds");
        IRI minuend = vf.createIRI(C, "minuend");
        IRI subtrahend = vf.createIRI(C, "subtrahend");
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
        } else if (model.contains(node, RDF.TYPE, minus)) {
            // Eq. (3): ⊖(C,d) = (∨C) ∧ ¬d. Without this branch a ⊖ gate fell through to the leaf case
            // and read as FALSE in every world, which silently weakened every non-monotone assertion.
            Value pos = model.filter(node, minuend, null).objects().iterator().next();
            Value neg = model.filter(node, subtrahend, null).objects().iterator().next();
            value = evaluate(model, (Resource) pos, world, memo)
                    && !evaluate(model, (Resource) neg, world, memo);
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
