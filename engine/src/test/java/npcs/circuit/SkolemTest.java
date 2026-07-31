package npcs.circuit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.LinkedHashSet;
import java.util.Set;

import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.model.Statement;
import org.eclipse.rdf4j.model.Value;
import org.eclipse.rdf4j.model.impl.LinkedHashModel;
import org.eclipse.rdf4j.model.impl.SimpleValueFactory;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.rio.RDFFormat;
import org.eclipse.rdf4j.sail.memory.MemoryStore;
import org.junit.Test;

import npcs.rewrite.Reification;
import npcs.rewrite.Skolem;

/** §4.2's {@code sk : B_G -> I}, and what it is for. */
public class SkolemTest {

    private static final String GRAPH =
            "@prefix d: <urn:d:> .\n"
          + "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
          + "d:t1 rdf:subject d:a ; rdf:predicate d:p ; rdf:object _:x .\n";

    @Test
    public void skIsInjectiveIntoAFreshNamespaceAndItsOwnInverse() {
        SimpleValueFactory vf = SimpleValueFactory.getInstance();
        for (String label : new String[]{"x", "b0", "genid-1", "uenicode-üñ"}) {
            String iri = Skolem.of(vf.createBNode(label), vf).stringValue();
            assertTrue(label + " must land in the fresh namespace", iri.startsWith(Skolem.NS));
            assertEquals("sk must invert without a stored map", label, Skolem.labelOf(iri));
        }
        assertNotEquals(Skolem.of(vf.createBNode("x"), vf), Skolem.of(vf.createBNode("y"), vf));
        assertNull("an ordinary IRI is not in sk's image", Skolem.labelOf("urn:d:a"));
        assertNull("nor is a near-miss", Skolem.labelOf("urn:skate:x"));
    }

    /**
     * Why this must happen before the data reaches an endpoint. A blank node in a binding used to
     * cost the answer outright: gate keys hash {@code STR(?term)}, RDF4J treats {@code STR(?bnode)}
     * as a type error, so the answer gate's BIND was unbound and CONSTRUCT dropped the whole answer
     * template -- leaving an orphan product gate, no root, and no diagnostic.
     */
    @Test
    public void aBlankNodeInAnAnswerNoLongerCostsTheAnswer() throws Exception {
        File data = File.createTempFile("skolem", ".ttl");
        try {
            Files.write(data.toPath(), GRAPH.getBytes(StandardCharsets.UTF_8));
            Set<Value> answered = new LinkedHashSet<>();
            for (Statement st : build(data)) {
                if (st.getPredicate().stringValue().equals("urn:circuit:val")) answered.add(st.getObject());
            }
            assertEquals("the answer must exist and bind one term", 1, answered.size());
            assertEquals("and it must be sk of the document's label", "x",
                    Skolem.labelOf(answered.iterator().next().stringValue()));
        } finally {
            data.delete();
        }
    }

    /**
     * sk has to be a function of the INPUT GRAPH, not of the parse. RDF4J mints a fresh
     * {@code genid-<uuid>} per document unless told otherwise, which would make two loads of one file
     * disagree -- the very instability sk removes, reintroduced one layer down.
     */
    @Test
    public void skDependsOnTheDocumentNotOnTheParse() throws Exception {
        File data = File.createTempFile("skolem", ".ttl");
        try {
            Files.write(data.toPath(), GRAPH.getBytes(StandardCharsets.UTF_8));
            assertEquals("two loads of the same file must give the same circuit",
                    canonical(build(data)), canonical(build(data)));
        } finally {
            data.delete();
        }
    }

    private static Model build(File data) {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            Skolem.load(con, data, "urn:base:", RDFFormat.TURTLE);
            Model circuit = new LinkedHashModel();
            CircuitConstructionPlan plan = new CircuitRewriter(
                    Reification.STANDARD, ConstructionMode.FLAT, "junit-skolem")
                    .constructionPlan("SELECT ?y WHERE { <urn:d:a> <urn:d:p> ?y }");
            CircuitRun.executeConstructionPlan(con, plan, circuit, false);
            return circuit;
        } catch (java.io.IOException failure) {
            throw new AssertionError(failure);
        } finally {
            repo.shutDown();
        }
    }

    private static Set<String> canonical(Model model) {
        Set<String> out = new java.util.TreeSet<>();
        for (Statement st : model) {
            out.add(st.getSubject() + " " + st.getPredicate() + " " + st.getObject());
        }
        return out;
    }
}
