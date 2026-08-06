package npcs.circuit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

import org.eclipse.rdf4j.model.IRI;
import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.model.Resource;
import org.eclipse.rdf4j.model.Value;
import org.eclipse.rdf4j.model.ValueFactory;
import org.eclipse.rdf4j.model.impl.LinkedHashModel;
import org.eclipse.rdf4j.model.impl.SimpleValueFactory;
import org.eclipse.rdf4j.model.vocabulary.RDF;
import org.eclipse.rdf4j.query.BindingSet;
import org.eclipse.rdf4j.query.TupleQueryResult;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.rio.ntriples.NTriplesUtil;
import org.eclipse.rdf4j.sail.memory.MemoryStore;
import org.junit.Test;

import npcs.rewrite.Reification;

/**
 * Semantic correctness of the circuit rewriting, checked against a possible-world oracle instead of
 * against a hand-written expectation.
 *
 * <p>The contract the rewriting has to satisfy is one statement: for every subset {@code W} of the
 * uncertain statement tokens and every candidate answer {@code μ},
 *
 * <pre>   eval(circuit, root(μ), W) = true   ⟺   μ ∈ [[Q]]_{G|W}   </pre>
 *
 * where {@code G|W} is the plain (non-reified) graph containing exactly the triples whose token is in
 * {@code W}. The right-hand side is computed by handing the ORIGINAL query to RDF4J's own W3C
 * evaluator over {@code G|W} — so the oracle is independent of every rewriting rule under test, and a
 * mistake in {@code normalize}, in an operand marginal, in the MINUS guard or in a gate identity shows
 * up as a mismatch rather than having to be predicted in advance.
 *
 * <p>{@link #everyCompositionDenotesTheQueryEventInEveryWorld()} runs this over a generated sweep of
 * operator compositions; the tests around it pin one rewriting rule each so a failure says which rule
 * broke, not just that something did.
 */
public class CircuitSemanticsTest {

    private static final String C = "urn:circuit:";
    private static final ValueFactory VF = SimpleValueFactory.getInstance();

    // ------------------------------------------------------------------ the fixture
    /**
     * One uncertain triple = one token. Chosen so the generated shapes are discriminating:
     * {@code p0} has two derivations for {@code ?x=s} (so a ⊕ over derivations is exercised),
     * {@code s2} gives a second answer binding, and {@code p1}/{@code p2} are single-derivation.
     */
    private static final String[][] FACTS = {
        {"urn:r:t0", "urn:s",  "urn:p0", "urn:a"},
        {"urn:r:t1", "urn:s",  "urn:p0", "urn:b"},
        {"urn:r:t2", "urn:s",  "urn:p1", "urn:c"},
        {"urn:r:t3", "urn:s",  "urn:p2", "urn:d"},
        {"urn:r:t4", "urn:s2", "urn:p0", "urn:e"},
        {"urn:r:t5", "urn:s",  "urn:p3", "urn:f"},
    };

    /** Leaf predicates the fixture provides, hence the widest shape the sweep can materialize. */
    private static final int LEAF_BUDGET = 4;

    /** A query plus the variables it projects (the sweep generates both together). */
    private static final class Shape {
        final String name, query;
        final List<String> projected;
        /**
         * Set for a {@code :p*} / {@code :p?} whose source is a CONSTANT, naming that source and the
         * variable its zero-length match binds. This is the one place the circuit deliberately departs
         * from W3C SPARQL and so the one place the oracle has to be told about (see
         * {@link #theZeroLengthRootIsTheTermsInGraphReading()} and {@code docs/CONFORMANCE.md} item 1):
         * W3C makes {@code <s> :p* ?y} yield {@code ?y = s} unconditionally, whereas the circuit gives
         * it the "s occurs in the graph" gate. They differ exactly when the source occurs in no triple
         * of the world, and {@link #oracle} subtracts that answer there.
         */
        final String zeroLengthSource, zeroLengthVar;

        Shape(String name, String query, List<String> projected) {
            this(name, query, projected, null, null);
        }

        Shape(String name, String query, List<String> projected,
              String zeroLengthSource, String zeroLengthVar) {
            this.name = name; this.query = query; this.projected = projected;
            this.zeroLengthSource = zeroLengthSource; this.zeroLengthVar = zeroLengthVar;
        }
    }

    // ------------------------------------------------------------------ the main sweep
    /**
     * Every composition of UNION/MINUS/OPTIONAL/AND up to two constructors, plus the FILTER variants,
     * checked answer-by-answer over all 2^5 worlds in BOTH construction modes.
     *
     * <p>The existing sweep in {@code CircuitRewriterTest} asserts that these shapes <em>plan</em>.
     * That is a real property (Thm. 4.13's coverage claim) but it cannot see a wrong circuit: a
     * mis-stated normalization identity, a MINUS guard applied on the wrong side, or an operand
     * marginal keyed by the wrong variable set all still produce a well-formed plan. This asserts the
     * denotation instead.
     */
    @Test
    public void everyCompositionDenotesTheQueryEventInEveryWorld() {
        List<Shape> shapes = new ArrayList<>();
        for (int n = 1; n <= 2; n++) shapes.addAll(sweep(n, false));
        for (int n = 1; n <= 2; n++) shapes.addAll(sweep(n, true));
        assertTrue("the generator must produce a real sweep, got " + shapes.size(), shapes.size() >= 60);
        checkAgainstOracle(shapes);
    }

    /**
     * The same check three constructors deep. 320 extra shapes x 2 modes x 32 worlds is too slow for
     * every build, so it is opt-in: {@code mvn test -Dsparqlcirc.deepSemantics=true}. Run it after any
     * change to {@code normalize} or to the operand machinery.
     */
    @Test
    public void deepCompositionDenotesTheQueryEventInEveryWorld() {
        if (!Boolean.getBoolean("sparqlcirc.deepSemantics")) return;
        List<Shape> shapes = new ArrayList<>(sweep(3, false));
        shapes.addAll(sweep(3, true));
        checkAgainstOracle(shapes);
    }

    // ------------------------------------------------------------------ per-rule regressions
    /**
     * {@code A OPT B ≡ Join(A,B) ∪ (A DIFF_unguarded B)} — the expansion every OPTIONAL goes through.
     *
     * <p>Two things have to hold at once and they are what the rule gets wrong when it is wrong: the
     * matched answer must denote {@code a ∧ b}, and the answer that leaves B's variable unbound must
     * denote {@code a ∧ ¬b} — NOT plain {@code a}. The oracle decides both; the assertion here is only
     * that the two answers exist separately, which the oracle cannot state.
     */
    @Test
    public void optionalExpandsIntoAJoinAndAnUnguardedAntiJoin() {
        Shape shape = new Shape("A OPT B",
                "SELECT ?x ?v1 WHERE { ?x <urn:p0> ?v0 OPTIONAL { ?x <urn:p1> ?v1 } }",
                Arrays.asList("x", "v1"));
        checkAgainstOracle(java.util.Collections.singletonList(shape));
        for (ConstructionMode mode : ConstructionMode.values()) {
            Model circuit = build(shape.query, mode);
            assertTrue(mode + ": the negative branch must build a ⊖ gate",
                    !gatesOfType(circuit, "Minus").isEmpty());
        }
    }

