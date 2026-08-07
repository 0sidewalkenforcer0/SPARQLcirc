package npcs.circuit;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
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
                    throw new IllegalArgumentException("--construction requires factored or flat");
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
            System.err.println("Usage: CircuitRun [--construction=factored|flat] [--parallelism=N] "
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
            System.err.println("    CIRCUIT_PARALLELISM=N          (same as --parallelism=N; default 1 = sequential)");
            System.err.println("  --parallelism=N runs a plan's INDEPENDENT steps concurrently, N at a time. The");
            System.err.println("  circuit is byte-identical either way (it is the set union of the steps' triples);");
            System.err.println("  only construction_ms changes, so published timings stay comparable at the default.");
            System.err.println("  It pays on flat operator plans (UNION/MINUS/OPTIONAL, whose CONSTRUCTs are all");
            System.err.println("  independent readers) and barely at all on factored plans, which are mostly writes.");
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
                // §4.2 assumes the client skolemized before loading. On this route it did the loading,
                // so the engine cannot know; ask once. A blank node left in the store makes gate keys
                // depend on a label the store invented -- and on RDF4J, STR(?bnode) is a type error
                // that leaves the answer gate unbound and drops the answer with no diagnostic at all.
                if (!"1".equals(System.getenv("CIRCUIT_SKIP_BNODE_CHECK"))
                        && npcs.rewrite.Skolem.graphHasBlankNodes(con)) {
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
            Model circuit = new org.eclipse.rdf4j.model.impl.LinkedHashModel();
            long constructionStartNanos = System.nanoTime();   // on-engine plan execution only (excludes JVM start + data load)
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
                if (parallelism > 1) {
                    System.err.println("# ---- step parallelism: up to " + parallelism
                            + " independent CONSTRUCTs concurrently ----");
                }
                executeConstructionPlan(con, repo, constructionPlan, circuit, true, parallelism);
            }
            // Uniform construction-time basis for flat vs factored: wall time of the on-engine plan
            // execution only (JVM startup and any data load happen outside this window). Parsed by the
            // D2 flat-vs-factored deployment-time harness (reference/rdfstar_factored.py).
            long constructionMs = (System.nanoTime() - constructionStartNanos) / 1_000_000L;
            System.err.println("# construction_ms: " + constructionMs);
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

    private static int envParallelism() {
        String value = System.getenv("CIRCUIT_PARALLELISM");
        return value == null || value.isEmpty() ? 1 : parseParallelism(value);
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
                    runStep(con, steps.get(j), j, circuit, workspace, logQueries);
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
                    runStep(con, steps.get(group.get(0)), group.get(0), circuit, workspace, false);
                } else {
                    if (pool == null) {
                        // Sized from the WIDEST level in the whole plan, not from this one: sizing it
                        // here would pin the pool to the first wide level and starve a later, wider one.
                        pool = Executors.newFixedThreadPool(Math.min(parallelism, widestLevel(level)));
                    }
                    runLevelConcurrently(repo, steps, group, circuit, workspace, logQueries, pool,
                            parallelism);
                }
            }
        } finally {
            // shutdown(), not shutdownNow(): every level awaits its futures, so by here the workers are
            // idle and interrupting them only produces spurious "interrupted" noise from the store.
            if (pool != null) pool.shutdown();
            if (!workspace.isEmpty()) {
                try {
                    removeBatched(con, workspace);
                } catch (RuntimeException cleanupFailure) {
                    System.err.println("# WARNING: could not remove the private factored workspace ("
                            + workspace.size() + " triples): " + cleanupFailure.getMessage());
                }
            }
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

    /** One step, on the given connection: log it, run it, split its output, feed back its rows. */
    private static void runStep(RepositoryConnection con, CircuitConstructionPlan.Step step, int index,
                                Model circuit, Model workspace, boolean logQueries) {
        if (logQueries) logStep(step, index);
        if (step.path() != null) {
            // A closure atom: not one CONSTRUCT but a data-dependent fixpoint. Its gates go to the
            // circuit; its urn:sc: rows are workspace, fed back so the enclosing operators can read
            // them and removed with the rest of the workspace afterwards.
            buildPathCircuit(con, step.path(), circuit, workspace);
            return;
        }
        Model messages = new LinkedHashModel();
        circuit.addAll(evaluate(con, step, messages));
        if (!messages.isEmpty()) {
            // Register the intended cleanup set *before* the remote write.  A server may commit an ADD
            // and then drop the response; recording afterwards would leak that session's rows when
            // con.add() reports the transport failure.
            workspace.addAll(messages);
            addBatched(con, messages);   // batch the UPDATE: a single huge INSERT broken-pipes on GraphDB
        }
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
    private static Model evaluate(RepositoryConnection con, CircuitConstructionPlan.Step step,
                                  Model messages) {
        Model emitted;
        try (GraphQueryResult result = con.prepareGraphQuery(step.query()).evaluate()) {
            emitted = QueryResults.asModel(result);
        }
        Model gates = new LinkedHashModel();
        Map<Value, Value> cache = new HashMap<>();
        for (Statement statement : emitted) {
            if (statement.getPredicate().stringValue().startsWith(FactoredBgpRewriter.META_NS)) {
                messages.add(statement);
            } else {
                addCanonical(gates, statement, cache);
            }
        }
        if (!messages.isEmpty() && !step.feedback()) {
            throw new IllegalStateException("step '" + step.label() + "' emitted " + messages.size()
                + " private " + FactoredBgpRewriter.META_NS + " rows but declares no feedback, so they "
                + "would be dropped and any step reading that relation would see an incomplete one");
        }
        return gates;
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
                                             int parallelism) {
        if (logQueries) {
            StringBuilder which = new StringBuilder();
            for (int j : group) which.append(which.length() == 0 ? "" : ", ").append(j + 1);
            System.err.println("# ---- steps " + which + " are independent; running up to "
                    + Math.min(parallelism, group.size()) + " concurrently ----");
        }
        List<Future<Model>> pending = new ArrayList<>(group.size());
        for (int j : group) {
            CircuitConstructionPlan.Step step = steps.get(j);
            pending.add(pool.submit(() -> {
                // A connection per task: RepositoryConnection is not thread-safe, and opening it here
                // means it sees everything the preceding level committed.
                try (RepositoryConnection own = repo.getConnection()) {
                    Model messages = new LinkedHashModel();
                    Model gates = evaluate(own, step, messages);
                    if (!messages.isEmpty()) {
                        synchronized (workspace) { workspace.addAll(messages); }
                        addBatched(own, messages);
                    }
                    return gates;
                }
            }));
        }
        RuntimeException failure = null;
        List<Model> results = new ArrayList<>(pending.size());
        for (Future<Model> future : pending) {
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
        for (Model result : results) circuit.addAll(result);
    }

    /** Batch size for feedback INSERT/DELETE: a single SPARQL UPDATE carrying the whole factored message
     *  relation (which can reach hundreds of thousands of triples on high-fan-out sources) overflows the
     *  remote's request buffer and broken-pipes. Chunking keeps each UPDATE well within engine limits. */
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
        buildPathCircuit(con, pathq, circuit, null);
    }

    /**
     * @param workspace when non-null, private {@code urn:sc:} rows (the atom's materialized
     *     {@code reif(C, g_C)}) are collected here instead of into the circuit, so the caller can
     *     feed them back and clean them up. Null for a whole-pattern path query, which emits none.
     */
    static void buildPathCircuit(RepositoryConnection con, CircuitRewriter.PathQuery pathq,
                                 Model circuit, Model workspace) {
        if (pathq.sourceValuesQuery() == null) {
            buildFromOneSource(con, pathq, circuit, workspace);
            return;
        }
        // §3's bound-source condition: a preceding operand binds the atom's source, so read I_C's
        // source column and replay the construction once per value, as Def. 4.7 clause 2 says. Every
        // run is then single-source and confined to its own reachable subgraph -- which is what
        // Thm. 4.11's O(n(n+|E_s|)) bounds -- and the all-pairs base is never built.
        java.util.List<String> sources = new java.util.ArrayList<>();
        try (TupleQueryResult r = con.prepareTupleQuery(pathq.sourceValuesQuery()).evaluate()) {
            while (r.hasNext()) {
                org.eclipse.rdf4j.model.Value value = r.next().getValue(pathq.sourceValuesBinding());
                if (value instanceof IRI && !sources.contains(value.stringValue())) {
                    sources.add(value.stringValue());
                }
            }
        }
        System.err.println("# ---- bound-source closure atom: |I_C| = " + sources.size() + " source(s) ----");
        for (String source : sources) {
            pathq.pinSource(source);
            buildFromOneSource(con, pathq, circuit, workspace);
        }
    }

    /** One source: discover its reachable subgraph, run the level-indexed fixpoint, then finish. */
    private static void buildFromOneSource(RepositoryConnection con, CircuitRewriter.PathQuery pathq,
                                           Model circuit, Model workspace) {
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
        for (String c : pathq.init()) runFeed(con, circuit, workspace, reachNodes, c);
        int k = 0, lastLevel = 0;
        while (k < cap) {
            for (String c : pathq.step(k)) runFeed(con, circuit, workspace, reachNodes, c);
            lastLevel = ++k;
            if (k >= reachNodes.size() - 1) break;     // exact reachable-set bound |V_s|-1
        }
        for (String c : pathq.finish(lastLevel)) runFeed(con, circuit, workspace, reachNodes, c);
        System.err.println("# ---- property-path plan: reachable-nodes=" + reachNodes.size()
            + ", rounds=" + lastLevel + " (cap=" + cap + "), path fp=" + pathq.fingerprint() + " ----");
        System.err.println("# reach/base gates are fingerprinted (urn:g:r: + c:rpath) so distinct path "
            + "queries on a shared writable endpoint never compose with each other's persisted gates.");
    }

    /** Run one path-round CONSTRUCT, add its triples to the accumulated circuit AND back into the
     *  store (feedback for the next round), and record any reach-gate endpoints (c:rfrom/c:rto) so the
     *  caller can bound the loop by the live reachable-set size |V_s|. */
    private static void runFeed(RepositoryConnection con, Model circuit, Model workspace,
                                java.util.Set<String> reachNodes, String construct) {
        System.err.println("# --- path CONSTRUCT ---\n" + construct);   // emit the plan (stderr)
        Model m = new org.eclipse.rdf4j.model.impl.LinkedHashModel();
        try (GraphQueryResult res = con.prepareGraphQuery(construct).evaluate()) {
            m.addAll(QueryResults.asModel(res));
        }
        Map<Value, Value> cache = new HashMap<>();
        for (Statement st : m) {
            if (workspace != null
                    && st.getPredicate().stringValue().startsWith(FactoredBgpRewriter.META_NS)) {
                workspace.add(st);                         // private row, not part of the circuit
            } else {
                addCanonical(circuit, st, cache);          // one term implementation, so it dedups
            }
        }
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
