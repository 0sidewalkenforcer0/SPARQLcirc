package npcs.circuit;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

import org.eclipse.rdf4j.model.Literal;
import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.query.GraphQueryResult;
import org.eclipse.rdf4j.query.QueryResults;
import org.eclipse.rdf4j.query.TupleQueryResult;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.repository.sparql.SPARQLRepository;
import org.eclipse.rdf4j.rio.RDFFormat;
import org.eclipse.rdf4j.rio.Rio;
import org.eclipse.rdf4j.sail.memory.MemoryStore;

import npcs.rewrite.Reification;

/**
 * Engine-native circuit construction: emit the CONSTRUCT (CircuitRewriter), run
 * it on an unmodified SPARQL engine (an in-memory RDF4J store here) over reified
 * probabilistic data, and print the materialized provenance circuit as N-Triples.
 *
 * <pre>
 *   java -cp target/npcs-rewrite.jar npcs.circuit.CircuitRun \
 *        Standard data.reified.ttl query.sparql
 * </pre>
 * The emitted CONSTRUCT is printed to stderr; the circuit RDF goes to stdout.
 */
public final class CircuitRun {

    public static void main(String[] args) throws Exception {
        if (args.length != 3 && args.length != 4) {
            System.err.println("Usage: CircuitRun <Standard|SPARQL_Star> <dataFile> <queryFile> [sparqlEndpointURL]");
            System.err.println("  With an endpoint URL, the circuit is built on that engine (e.g. GraphDB) instead");
            System.err.println("  of in-memory RDF4J -- the SAME standard-SPARQL-1.1 CONSTRUCTs, so the circuit is");
            System.err.println("  byte-identical across engines. The endpoint must be WRITABLE (the iterative path");
            System.err.println("  protocol INSERTs each round's gates back).");
            System.exit(2);
            return;
        }
        Reification scheme = Reification.fromName(args[0]);
        File dataFile = new File(args[1]);
        String query = new String(Files.readAllBytes(Paths.get(args[2])), StandardCharsets.UTF_8);
        String endpoint = args.length == 4 ? args[3] : null;
        RDFFormat fmt = args[1].endsWith(".ttls") ? RDFFormat.TURTLESTAR : RDFFormat.TURTLE;

        CircuitRewriter rw = new CircuitRewriter(scheme);
        CircuitRewriter.PathQuery pathq = rw.pathQuery(query);

        Repository repo;
        if (endpoint != null) {
            SPARQLRepository sparql = new SPARQLRepository(endpoint);   // GraphDB / Fuseki / any SPARQL 1.1 endpoint
            sparql.init();
            repo = sparql;
            System.err.println("# building the circuit on remote endpoint: " + endpoint);
        } else {
            repo = new SailRepository(new MemoryStore());
        }
        try (RepositoryConnection con = repo.getConnection()) {
            try {
                con.add(dataFile, "urn:base:", fmt);                   // in-memory: load; endpoint: INSERT (needs write access)
            } catch (RuntimeException e) {
                if (endpoint != null) {
                    System.err.println("# ERROR: could not write data to the endpoint (needs a WRITABLE repo for the "
                        + "iterative protocol): " + e.getMessage());
                }
                throw e;
            }
            Model circuit = new org.eclipse.rdf4j.model.impl.LinkedHashModel();
            if (pathq != null) {
                // property paths: CLIENT-DRIVEN ITERATIVE fixpoint. Count nodes to bound the
                // rounds (simple-path bound N-1), then INIT, loop STEP feeding each round back
                // into the store so the next round's WHERE can read reach^k, then FINAL.
                int n;
                try (TupleQueryResult r = con.prepareTupleQuery(pathq.nodeCountQuery()).evaluate()) {
                    n = ((Literal) r.next().getValue("c")).intValue();
                }
                int rounds = Math.max(1, n - 1);
                System.err.println("# ---- property-path plan: nodes=" + n + ", rounds=" + rounds + " ----");
                java.util.List<String> constructs = new java.util.ArrayList<>(pathq.init());
                for (int k = 0; k < rounds; k++) constructs.addAll(pathq.step(k));
                constructs.addAll(pathq.projectAnswers(rounds));
                int i = 0;
                for (String c : constructs) {
                    System.err.println("# --- path CONSTRUCT " + (++i) + " ---\n" + c);
                    Model m = new org.eclipse.rdf4j.model.impl.LinkedHashModel();
                    try (GraphQueryResult res = con.prepareGraphQuery(c).evaluate()) {
                        m.addAll(QueryResults.asModel(res));
                    }
                    circuit.addAll(m);
                    con.add(m);                 // FEEDBACK: materialize this round for the next
                }
            } else {
                java.util.List<String> planQueries = rw.plan(query);
                System.err.println("# ---- circuit construction plan: " + planQueries.size() + " CONSTRUCT(s) ----");
                for (int i = 0; i < planQueries.size(); i++) {
                    System.err.println("# --- step " + (i + 1) + " ---");
                    System.err.println(planQueries.get(i));
                }
                for (String construct : planQueries) {
                    try (GraphQueryResult res = con.prepareGraphQuery(construct).evaluate()) {
                        circuit.addAll(QueryResults.asModel(res));
                    }
                }
            }
            Rio.write(circuit, System.out, RDFFormat.NTRIPLES);
            System.err.println("# circuit triples: " + circuit.size());
        }
        repo.shutDown();
    }
}
