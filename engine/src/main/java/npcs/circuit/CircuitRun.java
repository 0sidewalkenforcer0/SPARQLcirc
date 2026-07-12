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
            System.err.println("  byte-identical across engines. Non-path queries are read-only CONSTRUCTs (run on");
            System.err.println("  ANY SPARQL 1.1 engine); property paths need a WRITABLE endpoint. Per-engine env:");
            System.err.println("    CIRCUIT_UPDATE_ENDPOINT=<url>  (default <endpoint>/statements = GraphDB/RDF4J;");
            System.err.println("                                    Fuseki/Oxigraph = <base>/update, Virtuoso = /sparql)");
            System.err.println("    CIRCUIT_SKIP_LOAD=1            (data already bulk-loaded on the engine)");
            System.err.println("    CIRCUIT_READONLY=1            (engine has no SPARQL UPDATE: QLever/MillenniumDB)");
            System.err.println("  See reference/engines/ for a profile per engine.");
            System.exit(2);
            return;
        }
        Reification scheme = Reification.fromName(args[0]);
        File dataFile = new File(args[1]);
        String query = new String(Files.readAllBytes(Paths.get(args[2])), StandardCharsets.UTF_8);
        String endpoint = args.length == 4 ? args[3] : null;
        RDFFormat fmt = args[1].endsWith(".ttls") ? RDFFormat.TURTLESTAR : RDFFormat.TURTLE;
        // Per-engine configuration via environment (script-friendly; GraphDB defaults unchanged when unset).
        // Profiles for Fuseki/Oxigraph/QLever/MillenniumDB/Virtuoso/Stardog live in reference/engines/.
        String updateEndpointEnv = System.getenv("CIRCUIT_UPDATE_ENDPOINT");   // override; else <endpoint>/statements
        boolean skipLoad = "1".equals(System.getenv("CIRCUIT_SKIP_LOAD"));     // data pre-loaded via bulk import
        boolean readOnly = "1".equals(System.getenv("CIRCUIT_READONLY"));      // engine exposes query only, no UPDATE
        if (readOnly) skipLoad = true;                                         // read-only implies data pre-loaded

        CircuitRewriter rw = new CircuitRewriter(scheme);
        CircuitRewriter.PathQuery pathq = rw.pathQuery(query);

        Repository repo;
        if (endpoint != null) {
            // Any SPARQL 1.1 endpoint. The *update* endpoint differs per engine: GraphDB/RDF4J use
            // <repo>/statements, Fuseki/Oxigraph use <base>/update, Virtuoso uses <base>/sparql -- override
            // via CIRCUIT_UPDATE_ENDPOINT. Read-only engines (QLever/MillenniumDB) expose query only.
            SPARQLRepository sparql;
            if (readOnly) {
                sparql = new SPARQLRepository(endpoint);               // query-only; no UPDATE is ever issued
                System.err.println("# building on remote READ-ONLY endpoint (non-path only): " + endpoint);
            } else {
                String updateEndpoint = (updateEndpointEnv != null && !updateEndpointEnv.isEmpty())
                    ? updateEndpointEnv : endpoint.replaceAll("/+$", "") + "/statements";
                sparql = new SPARQLRepository(endpoint, updateEndpoint);
                System.err.println("# building on remote endpoint: " + endpoint + "  (update: " + updateEndpoint + ")");
            }
            sparql.init();
            repo = sparql;
        } else {
            repo = new SailRepository(new MemoryStore());
        }
        try (RepositoryConnection con = repo.getConnection()) {
            if (skipLoad) {
                System.err.println("# CIRCUIT_SKIP_LOAD: assuming the (reified) data is already loaded on the engine");
            } else try {
                con.add(dataFile, "urn:base:", fmt);                   // in-memory: load; endpoint: INSERT (needs write access)
            } catch (RuntimeException e) {
                if (endpoint != null) {
                    System.err.println("# ERROR: could not write data to the endpoint (needs a WRITABLE repo, or set "
                        + "CIRCUIT_SKIP_LOAD=1 if the data is already loaded): " + e.getMessage());
                }
                throw e;
            }
            Model circuit = new org.eclipse.rdf4j.model.impl.LinkedHashModel();
            if (pathq != null && readOnly) {
                System.err.println("# ERROR: property-path queries need a WRITABLE endpoint -- the iterative protocol "
                    + "INSERTs each round's reach gates back so the next CONSTRUCT can match them. This engine is "
                    + "read-only (CIRCUIT_READONLY=1). Run non-path queries here; use a writable engine "
                    + "(Fuseki/Oxigraph/GraphDB) for paths. (Planned read-only route: inline the prior round's gates "
                    + "via VALUES instead of INSERT -- see reference/engines/README.md.)");
                System.exit(3);
                return;
            }
            if (pathq != null) {
                // property paths: CLIENT-DRIVEN ITERATIVE fixpoint with an EXACT reachable-set round
                // bound. INIT, then loop STEP, feeding each round back into the store; stop after
                // |V_s|-1 rounds where V_s = the nodes actually reached SO FAR (discovered live from the
                // reach gates' c:rfrom/c:rto). A simple path in the reachable subgraph has <= |V_s|-1
                // edges, so |V_s|-1 rounds capture every simple path -> exact provenance -- while
                // |V_s| << the global node count keeps a bounded/sparse query feasible.
                int cap;
                if (pathq.boundSource()) {
                    // G1: discover the source's REACHABLE subgraph by a read-only client BFS, then restrict
                    // the base relation to edges FROM reachable nodes (in pathq.init below). This avoids ever
                    // materializing the all-pairs base (every predicate edge = the OOM at KG scale); the
                    // composition protocol is unchanged, so provenance stays exact (all simple paths in V_s).
                    java.util.Set<String> reach = new java.util.LinkedHashSet<>();
                    java.util.Set<String> frontier = new java.util.LinkedHashSet<>();
                    reach.add(pathq.sourceValue()); frontier.add(pathq.sourceValue());
                    while (!frontier.isEmpty()) {
                        java.util.Set<String> next = new java.util.LinkedHashSet<>();
                        try (TupleQueryResult r = con.prepareTupleQuery(pathq.frontierStepQuery(frontier)).evaluate()) {
                            while (r.hasNext()) {
                                org.eclipse.rdf4j.model.Value v = r.next().getValue("v");
                                if (v != null && reach.add(v.stringValue())) next.add(v.stringValue());
                            }
                        }
                        frontier = next;
                    }
                    pathq.setReachable(reach);
                    cap = Math.max(1, reach.size() - 1);       // |V_s|-1 bounds every simple path in the reachable subgraph
                    System.err.println("# ---- G1 reachable-subgraph BFS: |V_s| = " + reach.size() + " ----");
                } else {
                    int nGlobal;                               // variable source (all-pairs): fall back to the global bound
                    try (TupleQueryResult r = con.prepareTupleQuery(pathq.nodeCountQuery()).evaluate()) {
                        nGlobal = ((Literal) r.next().getValue("c")).intValue();
                    }
                    cap = Math.max(1, nGlobal - 1);
                }
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
                    + ", rounds=" + lastLevel + " (cap=" + cap + "), path fp=" + pathq.fingerprint() + " ----");
                System.err.println("# reach/base gates are fingerprinted (urn:g:r: + c:rpath) so distinct path "
                    + "queries on a shared writable endpoint never compose with each other's persisted gates.");
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
            // Opt-in hygiene for a PERSISTENT endpoint: remove THIS run's provenance gates now that the
            // circuit is emitted. Correctness never depends on this (the per-path fingerprint already
            // isolates concurrent queries); it just reclaims space. Best-effort — the output is already out,
            // and `circuit` holds only urn:g:*/urn:circuit:* gate triples, so the loaded data is untouched.
            if (endpoint != null && "1".equals(System.getenv("CIRCUIT_CLEANUP"))) {
                try {
                    con.remove(circuit);
                    System.err.println("# CIRCUIT_CLEANUP: removed " + circuit.size() + " gate triples from the endpoint");
                } catch (RuntimeException e) {
                    System.err.println("# CIRCUIT_CLEANUP failed (non-fatal, circuit already emitted): " + e.getMessage());
                }
            }
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