    /** {@code Join(A∪B, Z) ≡ (A⋈Z) ∪ (B⋈Z)}, on both sides of the join. */
    @Test
    public void unionDistributesOverJoinOnEitherSide() {
        checkAgainstOracle(Arrays.asList(
            new Shape("Join(Union,·)",
                "SELECT ?x WHERE { { { ?x <urn:p0> ?v0 } UNION { ?x <urn:p1> ?v1 } } ?x <urn:p2> ?v2 }",
                Arrays.asList("x")),
            new Shape("Join(·,Union)",
                "SELECT ?x WHERE { ?x <urn:p2> ?v2 { { ?x <urn:p0> ?v0 } UNION { ?x <urn:p1> ?v1 } } }",
                Arrays.asList("x"))));
    }

    /** {@code (A∪B) MINUS P ≡ (A MINUS P) ∪ (B MINUS P)}. */
    @Test
    public void unionDistributesOverTheMinuend() {
        checkAgainstOracle(java.util.Collections.singletonList(new Shape("Diff(Union,·)",
                "SELECT ?x WHERE { { { ?x <urn:p0> ?v0 } UNION { ?x <urn:p1> ?v1 } } "
              + "MINUS { ?x <urn:p2> ?v2 } }", Arrays.asList("x"))));
    }

    /**
     * {@code P MINUS (C OPT D) ≡ P MINUS C} when P shares no D-only variable, and — the case the rule
     * must NOT fire on — the shape where P does share one, which stays a difference against the
     * OPTIONAL's full solution set. Both go to the oracle, so the shortcut is checked for soundness
     * rather than for having been taken.
     */
    @Test
    public void minusAgainstAnOptionalSubtrahend() {
        checkAgainstOracle(Arrays.asList(
            new Shape("no shared D-only var",
                "SELECT ?x WHERE { ?x <urn:p0> ?v0 MINUS { ?x <urn:p1> ?v1 OPTIONAL { ?x <urn:p2> ?v2 } } }",
                Arrays.asList("x")),
            new Shape("shared D-only var",
                "SELECT ?x ?v2 WHERE { ?x <urn:p0> ?v2 MINUS { ?x <urn:p1> ?v1 OPTIONAL { ?x <urn:p2> ?v2 } } }",
                Arrays.asList("x", "v2"))));
    }

    /** {@code (A ∖ B) ∖ P ≡ A ∖ (B ∪ P)} — chained MINUS, and MINUS chained onto an OPTIONAL. */
    @Test
    public void chainedDifferencesMergeTheirSubtrahends() {
        checkAgainstOracle(Arrays.asList(
            new Shape("two MINUSes",
                "SELECT ?x WHERE { ?x <urn:p0> ?v0 MINUS { ?x <urn:p1> ?v1 } MINUS { ?x <urn:p2> ?v2 } }",
                Arrays.asList("x")),
            new Shape("MINUS then OPTIONAL",
                "SELECT ?x ?v2 WHERE { ?x <urn:p0> ?v0 MINUS { ?x <urn:p1> ?v1 } "
              + "OPTIONAL { ?x <urn:p2> ?v2 } }", Arrays.asList("x", "v2")),
            new Shape("OPTIONAL then MINUS",
                "SELECT ?x ?v1 WHERE { ?x <urn:p0> ?v0 OPTIONAL { ?x <urn:p1> ?v1 } "
              + "MINUS { ?x <urn:p2> ?v2 } }", Arrays.asList("x", "v1")),
            new Shape("Diff(·,Diff)",
                "SELECT ?x WHERE { ?x <urn:p0> ?v0 MINUS { ?x <urn:p1> ?v1 MINUS { ?x <urn:p2> ?v2 } } }",
                Arrays.asList("x"))));
    }

    /**
     * The W3C shared-variable guard is on user MINUS only. On domain-disjoint operands MINUS removes
     * nothing while OPTIONAL's negative branch still must, and the oracle separates the two because
     * RDF4J implements exactly that asymmetry.
     */
    @Test
    public void theSharedVariableGuardIsOnMinusOnly() {
        checkAgainstOracle(Arrays.asList(
            new Shape("disjoint MINUS is a no-op",
                "SELECT ?x WHERE { ?x <urn:p0> ?v0 MINUS { ?u <urn:p1> ?w } }", Arrays.asList("x")),
            new Shape("disjoint OPTIONAL still subtracts",
                "SELECT ?x ?w WHERE { ?x <urn:p0> ?v0 OPTIONAL { ?u <urn:p1> ?w } }",
                Arrays.asList("x", "w"))));
    }

    /** {@code σ_φ(A ∪ B) ≡ σ_φ(A) ∪ σ_φ(B)}, and a FILTER inside each operand position. */
    @Test
    public void filterDistributesOverUnionAndTravelsWithItsOperand() {
        checkAgainstOracle(Arrays.asList(
            new Shape("filter over a union",
                "SELECT ?x WHERE { { { ?x <urn:p0> ?v0 } UNION { ?x <urn:p1> ?v1 } } "
              + "FILTER(?x = <urn:s>) }", Arrays.asList("x")),
            new Shape("filter in the subtrahend",
                "SELECT ?x WHERE { ?x <urn:p0> ?v0 MINUS { ?x <urn:p0> ?v0 FILTER(?v0 = <urn:a>) } }",
                Arrays.asList("x")),
            new Shape("filter in the optional",
                "SELECT ?x ?v0 WHERE { ?x <urn:p1> ?v1 OPTIONAL { ?x <urn:p0> ?v0 FILTER(?v0 = <urn:a>) } }",
                Arrays.asList("x", "v0")),
            new Shape("filter over an optional",
                "SELECT ?x ?v0 WHERE { ?x <urn:p1> ?v1 OPTIONAL { ?x <urn:p0> ?v0 } "
              + "FILTER(?x = <urn:s>) }", Arrays.asList("x", "v0"))));
    }

    /**
     * A ⊕ over several derivations of one answer. {@code ?x=s} is derived from {@code p0} twice, so it
     * must be ONE answer with the function {@code t0 ∨ t1} — the property that makes the answer gate a
     * ⊕ at all, and the reason a per-derivation root would be wrong.
     *
     * <p>Where the ∨ sits differs by plan (flat: both ⊗s feed the answer; factored: they meet in the
     * marginal one level down), so the assertion is on the function, not on the fan-in.
     */
    @Test
    public void severalDerivationsOfOneAnswerShareItsPlusGate() {
        Shape shape = new Shape("two derivations",
                "SELECT ?x WHERE { ?x <urn:p0> ?v0 }", Arrays.asList("x"));
        checkAgainstOracle(java.util.Collections.singletonList(shape));
        for (ConstructionMode mode : ConstructionMode.values()) {
            Model circuit = build(shape.query, mode);
            Resource root = null;
            for (Resource candidate : answerRoots(circuit)) {
                if (bindingsOf(circuit, candidate).get("x").contains("urn:s>")) root = candidate;
            }
            assertTrue(mode + ": the ?x=s answer must exist", root != null);
            for (String[] world : new String[][]{{"urn:r:t0"}, {"urn:r:t1"}, {"urn:r:t0", "urn:r:t1"}}) {
                assertTrue(mode + ": either derivation alone must satisfy the answer " + world[0],
                        evaluate(circuit, root, new LinkedHashSet<>(Arrays.asList(world)),
                                new HashMap<>()));
            }
        }
    }

