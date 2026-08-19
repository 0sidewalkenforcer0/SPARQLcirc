package npcs.circuit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.util.Collections;
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
    public void compositeFilterConditionsHaveDistinctAnswerRoots() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:bob", "urn:s", "urn:p", "urn:bob");
            reify(con, "urn:r:carol", "urn:s", "urn:q", "urn:carol");
            String prefix = "SELECT ?x WHERE { { { ?x <urn:p> ?who } UNION "
                    + "{ ?x <urn:q> ?who } } FILTER(?who = ";
            for (ConstructionMode mode : ConstructionMode.values()) {
                Set<Resource> bob = answerRoots(executePlan(con,
                        prefix + "<urn:bob>) }", mode));
                Set<Resource> carol = answerRoots(executePlan(con,
                        prefix + "<urn:carol>) }", mode));
                assertEquals(mode + ": Bob query has one answer", 1, bob.size());
                assertEquals(mode + ": Carol query has one answer", 1, carol.size());
                assertTrue(mode + ": different composite FILTER conditions must not alias one root",
                        Collections.disjoint(bob, carol));
            }
        } finally {
            repo.shutDown();
        }
    }

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

            // The ANSWER ⊕ is keyed by (pattern tag, binding) and its function is
            // whatever c:feeds edges accumulate, so an id shared across queries is ALIASING, not
            // sharing. It must therefore carry Def. 4.6's pattern tag θ, which includes the operand's
            // FILTERs. Sharing it here would look harmless because ?y is projected (every derivation
            // of one answer then agrees on the condition) — but the tag cannot depend on that: with
            // the condition on a NON-projected variable the two queries give one binding genuinely
            // different derivation sets, and an aliased root returns the unfiltered probability for
            // the filtered query as soon as both circuits reach one store. See
            // filterOnANonProjectedVariableDoesNotAliasTheUnfilteredAnswer below.
            assertTrue("a filtered query's answer roots must be disjoint from the unfiltered query's",
                    Collections.disjoint(answerRoots(unfiltered), answerRoots(filtered)));

            // Def. 4.7 clause 7 ("a filter builds no gate and renames none"), stated where it is
            // actually sound: the CONTENT-addressed layer. A ⊗ gate's id IS its sorted child multiset,
            // so equal id implies equal Boolean function and sharing one across queries is safe.
            // Compared flat with flat, because a filtered BGP falls back to the flat plan while the
            // unfiltered one takes the factored route, whose internal gates legitimately differ.
            Model flatUnfiltered = executePlan(con,
                    "SELECT ?y WHERE { <urn:s> <urn:p> ?y }", ConstructionMode.FLAT);
            Model flatFiltered = executePlan(con,
                    "SELECT ?y WHERE { <urn:s> <urn:p> ?y FILTER(?y != <urn:b>) }", ConstructionMode.FLAT);
            assertTrue("the filtered circuit's product layer must be a sub-circuit of the unfiltered one",
                    productGates(flatUnfiltered).containsAll(productGates(flatFiltered)));
            assertFalse("and a strict one, since one answer is gone",
                    productGates(flatFiltered).containsAll(productGates(flatUnfiltered)));
            assertTrue("a filter must introduce no leaf of its own",
                    leaves(flatUnfiltered).containsAll(leaves(flatFiltered)));
            // Everything below the answer gates is shared; only the roots separate.
            assertTrue("only the answer gates may distinguish the two circuits",
                    flatUnfiltered.containsAll(withoutAnswerLayer(flatFiltered)));
        } finally {
            repo.shutDown();
        }
    }

    /**
     * The reason the answer ⊕ must carry Def. 4.6's pattern tag θ, and why θ must include the
     * operand's FILTERs.
     *
     * <p>{@code ?x} has two derivations that differ only in the non-projected {@code ?z}. The
     * condition keeps one of them, so the filtered and unfiltered queries give the SAME binding
     * genuinely DIFFERENT derivation sets — Pr = 0.25 against 0.4375 at p = 0.5. An untagged answer
     * gate gave both the same IRI; merging the two circuits (a shared circuit store, {@code
     * CIRCUIT_PERSIST}, a cross-query cache) then silently answered the filtered query with the
     * unfiltered function. Unlike a ⊗ gate, whose id determines its children, a ⊕ gate's id says
     * nothing about the {@code c:feeds} edges that will accumulate on it, so this is aliasing.
     */
    @Test
    public void filterOnANonProjectedVariableDoesNotAliasTheUnfilteredAnswer() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reify(con, "urn:r:1", "urn:s1", "urn:p", "urn:m1");
            reifyLiteral(con, "urn:r:2", "urn:m1", "urn:q", 3);
            reify(con, "urn:r:3", "urn:s1", "urn:p", "urn:m2");
            reifyLiteral(con, "urn:r:4", "urn:m2", "urn:q", 9);

            String unfilteredQuery = "SELECT ?x WHERE { ?x <urn:p> ?y . ?y <urn:q> ?z }";
            String filteredQuery =
                    "SELECT ?x WHERE { ?x <urn:p> ?y . ?y <urn:q> ?z FILTER(?z > 5) }";

            for (ConstructionMode mode : ConstructionMode.values()) {
                Set<Resource> unfiltered = answerRoots(executePlan(con, unfilteredQuery, mode));
                Set<Resource> filtered = answerRoots(executePlan(con, filteredQuery, mode));
                assertEquals(mode + ": one answer either way", 1, unfiltered.size());
                assertEquals(mode + ": one answer either way", 1, filtered.size());
                assertTrue(mode + ": the two queries must not share an answer root — their derivation "
                                + "sets for ?x=s1 differ",
                        Collections.disjoint(unfiltered, filtered));
            }
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
    public void outputBindingsExecuteAndKeepQueryIdentity() {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            reifyLiteral(con, "urn:r:n", "urn:s", "urn:n", 1);
            ValueFactory vf = con.getValueFactory();
            reifyValue(con, "urn:r:date", "urn:s", "urn:date",
                    vf.createLiteral("1995-03-18",
                            vf.createIRI("http://www.w3.org/2001/XMLSchema#date")));

            String plus = "SELECT (?n + 1 AS ?x) WHERE { <urn:s> <urn:n> ?n }";
            String times = "SELECT (?n * 2 AS ?x) WHERE { <urn:s> <urn:n> ?n }";
            String year = "SELECT ?year WHERE { <urn:s> <urn:date> ?date "
                    + "BIND(YEAR(?date) AS ?year) }";
            CircuitConstructionPlan factored = new CircuitRewriter(
                    Reification.STANDARD, ConstructionMode.FACTORED, "junit-bind-factored")
                    .constructionPlan(plus);
            assertEquals("a BIND needs the flat group in which its expression is evaluated",
                    ConstructionMode.FLAT, factored.effectiveMode());

            Model plusCircuit = executePlan(con, plus);
            Model timesCircuit = executePlan(con, times);
            Model yearCircuit = executePlan(con, year);
            Value two = vf.createLiteral("2",
                    vf.createIRI("http://www.w3.org/2001/XMLSchema#integer"));
            assertEquals(Collections.singleton(two), bindingValues(plusCircuit, "x"));
            assertEquals(Collections.singleton(two), bindingValues(timesCircuit, "x"));
            assertEquals(Collections.singleton(vf.createLiteral("1995",
                            vf.createIRI("http://www.w3.org/2001/XMLSchema#integer"))),
                    bindingValues(yearCircuit, "year"));
            assertTrue("different extension expressions must not alias an answer root merely because "
                            + "this dataset happens to give them the same projected value",
                    Collections.disjoint(answerRoots(plusCircuit), answerRoots(timesCircuit)));
        } finally {
            repo.shutDown();
        }
    }

    @Test
    public void bindThatConstrainsALaterTriplePatternIsRejected() {
        assertRejected("SELECT ?x WHERE { <urn:s> <urn:n> ?n . BIND(?n AS ?x) . "
                        + "<urn:t> <urn:p> ?x }",
                "subsequently used by a triple pattern");
    }

    @Test
    public void everyGeneratedConstructWithAFilterParses() {
        String[] queries = {
            "SELECT ?y WHERE { <urn:s> <urn:p> ?y FILTER(?y != <urn:b>) }",
            "SELECT ?y WHERE { <urn:s> <urn:p> ?y FILTER(REGEX(STR(?y), \"^urn\", \"i\")) }",
            "SELECT ?y WHERE { <urn:s> <urn:p> ?y FILTER(sameTerm(?y, <urn:b>) || isIRI(?y)) }",
            "SELECT ?y WHERE { <urn:s> <urn:p> ?y FILTER(DATATYPE(?y) != <urn:d>) }",
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> "
                + "SELECT ?d WHERE { <urn:s> <urn:date> ?d "
                + "FILTER(STRDT(CONCAT(STR(?d), 'T00:00:00'), xsd:dateTime) "
                + "< '1998-09-24T00:00:00'^^xsd:dateTime) }",
            "SELECT ?name WHERE { <urn:s> <urn:name> ?name FILTER(CONTAINS(?name, 'thistle')) }",
            "SELECT ?mode WHERE { <urn:s> <urn:mode> ?mode FILTER(?mode IN ('AIR', 'AIR REG')) }",
            "SELECT (1 AS ?x) WHERE { <urn:s> <urn:p> ?value }",
            "SELECT ?alias ?year WHERE { <urn:s> <urn:date> ?date "
                + "BIND(?date AS ?alias) BIND(YEAR(?date) AS ?year) "
                + "BIND((2 * (1 - 0.1)) AS ?unused) }",
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
        return CircuitTestSupport.answerRoots(model);
    }

    private static Set<Value> bindingValues(Model model, String variable) {
        Set<Value> out = new LinkedHashSet<>();
        for (Resource root : answerRoots(model)) {
            Value value = CircuitTestSupport.bindingValues(model, root).get(variable);
            if (value != null) out.add(value);
        }
        return out;
    }

    /**
     * The circuit below the answer layer: drop every statement mentioning an answer gate or one of
     * the binding nodes derived from it (those are named {@code <answerIRI>#var}, so they carry the
     * answer IRI as a prefix and separate along with it).
     */
    private static Model withoutAnswerLayer(Model model) {
        Set<String> roots = new LinkedHashSet<>();
        for (Resource root : answerRoots(model)) roots.add(root.stringValue());
        Model out = new LinkedHashModel();
        for (Statement st : model) {
            if (mentionsAnswerLayer(st.getSubject(), roots) || mentionsAnswerLayer(st.getObject(), roots)) {
                continue;
            }
            out.add(st);
        }
        return out;
    }

    private static boolean mentionsAnswerLayer(Value term, Set<String> roots) {
        for (String root : roots) {
            if (term.stringValue().startsWith(root)) return true;
        }
        return false;
    }

    /** The ⊗ gates: content-addressed, so equal id ⇒ equal Boolean function ⇒ safe to share. */
    private static Set<Resource> productGates(Model model) {
        return CircuitTestSupport.gatesOfType(model, "Times");
    }

    private static Set<Resource> minusRoots(Model model) {
        return CircuitTestSupport.gatesOfType(model, "Minus");
    }

    private static Set<Value> subtrahends(Model model) {
        IRI sub = SimpleValueFactory.getInstance().createIRI(C, "subtrahend");
        Set<Value> out = new HashSet<>();
        for (Statement st : model.filter(null, sub, null)) out.add(st.getObject());
        return out;
    }

    /** The leaves of the circuit: {@code c:in} targets the circuit does not itself type as a gate. */
    private static Set<String> leaves(Model model) {
        return CircuitTestSupport.leaves(model);
    }

    private static void reify(RepositoryConnection con, String token, String s, String p, String o) {
        ValueFactory vf = con.getValueFactory();
        IRI t = vf.createIRI(token);
        con.add(t, vf.createIRI(RDF.NAMESPACE, "subject"), vf.createIRI(s));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "predicate"), vf.createIRI(p));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "object"), vf.createIRI(o));
    }

    private static void reifyLiteral(RepositoryConnection con, String token, String s, String p, int o) {
        reifyValue(con, token, s, p, con.getValueFactory().createLiteral(o));
    }

    private static void reifyValue(RepositoryConnection con, String token, String s, String p, Value o) {
        ValueFactory vf = con.getValueFactory();
        IRI t = vf.createIRI(token);
        con.add(t, vf.createIRI(RDF.NAMESPACE, "subject"), vf.createIRI(s));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "predicate"), vf.createIRI(p));
        con.add(t, vf.createIRI(RDF.NAMESPACE, "object"), o);
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
