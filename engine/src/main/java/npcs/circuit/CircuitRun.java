package npcs.circuit;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

import org.eclipse.rdf4j.model.Literal;
import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.model.Statement;
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
            // GraphDB / Fuseki / any SPARQL 1.1 endpoint. RDF4J-based servers (GraphDB) expose the
            // SPARQL *update* endpoint at <repo>/statements while the query endpoint is <repo>; the
            // single-URL constructor would POST updates to the query endpoint ("Missing parameter: query").
            String updateEndpoint = endpoint.replaceAll("/+$", "") + "/statements";
            SPARQLRepository sparql = new SPARQLRepository(endpoint, updateEndpoint);
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
                // property paths: CLIENT-DRIVEN ITERATIVE fixpoint with an EXACT reachable-set round
                // bound. INIT, then loop STEP, feeding each round back into the store; stop after
                // |V_s|-1 rounds where V_s = the nodes actually reached SO FAR (discovered live from the
                // reach gates' c:rfrom/c:rto). A simple path in the reachable subgraph has <= |V_s|-1
                // edges, so |V_s|-1 rounds capture every simple path -> exact provenance -- while
                // |V_s| << the global node count keeps a bounded/sparse query feasible.
                int nGlobal;
                try (TupleQueryResult r = con.prepareTupleQuery(pathq.nodeCountQuery()).evaluate()) {
                    nGlobal = ((Literal) r.next().getValue("c")).intValue();
                }
                int cap = Math.max(1, nGlobal - 1);            // global upper bound (safety net)
                java.util.Set<String> reachNodes = new java.util.HashSet<>();
                for (String c : pathq.init()) runFeed(con, circuit, reachNodes, c);
                int k = 0, lastLevel = 0;
                while (k < cap) {
                    for (String c : pathq.step(k)) runFeed(con, circuit, reachNodes, c);
                    lastLevel = ++k;
                    if (k >= reachNodes.size() - 1) break;     // exact reachable-set bound |V_s|-1
                }
                for (String c : pathq.projectAnswers(lastLevel)) runFeed(con, circuit, reachNodes, c);
                System.err.println("# ---- property-path plan: reachable-nodes=" + reachNodes.size()
                    + ", rounds=" + lastLevel + " (global-N cap=" + cap + ") ----");
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

    /** Run one path-round CONSTRUCT, add its triples to the accumulated circuit AND back into the
     *  store (feedback for the next round), and record any reach-gate endpoints (c:rfrom/c:rto) so the
     *  caller can bound the loop by the live reachable-set size |V_s|. */
    private static void runFeed(RepositoryConnection con, Model circuit,
                                java.util.Set<String> reachNodes, String construct) {
        System.err.println("# --- path CONSTRUCT ---\n" + construct);   // emit the plan (stderr)
        Model m = new org.eclipse.rdf4j.model.impl.LinkedHashModel();
        try (GraphQueryResult res = con.prepareGraphQuery(construct).evaluate()) {
            m.addAll(QueryResults.asModel(res));
        }
        circuit.addAll(m);
        con.add(m);
        // reachable-subgraph nodes = endpoints of the reach LEVEL gates (rlvl 0,1,2,...); the base
        // relation (rlvl "base") is all-pairs over the WHOLE graph, so exclude it from the bound.
        java.util.Set<String> baseGates = new java.util.HashSet<>();
        for (Statement st : m)
            if (st.getPredicate().stringValue().equals("urn:circuit:rlvl")
                    && st.getObject().stringValue().equals("base"))
                baseGates.add(st.getSubject().stringValue());
        for (Statement st : m) {
            String p = st.getPredicate().stringValue();
            if ((p.equals("urn:circuit:rfrom") || p.equals("urn:circuit:rto"))
                    && !baseGates.contains(st.getSubject().stringValue()))
                reachNodes.add(st.getObject().stringValue());
        }
    }
}
