package npcs.circuit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
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
import org.eclipse.rdf4j.query.parser.sparql.SPARQLParser;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.sail.memory.MemoryStore;
import org.junit.Test;

import npcs.rewrite.Reification;

/**
 * FILTER regressions for the circuit rewriting (Def. 4.5, clause 6: {@code γ(σ_φ(P)) = γ(P)} and
 * {@code g_{σ_φ(P)} = g_P}). Two properties are asserted throughout:
 * <ul>
 *   <li><b>Restriction.</b> The circuit contains exactly the answers the filtered query has — the
 *       condition really reaches the engine, it is not dropped.</li>
 *   <li><b>Gate identity.</b> A filter builds no gate and renames none, so a filtered BGP's circuit
 *       is a sub-circuit of the unfiltered one, gate IRI for gate IRI.</li>
 * </ul>
 * Offline: an in-memory store, no endpoint, files, or network.
 */
public class CircuitFilterTest {

    private static final String C = "urn:circuit:";

    @Test
    public void filterInBgpRestrictsAnswersAndPreservesGateIdentity() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:a", "urn:s", "urn:p", "urn:a");
            reify(con, "urn:r:b", "urn:s", "urn:p", "urn:b");
            reify(con, "urn:r:c", "urn:s", "urn:p", "urn:c");

            Model unfiltered = executePlan(con, "SELECT ?y WHERE { <urn:s> <urn:p> ?y }");
            Model filtered = executePlan(con,
                    "SELECT ?y WHERE { <urn:s> <urn:p> ?y FILTER(?y != <urn:b>) }");

            assertEquals("the unfiltered query has all three answers", 3, answerRoots(unfiltered).size());
            assertEquals("FILTER must remove exactly the excluded answer", 2, answerRoots(filtered).size());
            assertFalse("the excluded token must not appear as a leaf",
                    leaves(filtered).contains("urn:r:b"));
            assertTrue("the surviving tokens must still appear", leaves(filtered).contains("urn:r:a"));

            // Def. 4.5 clause 6: no gate is created or renamed by a filter. Answer-gate identity is
            // plan-independent, so it holds across the default (factored) route too.
            assertTrue("a filtered BGP's answer gates must be a subset of the unfiltered ones, byte for byte",
                    answerRoots(unfiltered).containsAll(answerRoots(filtered)));

            // The full sub-circuit property is a statement about ONE plan: compare flat with flat
            // (the unfiltered query would otherwise take the factored route, whose internal gates
            // legitimately differ while the answer gates agree).
            Model flatUnfiltered = executePlan(con,
                    "SELECT ?y WHERE { <urn:s> <urn:p> ?y }", ConstructionMode.FLAT);
            Model flatFiltered = executePlan(con,
                    "SELECT ?y WHERE { <urn:s> <urn:p> ?y FILTER(?y != <urn:b>) }", ConstructionMode.FLAT);
            assertTrue("the filtered circuit must be a sub-circuit of the unfiltered one",
                    flatUnfiltered.containsAll(flatFiltered));
            assertFalse("and a strict one, since one answer is gone",
                    flatFiltered.containsAll(flatUnfiltered));
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void filterSupportsLiteralAndArithmeticConditions() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reifyLiteral(con, "urn:r:young", "urn:s", "urn:age", 20);
            reifyLiteral(con, "urn:r:old", "urn:s", "urn:age", 40);

            Model gt = executePlan(con, "SELECT ?a WHERE { <urn:s> <urn:age> ?a FILTER(?a > 30) }");
            assertEquals(1, answerRoots(gt).size());
            assertTrue(leaves(gt).contains("urn:r:old"));

            Model arithmetic = executePlan(con,
                    "SELECT ?a WHERE { <urn:s> <urn:age> ?a FILTER(?a + 5 < 30) }");
            assertEquals(1, answerRoots(arithmetic).size());
            assertTrue(leaves(arithmetic).contains("urn:r:young"));

