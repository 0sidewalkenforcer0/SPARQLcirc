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
                Arrays.asList("x", "v2")),
            new Shape("D-only var hidden in C subtrahend",
                "SELECT ?x ?h WHERE { ?x <urn:p0> ?h MINUS { "
              + "{ ?x <urn:p1> ?v1 MINUS { ?h <urn:p2> ?v2 } } "
              + "OPTIONAL { ?h <urn:p3> ?v3 } } }",
                Arrays.asList("x", "h"))));
    }

    @Test
    public void propertyPathFrontierKeepsLiteralTermType() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            ValueFactory vf = con.getValueFactory();
            IRI token = vf.createIRI("urn:r:literal-path");
            Value literal = vf.createLiteral("terminal");
            con.add(token, vf.createIRI(RDF.NAMESPACE, "subject"), vf.createIRI("urn:s"));
            con.add(token, vf.createIRI(RDF.NAMESPACE, "predicate"), vf.createIRI("urn:p"));
            con.add(token, vf.createIRI(RDF.NAMESPACE, "object"), literal);

            Model circuit = new LinkedHashModel();
            CircuitRewriter.PathQuery path = new CircuitRewriter(
                    Reification.STANDARD, ConstructionMode.FLAT, "junit-path-literal")
                    .pathQuery("SELECT ?y WHERE { <urn:s> <urn:p>+ ?y }");
            CircuitRun.buildPathCircuit(con, path, circuit);

            Set<String> values = new TreeSet<>();
            for (Resource root : answerRoots(circuit)) values.add(bindingsOf(circuit, root).get("y"));
            assertEquals(java.util.Collections.singleton(NTriplesUtil.toNTriplesString(literal)), values);
        } finally {
            repo.shutDown();
        }
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

    /** RDF-star bindings use the quoted triple's canonical lexical form in the gate identity. */
    @Test
    public void gateIdentityDistinguishesQuotedTripleBindings() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            ValueFactory vf = con.getValueFactory();
            IRI occurrence = vf.createIRI("http://example.org/occurrenceOf");
            for (String suffix : new String[]{"one", "two"}) {
                Resource quoted = vf.createTriple(vf.createIRI("urn:nested:" + suffix),
                        vf.createIRI("urn:q"), vf.createLiteral("value"));
                Resource statement = vf.createTriple(vf.createIRI("urn:s"),
                        vf.createIRI("urn:p"), quoted);
                con.add(statement, occurrence, vf.createIRI("urn:token:" + suffix));
            }

            for (ConstructionMode mode : ConstructionMode.values()) {
                Model circuit = new LinkedHashModel();
                CircuitConstructionPlan plan = new CircuitRewriter(Reification.SPARQL_STAR, mode,
                        "junit-quoted-terms")
                        .constructionPlan("SELECT ?o WHERE { <urn:s> <urn:p> ?o }");
                CircuitRun.executeConstructionPlan(con, plan, circuit, false);
                Set<Resource> roots = answerRoots(circuit);
                assertEquals(mode + ": distinct quoted triples must have distinct answer roots",
                        2, roots.size());
                Set<String> values = new LinkedHashSet<>();
                for (Resource root : roots) values.add(bindingsOf(circuit, root).get("o"));
                assertEquals(mode + ": each root must retain its quoted-triple binding",
                        2, values.size());
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
            // Whole-pattern closure atoms differing ONLY in an endpoint. The per-path fingerprint
            // erases the endpoints by design (the base relation is all-pairs, shared across sources),
            // so a θ derived from the fingerprint alone minted byte-identical answer roots for the
            // first two — "s reaches y" and "a reaches y" collapsed onto one root when their circuits
            // met on one store. The endpoints enter ANSWERPATH separately now.
            "SELECT ?y WHERE { <urn:s> <urn:p0>+ ?y }",
            "SELECT ?y WHERE { <urn:a> <urn:p0>+ ?y }",
            "SELECT ?y WHERE { <urn:s> <urn:p0>* ?y }",
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
        assertEquals("... and re-planning the same whole-pattern path query must not move its root",
                answerIdentity("SELECT ?y WHERE { <urn:s> <urn:p0>+ ?y }"),
                answerIdentity("SELECT ?y WHERE { <urn:s> <urn:p0>+ ?y }"));
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
        // The whole-pattern closure atom keys its answers off the path fingerprint plus its own
        // endpoints (it is planned by pathQuery(), not by constructionPlan()); those are the IRIs the
        // published cross-engine path circuits carry. Tag re-pinned 2026-08-17 when the endpoints
        // entered ANSWERPATH: the fingerprint erases them (all-pairs base), so a tag derived from it
        // alone aliased {<a> :p+ ?y} with {<b> :p+ ?y}. The fingerprint itself is unchanged.
        CircuitRewriter rewriter = new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT,
                "junit-frozen");
        CircuitRewriter.PathQuery path = rewriter.pathQuery("SELECT ?y WHERE { <urn:a> <urn:p>+ ?y }");
        assertEquals("path fingerprint", "190ff1dd155514ac389c75756c4d06d2ac9db7749ceddf3bbf693c1d18d9a313",
                path.fingerprint());
        assertEquals("whole-pattern path answer tag",
                "A@d22cdde0f801faae86e182bc9b7c0f415b7bd28d34aa412b8ed598b6d0264bff",
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

    // ------------------------------------------------------------------ one-pass base relations
    /**
     * A selective BGP's k base relations come from ONE evaluation of the join instead of k. The whole
     * argument for that rests on the output being unchanged, so this compares the circuits triple by
     * triple — the same canonical N-Triples comparison the cross-engine byte-identity result uses.
     *
     * <p>Both shapes are reachable in the same build ({@code -Dsparqlcirc.perPatternBase=true} restores
     * the per-pattern queries), so this is a real differential and not a re-derivation of one side.
     */
    @Test
    public void onePassBaseRelationsAreByteIdenticalToThePerPatternQueries() {
        String[] queries = {
            // bound chain: every interior pattern needs the restriction, which is the case the pushdown
            // was introduced for
            "SELECT ?v3 WHERE { <urn:s> <urn:e> ?v1 . ?v1 <urn:e> ?v2 . ?v2 <urn:e> ?v3 }",
            // bound star, several patterns sharing one subject
            "SELECT ?a ?b WHERE { ?x <urn:p0> <urn:c0> . ?x <urn:p1> ?a . ?x <urn:p2> ?b }",
            // constant in OBJECT position only, and a projected variable two joins away
            "SELECT ?v2 WHERE { ?v1 <urn:p1> <urn:c0> . ?v1 <urn:e> ?v2 . ?v2 <urn:p2> ?w }",
            // a MINUS whose operands are themselves selective BGPs: the marginals go through the same
            // elimination, so the one-pass base has to be right there too
            "SELECT ?x WHERE { ?x <urn:p0> <urn:c0> . ?x <urn:e> ?y MINUS { ?y <urn:p1> <urn:c1> } }",
        };
        for (String query : queries) {
            Set<String> onePass, perPattern;
            System.clearProperty("sparqlcirc.perPatternBase");
            onePass = canonical(buildOnFixture(query));
            System.setProperty("sparqlcirc.perPatternBase", "true");
            try {
                perPattern = canonical(buildOnFixture(query));
            } finally {
                System.clearProperty("sparqlcirc.perPatternBase");
            }
            assertFalse("the fixture must actually produce a circuit for " + query, onePass.isEmpty());
            assertEquals("one-pass base materialization changed the circuit for " + query,
                    perPattern, onePass);
        }
    }

    /**
     * The oracle over SELECTIVE BGPs. Every shape the generated sweep produces is unbound (its leaves
     * are {@code ?x <urn:pK> ?vK}), so the sweep never reaches the source-restriction pushdown at all,
     * and now never reaches the one-pass base materialization either. These do: each carries a constant,
     * and the multi-pattern ones are exactly the plans that collapse k base passes into one.
     */
    @Test
    public void selectiveBgpsDenoteTheQueryEventInEveryWorld() {
        String[][] facts = {
            {"urn:r:b0", "urn:s",  "urn:e",   "urn:n1"},
            {"urn:r:b1", "urn:n1", "urn:e",   "urn:n2"},
            {"urn:r:b2", "urn:n2", "urn:e",   "urn:n3"},
            {"urn:r:b3", "urn:n1", "urn:tag", "urn:c"},
            {"urn:r:b4", "urn:n2", "urn:tag", "urn:c"},
            {"urn:r:b5", "urn:s",  "urn:q",   "urn:n2"},
        };
        checkAgainstOracle(Arrays.asList(
            new Shape("bound 2-chain", "SELECT ?y WHERE { <urn:s> <urn:e> ?m . ?m <urn:e> ?y }",
                    Arrays.asList("y")),
            new Shape("bound 3-chain, both ends constant",
                    "SELECT ?y WHERE { <urn:s> <urn:e> ?m . ?m <urn:e> ?y . ?y <urn:tag> <urn:c> }",
                    Arrays.asList("y")),
            new Shape("object-position constant only",
                    "SELECT ?m WHERE { ?m <urn:tag> <urn:c> . ?m <urn:e> ?y }", Arrays.asList("m")),
            new Shape("bound star plus chain",
                    "SELECT ?y WHERE { <urn:s> <urn:e> ?m . ?m <urn:e> ?y . ?m <urn:tag> <urn:c> }",
                    Arrays.asList("y")),
            new Shape("selective minuend",
                    "SELECT ?y WHERE { <urn:s> <urn:e> ?m . ?m <urn:e> ?y MINUS { ?y <urn:tag> <urn:c> } }",
                    Arrays.asList("y")),
            new Shape("selective operands of an optional",
                    "SELECT ?y ?t WHERE { <urn:s> <urn:e> ?m . ?m <urn:e> ?y OPTIONAL { ?y <urn:tag> ?t } }",
                    Arrays.asList("y", "t")),
            new Shape("selective union branches",
                    "SELECT ?y WHERE { { <urn:s> <urn:e> ?y } UNION { <urn:s> <urn:q> ?y } }",
                    Arrays.asList("y"))),
            facts, ConstructionMode.values(), Reification.STANDARD);
    }

    /** And it really is one pass: the plan must lose the k-1 redundant base steps. */
    @Test
    public void onePassBaseReplacesTheRedundantBaseSteps() {
        String selective = "SELECT ?v3 WHERE { <urn:s> <urn:e> ?v1 . ?v1 <urn:e> ?v2 . ?v2 <urn:e> ?v3 }";
        assertEquals("three patterns, three restricted base passes before", 3,
                baseSteps(selective, true));
        assertEquals("one afterwards", 1, baseSteps(selective, false));

        // An UNBOUND BGP must keep its per-pattern scans. There the restriction does not apply, so
        // combining them would turn k cheap single-pattern scans INTO a full join for nothing.
        String unbound = "SELECT ?v3 WHERE { ?v0 <urn:e> ?v1 . ?v1 <urn:e> ?v2 . ?v2 <urn:e> ?v3 }";
        assertEquals("an unbound BGP is not selective and keeps one scan per pattern", 3,
                baseSteps(unbound, false));
    }

    private static int baseSteps(String query, boolean perPattern) {
        if (perPattern) System.setProperty("sparqlcirc.perPatternBase", "true");
        try {
            int n = 0;
            for (CircuitConstructionPlan.Step step : new CircuitRewriter(Reification.STANDARD,
                    ConstructionMode.FACTORED, "junit-onepass").constructionPlan(query).steps()) {
                if (step.label() != null && step.label().startsWith("base[")) n++;
            }
            return n;
        } finally {
            System.clearProperty("sparqlcirc.perPatternBase");
        }
    }

    /** A fixture the selective queries above actually match, so the comparison is not of two empties. */
    private static Model buildOnFixture(String query) {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:c1", "urn:s", "urn:e", "urn:n1");
            reify(con, "urn:r:c2", "urn:n1", "urn:e", "urn:n2");
            reify(con, "urn:r:c3", "urn:n2", "urn:e", "urn:n3");
            reify(con, "urn:r:c4", "urn:n1", "urn:e", "urn:n2b");   // a second derivation
            reify(con, "urn:r:c5", "urn:n2b", "urn:e", "urn:n3");
            reify(con, "urn:r:s0", "urn:x", "urn:p0", "urn:c0");
            reify(con, "urn:r:s1", "urn:x", "urn:p1", "urn:av");
            reify(con, "urn:r:s2", "urn:x", "urn:p2", "urn:bv");
            reify(con, "urn:r:s3", "urn:x", "urn:e", "urn:n1");
            reify(con, "urn:r:s4", "urn:n1", "urn:p1", "urn:c1");
            reify(con, "urn:r:s5", "urn:n1", "urn:p2", "urn:wv");
            reify(con, "urn:r:s6", "urn:n2", "urn:p2", "urn:wv");
            reify(con, "urn:r:s7", "urn:n1", "urn:p1", "urn:c0");   // object-position constant match
            Model circuit = new LinkedHashModel();
            CircuitRun.executeConstructionPlan(con, new CircuitRewriter(Reification.STANDARD,
                    ConstructionMode.FACTORED, "junit-onepass").constructionPlan(query),
                    circuit, false);
            return circuit;
        } finally {
            repo.shutDown();
        }
    }

    /** A circuit as its canonical N-Triples line set: the right way to compare two circuits. */
    private static Set<String> canonical(Model model) {
        Set<String> out = new TreeSet<>();
        for (org.eclipse.rdf4j.model.Statement st : model) {
            out.add(NTriplesUtil.toNTriplesString(st.getSubject()) + " "
                  + NTriplesUtil.toNTriplesString(st.getPredicate()) + " "
                  + NTriplesUtil.toNTriplesString(st.getObject()) + " .");
        }
        return out;
    }

    @Test
    public void circuitSerializationIsSortedIndependentlyOfInsertionOrder() {
        ValueFactory vf = SimpleValueFactory.getInstance();
        org.eclipse.rdf4j.model.Statement first = vf.createStatement(
                vf.createIRI("urn:a"), vf.createIRI("urn:p"), vf.createLiteral("1"));
        org.eclipse.rdf4j.model.Statement second = vf.createStatement(
                vf.createIRI("urn:z"), vf.createIRI("urn:p"), vf.createLiteral("2"));
        Model forward = new LinkedHashModel();
        forward.add(first); forward.add(second);
        Model reverse = new LinkedHashModel();
        reverse.add(second); reverse.add(first);
        java.io.ByteArrayOutputStream left = new java.io.ByteArrayOutputStream();
        java.io.ByteArrayOutputStream right = new java.io.ByteArrayOutputStream();
        CircuitRun.writeCircuit(forward, left);
        CircuitRun.writeCircuit(reverse, right);
        assertEquals(left.toString(), right.toString());
        assertTrue(left.toString().indexOf("<urn:a>") < left.toString().indexOf("<urn:z>"));
    }

    // ------------------------------------------------------------------ the emitted plan log
    /**
     * The {@code # --- step N ---} headers are a machine boundary: the paper harnesses split the emitted
     * stderr on them and expect each chunk to start at {@code PREFIX}. So every step must be logged
     * exactly once, in PLAN order, whatever the parallelism.
     *
     * <p>Both halves of that were at risk. Level order is not plan order, so scheduling by DAG would have
     * reordered the log — which is why the default path skips the scheduler entirely — and a concurrent
     * level cannot log from inside its tasks, so the chunks are emitted up front instead.
     */
    @Test
    public void theStepLogStaysCompleteAndInPlanOrderAtAnyParallelism() {
        String query = "SELECT ?x ?b ?c WHERE { ?x <urn:p0> ?a OPTIONAL { ?x <urn:p1> ?b } "
                     + "OPTIONAL { ?x <urn:p2> ?c } }";
        for (ConstructionMode mode : ConstructionMode.values()) {
            for (int parallelism : new int[]{1, 4}) {
                Repository repo = new SailRepository(new MemoryStore());
                java.io.PrintStream saved = System.err;
                java.io.ByteArrayOutputStream captured = new java.io.ByteArrayOutputStream();
                int stepCount;
                try (RepositoryConnection con = repo.getConnection()) {
                    for (String[] fact : FACTS) reify(con, fact[0], fact[1], fact[2], fact[3]);
                    CircuitConstructionPlan plan = new CircuitRewriter(Reification.STANDARD, mode,
                            "junit-log").constructionPlan(query);
                    stepCount = plan.steps().size();
                    System.setErr(new java.io.PrintStream(captured, true, "UTF-8"));
                    try {
                        CircuitRun.executeConstructionPlan(con, repo, plan, new LinkedHashModel(),
                                true, parallelism);
                    } finally {
                        System.setErr(saved);
                    }
                } catch (java.io.UnsupportedEncodingException impossible) {
                    throw new AssertionError(impossible);
                } finally {
                    repo.shutDown();
                }
                List<Integer> headers = new ArrayList<>();
                List<String> firstLines = new ArrayList<>();
                String[] lines = captured.toString().split("\n", -1);
                for (int i = 0; i < lines.length; i++) {
                    if (!lines[i].startsWith("# --- step ")) continue;
                    headers.add(Integer.parseInt(lines[i].replaceAll("\\D+", "")));
                    if (i + 1 < lines.length) firstLines.add(lines[i + 1]);
                }
                String what = mode + " at parallelism " + parallelism;
                List<Integer> expected = new ArrayList<>();
                for (int n = 1; n <= stepCount; n++) expected.add(n);
                assertEquals(what + ": every step must be logged exactly once, in plan order",
                        expected, headers);
                for (String first : firstLines) {
                    assertTrue(what + ": each chunk must start at PREFIX, got: " + first,
                            first.startsWith("PREFIX"));
                }
            }
        }
    }

    // ------------------------------------------------------------------ the four options together
    /**
     * The closing check: every combination of the construction options must denote the same thing.
     *
     * <p>Four switches landed independently, each verified on its own — one-pass base materialization,
     * the restricted subtrahend marginal, step parallelism, and the construction mode. They interact:
     * turning off one-pass base puts k concurrent base WRITERS in level 0 of the DAG, and the restriction
     * changes which relations exist for the scheduler to order. So sweep all 2x2x2x2 of them.
     *
     * <p>Three invariants, at three different strengths, because the switches differ in what they
     * preserve:
     * <ul>
     *   <li><b>one-pass base and parallelism are byte-preserving.</b> Combinations differing only in
     *       those must agree triple for triple.</li>
     *   <li><b>the subtrahend restriction preserves the reachable circuit.</b> It drops gates no answer
     *       can reach, so across it only the sub-circuit reachable from the answer roots must agree —
     *       exactly its safety claim.</li>
     *   <li><b>the construction MODE preserves only the answers.</b> Factored deliberately replaces
     *       one-⊗-per-derivation with base/marginal ⊕ gates, so its internal structure differs and the
     *       reachable circuits are NOT comparable. What must hold is that both modes mint the same
     *       answer gates — θ is plan-independent — and the Boolean function of each is checked against
     *       the oracle elsewhere.</li>
     * </ul>
     */
    @Test
    public void everyCombinationOfTheConstructionOptionsAgrees() {
        String[] queries = {
            "SELECT ?x ?a WHERE { ?x <urn:p0> ?a MINUS { ?x <urn:p1> ?b } }",
            "SELECT ?x ?b WHERE { ?x <urn:p0> ?a OPTIONAL { ?x <urn:p1> ?b } }",
            "SELECT ?x ?b ?c WHERE { ?x <urn:p0> ?a OPTIONAL { ?x <urn:p1> ?b } "
          + "OPTIONAL { ?x <urn:p2> ?c } }",
            "SELECT ?x WHERE { { ?x <urn:p0> ?a MINUS { ?x <urn:p1> ?b } } ?x <urn:p2> ?c }",
            // selective BGP: the one-pass base path
            "SELECT ?y WHERE { <urn:s> <urn:p0> ?y . ?y <urn:p1> ?z }",
            // unbound BGP: per-pattern base scans, so a wide level of concurrent writers
            "SELECT ?x ?a ?b WHERE { ?x <urn:p0> ?a . ?x <urn:p1> ?b . ?x <urn:p2> ?c }",
        };
        String[][] facts = {
            {"urn:r:q0", "urn:s",  "urn:p0", "urn:m"},
            {"urn:r:q1", "urn:m",  "urn:p1", "urn:z"},
            {"urn:r:q2", "urn:s",  "urn:p0", "urn:a"},
            {"urn:r:q3", "urn:s",  "urn:p1", "urn:b"},
            {"urn:r:q4", "urn:s",  "urn:p2", "urn:c"},
            {"urn:r:q5", "urn:s2", "urn:p0", "urn:d"},
        };
        for (String query : queries) {
            // key = "<mode>|<restricted>" -> canonical circuit; every byte-preserving switch must land
            // on the same value, and the two restriction groups must agree on the reachable part.
            Map<String, Set<String>> whole = new LinkedHashMap<>();
            Map<String, Set<String>> reachable = new LinkedHashMap<>();
            Map<ConstructionMode, Set<Resource>> answers = new LinkedHashMap<>();
            for (ConstructionMode mode : ConstructionMode.values()) {
                for (boolean onePass : new boolean[]{true, false}) {
                    for (boolean restrict : new boolean[]{true, false}) {
                        for (int parallelism : new int[]{1, 4}) {
                            Model circuit = buildWithOptions(facts, query, mode, onePass, restrict,
                                    parallelism);
                            String what = query + " [" + mode + ", onePass=" + onePass + ", restrict="
                                    + restrict + ", p=" + parallelism + "]";
                            assertFalse("no circuit for " + what, circuit.isEmpty());
                            assertNoDuplicateStatements(circuit, what);

                            String key = mode + "|" + restrict;
                            Set<String> canonical = canonical(circuit);
                            Set<String> seen = whole.get(key);
                            if (seen == null) whole.put(key, canonical);
                            else assertEquals("one-pass base and parallelism must be byte-preserving: "
                                    + what, seen, canonical);

                            // Within one mode, the reachable circuit is invariant under all three
                            // switches -- including the restriction, which is its safety claim.
                            Set<String> reach = canonical(reachableSubcircuit(circuit));
                            Set<String> seenReach = reachable.get(mode.toString());
                            if (seenReach == null) reachable.put(mode.toString(), reach);
                            else assertEquals("the reachable circuit must not depend on the plan or on "
                                    + "the subtrahend restriction: " + what, seenReach, reach);

                            Set<Resource> roots = answerRoots(circuit);
                            Set<Resource> seenRoots = answers.get(mode);
                            if (seenRoots == null) answers.put(mode, roots);
                            else assertEquals("the answer gates must not depend on any switch: " + what,
                                    seenRoots, roots);
                        }
                    }
                }
            }
            assertEquals("both modes must have been built", 2, answers.size());
            assertEquals("flat and factored are two plans for ONE query, so they must mint the same "
                    + "answer gates for " + query,
                    answers.get(ConstructionMode.FLAT), answers.get(ConstructionMode.FACTORED));
        }
    }

    private static Model buildWithOptions(String[][] facts, String query, ConstructionMode mode,
                                          boolean onePassBase, boolean restrictSubtrahend,
                                          int parallelism) {
        if (onePassBase) System.clearProperty("sparqlcirc.perPatternBase");
        else System.setProperty("sparqlcirc.perPatternBase", "true");
        if (restrictSubtrahend) System.clearProperty("sparqlcirc.unrestrictedSubtrahendMarginal");
        else System.setProperty("sparqlcirc.unrestrictedSubtrahendMarginal", "true");
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            for (String[] fact : facts) reify(con, fact[0], fact[1], fact[2], fact[3]);
            Model circuit = new LinkedHashModel();
            CircuitRun.executeConstructionPlan(con, repo, new CircuitRewriter(Reification.STANDARD,
                    mode, "junit-combo").constructionPlan(query), circuit, false, parallelism);
            return circuit;
        } finally {
            repo.shutDown();
            System.clearProperty("sparqlcirc.perPatternBase");
            System.clearProperty("sparqlcirc.unrestrictedSubtrahendMarginal");
        }
    }

    /**
     * For a step that declares its dependencies, {@code feedback()} and a non-empty {@code writes()} must
     * say the same thing. They are two descriptions of "this step publishes rows", and the scheduler
     * trusts the second while {@link CircuitRun} and {@code requiresFeedback()} trust the first. A step
     * claiming feedback but declaring no write would be ordered as if it wrote nothing, so a later reader
     * of that relation would not wait for it.
     */
    @Test
    public void feedbackAndDeclaredWritesAgree() {
        List<Shape> shapes = new ArrayList<>();
        for (int n = 1; n <= 2; n++) shapes.addAll(sweep(n, false));
        shapes.add(new Shape("selective", "SELECT ?y WHERE { <urn:s> <urn:e> ?m . ?m <urn:e> ?y }",
                Arrays.asList("y")));
        shapes.add(new Shape("path", "SELECT ?y ?z WHERE { <urn:a> <urn:p>+ ?y . ?y <urn:q> ?z }",
                Arrays.asList("y", "z")));
        int checked = 0;
        for (Shape shape : shapes) {
            for (ConstructionMode mode : ConstructionMode.values()) {
                for (CircuitConstructionPlan.Step step : new CircuitRewriter(Reification.STANDARD, mode,
                        "junit-flags").constructionPlan(shape.query).steps()) {
                    if (!step.dependenciesDeclared()) continue;
                    checked++;
                    assertEquals("step '" + step.label() + "' of " + shape.query + " (" + mode
                            + "): feedback=" + step.feedback() + " but writes=" + step.writes(),
                            step.feedback(), !step.writes().isEmpty());
                }
            }
        }
        assertTrue("the sweep must contain declared steps", checked > 100);
    }

    // ------------------------------------------------------------------ declared step dependencies
    /**
     * Every step's declared {@code reads}/{@code writes} must match what its SPARQL actually touches.
     *
     * <p>This is the safety net for scheduling a factored plan. The declaration is hand-maintained at a
     * dozen call sites across two rewriters, and if one of them under-reports a read, the scheduler is
     * free to start that step before the pass that fills the relation — producing a circuit built from an
     * empty or half-written one, with no error. So the declaration is checked against the query TEXT,
     * which is what the engine executes: a private relation IRI in the CONSTRUCT template is a write, one
     * in the WHERE is a read, and both sets must agree exactly.
     *
     * <p>Run over the whole generated composition sweep in both modes, so it covers the shapes nobody
     * wrote down by hand.
     */
    @Test
    public void everyStepDeclaresExactlyTheRelationsItTouches() {
        java.util.regex.Pattern relation = java.util.regex.Pattern.compile("<(urn:sc:msg:[^>]+)>");
        List<Shape> shapes = new ArrayList<>();
        for (int n = 1; n <= 2; n++) shapes.addAll(sweep(n, false));
        for (int n = 1; n <= 2; n++) shapes.addAll(sweep(n, true));
        shapes.add(new Shape("path operand", "SELECT ?y ?z WHERE { <urn:a> <urn:p>+ ?y . ?y <urn:q> ?z }",
                Arrays.asList("y", "z")));
        shapes.add(new Shape("selective bgp",
                "SELECT ?y WHERE { <urn:s> <urn:e> ?m . ?m <urn:e> ?y }", Arrays.asList("y")));
        int declared = 0, undeclared = 0;
        for (Shape shape : shapes) {
            for (ConstructionMode mode : ConstructionMode.values()) {
                for (CircuitConstructionPlan.Step step : new CircuitRewriter(Reification.STANDARD, mode,
                        "junit-deps").constructionPlan(shape.query).steps()) {
                    if (!step.dependenciesDeclared()) { undeclared++; continue; }
                    declared++;
                    String text = step.query();
                    int split = text.indexOf("\nWHERE {");
                    assertTrue("a declared step must have a template and a WHERE: " + step.label(),
                            split > 0);
                    Set<String> inTemplate = new TreeSet<>(), inWhere = new TreeSet<>();
                    for (java.util.regex.Matcher m = relation.matcher(text.substring(0, split));
                            m.find(); ) inTemplate.add(m.group(1));
                    for (java.util.regex.Matcher m = relation.matcher(text.substring(split));
                            m.find(); ) inWhere.add(m.group(1));
                    assertEquals("step '" + step.label() + "' of " + shape.query + " (" + mode
                            + ") declares the wrong WRITES", inTemplate, new TreeSet<>(step.writes()));
                    assertEquals("step '" + step.label() + "' of " + shape.query + " (" + mode
                            + ") declares the wrong READS", inWhere, new TreeSet<>(step.reads()));
                }
            }
        }
        assertTrue("the sweep must actually contain declared steps", declared > 100);
        assertTrue("and the path fixpoint must stay undeclared, hence a barrier", undeclared > 0);
    }

    // ------------------------------------------------------------------ restricted subtrahend marginal
    /**
     * The subtrahend marginal is semi-joined to the minuend, so it stops materializing the whole right
     * operand. The claim that makes this safe is precise: the triples it no longer emits are exactly the
     * ones NO answer gate can reach.
     *
     * <p>So this asserts two things against a build with the restriction switched off
     * ({@code -Dsparqlcirc.unrestrictedSubtrahendMarginal=true}): the sub-circuit reachable from the
     * answer roots is identical triple for triple, and the total is strictly smaller. The first is the
     * correctness argument; the second proves the optimization actually fires, so the first cannot pass
     * by the two builds being the same thing.
     */
    @Test
    public void restrictingTheSubtrahendDropsOnlyUnreachableGates() {
        // (u1,p1) and (u2,p2) are minuend bindings; of the three subtrahend bindings only (u1,p1) is
        // compatible with one, so (u9,p9) and (u3,p3) contribute ⊕ gates nothing reads.
        String[][] facts = {
            {"urn:r:l1", "urn:u1", "urn:likes", "urn:p1"},
            {"urn:r:l2", "urn:u2", "urn:likes", "urn:p2"},
            {"urn:r:b1", "urn:u1", "urn:buys",  "urn:p1"},
            {"urn:r:b2", "urn:u9", "urn:buys",  "urn:p9"},
            {"urn:r:b3", "urn:u3", "urn:buys",  "urn:p3"},
        };
        String[] queries = {
            "SELECT ?u ?p WHERE { ?u <urn:likes> ?p MINUS { ?u <urn:buys> ?p } }",
            "SELECT ?u ?p ?q WHERE { ?u <urn:likes> ?p OPTIONAL { ?u <urn:buys> ?q } }",
        };
        for (String query : queries) {
            Model restricted, unrestricted;
            System.clearProperty("sparqlcirc.unrestrictedSubtrahendMarginal");
            restricted = buildOn(facts, query, ConstructionMode.FLAT);
            System.setProperty("sparqlcirc.unrestrictedSubtrahendMarginal", "true");
            try {
                unrestricted = buildOn(facts, query, ConstructionMode.FLAT);
            } finally {
                System.clearProperty("sparqlcirc.unrestrictedSubtrahendMarginal");
            }
            assertEquals("the sub-circuit reachable from the answers must not change: " + query,
                    canonical(reachableSubcircuit(unrestricted)),
                    canonical(reachableSubcircuit(restricted)));
            assertTrue("the restriction must actually drop something for " + query + ", but both builds "
                    + "emitted " + restricted.size() + " triples",
                    restricted.size() < unrestricted.size());
        }
    }

    /**
     * The restriction must NOT be applied to domain-disjoint operands. There it is a cross product: it
     * cannot drop a single subtrahend binding, because any of them is compatible with any minuend
     * binding, while making the marginal enumerate |P1|x|P2| solutions to emit the same triples. Timing
     * cannot show this cleanly — a disjoint unguarded difference is already expensive, since
     * {@code subFeeds} cross-products the operands by design — so assert the plan instead: for disjoint
     * operands the emitted plan must be exactly the one the restriction-free build produces.
     */
    @Test
    public void theRestrictionIsSkippedForDisjointOperands() {
        String disjoint = "SELECT ?x ?w WHERE { ?x <urn:p0> ?a OPTIONAL { ?u <urn:p1> ?w } }";
        String sharing  = "SELECT ?x ?a WHERE { ?x <urn:p0> ?a OPTIONAL { ?x <urn:p1> ?a } }";
        assertEquals("disjoint operands must plan identically with and without the restriction",
                planWithRestriction(disjoint, false), planWithRestriction(disjoint, true));
        assertNotEquals("but operands that share a variable must actually be restricted",
                planWithRestriction(sharing, false), planWithRestriction(sharing, true));
    }

    private static List<String> planWithRestriction(String query, boolean restrict) {
        if (restrict) System.clearProperty("sparqlcirc.unrestrictedSubtrahendMarginal");
        else System.setProperty("sparqlcirc.unrestrictedSubtrahendMarginal", "true");
        try {
            return new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT, "junit-guard")
                    .plan(query);
        } finally {
            System.clearProperty("sparqlcirc.unrestrictedSubtrahendMarginal");
        }
    }

    /**
     * Every triple of the circuit reachable from an answer gate, by the same edges {@link #evaluate}
     * follows: a ⊗ reaches its {@code c:in} children, a ⊕ reaches the gates that {@code c:feeds} it, a ⊖
     * reaches its minuend and subtrahend, and an answer reaches its binding nodes.
     */
    private static Model reachableSubcircuit(Model circuit) {
        Set<Resource> reached = new LinkedHashSet<>();
        java.util.Deque<Resource> todo = new java.util.ArrayDeque<>(answerRoots(circuit));
        reached.addAll(todo);
        while (!todo.isEmpty()) {
            Resource node = todo.poll();
            List<Value> next = new ArrayList<>();
            next.addAll(circuit.filter(node, VF.createIRI(C, "in"), null).objects());
            next.addAll(circuit.filter(node, VF.createIRI(C, "minuend"), null).objects());
            next.addAll(circuit.filter(node, VF.createIRI(C, "subtrahend"), null).objects());
            next.addAll(circuit.filter(node, VF.createIRI(C, "binding"), null).objects());
            next.addAll(circuit.filter(null, VF.createIRI(C, "feeds"), node).subjects());
            for (Value candidate : next) {
                if (candidate instanceof Resource && reached.add((Resource) candidate)) {
                    todo.add((Resource) candidate);
                }
            }
        }
        Model out = new LinkedHashModel();
        for (org.eclipse.rdf4j.model.Statement statement : circuit) {
            if (reached.contains(statement.getSubject())) out.add(statement);
        }
        return out;
    }

    private static Model buildOn(String[][] facts, String query, ConstructionMode mode) {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            for (String[] fact : facts) reify(con, fact[0], fact[1], fact[2], fact[3]);
            Model circuit = new LinkedHashModel();
            CircuitRun.executeConstructionPlan(con, new CircuitRewriter(Reification.STANDARD, mode,
                    "junit-restrict").constructionPlan(query), circuit, false);
            return circuit;
        } finally {
            repo.shutDown();
        }
    }

    // ------------------------------------------------------------------ concurrent step execution
    /**
     * Running a plan's independent steps concurrently must produce the SAME circuit, triple for triple.
     *
     * <p>The whole argument is that a step with no feedback writes nothing to the store and needs
     * nothing from its siblings — the ⊕/⊖ gates it references are computed from the binding by BIND, not
     * looked up, so steps that meet at a gate only agree on its name. If that were wrong, a concurrent
     * run would drop or duplicate exactly the cross-step gates, which a triple-level comparison catches
     * and an answer-count check would not.
     *
     * <p>Run with parallelism 1 and 4 over the shapes where the flat plan is all readers (a MINUS is
     * four independent CONSTRUCTs, OPTIONAL five, OPTIONAL-then-MINUS ten), plus shapes with real
     * barriers (a materialized operand, a closure atom) so the barrier handling is exercised too.
     */
    @Test
    public void concurrentStepExecutionIsByteIdenticalToSequential() {
        String[] queries = {
            "SELECT ?x WHERE { { ?x <urn:p0> ?a } UNION { ?x <urn:p1> ?b } UNION { ?x <urn:p2> ?c } }",
            "SELECT ?x WHERE { ?x <urn:p0> ?a MINUS { ?x <urn:p1> ?b } }",
            "SELECT ?x WHERE { ?x <urn:p0> ?a MINUS { { ?x <urn:p1> ?b } UNION { ?x <urn:p2> ?c } } }",
            "SELECT ?x ?b WHERE { ?x <urn:p0> ?a OPTIONAL { ?x <urn:p1> ?b } }",
            "SELECT ?x ?b WHERE { ?x <urn:p0> ?a OPTIONAL { ?x <urn:p1> ?b } MINUS { ?x <urn:p2> ?c } }",
            "SELECT ?x ?b ?c WHERE { ?x <urn:p0> ?a OPTIONAL { ?x <urn:p1> ?b } OPTIONAL { ?x <urn:p2> ?c } }",
            // barriers: a materialized MINUS operand, and a factored plan (nearly all writers)
            "SELECT ?x WHERE { { ?x <urn:p0> ?a MINUS { ?x <urn:p1> ?b } } ?x <urn:p2> ?c }",
            "SELECT ?a WHERE { ?x <urn:p0> ?a . ?x <urn:p1> ?b . ?x <urn:p2> ?c }",
            // A MINUS (P MINUS Q): two marginals derive the SAME ⊗ gate, which is what exposed the
            // Model deduplication defect below.
            "SELECT ?x WHERE { ?x <urn:p0> ?a MINUS { ?y <urn:p0> ?a MINUS { ?a <urn:p1> ?c } } }",
            // Factored plans are nearly all WRITERS, so these are the shapes that exercise concurrent
            // feedback passes rather than concurrent readers. An UNBOUND BGP keeps one base scan per
            // pattern (a wide level 0, since the one-pass collapse only applies to selective BGPs), and
            // two OPTIONALs give the widest level of any shape here.
            "SELECT ?x ?a ?b ?c WHERE { ?x <urn:p0> ?a . ?x <urn:p1> ?b . ?x <urn:p2> ?c }",
            "SELECT ?x ?b ?c WHERE { ?x <urn:p0> ?a OPTIONAL { ?x <urn:p1> ?b } "
          + "OPTIONAL { ?x <urn:p2> ?c } }",
        };
        int concurrentlyScheduled = 0;
        for (String query : queries) {
            for (ConstructionMode mode : ConstructionMode.values()) {
                Model sequentialModel = buildConcurrently(query, mode, 1);
                Set<String> sequential = canonical(sequentialModel);
                assertFalse(mode + ": the fixture must produce a circuit for " + query,
                        sequential.isEmpty());
                assertNoDuplicateStatements(sequentialModel, query + " (" + mode + ", sequential)");
                for (int parallelism : new int[]{2, 4, 8}) {
                    Model parallelModel = buildConcurrently(query, mode, parallelism);
                    assertEquals(mode + " at parallelism " + parallelism + " changed the circuit for "
                            + query, sequential, canonical(parallelModel));
                    assertNoDuplicateStatements(parallelModel,
                            query + " (" + mode + ", parallelism " + parallelism + ")");
                }
                if (widestDagLevel(query, mode) > 1) concurrentlyScheduled++;
            }
        }
        // Comparing two SEQUENTIAL runs would pass trivially, so require that the concurrent branch was
        // actually taken for most of the matrix: a plan whose reader runs are all width 1 never enters it.
        assertTrue("only " + concurrentlyScheduled + " of " + (queries.length * 2) + " plans had a DAG "
                + "level wide enough to schedule concurrently, so this is mostly comparing sequential "
                + "runs with each other", concurrentlyScheduled >= queries.length);
    }

    /**
     * The widest level of the plan's dependency DAG: the most steps the scheduler can overlap.
     *
     * <p>Derived here from the PUBLIC {@code reads}/{@code writes}/{@code dependenciesDeclared} contract
     * rather than by calling the scheduler, so this measures what the plan makes available and the
     * byte-identity assertion measures whether the scheduler used it correctly. An undeclared step is a
     * barrier in both directions, which is what keeps a closure atom's fixpoint alone in its level.
     */
    private static int widestDagLevel(String query, ConstructionMode mode) {
        List<CircuitConstructionPlan.Step> steps = new CircuitRewriter(Reification.STANDARD, mode,
                "junit-width").constructionPlan(query).steps();
        int[] level = new int[steps.size()];
        int afterLastBarrier = 0;
        for (int j = 0; j < steps.size(); j++) {
            CircuitConstructionPlan.Step step = steps.get(j);
            int earliest = afterLastBarrier;
            for (int i = 0; i < j; i++) {
                boolean conflicts = !step.dependenciesDeclared()
                        || !java.util.Collections.disjoint(step.reads(), steps.get(i).writes())
                        || !java.util.Collections.disjoint(step.writes(), steps.get(i).writes());
                if (conflicts) earliest = Math.max(earliest, level[i] + 1);
            }
            level[j] = earliest;
            if (!step.dependenciesDeclared()) afterLastBarrier = earliest + 1;
        }
        Map<Integer, Integer> perLevel = new HashMap<>();
        int widest = 0;
        for (int value : level) {
            int n = perLevel.merge(value, 1, Integer::sum);
            widest = Math.max(widest, n);
        }
        return widest;
    }

    /**
     * A closure atom's iterative fixpoint is a barrier, and its private rows have to be fed back and
     * cleaned up exactly as in the sequential run. Kept separate because it needs the path machinery.
     */
    @Test
    public void aConcurrentRunStillDrivesAndCleansUpAClosureAtom() {
        String query = "SELECT ?y ?z WHERE { <urn:n0> <urn:p>+ ?y . ?y <urn:q> ?z }";
        Set<String> sequential = null;
        for (int parallelism : new int[]{1, 4}) {
            Repository repo = new SailRepository(new MemoryStore());
            try (RepositoryConnection con = repo.getConnection()) {
                reify(con, "urn:r:e0", "urn:n0", "urn:p", "urn:n1");
                reify(con, "urn:r:e1", "urn:n1", "urn:p", "urn:n2");
                reify(con, "urn:r:q0", "urn:n2", "urn:q", "urn:z");
                Model circuit = new LinkedHashModel();
                CircuitRun.executeConstructionPlan(con, repo,
                        new CircuitRewriter(Reification.STANDARD, ConstructionMode.FLAT, "junit-par-path")
                                .constructionPlan(query), circuit, false, parallelism);
                Set<String> canonical = canonical(circuit);
                if (sequential == null) {
                    sequential = canonical;
                    assertFalse("the path fixpoint must produce a circuit", sequential.isEmpty());
                } else {
                    assertEquals("a concurrent run changed the path circuit", sequential, canonical);
                }
                for (org.eclipse.rdf4j.model.Statement st : con.getStatements(null, null, null)
                        .stream().collect(java.util.stream.Collectors.toList())) {
                    assertFalse("the atom's private rows must be cleaned up at parallelism " + parallelism,
                            st.getPredicate().stringValue().startsWith("urn:sc:"));
                }
            } finally {
                repo.shutDown();
            }
        }
    }

    /**
     * A factored plan is scheduled by its real dependency DAG, so a step whose dependencies are NOT
     * declared has to be a barrier in BOTH directions — otherwise the scheduler would happily overlap a
     * closure atom's fixpoint, which drives its own loop on the caller's connection, with something else.
     *
     * <p>Checked by planting an undeclared step in the middle of a plan that would otherwise have a wide
     * level, and requiring the DAG to serialize completely around it.
     */
    @Test
    public void anUndeclaredStepIsABarrierInBothDirections() {
        List<CircuitConstructionPlan.Step> steps = new ArrayList<>();
        Set<String> relation = java.util.Collections.singleton("urn:sc:msg:r1");
        steps.add(new CircuitConstructionPlan.Step("A", true, "writer-a",
                java.util.Collections.emptySet(), relation));
        steps.add(new CircuitConstructionPlan.Step("B", true, "writer-b",
                java.util.Collections.emptySet(), java.util.Collections.singleton("urn:sc:msg:r2")));
        steps.add(new CircuitConstructionPlan.Step("C", true, "undeclared"));   // 3-arg = undeclared
        steps.add(new CircuitConstructionPlan.Step("D", false, "reader-d",
                java.util.Collections.emptySet(), java.util.Collections.emptySet()));
        steps.add(new CircuitConstructionPlan.Step("E", false, "reader-e",
                relation, java.util.Collections.emptySet()));

        assertFalse("the 3-argument constructor must leave dependencies undeclared",
                steps.get(2).dependenciesDeclared());
        assertTrue("and the declaring constructor must mark them declared",
                steps.get(0).dependenciesDeclared());

        int[] level = levelsOf(steps);
        assertEquals("the two independent writers share level 0", level[0], level[1]);
        assertTrue("the undeclared step must come after both", level[2] > level[1]);
        assertTrue("and everything after it must come after it, even a step that depends on nothing",
                level[3] > level[2] && level[4] > level[2]);
        assertEquals("the two steps after the barrier are independent of each other", level[3], level[4]);
    }

    /** The level assignment, from the public reads/writes/dependenciesDeclared contract. */
    private static int[] levelsOf(List<CircuitConstructionPlan.Step> steps) {
        int[] level = new int[steps.size()];
        int afterLastBarrier = 0;
        for (int j = 0; j < steps.size(); j++) {
            CircuitConstructionPlan.Step step = steps.get(j);
            int earliest = afterLastBarrier;
            for (int i = 0; i < j; i++) {
                boolean conflicts = !step.dependenciesDeclared()
                        || !java.util.Collections.disjoint(step.reads(), steps.get(i).writes())
                        || !java.util.Collections.disjoint(step.writes(), steps.get(i).writes());
                if (conflicts) earliest = Math.max(earliest, level[i] + 1);
            }
            level[j] = earliest;
            if (!step.dependenciesDeclared()) afterLastBarrier = earliest + 1;
        }
        return level;
    }

    /**
     * The concurrent schedule is sound because "declares no feedback" implies "writes nothing". That is
     * now enforced rather than assumed: a step that emits private rows without declaring feedback used to
     * have them dropped silently, which would be a wrong circuit with no diagnostic.
     */
    @Test
    public void aStepThatWritesRowsWithoutDeclaringFeedbackIsRefused() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:t0", "urn:s", "urn:p0", "urn:a");
            // A hand-built step that emits a urn:sc: row but claims feedback=false.
            CircuitConstructionPlan.Step liar = new CircuitConstructionPlan.Step(
                    "CONSTRUCT { <urn:row> <urn:sc:message> <urn:sc:msg:fake> } "
                  + "WHERE { ?t <http://www.w3.org/1999/02/22-rdf-syntax-ns#subject> ?s }",
                    false, "mislabelled");
            CircuitConstructionPlan plan = new CircuitConstructionPlan(
                    java.util.Collections.singletonList(liar), ConstructionMode.FLAT,
                    ConstructionMode.FLAT, null);
            try {
                CircuitRun.executeConstructionPlan(con, plan, new LinkedHashModel(), false);
                fail("a step emitting private rows without declaring feedback must be refused");
            } catch (IllegalStateException expected) {
                assertTrue(expected.getMessage(), expected.getMessage().contains("declares no feedback"));
            }
        } finally {
            repo.shutDown();
        }
    }

    /**
     * The accumulated circuit must be a true SET of triples.
     *
     * <p>It was not, and comparing canonical triple TEXT cannot see it: a {@code TreeSet<String>} dedups
     * exactly the duplicates that are the defect. A CONSTRUCT result mixes store-owned terms
     * ({@code MemIRI}) with query-minted ones ({@code SimpleIRI}); {@code LinkedHashModel}'s indexed
     * {@code contains} then misses an RDF-equal statement and stores it twice, so {@code Rio.write}
     * emits a duplicate line. Sequentially that was a rare, timing-dependent flake; running steps
     * concurrently made it about one run in two on {@code A MINUS (P MINUS Q)}, where two marginals
     * derive the same ⊗ gate. Compare the model's own size against its canonical line count.
     */
    private static void assertNoDuplicateStatements(Model circuit, String what) {
        assertEquals("the accumulated circuit carries RDF-duplicate statements, so the emitted "
                + "N-Triples has duplicate lines: " + what, canonical(circuit).size(), circuit.size());
        // The invariant above is the thing that matters but it only fires when the race is lost, which
        // is rare in a small fixture. This is the deterministic half: no term in the circuit may be one
        // of the STORE's own objects. That is exactly what makes the model dedup by value, so a circuit
        // holding a MemIRI is one bad interleaving away from emitting a duplicate line.
        for (org.eclipse.rdf4j.model.Statement statement : circuit) {
            for (Value term : new Value[]{statement.getSubject(), statement.getPredicate(),
                                          statement.getObject()}) {
                assertFalse("the circuit must hold terms detached from the store, but " + term
                        + " is a " + term.getClass().getName() + " in " + what,
                        term.getClass().getName().startsWith("org.eclipse.rdf4j.sail."));
            }
        }
    }

    private static Model buildConcurrently(String query, ConstructionMode mode, int parallelism) {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            for (String[] fact : FACTS) reify(con, fact[0], fact[1], fact[2], fact[3]);
            Model circuit = new LinkedHashModel();
            CircuitRun.executeConstructionPlan(con, repo, new CircuitRewriter(Reification.STANDARD,
                    mode, "junit-par").constructionPlan(query), circuit, false, parallelism);
            return circuit;
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
                                        Arrays.asList("x", "y")),
            // Two atoms over the SAME sub-path from DIFFERENT sources: the common-ancestor shape. The
            // per-path fingerprint erases the endpoints on purpose, so naming the private row relation
            // after it made both atoms publish into one relation under one row IRI, and each operand
            // read the other's rows -- the join denoted a union and invented answers neither source
            // reaches. Here the answer is the INTERSECTION of the two reachable sets.
            new Shape("two sources, one target",
                                        "SELECT ?y WHERE { <urn:n0> <urn:p>+ ?y . <urn:n1> <urn:p>+ ?y }",
                                        Arrays.asList("y")));
        for (Reification scheme : new Reification[]{Reification.STANDARD, Reification.SPARQL_STAR}) {
            checkAgainstOracle(shapes, facts, ConstructionMode.values(), scheme);
        }
    }

    /**
     * The identity a query mints must be a function of the QUERY, not of the parse.
     *
     * <p>RDF4J names a query blank node — and the intermediate node of a sequence path — {@code
     * _anon_<random uuid>}. Those names used to reach {@code varSemanticKey} (hence θ and every
     * answer-gate IRI) and, through {@code reify}, a compound closure atom's per-path fingerprint
     * (hence every reach/base gate), so the same query text built a DIFFERENT circuit on every run:
     * no gate reuse on a shared endpoint, no idempotent re-run, and the cross-engine byte-identity
     * claim held only for queries with no anonymous variable in them.
     */
    @Test
    public void theSameQueryTextAlwaysMintsTheSameCircuitIdentity() {
        String[] queries = {
            "SELECT ?y WHERE { <urn:a> <urn:p> [ <urn:q> ?y ] }",                     // parser blank node
            "SELECT ?y WHERE { <urn:a> (<urn:p>/<urn:q>)+ ?y }",                      // sequence path
            "SELECT ?y ?z WHERE { <urn:a> (<urn:p>/<urn:q>)+ ?y . ?y <urn:r> ?z }",   // ... as an operand
        };
        for (String query : queries) {
            for (ConstructionMode mode : ConstructionMode.values()) {
                assertEquals(mode + ": " + query + " must plan identically on every parse",
                        planText(query, mode), planText(query, mode));
            }
        }
        String compound = "SELECT ?y WHERE { <urn:a> (<urn:p>/<urn:q>)+ ?y }";
        assertEquals("a compound closure atom's fingerprint must not depend on the parse",
                new CircuitRewriter(Reification.STANDARD).pathQuery(compound).fingerprint(),
                new CircuitRewriter(Reification.STANDARD).pathQuery(compound).fingerprint());
    }

    /** A plan's full emitted text, with each closure atom represented by its fingerprint. */
    private static String planText(String query, ConstructionMode mode) {
        StringBuilder out = new StringBuilder();
        for (CircuitConstructionPlan.Step step : new CircuitRewriter(Reification.STANDARD, mode,
                "junit-identity").constructionPlan(query).steps()) {
            out.append(step.query() == null ? "path:" + step.path().fingerprint() : step.query())
               .append('\n');
        }
        return out.toString();
    }

    /** RDF4J's parser-generated set wrappers around {@code :p?} remain transparent in every operand. */
    @Test
    public void optionalPathAtomsComposeWithEverySupportedOperator() {
        String[][] facts = {
            {"urn:r:e0", "urn:n0", "urn:p", "urn:n1"},
            {"urn:r:q0", "urn:n0", "urn:q", "urn:z"},
            {"urn:r:q1", "urn:n2", "urn:q", "urn:z"},
            {"urn:r:r0", "urn:n0", "urn:r", "urn:w"},
        };
        List<Shape> shapes = Arrays.asList(
            new Shape("p? in join",
                    "SELECT ?x ?y WHERE { ?x <urn:q> ?z . ?x <urn:p>? ?y }",
                    Arrays.asList("x", "y")),
            new Shape("p? under filter",
                    "SELECT ?x ?y WHERE { ?x <urn:q> ?z . ?x <urn:p>? ?y FILTER(?x = ?y) }",
                    Arrays.asList("x", "y")),
            new Shape("p? in optional",
                    "SELECT ?x ?y WHERE { ?x <urn:q> ?z OPTIONAL { ?x <urn:p>? ?y } }",
                    Arrays.asList("x", "y")),
            new Shape("p? in minus",
                    "SELECT ?x WHERE { ?x <urn:q> ?z MINUS { ?x <urn:p>? ?y . "
                  + "?y <urn:r> <urn:w> } }",
                    Arrays.asList("x")));
        for (Reification scheme : new Reification[]{Reification.STANDARD, Reification.SPARQL_STAR}) {
            checkAgainstOracle(shapes, facts, ConstructionMode.values(), scheme);
        }
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
        return CircuitTestSupport.bindingStrings(circuit, root);
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
        return CircuitTestSupport.answerRoots(model);
    }

    private static Set<Resource> gatesOfType(Model model, String type) {
        return CircuitTestSupport.gatesOfType(model, type);
    }

    /** Eq. (1)-(3): ⊗ = ∧ of children, ⊕ = ∨ of feeders, ⊖(C,d) = (∨C) ∧ ¬d, leaf = token ∈ world. */
    private static boolean evaluate(Model model, Resource node, Set<String> world,
                                    Map<Resource, Boolean> memo) {
        return CircuitTestSupport.evaluate(model, node, world, memo);
    }

    private static void reify(RepositoryConnection con, String token, String s, String p, String o) {
        ValueFactory vf = con.getValueFactory();
        IRI t = vf.createIRI(token);
        con.add(t, vf.createIRI(RDF.NAMESPACE, "subject"), vf.createIRI(s));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "predicate"), vf.createIRI(p));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "object"), vf.createIRI(o));
    }

}
