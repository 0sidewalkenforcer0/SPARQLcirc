package npcs;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Paths;
import java.util.List;

import org.eclipse.rdf4j.model.BNode;
import org.eclipse.rdf4j.model.IRI;
import org.eclipse.rdf4j.model.Literal;
import org.eclipse.rdf4j.model.Value;
import org.eclipse.rdf4j.model.vocabulary.XSD;
import org.eclipse.rdf4j.query.BindingSet;
import org.eclipse.rdf4j.query.TupleQueryResult;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.rio.RDFFormat;
import org.eclipse.rdf4j.sail.memory.MemoryStore;

/**
 * Evaluate one SELECT query text, unchanged, over a data file in an in-memory RDF4J store and
 * print SPARQL 1.1 Query Results JSON on stdout.
 *
 * <pre>
 *   java -cp npcs-rewrite.jar npcs.LocalSelect data.ttls query.rq &gt; results.json
 * </pre>
 *
 * <p>This is a test fixture for the client-side pipelines (NPCS post-processing, probabilities):
 * it plays the role of an endpoint for queries that were rewritten elsewhere, including the
 * output of the original NPCS jar. The data file is loaded as is (no skolemization), so it must
 * be ground. The shaded jar carries no JSON result writer, hence the small writer below.
 */
public final class LocalSelect {

    private LocalSelect() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("Usage: LocalSelect <dataFile(.ttl|.ttls|.nq|.trig)> <queryFile>");
            System.exit(2);
            return;
        }
        String dataPath = args[0];
        RDFFormat format = dataPath.endsWith(".ttls") ? RDFFormat.TURTLESTAR
                : dataPath.endsWith(".nq") ? RDFFormat.NQUADS
                : dataPath.endsWith(".trig") ? RDFFormat.TRIG
                : RDFFormat.TURTLE;
        String query = Utf8Text.read(Paths.get(args[1]));
        Repository repository = new SailRepository(new MemoryStore());
        StringBuilder out = new StringBuilder();
        try (RepositoryConnection connection = repository.getConnection()) {
            connection.add(new File(dataPath), "urn:base:", format);
            try (TupleQueryResult result = connection.prepareTupleQuery(query).evaluate()) {
                List<String> names = result.getBindingNames();
                out.append("{\"head\":{\"vars\":[");
                for (int i = 0; i < names.size(); i++) {
                    if (i > 0) out.append(',');
                    string(out, names.get(i));
                }
                out.append("]},\"results\":{\"bindings\":[");
                boolean first = true;
                while (result.hasNext()) {
                    BindingSet row = result.next();
                    if (!first) out.append(',');
                    first = false;
                    out.append('{');
                    boolean firstBinding = true;
                    for (String name : names) {
                        Value value = row.getValue(name);
                        if (value == null) continue;
                        if (!firstBinding) out.append(',');
                        firstBinding = false;
                        string(out, name);
                        out.append(':');
                        term(out, value);
                    }
                    out.append('}');
                }
                out.append("]}}\n");
            }
        } finally {
            repository.shutDown();
        }
        System.out.write(out.toString().getBytes(StandardCharsets.UTF_8));
        System.out.flush();
    }

    private static void term(StringBuilder out, Value value) {
        if (value instanceof IRI) {
            out.append("{\"type\":\"uri\",\"value\":");
            string(out, value.stringValue());
        } else if (value instanceof BNode) {
            out.append("{\"type\":\"bnode\",\"value\":");
            string(out, ((BNode) value).getID());
        } else if (value instanceof Literal) {
            Literal literal = (Literal) value;
            out.append("{\"type\":\"literal\",\"value\":");
            string(out, literal.getLabel());
            if (literal.getLanguage().isPresent()) {
                out.append(",\"xml:lang\":");
                string(out, literal.getLanguage().get());
            } else if (!XSD.STRING.equals(literal.getDatatype())) {
                out.append(",\"datatype\":");
                string(out, literal.getDatatype().stringValue());
            }
        } else {
            throw new IllegalStateException("unsupported result term: " + value);
        }
        out.append('}');
    }

    private static void string(StringBuilder out, String text) {
        out.append('"');
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        out.append('"');
    }
}