            Model conjunction = executePlan(con,
                    "SELECT ?a WHERE { <urn:s> <urn:age> ?a FILTER(?a > 10 && !(?a > 30)) }");
            assertEquals(1, answerRoots(conjunction).size());
            assertTrue(leaves(conjunction).contains("urn:r:young"));

            Model builtins = executePlan(con,
                    "SELECT ?a WHERE { <urn:s> <urn:age> ?a FILTER(isLiteral(?a) && BOUND(?a)) }");
            assertEquals(2, answerRoots(builtins).size());
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void filterAppliesPerUnionBranch() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:p1", "urn:s", "urn:p", "urn:keep");
            reify(con, "urn:r:p2", "urn:s", "urn:p", "urn:drop");
            reify(con, "urn:r:q1", "urn:s", "urn:q", "urn:keep");
            reify(con, "urn:r:q2", "urn:s", "urn:q", "urn:drop");

            // Each branch filters a different value, so exactly one binding survives per branch and
            // both branches feed the shared answer gate of ?y=keep / ?y=drop respectively.
            Model circuit = executePlan(con, "SELECT ?y WHERE { "
                    + "{ <urn:s> <urn:p> ?y FILTER(?y != <urn:drop>) } UNION "
                    + "{ <urn:s> <urn:q> ?y FILTER(?y != <urn:keep>) } }");
            assertEquals(2, answerRoots(circuit).size());
            Set<String> leaves = leaves(circuit);
            assertTrue(leaves.contains("urn:r:p1"));
            assertTrue(leaves.contains("urn:r:q2"));
            assertFalse("the p-branch filter must drop its own match only", leaves.contains("urn:r:p2"));
            assertFalse("the q-branch filter must drop its own match only", leaves.contains("urn:r:q1"));
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void filterAppliesToBothMinusOperands() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:a1", "urn:x1", "urn:a", "urn:v");
            reify(con, "urn:r:a2", "urn:x2", "urn:a", "urn:v");
            reify(con, "urn:r:p1", "urn:x1", "urn:p", "urn:hit");
            reify(con, "urn:r:p2", "urn:x2", "urn:p", "urn:miss");

            // Subtrahend filtered to <urn:miss>: only x2 is removed, so x1 survives.
            Model circuit = executePlan(con, "SELECT ?x WHERE { ?x <urn:a> <urn:v> "
                    + "MINUS { ?x <urn:p> ?o FILTER(?o = <urn:miss>) } }");
            assertEquals("both minuend answers keep a root", 2, answerRoots(circuit).size());
            assertTrue("the filtered subtrahend must still be fed for x2",
                    leaves(circuit).contains("urn:r:p2"));
            assertFalse("the filtered-out subtrahend match must not reach the circuit",
                    leaves(circuit).contains("urn:r:p1"));

            // Minuend filtered: the removed minuend answer must have no root at all.
            Model minuendFiltered = executePlan(con, "SELECT ?x WHERE { "
                    + "?x <urn:a> <urn:v> FILTER(?x = <urn:x1>) MINUS { ?x <urn:p> <urn:none> } }");
            assertEquals(1, answerRoots(minuendFiltered).size());
            assertTrue(leaves(minuendFiltered).contains("urn:r:a1"));
            assertFalse(leaves(minuendFiltered).contains("urn:r:a2"));
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void minusOperandsDifferingOnlyByAFilterDoNotHashCons() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:a", "urn:x", "urn:a", "urn:v");
            reify(con, "urn:r:p", "urn:x", "urn:p", "urn:hit");

            // Same patterns on both sides, different conditions: the two DIFF operators denote
            // different relations, so their ⊖/subtrahend gates must stay distinct.
            Model circuit = executePlan(con, "SELECT ?x WHERE { "
                    + "{ ?x <urn:a> <urn:v> MINUS { ?x <urn:p> ?o FILTER(?o = <urn:hit>) } } UNION "
                    + "{ ?x <urn:a> <urn:v> MINUS { ?x <urn:p> ?o FILTER(?o = <urn:other>) } } }");
            assertEquals("filters must enter the operand fingerprint", 2, minusRoots(circuit).size());
            assertEquals("each filtered DIFF needs its own subtrahend", 2, subtrahends(circuit).size());

