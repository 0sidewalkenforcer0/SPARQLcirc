package npcs.circuit;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.query.GraphQueryResult;
import org.eclipse.rdf4j.query.QueryResults;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.sail.SailRepository;
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
        if (args.length != 3) {
            System.err.println("Usage: CircuitRun <Standard|SPARQL_Star> <dataFile> <queryFile>");
            System.exit(2);
            return;
        }
        Reification scheme = Reification.fromName(args[0]);
        File dataFile = new File(args[1]);
        String query = new String(Files.readAllBytes(Paths.get(args[2])), StandardCharsets.UTF_8);
        RDFFormat fmt = args[1].endsWith(".ttls") ? RDFFormat.TURTLESTAR : RDFFormat.TURTLE;

        java.util.List<String> planQueries = new CircuitRewriter(scheme).plan(query);
        System.err.println("# ---- circuit construction plan: " + planQueries.size() + " CONSTRUCT(s) ----");
        for (int i = 0; i < planQueries.size(); i++) {
            System.err.println("# --- step " + (i + 1) + " ---");
            System.err.println(planQueries.get(i));
        }

        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            con.add(dataFile, "urn:base:", fmt);
            Model circuit = new org.eclipse.rdf4j.model.impl.LinkedHashModel();
            for (String construct : planQueries) {
                try (GraphQueryResult res = con.prepareGraphQuery(construct).evaluate()) {
                    circuit.addAll(QueryResults.asModel(res));
                }
            }
            Rio.write(circuit, System.out, RDFFormat.NTRIPLES);
            System.err.println("# circuit triples: " + circuit.size());
        }
        repo.shutDown();
    }
}
