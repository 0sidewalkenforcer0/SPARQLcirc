package npcs.circuit;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import org.eclipse.rdf4j.model.IRI;
import org.eclipse.rdf4j.model.Literal;
import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.model.Statement;
import org.eclipse.rdf4j.model.impl.LinkedHashModel;
import org.eclipse.rdf4j.model.impl.SimpleValueFactory;
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
 *        --construction=factored Standard data.reified.ttl query.sparql
 * </pre>
 * The emitted CONSTRUCT is printed to stderr; the circuit RDF goes to stdout.
 */
public final class CircuitRun {

    public static void main(String[] args) throws Exception {
        ConstructionMode constructionMode = ConstructionMode.FACTORED;
        List<String> positional = new ArrayList<>();
        for (int i = 0; i < args.length; i++) {
            if (args[i].startsWith("--construction=")) {
                constructionMode = ConstructionMode.fromCli(
                        args[i].substring("--construction=".length()));
            } else if ("--construction".equals(args[i])) {
                if (++i >= args.length) {
                    throw new IllegalArgumentException("--construction requires factored or flat");
                }
                constructionMode = ConstructionMode.fromCli(args[i]);
            } else {
                positional.add(args[i]);
            }
        }
        if (positional.size() != 3 && positional.size() != 4) {
            System.err.println("Usage: CircuitRun [--construction=factored|flat] "
                    + "<Standard|SPARQL_Star> <dataFile> <queryFile> [sparqlEndpointURL]");
            System.err.println("  Default construction: factored (pure BGP variable elimination).");
            System.err.println("  flat is the one-product-per-derivation ablation and read-only-endpoint route.");
            System.err.println("  With an endpoint URL, the circuit is built on that engine (e.g. GraphDB) instead");
            System.err.println("  of in-memory RDF4J -- the SAME standard-SPARQL-1.1 CONSTRUCTs, so the circuit is");
            System.err.println("  byte-identical across engines. Factored BGPs and property paths need a WRITABLE");
            System.err.println("  endpoint; --construction=flat runs non-path queries read-only. Per-engine env:");
            System.err.println("    CIRCUIT_UPDATE_ENDPOINT=<url>  (default <endpoint>/statements = GraphDB/RDF4J;");
            System.err.println("                                    Fuseki/Oxigraph = <base>/update, Virtuoso = /sparql)");
            System.err.println("    CIRCUIT_SKIP_LOAD=1            (data already bulk-loaded on the engine)");
            System.err.println("    CIRCUIT_READONLY=1            (engine has no SPARQL UPDATE: QLever/MillenniumDB)");
            System.err.println("  See reference/engines/ for a profile per engine.");
            System.exit(2);
            return;
        }
        Reification scheme = Reification.fromName(positional.get(0));
        File dataFile = new File(positional.get(1));
        String query = new String(Files.readAllBytes(Paths.get(positional.get(2))), StandardCharsets.UTF_8);
        String endpoint = positional.size() == 4 ? positional.get(3) : null;
        String dataPath = positional.get(1);
        RDFFormat fmt = dataPath.endsWith(".ttls") ? RDFFormat.TURTLESTAR
                      : dataPath.endsWith(".nq")   ? RDFFormat.NQUADS      // named-graph reification (quads)
                      : dataPath.endsWith(".trig") ? RDFFormat.TRIG
                      : RDFFormat.TURTLE;
        // Per-engine configuration via environment (script-friendly; GraphDB defaults unchanged when unset).
        // Profiles for Fuseki/Oxigraph/QLever/MillenniumDB/Virtuoso/Stardog live in reference/engines/.
        String updateEndpointEnv = System.getenv("CIRCUIT_UPDATE_ENDPOINT");   // override; else <endpoint>/statements
        boolean skipLoad = "1".equals(System.getenv("CIRCUIT_SKIP_LOAD"));     // data pre-loaded via bulk import
        boolean readOnly = "1".equals(System.getenv("CIRCUIT_READONLY"));      // engine exposes query only, no UPDATE
        if (readOnly) skipLoad = true;                                         // read-only implies data pre-loaded
        // Optional: persist the finished circuit into a per-run NAMED GRAPH (content-addressed by the query
        // text) instead of leaving it only client-side. Keeps the circuit isolated from the base data
        // (data stays in the default graph) and makes cleanup a SAFE per-run CLEAR GRAPH -- never the blanket
        // con.remove() that could delete a content-addressed gate shared with another circuit. Opt-in:
        // default behavior (no env) is byte-for-byte unchanged.
        String circuitGraphEnv = System.getenv("CIRCUIT_GRAPH");               // explicit IRI override, else auto
        boolean persistGraph = "1".equals(System.getenv("CIRCUIT_PERSIST"))
                || (circuitGraphEnv != null && !circuitGraphEnv.isEmpty());
        IRI runGraph = persistGraph
                ? SimpleValueFactory.getInstance().createIRI(
                    (circuitGraphEnv != null && !circuitGraphEnv.isEmpty())
                        ? circuitGraphEnv : "urn:circuit:run:" + sha256hex(query))
                : null;

        CircuitRewriter rw = new CircuitRewriter(scheme, constructionMode, UUID.randomUUID().toString());
        CircuitRewriter.PathQuery pathq = rw.pathQuery(query);
        CircuitConstructionPlan constructionPlan = pathq == null ? rw.constructionPlan(query) : null;

        if (readOnly && constructionPlan != null && constructionPlan.requiresFeedback()) {
            System.err.println("# ERROR: factored BGP construction needs a WRITABLE endpoint for its private "
                    + "message-relation passes. Re-run with --construction=flat on this read-only engine.");
            System.exit(3);
            return;
        }

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
                buildPathCircuit(con, pathq, circuit);          // client-driven iterative path fixpoint (below)
            } else {
                System.err.println("# ---- construction mode: requested="
                        + constructionPlan.requestedMode().cliName() + ", effective="
                        + constructionPlan.effectiveMode().cliName() + " ----");
                if (constructionPlan.fallbackReason() != null) {
                    System.err.println("# ---- explicit fallback: " + constructionPlan.fallbackReason() + " ----");
                }
                executeConstructionPlan(con, constructionPlan, circuit, true);
            }
            Rio.write(circuit, System.out, RDFFormat.NTRIPLES);
            System.err.println("# circuit triples: " + circuit.size());
            if (persistGraph && endpoint != null && runGraph != null) {
                con.add(circuit, runGraph);        // materialize the circuit as its own named graph
                System.err.println("# persisted " + circuit.size()
                        + " circuit triples into named graph <" + runGraph + ">");
            }
            // Opt-in hygiene for a SCRATCH endpoint used as a one-run workspace: remove THIS run's gates.
            // Best-effort; the loaded data (urn:base:) is untouched (circuit holds only urn:g:*/urn:circuit:*).
            // ⚠ NOT safe when the endpoint is a SHARED / long-lived circuit store: gate IRIs are
            // content-addressed, so a Times/answer gate here may be byte-identical to (and relied on by)
            // another query's persisted circuit — con.remove() would delete the shared triples. For a
            // persistent multi-circuit store use per-run named graphs or reference counting, not this flag;
            // and do not enable it alongside concurrent runs that still need this run's reach state.
            if (endpoint != null && "1".equals(System.getenv("CIRCUIT_CLEANUP"))) {
                try {
                    if (runGraph != null) {
                        con.clear(runGraph);       // SAFE: drop only THIS run's named graph, never shared gates
                        System.err.println("# CIRCUIT_CLEANUP: dropped named graph <" + runGraph + ">");
                    } else {
                        con.remove(circuit);       // legacy default-graph cleanup (see the warning above)
                        System.err.println("# CIRCUIT_CLEANUP: removed " + circuit.size() + " gate triples from the endpoint");
                    }
                } catch (RuntimeException e) {
                    System.err.println("# CIRCUIT_CLEANUP failed (non-fatal, circuit already emitted): " + e.getMessage());
                }
            }
        }
        repo.shutDown();
    }

    /** Deterministic hex SHA-256, used to name the per-run circuit graph {@code urn:circuit:run:<hash>}. */
    private static String sha256hex(String s) {
        try {
            byte[] d = java.security.MessageDigest.getInstance("SHA-256")
                       .digest(s.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(64);
            for (byte b : d) sb.append(Character.forDigit((b >> 4) & 0xF, 16)).append(Character.forDigit(b & 0xF, 16));
            return sb.toString();
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new RuntimeException(e);
        }
    }

    /**
     * Execute a non-path plan. Only private, session-scoped {@code urn:sc:*}
     * message triples are fed back; circuit triples are accumulated in memory.
     * The private workspace is removed in a finally block on success or failure.
     */
    static void executeConstructionPlan(RepositoryConnection con, CircuitConstructionPlan plan,
                                        Model circuit, boolean logQueries) {
        Model workspace = new LinkedHashModel();
        try {
            if (logQueries) {
                System.err.println("# ---- circuit construction plan: " + plan.steps().size()
                        + " CONSTRUCT(s) ----");
            }
            for (int i = 0; i < plan.steps().size(); i++) {
                CircuitConstructionPlan.Step step = plan.steps().get(i);
                if (logQueries) {
                    // Keep this exact header stable: paper harnesses parse it as a machine boundary.
                    System.err.println("# --- step " + (i + 1) + " ---");
                    System.err.println(step.query());
                    // Trailing SPARQL comment keeps each regex-delimited chunk starting at PREFIX.
                    System.err.println("# step label: " + step.label());
                }
                Model emitted;
                try (GraphQueryResult result = con.prepareGraphQuery(step.query()).evaluate()) {
                    emitted = QueryResults.asModel(result);
                }
                Model messages = new LinkedHashModel();
                for (Statement statement : emitted) {
                    if (statement.getPredicate().stringValue().startsWith(FactoredBgpRewriter.META_NS)) {
                        messages.add(statement);
                    } else {
                        circuit.add(statement);
                    }
                }
                if (step.feedback() && !messages.isEmpty()) {
                    // Register the intended cleanup set *before* the remote
                    // write.  A server may commit an ADD and then drop the
                    // response; recording afterwards would leak that session's
                    // rows when con.add() reports the transport failure.
                    workspace.addAll(messages);
                    con.add(messages);
                }
            }
        } finally {
            if (!workspace.isEmpty()) {
                try {
                    con.remove(workspace);
                } catch (RuntimeException cleanupFailure) {
                    System.err.println("# WARNING: could not remove the private factored workspace ("
                            + workspace.size() + " triples): " + cleanupFailure.getMessage());
                }
            }
        }
    }

    /** Build a property-path circuit on `con` via the client-driven iterative fixpoint: bound the reach
     *  loop by |V_s|-1 rounds (V_s = the source's reachable subgraph, discovered live), INIT, loop STEP
     *  feeding each round back so the next CONSTRUCT can match it, then PROJECT. Reused by main() and by
     *  the PathIsoSeq harness, which runs two path queries on ONE shared connection to prove the per-path
     *  fingerprint prevents cross-query contamination on a real persistent store. */
    static void buildPathCircuit(RepositoryConnection con, CircuitRewriter.PathQuery pathq, Model circuit) {
        // property paths: CLIENT-DRIVEN ITERATIVE fixpoint with an EXACT reachable-set round bound. A
        // simple path in the reachable subgraph has <= |V_s|-1 edges, so |V_s|-1 rounds capture every
        // simple path -> exact provenance -- while |V_s| << the global node count keeps it feasible.
        int cap;
        if (pathq.boundSource()) {
            // G1: discover the source's REACHABLE subgraph by a read-only client BFS, then restrict the
            // base relation to edges FROM reachable nodes (in pathq.init) -- never materialize the
            // all-pairs base (the OOM at KG scale); provenance stays exact (all simple paths in V_s).
            java.util.Set<String> reach = new java.util.LinkedHashSet<>();
            java.util.Set<String> frontier = new java.util.LinkedHashSet<>();
            reach.add(pathq.sourceValue()); frontier.add(pathq.sourceValue());
            while (!frontier.isEmpty()) {
                java.util.Set<String> next = new java.util.LinkedHashSet<>();
                try (TupleQueryResult r = con.prepareTupleQuery(pathq.frontierStepQuery(frontier)).evaluate()) {
                    while (r.hasNext()) {
                        org.eclipse.rdf4j.model.Value v = r.next().getValue(pathq.frontierValueBinding());
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
                nGlobal = ((Literal) r.next().getValue(pathq.nodeCountBinding())).intValue();
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
