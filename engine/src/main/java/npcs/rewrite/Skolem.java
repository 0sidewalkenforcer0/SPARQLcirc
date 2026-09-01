package npcs.rewrite;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import org.eclipse.rdf4j.model.BNode;
import org.eclipse.rdf4j.model.Resource;
import org.eclipse.rdf4j.model.Statement;
import org.eclipse.rdf4j.model.Triple;
import org.eclipse.rdf4j.model.Value;
import org.eclipse.rdf4j.model.ValueFactory;
import org.eclipse.rdf4j.model.impl.SimpleValueFactory;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.rio.RDFFormat;
import org.eclipse.rdf4j.rio.RDFHandler;
import org.eclipse.rdf4j.rio.RDFHandlerException;
import org.eclipse.rdf4j.rio.RDFParser;
import org.eclipse.rdf4j.rio.helpers.BasicParserSettings;
import org.eclipse.rdf4j.rio.Rio;
import org.eclipse.rdf4j.rio.helpers.AbstractRDFHandler;

/**
 * The skolemization the rewriting assumes (§4.2): one injective map {@code sk : B_G → I} whose image
 * lies in a fresh IRI namespace, applied to the input graph BEFORE it reaches any endpoint.
 *
 * <p>It is not a convenience. A blank node has no identity outside the document that mentions it, and
 * gate keys hash {@code STR(?term)}, so a blank node in a binding makes the gate depend on a label
 * the store invented. Worse, the two engines do not even fail the same way: RDF4J treats
 * {@code STR(?bnode)} as a type error, which leaves the answer gate unbound and DROPS the answer
 * silently, while rdflib returns the internal label and produces a store-dependent gate. Neither is
 * the circuit the query denotes, and there is no SPARQL 1.1 function that could rescue this at query
 * time — a stable name for a blank node is precisely what RDF declines to give.
 *
 * <p>{@code sk(b) = urn:sk:<hex of the UTF-8 label>}. Hex keeps it injective and makes it its own
 * inverse, so {@code sk⁻¹} needs no stored map on either side of the pipeline.
 *
 * <p><b>What this does and does not stabilize.</b> The same skolemized graph loaded into two engines
 * yields identical circuits — the byte-identity claim. It does NOT make two different serializations
 * of the same graph agree, since {@code _:x} and {@code _:y} are different labels; that would need
 * graph canonicalization (RDFC-1.0), which is out of scope and not what the claim says.
 *
 * <pre>
 *   java -cp npcs-rewrite.jar npcs.rewrite.Skolem in.ttl out.nt
 * </pre>
 */
public final class Skolem {

    /** The fresh namespace {@code sk}'s image lives in. */
    public static final String NS = "urn:sk:";

    private static final char[] HEX = "0123456789abcdef".toCharArray();

    private Skolem() {}

    /** {@code sk(b)}. */
    public static org.eclipse.rdf4j.model.IRI of(BNode blank, ValueFactory vf) {
        return vf.createIRI(NS + hex(blank.getID()));
    }

    /** {@code sk⁻¹}: the original label, or {@code null} when the IRI is not in the image of sk. */
    public static String labelOf(String iri) {
        if (iri == null || !iri.startsWith(NS)) return null;
        return unhex(iri.substring(NS.length()));
    }

    /** {@code sk} on one term, recursively including the constituents of quoted triples. */
    public static Value apply(Value value, ValueFactory vf) {
        if (value instanceof BNode) return of((BNode) value, vf);
        if (value instanceof Triple) {
            Triple triple = (Triple) value;
            Resource subject = (Resource) apply(triple.getSubject(), vf);
            Value object = apply(triple.getObject(), vf);
            if (subject == triple.getSubject() && object == triple.getObject()) return value;
            return vf.createTriple(subject, triple.getPredicate(), object);
        }
        return value;
    }

    public static Statement apply(Statement st, ValueFactory vf) {
        Value subject = apply(st.getSubject(), vf);
        Value object = apply(st.getObject(), vf);
        if (subject == st.getSubject() && object == st.getObject()) return st;
        return vf.createStatement((Resource) subject, st.getPredicate(), object, st.getContext());
    }

    /**
     * A parser that keeps the DOCUMENT's blank-node labels. Without this RDF4J mints a fresh
     * {@code genid-<uuid>-x} per parse, so {@code sk} would be a function of the parse rather than of
     * the input graph and two loads of the same file would disagree — exactly the instability sk
     * exists to remove. The paper's sk is over B_G, the blank nodes OF THE GRAPH, so the label the
     * document gives is the right domain.
     */
    private static RDFParser parser(RDFFormat format) {
        RDFParser parser = Rio.createParser(format);
        parser.set(BasicParserSettings.PRESERVE_BNODE_IDS, true);
        return parser;
    }