            // ... while identical conditions must still hash-cons, as unfiltered operands do.
            Model shared = executePlan(con, "SELECT ?x WHERE { "
                    + "{ ?x <urn:a> <urn:v> MINUS { ?x <urn:p> ?o FILTER(?o = <urn:hit>) } } UNION "
                    + "{ ?x <urn:a> <urn:v> MINUS { ?x <urn:p> ?o FILTER(?o = <urn:hit>) } } }");
            assertEquals("equal conditions must still collapse to one gate", 1, minusRoots(shared).size());
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void filterAppliesInsideOptionalOperands() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:a", "urn:x", "urn:a", "urn:v");
            reify(con, "urn:r:b", "urn:x", "urn:b", "urn:skip");

            // The OPTIONAL's only match is filtered away, so the negative (⊖) branch must carry ?x.
            Model circuit = executePlan(con, "SELECT ?x ?y WHERE { ?x <urn:a> <urn:v> "
                    + "OPTIONAL { ?x <urn:b> ?y FILTER(?y != <urn:skip>) } }");
            assertEquals("the unmatched OPTIONAL keeps one answer", 1, answerRoots(circuit).size());
            assertEquals("the negative branch must be present", 1, minusRoots(circuit).size());
            assertFalse("the filtered-out OPTIONAL match must not appear",
                    leaves(circuit).contains("urn:r:b"));
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void filteredBgpFallsBackToFlatWithTheSameAnswerGates() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:1", "urn:s", "urn:p", "urn:mid");
            reify(con, "urn:r:2", "urn:mid", "urn:q", "urn:keep");
            reify(con, "urn:r:3", "urn:mid", "urn:q", "urn:drop");

            String query = "SELECT ?z WHERE { <urn:s> <urn:p> ?m . ?m <urn:q> ?z "
                    + "FILTER(?z != <urn:drop>) }";
            CircuitConstructionPlan factored = new CircuitRewriter(
                    Reification.STANDARD, ConstructionMode.FACTORED, "junit-filter-factored")
                    .constructionPlan(query);
            assertEquals("a filtered BGP must not take the factored multi-pass route",
                    ConstructionMode.FLAT, factored.effectiveMode());
            for (String construct : factored.queries()) {
                new SPARQLParser().parseQuery(construct, null);   // the rendered FILTER must parse
            }

            Model viaFactored = executePlan(con, query, ConstructionMode.FACTORED);
            Model viaFlat = executePlan(con, query, ConstructionMode.FLAT);
            assertEquals("both modes must emit the same circuit for a filtered BGP", viaFlat, viaFactored);
            assertEquals(1, answerRoots(viaFlat).size());
            assertFalse(leaves(viaFlat).contains("urn:r:3"));
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void everyGeneratedConstructWithAFilterParses() {
        String[] queries = {
            "SELECT ?y WHERE { <urn:s> <urn:p> ?y FILTER(?y != <urn:b>) }",
            "SELECT ?y WHERE { <urn:s> <urn:p> ?y FILTER(REGEX(STR(?y), \"^urn\", \"i\")) }",
            "SELECT ?y WHERE { <urn:s> <urn:p> ?y FILTER(sameTerm(?y, <urn:b>) || isIRI(?y)) }",
            "SELECT ?y WHERE { <urn:s> <urn:p> ?y FILTER(DATATYPE(?y) != <urn:d>) }",
            "SELECT ?x WHERE { ?x <urn:a> <urn:v> MINUS { ?x <urn:p> ?o FILTER(?o = <urn:hit>) } }",
            "SELECT ?x ?y WHERE { ?x <urn:a> <urn:v> OPTIONAL { ?x <urn:b> ?y FILTER(?y != <urn:s>) } }",
        };
        for (String query : queries) {
            for (ConstructionMode mode : ConstructionMode.values()) {
                CircuitRewriter rw = new CircuitRewriter(Reification.STANDARD, mode, "junit-parse");
                List<String> plan = rw.plan(query);
                for (String construct : plan) new SPARQLParser().parseQuery(construct, null);
                assertEquals("plan generation must be deterministic", plan, rw.plan(query));
            }
        }
    }

