package npcs.circuit;

import java.io.BufferedWriter;
import java.io.File;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicLong;
import java.util.UUID;

import org.eclipse.rdf4j.model.BNode;
import org.eclipse.rdf4j.model.Resource;
import org.eclipse.rdf4j.model.Triple;
import org.eclipse.rdf4j.model.Value;
import org.eclipse.rdf4j.model.ValueFactory;
import org.eclipse.rdf4j.model.IRI;
import org.eclipse.rdf4j.model.Literal;
import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.model.Statement;
import org.eclipse.rdf4j.model.impl.LinkedHashModel;
import org.eclipse.rdf4j.model.impl.SimpleValueFactory;
import org.eclipse.rdf4j.query.GraphQueryResult;
import org.eclipse.rdf4j.query.TupleQueryResult;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.http.HTTPRepository;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.repository.sparql.SPARQLRepository;
import org.eclipse.rdf4j.rio.RDFFormat;
import org.eclipse.rdf4j.rio.ntriples.NTriplesUtil;
import org.eclipse.rdf4j.sail.memory.MemoryStore;

import npcs.rewrite.Reification;
import npcs.Utf8Text;

/**
 * Engine-native circuit construction: emit the CONSTRUCT (CircuitRewriter), run
 * it on an unmodified SPARQL engine (an in-memory RDF4J store here) over reified
 * probabilistic data, and print the materialized provenance circuit as N-Triples.
 *
 * <pre>
 *   java -cp target/npcs-rewrite.jar npcs.circuit.CircuitRun \
 *        --construction=factorised Standard data.reified.ttl query.sparql
 * </pre>
 * The emitted CONSTRUCT is printed to stderr; the circuit RDF goes to stdout.
 */
public final class CircuitRun {
    static final String RDF4J_MAXIMUM_URL_LENGTH_PROPERTY = "rdf4j.sparql.url.maxlength";

    private static final String STAGE_SCHEMA = "sparqlcirc-c-stage-v1";
    private static final String STAGE_PREFIX = "# sc-stage ";
    private static final boolean STRUCTURED_TIMING =
            "1".equals(System.getenv("CIRCUIT_STRUCTURED_TIMING"));
    private static final AtomicLong STRUCTURED_TIMING_EMIT_NANOS = new AtomicLong();

    private static double milliseconds(long nanos) {
        return nanos / 1_000_000.0;
    }

    private static long elapsedNanos(long started) {
        return System.nanoTime() - started;
    }

    /** Emit one self-contained JSON record without adding a JSON dependency to the fat JAR. */
    private static void stage(String event, Object... fields) {
        if (!STRUCTURED_TIMING) return;
        long started = System.nanoTime();
        if ((fields.length & 1) != 0) {
            throw new IllegalArgumentException("structured timing fields must be key/value pairs");
        }
        StringBuilder out = new StringBuilder(256);
        out.append('{');
        appendJsonField(out, "schema", STAGE_SCHEMA);
        out.append(',');
        appendJsonField(out, "event", event);
        for (int i = 0; i < fields.length; i += 2) {
            out.append(',');
            appendJsonField(out, String.valueOf(fields[i]), fields[i + 1]);
        }
        out.append('}');
        System.err.println(STAGE_PREFIX + out);
        STRUCTURED_TIMING_EMIT_NANOS.addAndGet(elapsedNanos(started));
    }