    /**
     * A self-join derives one answer through two orderings of the same token multiset. The ⊗ key is
     * canonical over that multiset, so the two orderings must collapse to ONE product gate — the
     * hash-consing the shared circuit depends on.
     */
    @Test
    public void aProductGateIsCanonicalOverItsChildMultiset() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:x", "urn:n", "urn:e", "urn:n");        // a self-loop: one token, two roles
            Model circuit = new LinkedHashModel();
            CircuitConstructionPlan plan = new CircuitRewriter(Reification.STANDARD,
                    ConstructionMode.FLAT, "junit-multiset")
                    .constructionPlan("SELECT ?a WHERE { ?a <urn:e> ?b . ?b <urn:e> ?a }");
            CircuitRun.executeConstructionPlan(con, plan, circuit, false);
            assertEquals("both derivation orders address the same ⊗ gate",
                    1, gatesOfType(circuit, "Times").size());
        } finally {
            repo.shutDown();
        }
    }

    /**
     * Gate identity is term-type aware. Two tokens whose objects have the same {@code STR} but
     * different term types ({@code <urn:v>} vs {@code "urn:v"}) are different answers and must not
     * share an answer gate; the historical raw-{@code STR} key aliased them.
     */
    @Test
    public void gateIdentityDistinguishesTermTypesWithEqualLexicalForms() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            ValueFactory vf = con.getValueFactory();
            IRI iriToken = vf.createIRI("urn:r:iri"), litToken = vf.createIRI("urn:r:lit");
            for (IRI token : new IRI[]{iriToken, litToken}) {
                con.add(token, vf.createIRI(RDF.NAMESPACE, "subject"), vf.createIRI("urn:s"));
                con.add(token, vf.createIRI(RDF.NAMESPACE, "predicate"), vf.createIRI("urn:p"));
            }
            con.add(iriToken, vf.createIRI(RDF.NAMESPACE, "object"), vf.createIRI("urn:v"));
            con.add(litToken, vf.createIRI(RDF.NAMESPACE, "object"), vf.createLiteral("urn:v"));

            for (ConstructionMode mode : ConstructionMode.values()) {
                Model circuit = new LinkedHashModel();
                CircuitConstructionPlan plan = new CircuitRewriter(Reification.STANDARD, mode,
                        "junit-terms").constructionPlan("SELECT ?o WHERE { <urn:s> <urn:p> ?o }");
                CircuitRun.executeConstructionPlan(con, plan, circuit, false);
                assertEquals(mode + ": an IRI answer and a literal answer are two answers",
                        2, answerRoots(circuit).size());
            }
        } finally {
            repo.shutDown();
        }
    }

    /**
     * Def. 4.6's θ: distinct queries must not mint the same answer root (merging their circuits in one
     * store would OR unrelated Boolean functions), while the branches of ONE query must all converge
     * on a single root.
     *
     * <p>Includes the composed-closure-atom case: two queries that differ only outside the atom.
     */
    @Test
    public void distinctQueriesNeverShareAnAnswerRoot() {
        String[] queries = {
            "SELECT ?y WHERE { <urn:s> <urn:p0> ?y }",
            "SELECT ?y WHERE { ?y <urn:p0> <urn:a> }",
            "SELECT ?y ?z WHERE { <urn:s> <urn:p0> ?y . ?y <urn:p1> ?z }",
            "SELECT ?y ?z WHERE { <urn:s> <urn:p0> ?y . ?y <urn:p2> ?z }",
            "SELECT ?y ?z WHERE { <urn:s> <urn:p0>+ ?y . ?y <urn:p1> ?z }",
            "SELECT ?y ?z WHERE { <urn:s> <urn:p0>+ ?y . ?y <urn:p2> ?z }",
            "SELECT ?y ?z WHERE { <urn:s> <urn:p0>+ ?y . ?y <urn:p1> ?z . ?z <urn:p2> ?y }",
        };
        Map<String, String> rootByQuery = new LinkedHashMap<>();
        for (String query : queries) {
            String tag = answerIdentity(query);
            String clash = rootByQuery.get(tag);
            if (clash != null) {
                fail("two different queries mint byte-identical answer gates, so merging their "
                   + "circuits in one store aliases unrelated Boolean functions:\n  " + clash
                   + "\n  " + query);
            }
            rootByQuery.put(tag, query);
        }
        // ... and the flip side: one query's branches must NOT be isolated from each other.
        assertEquals("flat and factored are two plans for one query",
                answerIdentity("SELECT ?y ?z WHERE { <urn:s> <urn:p0> ?y . ?y <urn:p1> ?z }",
                        ConstructionMode.FLAT),
                answerIdentity("SELECT ?y ?z WHERE { <urn:s> <urn:p0> ?y . ?y <urn:p1> ?z }",
                        ConstructionMode.FACTORED));
    }

    /**
     * Gate identities are a PUBLISHED interface, not an internal detail: the paper's cross-engine
     * byte-identity result and the frozen artifacts under {@code artifacts/} both name these IRIs, so a
     * refactor of {@code querySemanticKey}, {@code bgpSemanticKey}, {@code answerTag} or the path
     * fingerprint silently invalidates them. Pin the values.
     *
     * <p>If this test fails, the change moved every answer-gate IRI. That may well be intended — then
     * update the constants here, regenerate the affected artifacts, and say so in the commit. What it
     * must not be is a side effect nobody noticed.
     */
    @Test
    public void answerGateIdentitiesAreFrozen() {
        Map<String, String> golden = new LinkedHashMap<>();
        golden.put("SELECT ?x ?y WHERE { ?x <urn:p> ?m . ?m <urn:q> ?y }",
                "A@c0561d4e606065e2ead949a6046354fcc2c62851f593b20b3b44c89c12830149");
        golden.put("SELECT ?x WHERE { { ?x <urn:p> ?y } UNION { ?x <urn:q> ?z } }",
                "A@8a29ac9b39db6c255870510e391056f2e5a0484dfbe8deeb15a94d9ad27606f3");
        golden.put("SELECT ?x WHERE { ?x <urn:p> ?y MINUS { ?x <urn:q> ?z } }",
                "A@64dd46241bb6e9f6ab20e1cecb14e9ba950382f655924c488f5c514a25490344");
        golden.put("SELECT ?x ?z WHERE { ?x <urn:p> ?y OPTIONAL { ?x <urn:q> ?z } }",
                "A@eeb13ccce0c6d06c16be495c880a10b98cc1bad773de0d8a7df3dc8290f569b3");
        golden.put("SELECT ?x WHERE { ?x <urn:p> ?y FILTER(?y != <urn:zz>) }",
                "A@9fe0cba011ef4c1c674127e233b0a75352dc0e645b93e276330806a026e6d8a4");
        for (Map.Entry<String, String> entry : golden.entrySet()) {
            for (ConstructionMode mode : ConstructionMode.values()) {
                assertEquals(mode + ": " + entry.getKey(), entry.getValue(),
                        patternTag(entry.getKey(), mode));
            }
        }
        // The whole-pattern closure atom keys its answers off the path fingerprint instead (it is
        // planned by pathQuery(), not by constructionPlan()); those are the IRIs the published
        // cross-engine path circuits carry.
        CircuitRewriter rewriter = new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT,
                "junit-frozen");
        CircuitRewriter.PathQuery path = rewriter.pathQuery("SELECT ?y WHERE { <urn:a> <urn:p>+ ?y }");
        assertEquals("path fingerprint", "190ff1dd155514ac389c75756c4d06d2ac9db7749ceddf3bbf693c1d18d9a313",
                path.fingerprint());
        assertEquals("whole-pattern path answer tag",
                "A@c97ef7e2236b3c1ea40771e06dd84ea119e8611eade389f60f2df140b2cbd91c",
                tagOf(path.projectAnswers(3).get(0)));
    }

    /** θ must not depend on harmless re-association or on the physical plan. */
    @Test
    public void thePatternTagIsStableUnderReassociation() {
        assertEquals("BGP conjunction is commutative",
                answerIdentity("SELECT ?x WHERE { ?x <urn:p0> ?v0 . ?x <urn:p1> ?v1 }"),
                answerIdentity("SELECT ?x WHERE { ?x <urn:p1> ?v1 . ?x <urn:p0> ?v0 }"));
        assertEquals("UNION is commutative",
                answerIdentity("SELECT ?x WHERE { { ?x <urn:p0> ?v0 } UNION { ?x <urn:p1> ?v1 } }"),
                answerIdentity("SELECT ?x WHERE { { ?x <urn:p1> ?v1 } UNION { ?x <urn:p0> ?v0 } }"));
        assertNotEquals("but DIFF operand order is load-bearing",
                answerIdentity("SELECT ?x WHERE { ?x <urn:p0> ?v0 MINUS { ?x <urn:p1> ?v1 } }"),
                answerIdentity("SELECT ?x WHERE { ?x <urn:p1> ?v1 MINUS { ?x <urn:p0> ?v0 } }"));
    }

    /**
     * The emitted plan must be a function of the query alone. It was not: {@code operandSerial} names
     * the gate variable of each materialized operand and was never reset, so a rewriter instance that
     * had already planned one query emitted DIFFERENT text for the next — {@code plan(q)} twice
     * returned two different strings. Gate IRIs are content-addressed and were unaffected, but the plan
     * is logged, parsed by the paper harnesses, and documented as idempotent.
     *
     * <p>The pre-existing determinism test used a MINUS with no materialized operand, which is exactly
     * the case that stayed deterministic, so it could not see this.
     */
    @Test
    public void theEmittedPlanIsAFunctionOfTheQueryAlone() {
        String[] queries = {
            "SELECT ?x WHERE { { ?x <urn:p0> ?v0 MINUS { ?x <urn:p1> ?v1 } } ?x <urn:p2> ?v2 }",
            "SELECT ?x ?v1 ?v2 WHERE { ?x <urn:p0> ?v0 OPTIONAL { ?x <urn:p1> ?v1 } "
          + "OPTIONAL { ?x <urn:p2> ?v2 } }",
            "SELECT ?x WHERE { ?x <urn:p0> ?v0 MINUS { ?x <urn:p1> ?v1 } }",
        };
        for (ConstructionMode mode : ConstructionMode.values()) {
            // One instance, several queries, then the first one again: the plan must not depend on what
            // the instance has seen before.
            CircuitRewriter shared = new CircuitRewriter(Reification.STANDARD, mode, "junit-det");
            List<String> first = shared.plan(queries[0]);
            for (String query : queries) shared.plan(query);
            assertEquals(mode + ": replanning the same query on a used rewriter must be identical",
                    first, shared.plan(queries[0]));
            // ... and a fresh instance with the same workspace id must agree with it.
            assertEquals(mode + ": a fresh rewriter must emit the same plan",
                    first, new CircuitRewriter(Reification.STANDARD, mode, "junit-det")
                            .plan(queries[0]));
        }
    }

    /**
     * Normalization multiplies branches, and a plan of thousands of CONSTRUCTs is never what the user
     * meant. The bound has to fire as a diagnostic — and it has to fire BEFORE the blowup is allocated.
     *
     * <p>It did not: the check counted the branches of the already-distributed tree, so 18 joined
     * UNIONs exhausted a 512M heap after 56 seconds and never reached the message. 22 joined UNIONs is
     * 4M branches; if this test hangs or dies instead of failing, the early check in {@code normalize}
     * has been lost.
     */
    @Test(timeout = 30_000)
    public void tooManyUnionBranchesIsRefusedBeforeTheBlowupIsAllocated() {
        for (int unions : new int[]{9, 22}) {              // 2^9 = 512, 2^22 = 4M branches
            StringBuilder query = new StringBuilder("SELECT ?x WHERE { ");
            for (int i = 0; i < unions; i++) {
                query.append("{ { ?x <urn:a").append(i).append("> ?m } UNION { ?x <urn:b").append(i)
                     .append("> ?n } } ");
            }
            query.append("}");
            try {
                new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT, "junit-branches")
                        .constructionPlan(query.toString());
                fail(unions + " joined UNIONs must be refused");
            } catch (UnsupportedOperationException expected) {
                assertTrue(expected.getMessage(), expected.getMessage().contains("Query too large"));
            }
        }
        // The early check must not shrink the accepted fragment: distribution only adds branches, so a
        // query that stays under the limit has to keep planning.
        assertTrue(new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT, "junit-branches")
                .constructionPlan("SELECT ?x WHERE { { { ?x <urn:p0> ?a } UNION { ?x <urn:p1> ?b } } "
                        + "{ { ?x <urn:p2> ?c } UNION { ?x <urn:p3> ?d } } }")
                .steps().size() >= 4);
    }

    /**
     * Shapes the generated sweep cannot reach, all through the oracle: a variable predicate, a triple
     * pattern with NO variable at all (it contributes a token but no binding), a repeated variable
     * inside one pattern, and a projected variable the body never binds.
     */
    @Test
    public void unusualPatternShapesDenoteTheQueryEvent() {
        String[][] facts = {
            {"urn:r:s1", "urn:s", "urn:p0", "urn:a"},
            {"urn:r:s2", "urn:s", "urn:p1", "urn:s"},      // a self-referencing triple
            {"urn:r:s3", "urn:k", "urn:p2", "urn:k"},      // a self-loop
        };
        checkAgainstOracle(Arrays.asList(
            new Shape("variable predicate", "SELECT ?p ?o WHERE { <urn:s> ?p ?o }",
                    Arrays.asList("p", "o")),
            new Shape("ground pattern joined", "SELECT ?o WHERE { <urn:s> <urn:p1> <urn:s> . "
                    + "<urn:s> <urn:p0> ?o }", Arrays.asList("o")),
            new Shape("repeated variable in one pattern", "SELECT ?u WHERE { ?u <urn:p2> ?u }",
                    Arrays.asList("u")),
            new Shape("projected but never bound", "SELECT ?o ?never WHERE { <urn:s> <urn:p0> ?o }",
                    Arrays.asList("o", "never"))),
            facts, ConstructionMode.values(), Reification.STANDARD);
    }

    /**
     * Answers that are literals, not IRIs. The gate identity hashes the lexical form, the datatype and
     * the lower-cased language tag separately, and the recovered {@code c:val} has to come back as the
     * same RDF term — otherwise two distinct answers share a root, or one answer's binding is reported
     * wrongly.
     */
    @Test
    public void literalAnswersKeepTheirDatatypeAndLanguage() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            ValueFactory vf = con.getValueFactory();
            Value[] objects = {
                vf.createLiteral("5"),                                    // xsd:string
                vf.createLiteral(5),                                      // xsd:int
                vf.createLiteral("5", vf.createIRI("http://www.w3.org/2001/XMLSchema#decimal")),
                vf.createLiteral("hello", "en"),
                vf.createLiteral("hello", "de"),
                vf.createIRI("urn:5"),
            };
            for (int i = 0; i < objects.length; i++) {
                IRI token = vf.createIRI("urn:r:lit" + i);
                con.add(token, vf.createIRI(RDF.NAMESPACE, "subject"), vf.createIRI("urn:s"));
                con.add(token, vf.createIRI(RDF.NAMESPACE, "predicate"), vf.createIRI("urn:p"));
                con.add(token, vf.createIRI(RDF.NAMESPACE, "object"), objects[i]);
            }
            for (ConstructionMode mode : ConstructionMode.values()) {
                Model circuit = new LinkedHashModel();
                CircuitRun.executeConstructionPlan(con, new CircuitRewriter(Reification.STANDARD, mode,
                        "junit-lit").constructionPlan("SELECT ?o WHERE { <urn:s> <urn:p> ?o }"),
                        circuit, false);
                Set<String> recovered = new TreeSet<>();
                for (Resource root : answerRoots(circuit)) {
                    recovered.add(bindingsOf(circuit, root).get("o"));
                }
                Set<String> want = new TreeSet<>();
                for (Value object : objects) want.add(NTriplesUtil.toNTriplesString(object));
                assertEquals(mode + ": every term must be its own answer, recovered exactly",
                        want, recovered);
            }
        } finally {
            repo.shutDown();
        }
    }

    // ------------------------------------------------------------------ closure atoms
    /**
     * A closure atom composed with every operator, checked against the oracle. RDF4J evaluates
     * {@code :p+} itself, so the level-indexed fixpoint is compared with a transitive closure nobody
     * in this codebase wrote.
     *
     * <p>The data is a 3-chain plus a branch, so {@code :p+} has answers at several depths and the
     * round bound is exercised.
     */
    @Test
    public void closureAtomsDenoteTheQueryEventInEveryWorld() {
        String[][] facts = {
            {"urn:r:e0", "urn:n0", "urn:p", "urn:n1"},
            {"urn:r:e1", "urn:n1", "urn:p", "urn:n2"},
            {"urn:r:e2", "urn:n2", "urn:p", "urn:n3"},
            {"urn:r:q0", "urn:n2", "urn:q", "urn:z"},
        };
        List<Shape> shapes = Arrays.asList(
            new Shape("p+ alone",       "SELECT ?y WHERE { <urn:n0> <urn:p>+ ?y }", Arrays.asList("y")),
            new Shape("p* alone",       "SELECT ?y WHERE { <urn:n0> <urn:p>* ?y }", Arrays.asList("y"),
                                        "urn:n0", "y"),
            new Shape("p+ then join",   "SELECT ?y ?z WHERE { <urn:n0> <urn:p>+ ?y . ?y <urn:q> ?z }",
                                        Arrays.asList("y", "z")),
            new Shape("p+ in a union",  "SELECT ?y WHERE { { <urn:n0> <urn:p>+ ?y } UNION "
                                      + "{ <urn:n0> <urn:q> ?y } }", Arrays.asList("y")),
            new Shape("p+ as minuend",  "SELECT ?y WHERE { <urn:n0> <urn:p>+ ?y MINUS { ?y <urn:q> ?z } }",
                                        Arrays.asList("y")),
            new Shape("p+ in the subtrahend",
                                        "SELECT ?y WHERE { ?y <urn:q> ?z MINUS { <urn:n0> <urn:p>+ ?y } }",
                                        Arrays.asList("y")),
            new Shape("p+ with optional","SELECT ?y ?z WHERE { <urn:n0> <urn:p>+ ?y OPTIONAL { ?y <urn:q> ?z } }",
                                        Arrays.asList("y", "z")),
            new Shape("p+ filtered",    "SELECT ?y WHERE { <urn:n0> <urn:p>+ ?y FILTER(?y != <urn:n3>) }",
                                        Arrays.asList("y")),
            new Shape("bound variable source",
                                        "SELECT ?x ?y WHERE { ?x <urn:q> ?z . ?x <urn:p>+ ?y }",
                                        Arrays.asList("x", "y")));
        checkAgainstOracle(shapes, facts, ConstructionMode.values());
    }

    /**
     * {@code :p*} on cyclic data. The level index is what keeps the emitted RDF an acyclic DAG on a
     * cycle; without it the reach gates would feed each other and no evaluation order exists.
     */
    @Test
    public void aCyclicClosureStaysAnAcyclicCircuit() {
        String[][] facts = {
            {"urn:r:c0", "urn:n0", "urn:p", "urn:n1"},
            {"urn:r:c1", "urn:n1", "urn:p", "urn:n0"},
        };
        checkAgainstOracle(Arrays.asList(
            new Shape("cyclic p+", "SELECT ?y WHERE { <urn:n0> <urn:p>+ ?y }", Arrays.asList("y")),
            new Shape("cyclic p*", "SELECT ?y WHERE { <urn:n0> <urn:p>* ?y }", Arrays.asList("y"),
                    "urn:n0", "y")),
            facts, new ConstructionMode[]{ConstructionMode.FLAT});
    }

    /**
     * The one deliberate departure from W3C SPARQL, pinned so it cannot drift silently in either
     * direction: the zero-length root of {@code e*}/{@code e?} is the "source occurs in the graph"
     * gate, not the paper's constant {@code g⊤}.
     *
     * <p>W3C makes {@code <n0> :p* ?y} yield {@code ?y = n0} even over the empty graph. The circuit
     * gives that answer the ⊕ of the tokens of the triples mentioning {@code n0}, so over an empty
     * world it has no answer at all. {@code docs/CONFORMANCE.md} item 1 records this as the paper's
     * text being the thing to change, and notes that the Python oracle cannot see the difference
     * because it encodes the same convention — this oracle is RDF4J's own evaluator, so it can, and
     * that is why the deviation has to be subtracted explicitly rather than left implicit.
     *
     * <p>If someone implements the paper's {@code g⊤}, this test fails and the fix is to update it
     * together with {@code CONFORMANCE.md}, not to re-hide the divergence.
     */
    @Test
    public void theZeroLengthRootIsTheTermsInGraphReading() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:e0", "urn:n0", "urn:p", "urn:n1");
            Model circuit = new LinkedHashModel();
            CircuitRewriter rewriter = new CircuitRewriter(Reification.STANDARD,
                    ConstructionMode.FLAT, "junit-zerolen");
            CircuitRun.executeConstructionPlan(con,
                    rewriter.constructionPlan("SELECT ?y WHERE { <urn:n0> <urn:p>* ?y }"),
                    circuit, false);
            Resource selfAnswer = null;
            for (Resource root : answerRoots(circuit)) {
                if ("<urn:n0>".equals(bindingsOf(circuit, root).get("y"))) selfAnswer = root;
            }
            assertTrue("the zero-length answer ?y = n0 must exist", selfAnswer != null);
            assertTrue("and it must hold once the token mentioning n0 is present",
                    evaluate(circuit, selfAnswer,
                            new LinkedHashSet<>(Arrays.asList("urn:r:e0")), new HashMap<>()));
            assertFalse("terms-in-graph, NOT the paper's g⊤: with no triple mentioning n0 the "
                    + "zero-length answer does not hold. If this is ever changed to g⊤, update "
                    + "docs/CONFORMANCE.md item 1 in the same commit.",
                    evaluate(circuit, selfAnswer, new LinkedHashSet<>(), new HashMap<>()));
        } finally {
            repo.shutDown();
        }
    }

    // ------------------------------------------------------------------ reification schemes
    /**
     * A reification scheme is a LEAF encoding only: {@code Reification.reify} changes how one triple
     * pattern binds its token, and the ⊗/⊕/⊖ machinery above it is supposed to be untouched. That is a
     * claim about every scheme, and it had no semantic coverage — the suite only ever built Standard,
     * so a scheme could bind the wrong token, or none, and every test still passed.
     *
     * <p>Each scheme gets the same composed query and the same possible worlds; only the physical
     * encoding of the data differs, so the circuits must denote the same event.
     */
    @Test
    public void everyReificationSchemeDenotesTheSameEvent() {
        List<Shape> shapes = Arrays.asList(
            new Shape("bgp",      "SELECT ?x ?v0 WHERE { ?x <urn:p0> ?v0 }", Arrays.asList("x", "v0")),
            new Shape("join",     "SELECT ?x WHERE { ?x <urn:p0> ?v0 . ?x <urn:p1> ?v1 }",
                                  Arrays.asList("x")),
            new Shape("union",    "SELECT ?x WHERE { { ?x <urn:p0> ?v0 } UNION { ?x <urn:p1> ?v1 } }",
                                  Arrays.asList("x")),
            new Shape("minus",    "SELECT ?x WHERE { ?x <urn:p0> ?v0 MINUS { ?x <urn:p1> ?v1 } }",
                                  Arrays.asList("x")),
            new Shape("optional", "SELECT ?x ?v1 WHERE { ?x <urn:p0> ?v0 OPTIONAL { ?x <urn:p1> ?v1 } }",
                                  Arrays.asList("x", "v1")),
            new Shape("composite","SELECT ?x WHERE { { ?x <urn:p0> ?v0 MINUS { ?x <urn:p1> ?v1 } } "
                                + "?x <urn:p2> ?v2 }", Arrays.asList("x")));
        for (Reification scheme : new Reification[]{Reification.STANDARD, Reification.SPARQL_STAR,
                                                    Reification.NAMED_GRAPH}) {
            checkAgainstOracle(shapes, FACTS, ConstructionMode.values(), scheme);
        }
    }

    /**
     * Wikidata's native statement reification, which constrains the query rather than just the data:
     * the predicate must be a constant {@code wdt:} IRI, and a variable or non-{@code wdt:} predicate
     * must be refused rather than encoded wrongly.
     */
    @Test
    public void wikidataReificationNeedsAConstantDirectPredicate() {
        String wdt = "http://www.wikidata.org/prop/direct/";
        String[][] facts = {
            {"urn:st:1", "urn:q1", wdt + "P1", "urn:q2"},
            {"urn:st:2", "urn:q1", wdt + "P2", "urn:q3"},
        };
        checkAgainstOracle(Arrays.asList(
            new Shape("wdt join", "SELECT ?x ?y WHERE { ?x <" + wdt + "P1> ?y }",
                    Arrays.asList("x", "y")),
            new Shape("wdt optional",
                    "SELECT ?x ?z WHERE { ?x <" + wdt + "P1> ?y OPTIONAL { ?x <" + wdt + "P2> ?z } }",
                    Arrays.asList("x", "z"))),
            facts, ConstructionMode.values(), Reification.WIKIDATA);

        for (String bad : new String[]{"SELECT ?x WHERE { ?x ?p ?y }",
                                       "SELECT ?x WHERE { ?x <urn:notwdt> ?y }"}) {
            try {
                new CircuitRewriter(Reification.WIKIDATA, ConstructionMode.FLAT, "junit-wd")
                        .constructionPlan(bad);
                fail("Wikidata reification must refuse " + bad + " rather than encode it wrongly");
            } catch (UnsupportedOperationException expected) {
                assertTrue(expected.getMessage(), expected.getMessage().contains("wdt:"));
            }
        }
    }

    /**
     * {@code naryrel} is a different provenance GRANULARITY, not just a different encoding: the token
     * is the row entity (the subject), the data stays plain, and every triple about a row shares that
     * row's token. So a world toggles ROWS, and the oracle graph for a world contains every triple
     * whose subject is a live row — which is what this checks, because the shared per-triple oracle
     * above cannot express it.
     */
    @Test
    public void naryRelationReificationIsPerRowNotPerTriple() {
        String[][] rows = {
            {"urn:row1", "urn:p0", "urn:a"},
            {"urn:row1", "urn:p1", "urn:c"},          // same row: ONE token for both triples
            {"urn:row2", "urn:p0", "urn:b"},
        };
        List<String> tokens = Arrays.asList("urn:row1", "urn:row2");
        String query = "SELECT ?x WHERE { ?x <urn:p0> ?v0 . ?x <urn:p1> ?v1 }";

        Repository plain = new SailRepository(new MemoryStore());
        Model circuit = new LinkedHashModel();
        try (RepositoryConnection con = plain.getConnection()) {
            ValueFactory vf = con.getValueFactory();
            for (String[] row : rows) {
                con.add(vf.createIRI(row[0]), vf.createIRI(row[1]), vf.createIRI(row[2]));
            }
            CircuitRun.executeConstructionPlan(con, new CircuitRewriter(Reification.NARYREL,
                    ConstructionMode.FLAT, "junit-nary").constructionPlan(query), circuit, false);
        } finally {
            plain.shutDown();
        }
        Set<Resource> roots = answerRoots(circuit);
        assertEquals("only row1 has both attributes", 1, roots.size());
        Resource root = roots.iterator().next();
        assertTrue("row1 live ⇒ the answer holds; the two triples share ONE token, so a single "
                + "row toggle decides it", evaluate(circuit, root,
                new LinkedHashSet<>(Arrays.asList(tokens.get(0))), new HashMap<>()));
        assertFalse("row1 absent ⇒ the answer is gone",
                evaluate(circuit, root, new LinkedHashSet<>(Arrays.asList(tokens.get(1))),
                        new HashMap<>()));
    }

    /** Write one uncertain triple in {@code scheme}'s physical encoding. */
    private static void encode(RepositoryConnection con, Reification scheme,
                              String token, String s, String p, String o) {
        ValueFactory vf = con.getValueFactory();
        IRI t = vf.createIRI(token);
        switch (scheme) {
            case STANDARD:
                reify(con, token, s, p, o);
                return;
            case SPARQL_STAR:
                con.add(vf.createTriple(vf.createIRI(s), vf.createIRI(p), vf.createIRI(o)),
                        vf.createIRI("http://example.org/occurrenceOf"), t);
                return;
            case NAMED_GRAPH:
                con.add(vf.createIRI(s), vf.createIRI(p), vf.createIRI(o), t);   // token = graph name
                return;
            case WIKIDATA: {
                String local = p.substring("http://www.wikidata.org/prop/direct/".length());
                con.add(vf.createIRI(s), vf.createIRI("http://www.wikidata.org/prop/" + local), t);
                con.add(t, vf.createIRI("http://www.wikidata.org/prop/statement/" + local),
                        vf.createIRI(o));
                return;
            }
            default:
                throw new UnsupportedOperationException("no fixture encoding for " + scheme);
        }
    }

    // ------------------------------------------------------------------ the oracle harness
    private void checkAgainstOracle(List<Shape> shapes) {
        checkAgainstOracle(shapes, FACTS, ConstructionMode.values());
    }

    private void checkAgainstOracle(List<Shape> shapes, String[][] facts, ConstructionMode[] modes) {
        checkAgainstOracle(shapes, facts, modes, Reification.STANDARD);
    }

    /**
     * Build each shape's circuit once over the reified data, then walk every possible world and
     * compare, per shape and per mode, the set of answers the circuit makes true against the set
     * RDF4J's own evaluator produces for the original query over that world's plain graph.
     */
    private void checkAgainstOracle(List<Shape> shapes, String[][] facts, ConstructionMode[] modes,
                                    Reification scheme) {
        List<String> tokens = new ArrayList<>();
        for (String[] fact : facts) tokens.add(fact[0]);

        Map<String, Model> circuits = new LinkedHashMap<>();
        Repository reified = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = reified.getConnection()) {
            for (String[] fact : facts) encode(con, scheme, fact[0], fact[1], fact[2], fact[3]);
            for (Shape shape : shapes) {
                for (ConstructionMode mode : modes) {
                    Model circuit = new LinkedHashModel();
                    // A fresh workspace id per (shape,mode): the private urn:sc: rows of one shape must
                    // never be visible to the next, which is also what a real concurrent run relies on.
                    CircuitConstructionPlan plan = new CircuitRewriter(scheme, mode,
                            "junit-oracle-" + circuits.size()).constructionPlan(shape.query);
                    try {
                        CircuitRun.executeConstructionPlan(con, plan, circuit, false);
                    } catch (RuntimeException failure) {
                        throw new AssertionError("construction failed for " + shape.name + " ("
                                + mode + "): " + shape.query, failure);
                    }
                    circuits.put(key(shape, mode), circuit);
                }
            }
        } finally {
            reified.shutDown();
        }

        // A comparison of two empty sets passes without testing anything, so require every shape to
        // carry a real answer in at least one world. Without this a typo in the fixture (or a leaf
        // budget that silently skips the widest shapes) turns the whole sweep green while checking
        // nothing — which is how the first version of this test "passed" three constructors deep.
        Set<String> witnessed = new LinkedHashSet<>();
        for (int bits = 0; bits < (1 << tokens.size()); bits++) {
            Set<String> world = new LinkedHashSet<>();
            for (int i = 0; i < tokens.size(); i++) if ((bits & (1 << i)) != 0) world.add(tokens.get(i));
            Repository plain = new SailRepository(new MemoryStore());
            try (RepositoryConnection con = plain.getConnection()) {
                ValueFactory vf = con.getValueFactory();
                for (String[] fact : facts) {
                    if (world.contains(fact[0])) {
                        con.add(vf.createIRI(fact[1]), vf.createIRI(fact[2]), vf.createIRI(fact[3]));
                    }
                }
                for (Shape shape : shapes) {
                    Set<String> expected = oracle(con, shape);
                    for (ConstructionMode mode : modes) {
                        Model circuit = circuits.get(key(shape, mode));
                        Set<String> actual = new TreeSet<>();
                        Map<Resource, Boolean> memo = new HashMap<>();
                        for (Resource root : answerRoots(circuit)) {
                            if (evaluate(circuit, root, world, memo)) {
                                actual.add(render(bindingsOf(circuit, root), shape.projected));
                            }
                        }
                        if (!expected.isEmpty()) witnessed.add(shape.name);
                        if (!expected.equals(actual)) {
                            fail(shape.name + " (" + mode + ", " + scheme + ") denotes the wrong "
                               + "event.\n  query:    " + shape.query + "\n  world:    " + world
                               + "\n  expected: " + expected + "\n  circuit:  " + actual);
                        }
                    }
                }
            } finally {
                plain.shutDown();
            }
        }
        List<String> vacuous = new ArrayList<>();
        for (Shape shape : shapes) if (!witnessed.contains(shape.name)) vacuous.add(shape.name);
        assertTrue("these shapes have no answer in ANY world, so their comparison is vacuous and they "
                + "test nothing: " + vacuous, vacuous.isEmpty());
    }

    private static String key(Shape shape, ConstructionMode mode) { return shape.name + "|" + mode; }

    /** The answers RDF4J's own W3C evaluator gives the ORIGINAL query over this world's plain graph. */
    private static Set<String> oracle(RepositoryConnection con, Shape shape) {
        Set<String> out = new TreeSet<>();
        try (TupleQueryResult result = con.prepareTupleQuery(shape.query).evaluate()) {
            while (result.hasNext()) {
                BindingSet row = result.next();
                Map<String, String> bindings = new TreeMap<>();
                for (String variable : shape.projected) {
                    Value value = row.getValue(variable);
                    bindings.put(variable, value == null ? null : NTriplesUtil.toNTriplesString(value));
                }
                out.add(render(bindings, shape.projected));
            }
        }
        if (shape.zeroLengthSource != null && !occurs(con, shape.zeroLengthSource)) {
            // The documented terms-in-graph deviation. The source occurs in no triple of this world, so
            // no path of length >= 1 leaves it either: the ONLY answer binding the source is the
            // zero-length one, and dropping it is exactly the reading the circuit implements.
            Map<String, String> zeroLength = new TreeMap<>();
            for (String variable : shape.projected) zeroLength.put(variable, null);
            zeroLength.put(shape.zeroLengthVar, "<" + shape.zeroLengthSource + ">");
            out.remove(render(zeroLength, shape.projected));
        }
        return out;
    }

    private static boolean occurs(RepositoryConnection con, String term) {
        IRI iri = con.getValueFactory().createIRI(term);
        return con.hasStatement(iri, null, null, false) || con.hasStatement(null, null, iri, false);
    }

    private static String render(Map<String, String> bindings, List<String> projected) {
        StringBuilder out = new StringBuilder();
        for (String variable : new TreeSet<>(projected)) {
            String value = bindings.get(variable);
            out.append(variable).append('=').append(value == null ? "UNBOUND" : value).append(' ');
        }
        return out.toString();
    }

    /** An answer gate's recovered bindings: {@code c:binding} -> {@code c:var} / {@code c:val}. */
    private static Map<String, String> bindingsOf(Model circuit, Resource root) {
        Map<String, String> out = new TreeMap<>();
        IRI binding = VF.createIRI(C, "binding");
        IRI var = VF.createIRI(C, "var");
        IRI val = VF.createIRI(C, "val");
        for (Value node : circuit.filter(root, binding, null).objects()) {
            Set<Value> names = circuit.filter((Resource) node, var, null).objects();
            if (names.isEmpty()) continue;
            Set<Value> values = circuit.filter((Resource) node, val, null).objects();
            out.put(names.iterator().next().stringValue(), values.isEmpty()
                    ? null : NTriplesUtil.toNTriplesString(values.iterator().next()));
        }
        return out;
    }

    // ------------------------------------------------------------------ shape generation
    /** Every group expression with exactly {@code n} binary constructors; FILTER adds a unary one. */
    private static List<Shape> sweep(int n, boolean withFilter) {
        List<Shape> out = new ArrayList<>();
        for (String shape : compositions(n, withFilter)) {
            int leaves = 0;
            for (int i = 0; i < shape.length(); i++) if (shape.startsWith("LEAF", i)) leaves++;
            if (leaves > LEAF_BUDGET) continue;             // the fixture provides p0..p3
            List<String> projected = new ArrayList<>();
            projected.add("x");
            for (int i = 0; i < leaves; i++) projected.add("v" + i);
            out.add(new Shape(shape, "SELECT " + withQ(projected) + " WHERE " + materialize(shape),
                    projected));
        }
        return out;
    }

    private static String withQ(List<String> variables) {
        StringBuilder out = new StringBuilder();
        for (String variable : variables) out.append('?').append(variable).append(' ');
        return out.toString();
    }

    private static List<String> compositions(int n, boolean withFilter) {
        List<String> out = new ArrayList<>();
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
            for (String a : compositions(n - 1, true)) out.add("{" + a + " FILTER(?x = <urn:s>)}");
        }
        return out;
    }

    /** Give each LEAF placeholder its own predicate, all sharing {@code ?x}. */
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

    // ------------------------------------------------------------------ small helpers
    private static Model build(String query, ConstructionMode mode) {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            for (String[] fact : FACTS) reify(con, fact[0], fact[1], fact[2], fact[3]);
            Model circuit = new LinkedHashModel();
            CircuitRun.executeConstructionPlan(con, new CircuitRewriter(Reification.STANDARD, mode,
                    "junit-build").constructionPlan(query), circuit, false);
            return circuit;
        } finally {
            repo.shutDown();
        }
    }

    /** The answer gate's BIND expression: the query's answer IDENTITY, independent of any data. */
    private static String answerIdentity(String query) {
        return answerIdentity(query, ConstructionMode.FLAT);
    }

    private static String answerIdentity(String query, ConstructionMode mode) {
        CircuitRewriter rewriter = new CircuitRewriter(Reification.STANDARD, mode, "junit-identity");
        CircuitRewriter.PathQuery path = rewriter.pathQuery(query);
        List<String> emitted = path != null ? path.projectAnswers(1)
                : rewriter.constructionPlan(query).queries();
        for (String text : emitted) {
            int gate = text.indexOf("urn:g:a:");
            if (gate < 0) continue;
            int start = text.lastIndexOf("BIND(", gate);
            return text.substring(start, text.indexOf('\n', gate));
        }
        throw new AssertionError("no answer gate emitted for " + query);
    }

    /** Def. 4.6's θ as it is embedded in an emitted answer-gate BIND. */
    private static String patternTag(String query, ConstructionMode mode) {
        for (String text : new CircuitRewriter(Reification.STANDARD, mode, "junit-tag")
                .constructionPlan(query).queries()) {
            String tag = tagOf(text);
            if (tag != null) return tag;
        }
        throw new AssertionError("no answer gate emitted for " + query);
    }

    private static String tagOf(String constructText) {
        int gate = constructText.indexOf("urn:g:a:");
        if (gate < 0) return null;
        int start = constructText.indexOf("\"A@", gate);
        return start < 0 ? null : constructText.substring(start + 1, constructText.indexOf('"', start + 1));
    }

    private static Set<Resource> answerRoots(Model model) {
        return new LinkedHashSet<>(model.filter(null, VF.createIRI(C, "answer"), null).subjects());
    }

    private static Set<Resource> gatesOfType(Model model, String type) {
        return new LinkedHashSet<>(model.filter(null, RDF.TYPE, VF.createIRI(C, type)).subjects());
    }

    /** Eq. (1)-(3): ⊗ = ∧ of children, ⊕ = ∨ of feeders, ⊖(C,d) = (∨C) ∧ ¬d, leaf = token ∈ world. */
    private static boolean evaluate(Model model, Resource node, Set<String> world,
                                    Map<Resource, Boolean> memo) {
        Boolean known = memo.get(node);
        if (known != null) return known;
        memo.put(node, false);                    // cycle guard: a level-indexed circuit has none
        boolean value;
        if (model.contains(node, RDF.TYPE, VF.createIRI(C, "Times"))) {
            value = true;
            for (Value child : model.filter(node, VF.createIRI(C, "in"), null).objects()) {
                value &= evaluate(model, (Resource) child, world, memo);
            }
        } else if (model.contains(node, RDF.TYPE, VF.createIRI(C, "Plus"))) {
            value = false;
            for (Resource child : model.filter(null, VF.createIRI(C, "feeds"), node).subjects()) {
                value |= evaluate(model, child, world, memo);
            }
        } else if (model.contains(node, RDF.TYPE, VF.createIRI(C, "Minus"))) {
            Value positive = model.filter(node, VF.createIRI(C, "minuend"), null)
                    .objects().iterator().next();
            Value negative = model.filter(node, VF.createIRI(C, "subtrahend"), null)
                    .objects().iterator().next();
            value = evaluate(model, (Resource) positive, world, memo)
                    && !evaluate(model, (Resource) negative, world, memo);
        } else {
            value = world.contains(node.stringValue());
        }
        memo.put(node, value);
        return value;
    }

    private static void reify(RepositoryConnection con, String token, String s, String p, String o) {
        ValueFactory vf = con.getValueFactory();
        IRI t = vf.createIRI(token);
        con.add(t, vf.createIRI(RDF.NAMESPACE, "subject"), vf.createIRI(s));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "predicate"), vf.createIRI(p));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "object"), vf.createIRI(o));
    }

}