    @Test
    public void filterConditionsOutsideTheRenderableSubsetAreRejected() {
        // EXISTS carries a pattern, hence provenance, of its own.
        assertRejected("SELECT ?x WHERE { ?x <urn:a> <urn:v> FILTER EXISTS { ?x <urn:p> ?o } }",
                "Unsupported FILTER");
        assertRejected("SELECT ?x WHERE { ?x <urn:a> <urn:v> FILTER NOT EXISTS { ?x <urn:p> ?o } }",
                "Unsupported FILTER");
        // An extension function we cannot reproduce must never be silently dropped.
        assertRejected("SELECT ?x WHERE { ?x <urn:a> ?v FILTER(<urn:fn:custom>(?v)) }",
                "Unsupported FILTER");
    }

    @Test
    public void filterReferencingAVariableItsOwnGroupDoesNotBindIsRejected() {
        // Inside the nested group ?y is unbound, so the condition is an error there (no match), while
        // hoisting it to the enclosing group would evaluate it on a bound ?y. Refuse rather than change
        // the query's meaning.
        assertRejected("SELECT ?x ?y WHERE { { ?x <urn:a> ?v FILTER(?y = <urn:z>) } ?x <urn:b> ?y }",
                "which its own group does not bind");
    }

    @Test
    public void theFilteredLeftJoinIsStillRejected() {
        // A FILTER whose condition spans both OPTIONAL operands becomes the LeftJoin condition, which
        // the rewriting does not model; that guard must survive filter support.
        assertRejected("SELECT ?x ?y WHERE { ?x <urn:a> ?v OPTIONAL { ?x <urn:b> ?y FILTER(?y != ?v) } }",
                "filtered left join");
    }

    // --------------------------- helpers ---------------------------

    private static Model executePlan(RepositoryConnection con, String query) {
        return executePlan(con, query, ConstructionMode.FACTORED);
    }

    private static Model executePlan(RepositoryConnection con, String query, ConstructionMode mode) {
        Model circuit = new LinkedHashModel();
        CircuitConstructionPlan plan = new CircuitRewriter(
                Reification.STANDARD, mode, "junit-filter-" + mode.cliName()).constructionPlan(query);
        CircuitRun.executeConstructionPlan(con, plan, circuit, false);
        return circuit;
    }

    private static Set<Resource> answerRoots(Model model) {
        IRI answer = SimpleValueFactory.getInstance().createIRI(C, "answer");
        return new LinkedHashSet<>(model.filter(null, answer, null).subjects());
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

    /** The leaves of the circuit: {@code c:in} targets the circuit does not itself type as a gate. */
    private static Set<String> leaves(Model model) {
        IRI in = SimpleValueFactory.getInstance().createIRI(C, "in");
        Set<String> out = new HashSet<>();
        for (Statement st : model.filter(null, in, null)) {
            Value child = st.getObject();
            if (child instanceof Resource && model.filter((Resource) child, RDF.TYPE, null).isEmpty()) {
                out.add(child.stringValue());
            }
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

    private static void reifyLiteral(RepositoryConnection con, String token, String s, String p, int o) {
        ValueFactory vf = con.getValueFactory();
        IRI t = vf.createIRI(token);
        con.add(t, vf.createIRI(RDF.NAMESPACE, "subject"), vf.createIRI(s));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "predicate"), vf.createIRI(p));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "object"), vf.createLiteral(o));
    }

    private static void assertRejected(String query, String messagePart) {
        for (ConstructionMode mode : ConstructionMode.values()) {
            try {
                new CircuitRewriter(Reification.STANDARD, mode, "junit-reject").constructionPlan(query);
                fail("expected rejection in " + mode + " mode: " + query);
            } catch (UnsupportedOperationException expected) {
                assertTrue("unexpected rejection message: " + expected.getMessage(),
                        expected.getMessage().contains(messagePart));
            }
        }
    }
}