    private static void appendJsonField(StringBuilder out, String key, Object value) {
        appendJsonString(out, key);
        out.append(':');
        if (value == null) {
            out.append("null");
        } else if (value instanceof Boolean || value instanceof Integer
                || value instanceof Long) {
            out.append(value);
        } else if (value instanceof Float || value instanceof Double) {
            double number = ((Number) value).doubleValue();
            if (!Double.isFinite(number)) throw new IllegalArgumentException("non-finite metric");
            out.append(String.format(Locale.ROOT, "%.6f", number));
        } else if (value instanceof Iterable<?>) {
            out.append('[');
            boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) out.append(',');
                appendJsonString(out, String.valueOf(item));
                first = false;
            }
            out.append(']');
        } else {
            appendJsonString(out, String.valueOf(value));
        }
    }

    private static void appendJsonString(StringBuilder out, String value) {
        out.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format(Locale.ROOT, "\\u%04x", (int) c));
                    else out.append(c);
            }
        }
        out.append('"');
    }

    private static int batches(int statements) {
        return statements == 0 ? 0 : (statements + FEEDBACK_BATCH - 1) / FEEDBACK_BATCH;
    }

    public static void main(String[] args) throws Exception {
        long mainStartNanos = System.nanoTime();
        ConstructionMode constructionMode = ConstructionMode.FACTORISED;
        // Default 1 = sequential, so an existing invocation behaves exactly as before. Parallelism does
        // not change the circuit (it is a set union of the steps' triples) but it does change how much
        // load the plan puts on the engine at once, and it changes construction_ms -- so it is opt-in
        // rather than something that silently makes the published timings incomparable.
        int parallelism = envParallelism();
        List<String> positional = new ArrayList<>();
        for (int i = 0; i < args.length; i++) {
            if (args[i].startsWith("--construction=")) {
                constructionMode = ConstructionMode.fromCli(
                        args[i].substring("--construction=".length()));
            } else if ("--construction".equals(args[i])) {
                if (++i >= args.length) {
                    throw new IllegalArgumentException(
                            "--construction requires factorised or flat");
                }
                constructionMode = ConstructionMode.fromCli(args[i]);
            } else if (args[i].startsWith("--parallelism=")) {
                parallelism = parseParallelism(args[i].substring("--parallelism=".length()));
            } else if ("--parallelism".equals(args[i])) {
                if (++i >= args.length) {
                    throw new IllegalArgumentException("--parallelism requires a positive integer");
                }
                parallelism = parseParallelism(args[i]);
            } else {
                positional.add(args[i]);
            }
        }
        if (positional.size() != 3 && positional.size() != 4) {
            System.err.println("Usage: CircuitRun [--construction=factorised|flat] [--parallelism=N] "
                    + "<Standard|SPARQL_Star|SPARQL_Star_Row> <dataFile> <queryFile> [sparqlEndpointURL]");
            System.err.println("  Default construction: factorised (optimized variable elimination).");
            System.err.println("  flat is the one-product-per-derivation ablation and read-only-endpoint route.");
            System.err.println("  With an endpoint URL, the circuit is built on that engine (e.g. GraphDB) instead");
            System.err.println("  of in-memory RDF4J -- the SAME standard-SPARQL-1.1 CONSTRUCTs, so the circuit is");
            System.err.println("  byte-identical across engines. Factorised BGPs and property paths need a WRITABLE");
            System.err.println("  endpoint; --construction=flat runs non-path queries read-only. Per-engine env:");
            System.err.println("    CIRCUIT_UPDATE_ENDPOINT=<url>  (default: query endpoint; override for");
            System.err.println("                                    Fuseki/Oxigraph /update or other deployments)");
            System.err.println("    CIRCUIT_ENDPOINT_PROTOCOL=<sparql|rdf4j>  (rdf4j for GraphDB/RDF4J Server)");
            System.err.println("    CIRCUIT_SKIP_LOAD=1            (data already bulk-loaded on the engine)");
            System.err.println("    CIRCUIT_READONLY=1            (engine has no SPARQL UPDATE: QLever/MillenniumDB)");
            System.err.println("    CIRCUIT_PARALLELISM=N          (same as --parallelism=N; default 1 = sequential)");
            System.err.println("    CIRCUIT_EXACT_LEVELS=0         (property paths: restore the pre-2026-08-17 carry,");
            System.err.println("                                    which republished each reach level into the next)");
            System.err.println("  --parallelism=N runs a plan's INDEPENDENT steps concurrently, N at a time. The");
            System.err.println("  circuit is byte-identical either way (it is the set union of the steps' triples);");
            System.err.println("  only construction_ms changes, so published timings stay comparable at the default.");
            System.err.println("  It pays on flat operator plans (UNION/MINUS/OPTIONAL, whose CONSTRUCTs are all");
            System.err.println("  independent readers) and barely at all on factorised plans, which are mostly writes.");
            System.err.println("  See reference/engines/ for a profile per engine.");
            System.exit(2);
            return;
        }
        Reification scheme = Reification.fromName(positional.get(0));
        File dataFile = new File(positional.get(1));
        long queryReadStarted = System.nanoTime();
        String query = Utf8Text.read(Paths.get(positional.get(2)));
        stage("query_read",
                "duration_ms", milliseconds(elapsedNanos(queryReadStarted)),
                "query_bytes", query.getBytes(StandardCharsets.UTF_8).length);
        String endpoint = positional.size() == 4 ? positional.get(3) : null;
        String dataPath = positional.get(1);
        RDFFormat fmt = dataPath.endsWith(".ttls") ? RDFFormat.TURTLESTAR
                      : dataPath.endsWith(".nq")   ? RDFFormat.NQUADS      // named-graph reification (quads)
                      : dataPath.endsWith(".trig") ? RDFFormat.TRIG
                      : RDFFormat.TURTLE;
        // Per-engine configuration via environment (script-friendly; GraphDB defaults unchanged when unset).
        // Profiles for Fuseki/Oxigraph/QLever/MillenniumDB/Virtuoso/Stardog live in reference/engines/.
        String updateEndpointEnv = System.getenv("CIRCUIT_UPDATE_ENDPOINT");   // override; else query endpoint
        String endpointProtocol = remoteEndpointProtocol(
                System.getenv("CIRCUIT_ENDPOINT_PROTOCOL"));
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

        long planStarted = System.nanoTime();
        CircuitRewriter rw = new CircuitRewriter(
                scheme, constructionMode, UUID.randomUUID().toString());
        CircuitRewriter.PathQuery pathq = rw.pathQuery(query);
        CircuitConstructionPlan constructionPlan = pathq == null ? rw.constructionPlan(query) : null;
        boolean endpointFeedbackWritten = pathq != null
                || (constructionPlan != null && constructionPlan.requiresFeedback());
        boolean endpointCleanupRequested = endpoint != null
                && "1".equals(System.getenv("CIRCUIT_CLEANUP"));
        boolean containsPropertyPath = pathq != null
                || (constructionPlan != null && constructionPlan.containsPathStep());
        boolean forcedPost = configureRemoteQueryTransport(endpoint != null, containsPropertyPath);
        long planNanos = elapsedNanos(planStarted);
        stage("plan_generation",
                "duration_ms", milliseconds(planNanos),
                "plan_kind", pathq == null ? "construction-plan" : "property-path-dedicated",
                "requested_mode", pathq == null
                        ? constructionPlan.requestedMode().cliName() : "property-path-dedicated",
                "effective_mode", pathq == null
                        ? constructionPlan.effectiveMode().cliName() : "property-path-dedicated",
                "strategy_fragments", pathq == null
                        ? constructionPlan.strategyFragments()
                        : java.util.Collections.singletonList("property-path-dedicated"),
                "fallback_reason", pathq == null
                        ? constructionPlan.fallbackReason() : null,
                "query_transport", forcedPost ? "post" : "rdf4j-auto",
                "plan_steps", pathq == null ? constructionPlan.steps().size() : 0,
                "requires_feedback", endpointFeedbackWritten);

        if (readOnly && constructionPlan != null && constructionPlan.requiresFeedback()) {
            System.err.println("# ERROR: factorised BGP construction needs a WRITABLE endpoint for its private "
                    + "message-relation passes. Re-run with --construction=flat on this read-only engine.");
            System.exit(3);
            return;
        }

        long repositoryStarted = System.nanoTime();
        Repository repo;
        if (endpoint != null) {
            // RDF4J Server and GraphDB expose the RDF4J HTTP protocol, including transaction,
            // statement, and update routes. Generic engines expose only SPARQL Protocol endpoints.
            // Keep this choice explicit so a GraphDB repository URL is never mistaken for a generic
            // form-encoded SPARQL Update endpoint (and vice versa).
            if ("rdf4j".equals(endpointProtocol)) {
                if (updateEndpointEnv != null && !updateEndpointEnv.isEmpty()) {
                    System.err.println("# CIRCUIT_UPDATE_ENDPOINT ignored with rdf4j endpoint protocol");
                }
                repo = new HTTPRepository(endpoint);
                System.err.println("# building through RDF4J HTTP repository protocol: " + endpoint);
            } else {
                SPARQLRepository sparql;
                if (readOnly) {
                    sparql = new SPARQLRepository(endpoint);           // query-only; no UPDATE is ever issued
                    System.err.println("# building on remote READ-ONLY endpoint (non-path only): " + endpoint);
                } else {
                    String updateEndpoint = resolveUpdateEndpoint(endpoint, updateEndpointEnv);
                    sparql = new SPARQLRepository(endpoint, updateEndpoint);
                    System.err.println("# building on remote SPARQL endpoint: " + endpoint
                            + "  (update: " + updateEndpoint + ")");
                }
                repo = sparql;
            }
            repo.init();
        } else {
            repo = new SailRepository(new MemoryStore());
        }
        stage("repository_init",
                "duration_ms", milliseconds(elapsedNanos(repositoryStarted)),
                "remote", endpoint != null,
                "read_only", readOnly,
                "remote_protocol", endpoint == null ? "embedded" : endpointProtocol,
                "query_transport", forcedPost ? "post" : "rdf4j-auto");
        try {
            try (RepositoryConnection con = repo.getConnection()) {
            long dataReadyStarted = System.nanoTime();
            if (skipLoad) {
                System.err.println("# CIRCUIT_SKIP_LOAD: assuming the (reified) data is already loaded on the engine");
                // §4.2 assumes the client skolemized before loading. On this route it did the loading,
                // so the engine cannot know; ask once. A blank node left in the store makes gate keys
                // depend on a label the store invented -- and on RDF4J, STR(?bnode) is a type error
                // that leaves the answer gate unbound and drops the answer with no diagnostic at all.
                if (!"1".equals(System.getenv("CIRCUIT_SKIP_BNODE_CHECK"))
                        && npcs.rewrite.Skolem.graphHasBlankNodes(
                                con, scheme.isSparqlStar())) {
                    System.err.println("# ERROR: the loaded graph still contains blank nodes. Gate keys "
                        + "hash STR(?term), which has no stable value for a blank node, so the circuit "
                        + "would depend on labels this store invented -- and answers binding one are "
                        + "dropped silently. Skolemize before loading:\n"
                        + "#   java -cp npcs-rewrite.jar npcs.rewrite.Skolem in.ttl out.nt\n"
                        + "# (CIRCUIT_SKIP_BNODE_CHECK=1 skips this probe on a store where the ASK is "
                        + "too expensive and you know the data is ground.)");
                    System.exit(3);
                    return;
                }
            } else try {
                // §4.2: "Before loading either endpoint, the client applies one injective
                // skolemization map sk : B_G -> I". Here the engine IS the client doing the loading.
                npcs.rewrite.Skolem.load(con, dataFile, "urn:base:", fmt);
            } catch (RuntimeException e) {
                if (endpoint != null) {
                    System.err.println("# ERROR: could not write data to the endpoint (needs a WRITABLE repo, or set "
                        + "CIRCUIT_SKIP_LOAD=1 if the data is already loaded): " + e.getMessage());
                }
                throw e;
            }
            stage("data_ready",
                    "duration_ms", milliseconds(elapsedNanos(dataReadyStarted)),
                    "mode", skipLoad ? "preloaded" : "loaded-by-circuit-run",
                    "blank_node_probe", skipLoad
                            && !"1".equals(System.getenv("CIRCUIT_SKIP_BNODE_CHECK")));
            Model circuit = new org.eclipse.rdf4j.model.impl.LinkedHashModel();
            // A whole property path keeps only its private two-triple reach rows on the endpoint.
            // The complete circuit remains client-side and is never fed back merely to drive a round.
            Model pathWorkspace = pathq == null ? null : new LinkedHashModel();
            long constructionStartNanos = System.nanoTime();   // on-engine plan execution only (excludes JVM start + data load)
            long constructionMetricEmitBaseline = STRUCTURED_TIMING_EMIT_NANOS.get();
            if (pathq != null && readOnly) {
                System.err.println("# ERROR: property-path queries need a WRITABLE endpoint -- the iterative protocol "
                    + "INSERTs each round's reach gates back so the next CONSTRUCT can match them. This engine is "
                    + "read-only (CIRCUIT_READONLY=1). Run non-path queries here; use a writable engine "
                    + "(Fuseki/Oxigraph/GraphDB) for paths. (A future read-only route could inline the prior "
                    + "round's gates via VALUES instead of INSERT.)");
                System.exit(3);
                return;
            }
            if (pathq != null) {
                buildPathCircuit(con, pathq, circuit, pathWorkspace); // client-driven iterative path fixpoint (below)
            } else {
                System.err.println("# ---- construction mode: requested="
                        + constructionPlan.requestedMode().cliName() + ", effective="
                        + constructionPlan.effectiveMode().cliName() + " ----");
                if (constructionPlan.fallbackReason() != null) {
                    System.err.println("# ---- explicit fallback: " + constructionPlan.fallbackReason() + " ----");
                }
                if (parallelism > 1) {
                    System.err.println("# ---- step parallelism: up to " + parallelism
                            + " independent CONSTRUCTs concurrently ----");
                }
                executeConstructionPlan(con, repo, constructionPlan, circuit, true, parallelism);
            }
            Model preNormalizedCircuit = null;
            // Property-path rounds feed a private reach relation into the endpoint; factored rounds
            // feed their pre-normalization message rows. Retain that exact removal set before folding
            // client-side unary Plus gates; removing only the normalized circuit would leak state.
            if (endpointCleanupRequested && endpointFeedbackWritten) {
                preNormalizedCircuit = pathWorkspace == null
                        ? new LinkedHashModel(circuit) : new LinkedHashModel(pathWorkspace);
            }
            long normalizationStarted = System.nanoTime();
            CircuitNormalizer.Result normalized = CircuitNormalizer.normalize(circuit);
            long normalizationNanos = elapsedNanos(normalizationStarted);
            stage("normalization",
                    "duration_ms", milliseconds(normalizationNanos),
                    "input_triples", normalized.originalTriples,
                    "output_triples", circuit.size(),
                    "collapsed_unary_plus", normalized.collapsedUnaryPlus,
                    "omitted_types", normalized.omittedTypes);
            System.err.println("# ---- circuit encoding: native_ids=128bit, direct_bindings=true, "
                    + "inferred_types=true; final_triples=" + normalized.originalTriples + " -> "
                    + circuit.size() + ", collapsed_unary_plus=" + normalized.collapsedUnaryPlus
                    + ", omitted_types=" + normalized.omittedTypes + " ----");
            // Uniform construction-time basis for flat vs factored: wall time of plan execution plus
            // final normalization. JVM startup and data load remain
            // outside this window. Parsed by the D2 harness (reference/rdfstar_factored.py).
            long constructionMs = (System.nanoTime() - constructionStartNanos) / 1_000_000L;
            System.err.println("# construction_ms: " + constructionMs);
            stage("construction_complete",
                    "duration_ms", milliseconds(elapsedNanos(constructionStartNanos)),
                    "reported_construction_ms", constructionMs,
                    "structured_log_emit_ms", milliseconds(
                            STRUCTURED_TIMING_EMIT_NANOS.get() - constructionMetricEmitBaseline),
                    "circuit_triples", circuit.size());
            long serializationStarted = System.nanoTime();
            writeCircuit(circuit, System.out);
            System.out.flush();
            stage("serialization",
                    "duration_ms", milliseconds(elapsedNanos(serializationStarted)),
                    "circuit_triples", circuit.size(),
                    "format", "N-Triples");
            System.err.println("# circuit triples: " + circuit.size());
            long namedGraphStarted = System.nanoTime();
            if (persistGraph && endpoint != null && runGraph != null) {
                con.add(circuit, runGraph);        // materialize the circuit as its own named graph
                System.err.println("# persisted " + circuit.size()
                        + " circuit triples into named graph <" + runGraph + ">");
            }
            stage("named_graph_persist",
                    "duration_ms", milliseconds(elapsedNanos(namedGraphStarted)),
                    "enabled", persistGraph && endpoint != null && runGraph != null,
                    "triples", persistGraph && endpoint != null && runGraph != null
                            ? circuit.size() : 0);
            // Opt-in hygiene for a SCRATCH endpoint used as a one-run workspace: remove THIS run's gates.
            // Best-effort; the loaded data (urn:base:) is untouched (circuit holds only urn:g:*/urn:circuit:*).
            // ⚠ NOT safe when the endpoint is a SHARED / long-lived circuit store: gate IRIs are
            // content-addressed, so a Times/answer gate here may be byte-identical to (and relied on by)
            // another query's persisted circuit — con.remove() would delete the shared triples. For a
            // persistent multi-circuit store use per-run named graphs or reference counting, not this flag;
            // and do not enable it alongside concurrent runs that still need this run's reach state.
            long endpointCleanupStarted = System.nanoTime();
            String endpointCleanupMode = "disabled";
            boolean endpointCleanupOk = true;
            int endpointCleanupTriples = 0;
            int endpointCleanupAttempts = 0;
            boolean endpointCleanupReconnected = false;
            String endpointCleanupFirstError = null;
            boolean endpointCleanupRequired = needsEndpointCleanup(
                    endpointCleanupRequested, persistGraph, endpointFeedbackWritten);
            if (endpointCleanupRequested && !endpointCleanupRequired) {
                endpointCleanupMode = "not-required";
                System.err.println("# CIRCUIT_CLEANUP: skipped; this plan wrote no endpoint state");
            } else if (endpointCleanupRequired) {
                if (runGraph != null) {
                    endpointCleanupMode = endpointFeedbackWritten
                            ? "named-graph-and-workspace" : "named-graph";
                    endpointCleanupTriples = preNormalizedCircuit == null
                            ? 0 : preNormalizedCircuit.size();
                } else {
                    endpointCleanupMode = "default-graph-workspace";
                    Model removal = preNormalizedCircuit == null ? circuit : preNormalizedCircuit;
                    endpointCleanupTriples = removal.size();
                }
                try {
                    endpointCleanupAttempts++;
                    cleanupEndpointState(con, runGraph, endpointFeedbackWritten,
                            preNormalizedCircuit, circuit);
                } catch (RuntimeException firstFailure) {
                    endpointCleanupFirstError = firstFailure.getMessage();
                    endpointCleanupReconnected = true;
                    System.err.println("# CIRCUIT_CLEANUP: first attempt failed; retrying on a fresh "
                            + "repository connection: " + endpointCleanupFirstError);
                    try (RepositoryConnection retryConnection = repo.getConnection()) {
                        endpointCleanupAttempts++;
                        cleanupEndpointState(retryConnection, runGraph, endpointFeedbackWritten,
                                preNormalizedCircuit, circuit);
                    } catch (RuntimeException retryFailure) {
                        endpointCleanupOk = false;
                        System.err.println("# CIRCUIT_CLEANUP failed after reconnect (non-fatal, circuit "
                                + "already emitted): " + retryFailure.getMessage());
                    }
                }
                if (endpointCleanupOk) {
                    System.err.println("# CIRCUIT_CLEANUP: removed " + endpointCleanupTriples
                            + " private workspace triples"
                            + (runGraph == null ? "" : " and dropped named graph <" + runGraph + ">"));
                }
            }
            stage("endpoint_cleanup",
                    "duration_ms", milliseconds(elapsedNanos(endpointCleanupStarted)),
                    "mode", endpointCleanupMode,
                    "triples", endpointCleanupTriples,
                    "attempts", endpointCleanupAttempts,
                    "reconnected", endpointCleanupReconnected,
                    "first_error", endpointCleanupFirstError,
                    "success", endpointCleanupOk);
            stage("run_complete",
                    "duration_ms", milliseconds(elapsedNanos(mainStartNanos)),
                    "construction_ms", constructionMs,
                    "circuit_triples", circuit.size(),
                    "structured_log_emit_ms", milliseconds(STRUCTURED_TIMING_EMIT_NANOS.get()));
            }
        } finally {
            repo.shutDown();
        }
    }

    static String resolveUpdateEndpoint(String queryEndpoint, String override) {
        if (override != null && !override.isEmpty()) return override;
        return queryEndpoint.replaceAll("/+$", "");
    }

    static String remoteEndpointProtocol(String value) {
        if (value == null || value.isEmpty()) return "sparql";
        String normalized = value.toLowerCase(Locale.ROOT);
        if ("sparql".equals(normalized) || "rdf4j".equals(normalized)) return normalized;
        throw new IllegalArgumentException(
                "CIRCUIT_ENDPOINT_PROTOCOL must be sparql or rdf4j: " + value);
    }

    static boolean needsEndpointCleanup(
            boolean cleanupRequested, boolean persistGraph, boolean endpointFeedbackWritten) {
        return cleanupRequested && (persistGraph || endpointFeedbackWritten);
    }

    /** Delete only state written by this invocation. Retrying this operation is safe and idempotent. */
    private static void cleanupEndpointState(
            RepositoryConnection con, IRI runGraph, boolean endpointFeedbackWritten,
            Model workspace, Model circuit) {
        if (runGraph != null) {
            con.clear(runGraph);                 // a per-run graph, never a shared circuit graph
            if (endpointFeedbackWritten && workspace != null) removeBatched(con, workspace);
            return;
        }
        removeBatched(con, workspace == null ? circuit : workspace);
    }

    /** Write a byte-stable N-Triples document independently of endpoint result order. */
    static void writeCircuit(Model circuit, OutputStream output) {
        List<String> lines = new ArrayList<>(circuit.size());
        for (Statement statement : circuit) lines.add(ntriplesLine(statement));
        java.util.Collections.sort(lines);
        try {
            BufferedWriter writer = new BufferedWriter(
                    new OutputStreamWriter(output, StandardCharsets.UTF_8));
            for (String line : lines) {
                writer.write(line);
                writer.write('\n');
            }
            writer.flush();
        } catch (java.io.IOException e) {
            throw new IllegalStateException("could not serialize circuit as N-Triples", e);
        }
    }

    private static String ntriplesLine(Statement statement) {
        return NTriplesUtil.toNTriplesString(statement.getSubject()) + " "
                + NTriplesUtil.toNTriplesString(statement.getPredicate()) + " "
                + NTriplesUtil.toNTriplesString(statement.getObject()) + " .";
    }

    private static int envParallelism() {
        String value = System.getenv("CIRCUIT_PARALLELISM");
        return value == null || value.isEmpty() ? 1 : parseParallelism(value);
    }

    /**
     * RDF4J 4.2.1 chooses GET or POST from the system property below when it creates each
     * {@code SPARQLProtocolSession}. Property-path construction can put hundreds of thousands of
     * values into a frontier query; allowing that query to become a GET makes GraphDB reject the
     * request header before SPARQL evaluation starts. Keep the existing transport for non-path
     * construction, but force every connection created by a remote whole-path or composite-path run
     * to use POST.
     */
    static boolean configureRemoteQueryTransport(boolean remote, boolean propertyPath) {
        if (!remote || !propertyPath) return false;
        System.setProperty(RDF4J_MAXIMUM_URL_LENGTH_PROPERTY, "0");
        return true;
    }

    private static int parseParallelism(String value) {
        int n;
        try {
            n = Integer.parseInt(value.trim());
        } catch (NumberFormatException notANumber) {
            throw new IllegalArgumentException("--parallelism expects a positive integer, got: " + value);
        }
        if (n < 1) throw new IllegalArgumentException("--parallelism must be at least 1, got: " + n);
        return n;
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
     * Execute a non-path plan sequentially. Only private, session-scoped {@code urn:sc:*}
     * message triples are fed back; circuit triples are accumulated in memory.
     * The private workspace is removed in a finally block on success or failure.
     */
    static void executeConstructionPlan(RepositoryConnection con, CircuitConstructionPlan plan,
                                        Model circuit, boolean logQueries) {
        executeConstructionPlan(con, null, plan, circuit, logQueries, 1);
    }

    /**
     * Execute a non-path plan, optionally running independent steps concurrently.
     *
     * <p><b>What makes a step independent.</b> A step whose {@link CircuitConstructionPlan.Step#feedback()}
     * is false writes NOTHING to the store — every triple it emits is a circuit triple — so with respect
     * to the engine it is a pure reader. And it needs nothing from its siblings: the ⊕/⊖ gates it
     * references are computed from the binding by {@code BIND(IRI(CONCAT(..., SHA256(...))))} rather than
     * looked up, so two steps that meet at a gate only ever <em>agree on its name</em>. That is what
     * content addressing buys, and it is why a MINUS plan's four CONSTRUCTs can all be in flight at once
     * even though one of them builds the ⊖ over gates the others produce.
     *
     * <p><b>The schedule.</b> Writers (factored passes, materialized operand rows, closure atoms) are
     * barriers: each runs alone on {@code con}, in plan order. Every maximal run of consecutive readers
     * between two barriers runs concurrently. That needs no dependency analysis to be sound — a reader
     * cannot affect any other step, and all earlier writers have completed — and it is what actually
     * pays: a flat operator plan is all readers (measured step-count bounds 3x for a UNION of three, 4x
     * for a MINUS, 10x for OPTIONAL-then-MINUS), while a factored plan is nearly all writers and barely
     * moves. Scheduling a factored plan needs the real dependency DAG, which {@code feedback()} does not
     * carry; that is deliberately not attempted here.
     *
     * <p><b>Why the circuit is unchanged.</b> The result is the set union of the steps' triples, so it
     * does not depend on the order they arrive in. Byte identity is preserved and tested.
     *
     * @param repo source of a fresh connection per concurrent task ({@code con} is not thread-safe, and a
     *     connection opened after a barrier is guaranteed to see what that barrier committed). Null runs
     *     everything on {@code con}.
     * @param parallelism maximum concurrent readers; 1 keeps the sequential path exactly as it was.
     */
    static void executeConstructionPlan(RepositoryConnection con, Repository repo,
                                        CircuitConstructionPlan plan, Model circuit,
                                        boolean logQueries, int parallelism) {
        Model workspace = new LinkedHashModel();
        ExecutorService pool = null;
        try {
            List<CircuitConstructionPlan.Step> steps = plan.steps();
            if (logQueries) {
                System.err.println("# ---- circuit construction plan: " + steps.size()
                        + " CONSTRUCT(s) ----");
            }
            if (parallelism <= 1 || repo == null) {
                // The default. Plan order, one connection, no scheduling at all -- byte-for-byte the
                // behaviour this had before any of it existed. Running the level schedule here instead
                // would be correct but would REORDER the steps (level order is not plan order), and the
                // "# --- step N ---" headers are a machine boundary the paper harnesses parse.
                for (int j = 0; j < steps.size(); j++) {
                    runStep(con, steps.get(j), j, circuit, workspace, logQueries, -1);
                }
                return;
            }
            int[] level = scheduleLevels(steps);
            int levels = 0;
            for (int value : level) levels = Math.max(levels, value + 1);
            if (logQueries) {
                // Concurrently, a level's steps overlap and levels do not follow plan order, so the
                // per-step chunks are emitted here, ONCE, in plan order. The execution loop below then
                // announces level boundaries only.
                for (int j = 0; j < steps.size(); j++) logStep(steps.get(j), j);
            }
            for (int current = 0; current < levels; current++) {
                List<Integer> group = new ArrayList<>();
                for (int j = 0; j < steps.size(); j++) if (level[j] == current) group.add(j);
                if (group.isEmpty()) continue;
                if (group.size() == 1) {
                    runStep(con, steps.get(group.get(0)), group.get(0), circuit, workspace,
                            false, current);
                } else {
                    if (pool == null) {
                        // Sized from the WIDEST level in the whole plan, not from this one: sizing it
                        // here would pin the pool to the first wide level and starve a later, wider one.
                        pool = Executors.newFixedThreadPool(Math.min(parallelism, widestLevel(level)));
                    }
                    runLevelConcurrently(repo, steps, group, circuit, workspace, logQueries, pool,
                            parallelism, current);
                }
            }
        } finally {
            // shutdown(), not shutdownNow(): every level awaits its futures, so by here the workers are
            // idle and interrupting them only produces spurious "interrupted" noise from the store.
            if (pool != null) pool.shutdown();
            long cleanupStarted = System.nanoTime();
            int cleanupTriples = workspace.size();
            boolean cleanupOk = true;
            if (!workspace.isEmpty()) {
                try {
                    removeBatched(con, workspace);
                } catch (RuntimeException cleanupFailure) {
                    cleanupOk = false;
                    System.err.println("# WARNING: could not remove the private factored workspace ("
                            + workspace.size() + " triples): " + cleanupFailure.getMessage());
                }
            }
            stage("workspace_cleanup",
                    "duration_ms", milliseconds(elapsedNanos(cleanupStarted)),
                    "triples", cleanupTriples,
                    "batches", batches(cleanupTriples),
                    "success", cleanupOk);
        }
    }

    /**
     * Assign each step the earliest level it can run at, given what it reads and writes.
     *
     * <p>Step j depends on an earlier step i when j reads a relation i writes (so i must have filled it),
     * or when both write the same relation (two hash-consed operands can produce the same rows; letting
     * them race adds nothing and is easier to rule out than to reason about). {@code level[j]} is one past
     * the deepest such i, so every step in one level is independent of every other and the levels run in
     * order.
     *
     * <p>A step that does NOT declare its dependencies is a full barrier in both directions: it lands
     * after everything before it, and everything after it lands after it. That is the conservative
     * reading of "unknown", and it is what keeps a closure atom's fixpoint — which drives its own loop on
     * the caller's connection — from ever overlapping anything.
     */
    private static int[] scheduleLevels(List<CircuitConstructionPlan.Step> steps) {
        int[] level = new int[steps.size()];
        int afterLastBarrier = 0;
        for (int j = 0; j < steps.size(); j++) {
            CircuitConstructionPlan.Step step = steps.get(j);
            int earliest = afterLastBarrier;
            if (step.dependenciesDeclared()) {
                for (int i = 0; i < j; i++) {
                    if (!java.util.Collections.disjoint(step.reads(), steps.get(i).writes())
                            || !java.util.Collections.disjoint(step.writes(), steps.get(i).writes())) {
                        earliest = Math.max(earliest, level[i] + 1);
                    }
                }
                level[j] = earliest;
            } else {
                for (int i = 0; i < j; i++) earliest = Math.max(earliest, level[i] + 1);
                level[j] = earliest;
                afterLastBarrier = earliest + 1;      // nothing later may share or precede this level
            }
        }
        return level;
    }

    /** The most steps any one level holds: the most the schedule can ever overlap. */
    private static int widestLevel(int[] level) {
        int[] count = new int[level.length + 1];
        int widest = 1;
        for (int value : level) widest = Math.max(widest, ++count[value]);
        return widest;
    }

    private static final class StepEvaluation {
        final Model gates;
        final Model messages;
        final int emittedTriples;
        final long queryNanos;
        final long splitNanos;

        StepEvaluation(Model gates, Model messages, int emittedTriples,
                       long queryNanos, long splitNanos) {
            this.gates = gates;
            this.messages = messages;
            this.emittedTriples = emittedTriples;
            this.queryNanos = queryNanos;
            this.splitNanos = splitNanos;
        }
    }

    private static final class StepOutcome {
        final CircuitConstructionPlan.Step step;
        final int index;
        final StepEvaluation evaluation;
        final long workspaceRegisterNanos;
        final long feedbackNanos;
        final long executionNanos;

        StepOutcome(CircuitConstructionPlan.Step step, int index,
                    StepEvaluation evaluation, long workspaceRegisterNanos,
                    long feedbackNanos, long executionNanos) {
            this.step = step;
            this.index = index;
            this.evaluation = evaluation;
            this.workspaceRegisterNanos = workspaceRegisterNanos;
            this.feedbackNanos = feedbackNanos;
            this.executionNanos = executionNanos;
        }
    }

    private static StepOutcome executePlainStep(RepositoryConnection con,
                                                CircuitConstructionPlan.Step step,
                                                int index, Model workspace) {
        long stepStarted = System.nanoTime();
        StepEvaluation evaluation = evaluate(con, step);
        long workspaceRegisterNanos = 0;
        long feedbackNanos = 0;
        if (!evaluation.messages.isEmpty()) {
            // Register before the remote write so cleanup remains possible after an uncertain write.
            long registerStarted = System.nanoTime();
            synchronized (workspace) { workspace.addAll(evaluation.messages); }
            workspaceRegisterNanos = elapsedNanos(registerStarted);
            long feedbackStarted = System.nanoTime();
            addBatched(con, evaluation.messages);
            feedbackNanos = elapsedNanos(feedbackStarted);
        }
        return new StepOutcome(step, index, evaluation, workspaceRegisterNanos,
                feedbackNanos, elapsedNanos(stepStarted));
    }

    private static void mergeAndRecordStep(StepOutcome outcome, Model circuit,
                                           Model workspace, boolean parallel,
                                           int scheduleLevel) {
        long mergeStarted = System.nanoTime();
        circuit.addAll(outcome.evaluation.gates);
        long mergeNanos = elapsedNanos(mergeStarted);
        stage("construct_step",
                "step_index", outcome.index + 1,
                "label", outcome.step.label(),
                "parallel", parallel,
                "schedule_level", scheduleLevel,
                "feedback_declared", outcome.step.feedback(),
                "dependencies_declared", outcome.step.dependenciesDeclared(),
                "read_relation_count", outcome.step.reads().size(),
                "write_relation_count", outcome.step.writes().size(),
                "query_ms", milliseconds(outcome.evaluation.queryNanos),
                "split_ms", milliseconds(outcome.evaluation.splitNanos),
                "workspace_register_ms", milliseconds(outcome.workspaceRegisterNanos),
                "feedback_ms", milliseconds(outcome.feedbackNanos),
                "merge_ms", milliseconds(mergeNanos),
                "step_wall_ms", milliseconds(outcome.executionNanos + mergeNanos),
                "emitted_triples", outcome.evaluation.emittedTriples,
                "gate_triples", outcome.evaluation.gates.size(),
                "message_triples", outcome.evaluation.messages.size(),
                "feedback_batches", batches(outcome.evaluation.messages.size()),
                "circuit_triples_after", circuit.size(),
                "workspace_triples_after", workspace.size());
    }

    /** One step, on the given connection: log it, run it, split its output, feed back its rows. */
    private static void runStep(RepositoryConnection con, CircuitConstructionPlan.Step step, int index,
                                Model circuit, Model workspace, boolean logQueries,
                                int scheduleLevel) {
        if (logQueries) logStep(step, index);
        if (step.path() != null) {
            // A closure atom: not one CONSTRUCT but a data-dependent fixpoint. Its gates go to the
            // circuit; its urn:sc: rows are workspace, fed back so the enclosing operators can read
            // them and removed with the rest of the workspace afterwards.
            int circuitBefore = circuit.size();
            int workspaceBefore = workspace.size();
            long stepStarted = System.nanoTime();
            buildPathCircuit(con, step.path(), circuit, workspace);
            stage("closure_step",
                    "step_index", index + 1,
                    "label", step.label(),
                    "schedule_level", scheduleLevel,
                    "dependencies_declared", step.dependenciesDeclared(),
                    "read_relation_count", step.reads().size(),
                    "write_relation_count", step.writes().size(),
                    "step_wall_ms", milliseconds(elapsedNanos(stepStarted)),
                    "circuit_triples_added", circuit.size() - circuitBefore,
                    "workspace_triples_added", workspace.size() - workspaceBefore,
                    "circuit_triples_after", circuit.size(),
                    "workspace_triples_after", workspace.size());
            return;
        }
        mergeAndRecordStep(executePlainStep(con, step, index, workspace),
                circuit, workspace, false, scheduleLevel);
    }

    /**
     * Run one CONSTRUCT and split its output: circuit triples are returned, {@code urn:sc:} rows are
     * collected into {@code messages}.
     *
     * <p>A step that declares no feedback but emits rows anyway is an error, not something to drop
     * quietly. The concurrent schedule's soundness rests on "no feedback implies no write", so the
     * assumption is enforced here rather than trusted; the sequential path used to discard such rows
     * silently, which would have been a wrong circuit with no diagnostic.
     */
    private static StepEvaluation evaluate(RepositoryConnection con,
                                           CircuitConstructionPlan.Step step) {
        long queryStarted = System.nanoTime();
        Model gates = new LinkedHashModel();
        Model messages = new LinkedHashModel();
        Map<Value, Value> cache = new HashMap<>();
        int emittedTriples = 0;
        try (GraphQueryResult result = con.prepareGraphQuery(step.query()).evaluate()) {
            while (result.hasNext()) {
                Statement statement = result.next();
                emittedTriples++;
                if (statement.getPredicate().stringValue().startsWith(FactoredBgpRewriter.META_NS)) {
                    messages.add(statement);
                } else {
                    addCanonical(gates, statement, cache);
                }
            }
        }
        long queryNanos = elapsedNanos(queryStarted);
        if (!messages.isEmpty() && !step.feedback()) {
            throw new IllegalStateException("step '" + step.label() + "' emitted " + messages.size()
                + " private " + FactoredBgpRewriter.META_NS + " rows but declares no feedback, so they "
                + "would be dropped and any step reading that relation would see an incomplete one");
        }
        return new StepEvaluation(gates, messages, emittedTriples, queryNanos, 0);
    }

    /**
     * Add one circuit statement with every term rebuilt by a single value factory.
     *
     * <p>A CONSTRUCT result mixes terms OWNED BY THE STORE ({@code MemIRI}, {@code MemLiteral}) with
     * terms the query MINTS ({@code SimpleIRI}, {@code SimpleLiteral}), and which of the two a given
     * term comes back as depends on timing. {@code LinkedHashModel}'s indexed {@code contains} then
     * misses a statement that is equal as RDF, stores the same gate triple twice, and the emitted
     * N-Triples carries a duplicate line — on a circuit whose byte-identity across engines is a
     * published claim.
     *
     * <p>Sequentially this was a rare flake (measured at roughly 1 in 10 by
     * {@code CircuitRewriterTest.canonicalStatements}, which is why that helper compares triple TEXT and
     * not models). Running steps concurrently made it reproducible: a right-nested MINUS emitted 64
     * lines instead of 62 on about half of its runs, the two extra being an exact duplicate of a ⊗ gate
     * two marginals both derive. Rebuilding every term through one factory makes the deduplication
     * exact, so the accumulated circuit is a true set again in both schedules.
     */
    private static void addCanonical(Model circuit, Statement statement, Map<Value, Value> cache) {
        Resource subject = (Resource) canonicalTerm(statement.getSubject(), cache);
        IRI predicate = (IRI) canonicalTerm(statement.getPredicate(), cache);
        Value object = canonicalTerm(statement.getObject(), cache);
        Resource context = statement.getContext() == null
                ? null : (Resource) canonicalTerm(statement.getContext(), cache);
        if (context == null) {
            circuit.add(subject, predicate, object);
        } else {
            circuit.add(subject, predicate, object, context);
        }
    }

    /**
     * @param cache memo for one step's output. A step repeats the same terms constantly — {@code c:in},
     *     {@code rdf:type}, {@code c:Times}, and every gate IRI it fans out from — so rebuilding each one
     *     once per occurrence measurably slowed construction. Keyed by the ORIGINAL term, which is what
     *     makes it work across implementations: {@code MemIRI.equals(SimpleIRI)} is true by string value,
     *     so a store-owned term hits the entry a minted one created. Per call, hence confined to one
     *     thread and bounded by that step's distinct terms.
     */
    private static Value canonicalTerm(Value term, Map<Value, Value> cache) {
        Value hit = cache.get(term);
        if (hit != null) return hit;
        ValueFactory vf = SimpleValueFactory.getInstance();
        Value canonical;
        if (term instanceof IRI) {
            canonical = vf.createIRI(term.stringValue());
        } else if (term instanceof BNode) {
            canonical = vf.createBNode(((BNode) term).getID());
        } else if (term instanceof Literal) {
            Literal literal = (Literal) term;
            canonical = literal.getLanguage().isPresent()
                    ? vf.createLiteral(literal.getLabel(), literal.getLanguage().get())
                    : vf.createLiteral(literal.getLabel(), literal.getDatatype());
        } else if (term instanceof Triple) {                // RDF-star quoted triple
            Triple triple = (Triple) term;
            canonical = vf.createTriple((Resource) canonicalTerm(triple.getSubject(), cache),
                    (IRI) canonicalTerm(triple.getPredicate(), cache),
                    canonicalTerm(triple.getObject(), cache));
        } else {
            canonical = term;
        }
        cache.put(term, canonical);
        return canonical;
    }

    private static void logStep(CircuitConstructionPlan.Step step, int index) {
        // Keep this exact header stable: paper harnesses parse it as a machine boundary.
        System.err.println("# --- step " + (index + 1) + " ---");
        System.err.println(step.query());
        // Trailing SPARQL comment keeps each regex-delimited chunk starting at PREFIX.
        System.err.println("# step label: " + step.label());
    }

    /**
     * Run one DAG level concurrently, each step on its own connection, merging their triples in plan
     * order once they have all finished.
     *
     * <p>Unlike the reader-only case this level may contain WRITERS -- the factored passes, which are most
     * of a factored plan. Two writers in one level never target the same relation ({@code scheduleLevels}
     * adds a write-write edge if they would), so they cannot corrupt each other; each writes through its
     * own connection, and the level barrier is what makes the next level see it.
     *
     * <p>A writer registers its rows in the shared workspace BEFORE writing them, under a lock. That
     * ordering is the same one the sequential path keeps and for the same reason: a server may commit an
     * ADD and then drop the response, and rows recorded afterwards would leak when the add reports a
     * transport failure. The lock is needed because {@code Model} is not thread-safe.
     *
     * <p>The step text is logged up front, sequentially, so the {@code # --- step N ---} boundaries stay
     * in plan order for the harnesses that parse them even though the queries then overlap.
     */
    private static void runLevelConcurrently(Repository repo,
                                             List<CircuitConstructionPlan.Step> steps,
                                             List<Integer> group, Model circuit, Model workspace,
                                             boolean logQueries, ExecutorService pool,
                                             int parallelism, int scheduleLevel) {
        if (logQueries) {
            StringBuilder which = new StringBuilder();
            for (int j : group) which.append(which.length() == 0 ? "" : ", ").append(j + 1);
            System.err.println("# ---- steps " + which + " are independent; running up to "
                    + Math.min(parallelism, group.size()) + " concurrently ----");
        }
        List<Future<StepOutcome>> pending = new ArrayList<>(group.size());
        for (int j : group) {
            int stepIndex = j;
            CircuitConstructionPlan.Step step = steps.get(j);
            pending.add(pool.submit(() -> {
                // A connection per task: RepositoryConnection is not thread-safe, and opening it here
                // means it sees everything the preceding level committed.
                try (RepositoryConnection own = repo.getConnection()) {
                    return executePlainStep(own, step, stepIndex, workspace);
                }
            }));
        }
        RuntimeException failure = null;
        List<StepOutcome> results = new ArrayList<>(pending.size());
        for (Future<StepOutcome> future : pending) {
            try {
                results.add(future.get());
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                results.add(null);
                if (failure == null) failure = new IllegalStateException("interrupted while building the "
                        + "circuit concurrently", interrupted);
            } catch (ExecutionException executionFailure) {
                results.add(null);
                Throwable cause = executionFailure.getCause();
                // Every task is awaited before rethrowing: a half-finished level must not race with the
                // workspace cleanup the caller runs next.
                if (failure == null) {
                    failure = cause instanceof RuntimeException ? (RuntimeException) cause
                            : new IllegalStateException("concurrent construction step failed", cause);
                }
            }
        }
        if (failure != null) throw failure;
        for (StepOutcome result : results) {
            mergeAndRecordStep(result, circuit, workspace, true, scheduleLevel);
        }
    }

    /** Maximum number of RDF terms placed in one property-path {@code VALUES} clause. GraphDB accepts
     *  the 5,000-value requests while a 59,274-value frontier is rejected by its form parser as a POST
     *  with a missing {@code query} parameter. The split is semantic-preserving set union. */
    static final int PATH_VALUES_BATCH = 5000;

    /** Batch size for feedback INSERT/DELETE: a single SPARQL UPDATE carrying a whole factored message
     *  or path-round relation (which can reach hundreds of thousands of triples on high-fan-out sources)
     *  overflows the remote's request buffer and broken-pipes. Chunking keeps each UPDATE within engine limits. */
    private static final int FEEDBACK_BATCH = 5000;

    private static void addBatched(RepositoryConnection con, Model m) {
        java.util.List<Statement> all = new java.util.ArrayList<>(m);
        for (int i = 0; i < all.size(); i += FEEDBACK_BATCH) {
            con.add(all.subList(i, Math.min(i + FEEDBACK_BATCH, all.size())));
        }
    }

    private static void removeBatched(RepositoryConnection con, Model m) {
        java.util.List<Statement> all = new java.util.ArrayList<>(m);
        for (int i = 0; i < all.size(); i += FEEDBACK_BATCH) {
            con.remove(all.subList(i, Math.min(i + FEEDBACK_BATCH, all.size())));
        }
    }

    /** Build a property-path circuit on `con` via the client-driven iterative fixpoint: bound the reach
     *  loop by |V_s|-1 rounds (V_s = the source's reachable subgraph, discovered live), INIT, loop STEP
     *  feeding each round back so the next CONSTRUCT can match it, then PROJECT. Reused by main() and by
     *  the PathIsoSeq harness, which runs two path queries on ONE shared connection to prove the per-path
     *  fingerprint prevents cross-query contamination on a real persistent store. */
    static void buildPathCircuit(RepositoryConnection con, CircuitRewriter.PathQuery pathq, Model circuit) {
        buildPathCircuit(con, pathq, circuit, new LinkedHashModel());
    }

    /**
     * @param workspace private {@code urn:sc:} rows used by later plan steps and the two-triple
     *     property-path reach relation are collected here instead of into the public circuit, so the
     *     caller can feed them back and clean them up.
     */
    static void buildPathCircuit(RepositoryConnection con, CircuitRewriter.PathQuery pathq,
                                 Model circuit, Model workspace) {
        if (pathq.sourceValuesQuery() == null) {
            buildFromOneSource(con, pathq, circuit, workspace, 1);
            return;
        }
        // §3's bound-source condition: a preceding operand binds the atom's source, so read I_C's
        // source column and replay the construction once per value, as Def. 4.7 clause 2 says. Every
        // run is then single-source and confined to its own reachable subgraph -- which is what
        // Thm. 4.11's O(n(n+|E_s|)) bounds -- and the all-pairs base is never built.
        java.util.List<String> sources = new java.util.ArrayList<>();
        long sourceValuesStarted = System.nanoTime();
        try (TupleQueryResult r = con.prepareTupleQuery(pathq.sourceValuesQuery()).evaluate()) {
            while (r.hasNext()) {
                org.eclipse.rdf4j.model.Value value = r.next().getValue(pathq.sourceValuesBinding());
                if (value instanceof IRI && !sources.contains(value.stringValue())) {
                    sources.add(value.stringValue());
                }
            }
        }
        stage("path_source_values",
                "duration_ms", milliseconds(elapsedNanos(sourceValuesStarted)),
                "source_count", sources.size());
        System.err.println("# ---- bound-source closure atom: |I_C| = " + sources.size() + " source(s) ----");
        for (int sourceIndex = 0; sourceIndex < sources.size(); sourceIndex++) {
            String source = sources.get(sourceIndex);
            pathq.pinSource(source);
            buildFromOneSource(con, pathq, circuit, workspace, sourceIndex + 1);
        }
    }

    /** One source: discover its reachable subgraph, run the level-indexed fixpoint, then finish. */
    private static void buildFromOneSource(RepositoryConnection con, CircuitRewriter.PathQuery pathq,
                                           Model circuit, Model workspace, int sourceIndex) {
        long sourceStarted = System.nanoTime();
        // property paths: CLIENT-DRIVEN ITERATIVE fixpoint with an EXACT reachable-set round bound. A
        // simple path in the reachable subgraph has <= |V_s|-1 edges, so |V_s|-1 rounds capture every
        // simple path -> exact provenance -- while |V_s| << the global node count keeps it feasible.
        int cap;
        if (pathq.boundSource()) {
            // G1: discover the source's REACHABLE subgraph by a read-only client BFS, then restrict the
            // base relation to edges FROM reachable nodes (in pathq.init) -- never materialize the
            // all-pairs base (the OOM at KG scale); provenance stays exact (all simple paths in V_s).
            java.util.Set<Value> reach = new java.util.LinkedHashSet<>();
            java.util.Set<Value> frontier = new java.util.LinkedHashSet<>();
            Value source = SimpleValueFactory.getInstance().createIRI(pathq.sourceValue());
            reach.add(source); frontier.add(source);
            int bfsRound = 0;
            while (!frontier.isEmpty()) {
                int frontierSize = frontier.size();
                long bfsStarted = System.nanoTime();
                java.util.Set<Value> next = new java.util.LinkedHashSet<>();
                List<String> frontierQueries = pathq.frontierStepQueries(
                        frontier, PATH_VALUES_BATCH);
                int maxQueryBytes = 0;
                for (String frontierQuery : frontierQueries) {
                    maxQueryBytes = Math.max(maxQueryBytes,
                            frontierQuery.getBytes(StandardCharsets.UTF_8).length);
                    try (TupleQueryResult r = con.prepareTupleQuery(frontierQuery).evaluate()) {
                        while (r.hasNext()) {
                            org.eclipse.rdf4j.model.Value v = r.next().getValue(
                                    pathq.frontierValueBinding());
                            if (v != null && reach.add(v)) next.add(v);
                        }
                    }
                }
                frontier = next;
                stage("path_bfs_round",
                        "source_index", sourceIndex,
                        "round", bfsRound++,
                        "duration_ms", milliseconds(elapsedNanos(bfsStarted)),
                        "frontier_nodes", frontierSize,
                        "frontier_queries", frontierQueries.size(),
                        "max_query_bytes", maxQueryBytes,
                        "new_nodes", next.size(),
                        "reachable_nodes", reach.size());
            }
            pathq.setReachable(reach);
            cap = Math.max(1, reach.size() - 1);       // |V_s|-1 bounds every simple path in the reachable subgraph
            System.err.println("# ---- G1 reachable-subgraph BFS: |V_s| = " + reach.size() + " ----");
        } else {
            int nGlobal;                               // variable source (all-pairs): fall back to the global bound
            long nodeCountStarted = System.nanoTime();
            try (TupleQueryResult r = con.prepareTupleQuery(pathq.nodeCountQuery()).evaluate()) {
                nGlobal = ((Literal) r.next().getValue(pathq.nodeCountBinding())).intValue();
            }
            stage("path_node_count",
                    "source_index", sourceIndex,
                    "duration_ms", milliseconds(elapsedNanos(nodeCountStarted)),
                    "nodes", nGlobal);
            cap = Math.max(1, nGlobal - 1);
        }
        java.util.Set<String> reachNodes = new java.util.HashSet<>();
        int constructIndex = 0;
        for (String c : pathq.init(PATH_VALUES_BATCH)) {
            runFeed(con, pathq, circuit, workspace, reachNodes, c, sourceIndex,
                    ++constructIndex, "init", 0);
        }
        int k = 0, lastLevel = 0;
        while (k < cap) {
            int produced = 0;
            for (String c : pathq.step(k)) {
                produced += runFeed(con, pathq, circuit, workspace, reachNodes, c, sourceIndex,
                        ++constructIndex, "round", k + 1);
            }
            // Fixpoint reached: reach^{k+1} is empty, so reach^{k+2} = reach^{k+1} ∘ base is too, and
            // every later level with it. lastLevel deliberately stays on the last NON-empty level.
            // Only ever fires under exact levels -- the cumulative form's carry republishes reach^k at
            // every level, so a round there is never empty and this signal does not exist.
            if (produced == 0) {
                System.err.println("# ---- fixpoint converged at level " + k
                    + ": round produced no gates, " + (cap - k) + " of " + cap + " rounds skipped ----");
                break;
            }
            lastLevel = ++k;
            if (k >= reachNodes.size() - 1) break;     // exact reachable-set bound |V_s|-1
        }
        for (String c : pathq.finish(lastLevel)) {
            runFeed(con, pathq, circuit, workspace, reachNodes, c, sourceIndex,
                    ++constructIndex, "finish", lastLevel);
        }
        System.err.println("# ---- property-path plan: reachable-nodes=" + reachNodes.size()
            + ", rounds=" + lastLevel + " (cap=" + cap + "), path fp=" + pathq.fingerprint() + " ----");
        System.err.println("# reach/base gates are fingerprinted (urn:g:r: + c:rpath) so distinct path "
            + "queries on a shared writable endpoint never compose with each other's persisted gates.");
        stage("path_source_complete",
                "source_index", sourceIndex,
                "duration_ms", milliseconds(elapsedNanos(sourceStarted)),
                "constructs", constructIndex,
                "reachable_nodes", reachNodes.size(),
                "rounds", lastLevel,
                "round_cap", cap);
    }

    /** Run one path-round CONSTRUCT, retain its circuit client-side, feed only the two-column private
     *  reach relation back for the next round, and record reach-gate endpoints (c:rfrom/c:rto) so the
     *  caller can bound the loop by the live reachable-set size |V_s|. */
    private static int runFeed(RepositoryConnection con, CircuitRewriter.PathQuery pathq,
                               Model circuit, Model workspace,
                               java.util.Set<String> reachNodes, String construct,
                               int sourceIndex, int constructIndex, String phase, int round) {
        long stepStarted = System.nanoTime();
        System.err.println("# --- path CONSTRUCT ---\n" + construct);   // emit the plan (stderr)
        Model m = new org.eclipse.rdf4j.model.impl.LinkedHashModel();
        long queryStarted = System.nanoTime();
        try (GraphQueryResult res = con.prepareGraphQuery(construct).evaluate()) {
            while (res.hasNext()) m.add(res.next());
        }
        long queryNanos = elapsedNanos(queryStarted);
        int circuitBefore = circuit.size();
        int workspaceBefore = workspace == null ? 0 : workspace.size();
        long splitStarted = System.nanoTime();
        Model feedback = new LinkedHashModel();
        Map<Value, Value> cache = new HashMap<>();
        for (Statement st : m) {
            if (st.getPredicate().stringValue().startsWith(FactoredBgpRewriter.META_NS)) {
                feedback.add(st);                          // enclosing-plan row, not part of the circuit
            } else {
                addCanonical(circuit, st, cache);          // one term implementation, so it dedups
            }
        }
        int feedbackDescriptors = addPathFeedbackRows(pathq, m, feedback);
        if (workspace != null) workspace.addAll(feedback);
        long splitNanos = elapsedNanos(splitStarted);
        long feedbackStarted = System.nanoTime();
        addBatched(con, feedback);
        long feedbackNanos = elapsedNanos(feedbackStarted);
        // reachable-subgraph nodes = endpoints of the reach LEVEL gates (rlvl 0,1,2,...); the base
        // relation (rlvl "base") is all-pairs over the WHOLE graph, so exclude it from the bound.
        long reachScanStarted = System.nanoTime();
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
        long reachScanNanos = elapsedNanos(reachScanStarted);
        stage("path_construct",
                "source_index", sourceIndex,
                "construct_index", constructIndex,
                "phase", phase,
                "round", round,
                "query_ms", milliseconds(queryNanos),
                "split_ms", milliseconds(splitNanos),
                "feedback_ms", milliseconds(feedbackNanos),
                "reach_scan_ms", milliseconds(reachScanNanos),
                "step_wall_ms", milliseconds(elapsedNanos(stepStarted)),
                "emitted_triples", m.size(),
                "feedback_triples", feedback.size(),
                "feedback_descriptor_triples", feedbackDescriptors,
                "feedback_batches", batches(feedback.size()),
                "circuit_triples_added", circuit.size() - circuitBefore,
                "workspace_triples_added", workspace == null
                        ? 0 : workspace.size() - workspaceBefore,
                "circuit_triples_after", circuit.size(),
                "reachable_nodes_after", reachNodes.size());
        return m.size();
    }

    /**
     * Derive the endpoint's private reach relation from the circuit metadata already returned by the
     * CONSTRUCT. One reach gate becomes exactly two rows (from/to); its type, level, fingerprint and
     * circuit edges remain client-side. A fixed, per-workspace descriptor records the two dynamic
     * predicates for each level before any gate rows are written. It lets crash recovery find those
     * rows through an indexed predicate instead of scanning the full store.
     */
    private static int addPathFeedbackRows(CircuitRewriter.PathQuery pathq, Model emitted,
                                           Model feedback) {
        String levelPredicate = "urn:circuit:rlvl";
        String fromPredicate = "urn:circuit:rfrom";
        String toPredicate = "urn:circuit:rto";
        Map<Resource, String> levels = new HashMap<>();
        Map<Resource, Value> from = new HashMap<>();
        Map<Resource, Value> to = new HashMap<>();
        for (Statement statement : emitted) {
            String predicate = statement.getPredicate().stringValue();
            if (predicate.equals(levelPredicate)) {
                levels.put(statement.getSubject(), statement.getObject().stringValue());
            } else if (predicate.equals(fromPredicate)) {
                from.put(statement.getSubject(), statement.getObject());
            } else if (predicate.equals(toPredicate)) {
                to.put(statement.getSubject(), statement.getObject());
            }
        }
        ValueFactory values = SimpleValueFactory.getInstance();
        IRI marker = values.createIRI(CircuitRewriter.PathQuery.FEEDBACK_PREDICATE_MARKER);
        Resource workspace = values.createIRI(pathq.feedbackWorkspaceIri());
        java.util.Set<IRI> feedbackPredicates = new java.util.LinkedHashSet<>();
        for (String level : levels.values()) {
            feedbackPredicates.add(values.createIRI(pathq.feedbackFromPredicate(level)));
            feedbackPredicates.add(values.createIRI(pathq.feedbackToPredicate(level)));
        }
        // LinkedHashModel preserves insertion order: descriptor rows enter the first write batch.
        for (IRI predicate : feedbackPredicates) feedback.add(workspace, marker, predicate);
        for (Map.Entry<Resource, String> entry : levels.entrySet()) {
            Resource gate = entry.getKey();
            Value source = from.get(gate), target = to.get(gate);
            if (source == null || target == null) continue;
            feedback.add(gate, values.createIRI(pathq.feedbackFromPredicate(entry.getValue())), source);
            feedback.add(gate, values.createIRI(pathq.feedbackToPredicate(entry.getValue())), target);
        }
        return feedbackPredicates.size();
    }
}