    /** Skolemize every statement on the way through, so a load stays streaming. */
    public static RDFHandler handler(RDFHandler downstream) {
        ValueFactory vf = SimpleValueFactory.getInstance();
        return new AbstractRDFHandler() {
            @Override public void startRDF() { downstream.startRDF(); }
            @Override public void endRDF() { downstream.endRDF(); }
            @Override public void handleNamespace(String p, String u) { downstream.handleNamespace(p, u); }
            @Override public void handleComment(String c) { downstream.handleComment(c); }
            @Override public void handleStatement(Statement st) { downstream.handleStatement(apply(st, vf)); }
        };
    }

    /**
     * Load a file into {@code con}, skolemizing on the way. Streaming and batched, so a large graph
     * does not have to be held in memory just to be rewritten.
     */
    public static void load(RepositoryConnection con, File file, String baseIri, RDFFormat format)
            throws IOException {
        ValueFactory vf = SimpleValueFactory.getInstance();
        RDFParser parser = parser(format);
        parser.setRDFHandler(new AbstractRDFHandler() {
            private final List<Statement> batch = new ArrayList<>();
            @Override public void handleStatement(Statement st) {
                batch.add(apply(st, vf));
                if (batch.size() >= 10_000) flush();
            }
            @Override public void endRDF() { flush(); }
            private void flush() {
                if (!batch.isEmpty()) { con.add(batch); batch.clear(); }
            }
        });
        try (InputStream in = new FileInputStream(file)) {
            parser.parse(in, baseIri);
        }
    }

    /**
     * Does this store still hold blank nodes? Asked once before building on data somebody else
     * loaded, because that is the one route where the engine cannot have applied {@code sk} itself.
     */
    public static boolean graphHasBlankNodes(RepositoryConnection con) {
        return graphHasBlankNodes(con, false);
    }

    /** Also inspect quoted-triple constituents when the selected endpoint scheme supports RDF-star. */
    public static boolean graphHasBlankNodes(RepositoryConnection con, boolean inspectQuotedTriples) {
        if (con.prepareBooleanQuery(
                "ASK { ?s ?p ?o FILTER(isBlank(?s) || isBlank(?o)) }").evaluate()) {
            return true;
        }
        if (!inspectQuotedTriples) return false;
        String query = "SELECT ?qs ?qo WHERE { "
                + "{ << ?qs ?qp ?qo >> ?p ?o } UNION { ?s ?p << ?qs ?qp ?qo >> } }";
        try (org.eclipse.rdf4j.query.TupleQueryResult result = con.prepareTupleQuery(query).evaluate()) {
            while (result.hasNext()) {
                org.eclipse.rdf4j.query.BindingSet row = result.next();
                if (containsBlankNode(row.getValue("qs")) || containsBlankNode(row.getValue("qo"))) {
                    return true;
                }
            }
        }
        return false;
    }

    private static boolean containsBlankNode(Value value) {
        if (value instanceof BNode) return true;
        if (!(value instanceof Triple)) return false;
        Triple triple = (Triple) value;
        return containsBlankNode(triple.getSubject()) || containsBlankNode(triple.getObject());
    }

    private static String hex(String label) {
        byte[] bytes = label.getBytes(StandardCharsets.UTF_8);
        StringBuilder out = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) out.append(HEX[(b >> 4) & 0xF]).append(HEX[b & 0xF]);
        return out.toString();
    }

    private static String unhex(String encoded) {
        if (encoded.isEmpty() || (encoded.length() & 1) != 0) return null;
        byte[] bytes = new byte[encoded.length() / 2];
        for (int i = 0; i < bytes.length; i++) {
            int hi = Character.digit(encoded.charAt(2 * i), 16);
            int lo = Character.digit(encoded.charAt(2 * i + 1), 16);
            if (hi < 0 || lo < 0) return null;
            bytes[i] = (byte) ((hi << 4) | lo);
        }
        return new String(bytes, StandardCharsets.UTF_8);
    }

    public static void main(String[] args) throws IOException {
        if (args.length != 2) {
            System.err.println("Usage: Skolem <in.ttl|.nt|.trig> <out.nt>");
            System.err.println("  Applies sk : B_G -> urn:sk:<hex label>, so the graph can be loaded");
            System.err.println("  into any endpoint and yield the same circuit. Run this before a bulk");
            System.err.println("  load; CircuitRun applies it itself when it does the loading.");
            System.exit(2);
            return;
        }
        File in = new File(args[0]);
        RDFFormat format = Rio.getParserFormatForFileName(in.getName()).orElse(RDFFormat.TURTLE);
        try (InputStream source = new FileInputStream(in);
             OutputStream sink = new FileOutputStream(args[1])) {
            RDFParser parser = parser(format);
            parser.setRDFHandler(handler(Rio.createWriter(RDFFormat.NTRIPLES, sink)));
            parser.parse(source, "urn:base:");
        } catch (RDFHandlerException failure) {
            throw new IOException(failure);
        }
    }
}
