package npcs.circuit;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Executable sequence of standard-SPARQL CONSTRUCT queries.
 *
 * <p>A factored BGP step can emit private message-relation triples which must be
 * fed back to the same engine before the next step. Circuit triples are never
 * fed back by this protocol and remain ordinary output.</p>
 */
public final class CircuitConstructionPlan {

    public static final class Step {
        private final String query;
        private final boolean feedback;
        private final String label;
        private final CircuitRewriter.PathQuery path;
        private final Set<String> reads;
        private final Set<String> writes;
        private final boolean dependenciesDeclared;

        /**
         * A step whose dependencies are NOT declared. {@link #dependenciesDeclared()} is false, and a
         * scheduler must then treat it as a barrier — it may read or write any relation.
         */
        public Step(String query, boolean feedback, String label) {
            this(query, feedback, label, null, Collections.emptySet(), Collections.emptySet(), false);
        }

        /**
         * A step that declares exactly which private message relations it consumes and produces.
         *
         * @param reads every {@code urn:sc:msg:} relation the step's WHERE matches. Must be complete: a
         *     missing entry lets the scheduler start the step before the pass that fills that relation,
         *     which silently builds the circuit from an empty or half-written one.
         * @param writes every such relation the step's CONSTRUCT template publishes.
         */
        public Step(String query, boolean feedback, String label,
                    Set<String> reads, Set<String> writes) {
            this(query, feedback, label, null, reads, writes, true);
        }

        private Step(String query, boolean feedback, String label, CircuitRewriter.PathQuery path,
                     Set<String> reads, Set<String> writes, boolean dependenciesDeclared) {
            this.query = query;
            this.feedback = feedback;
            this.label = label;
            this.path = path;
            this.reads = Collections.unmodifiableSet(new LinkedHashSet<>(reads));
            this.writes = Collections.unmodifiableSet(new LinkedHashSet<>(writes));
            this.dependenciesDeclared = dependenciesDeclared;
        }

        /**
         * A closure atom as a plan step. Unlike every other step this is not one CONSTRUCT: the
         * level-indexed fixpoint runs as many rounds as the data demands, so the step carries the
         * plan and {@link CircuitRun} drives it. It ends by publishing the atom's rows —
         * Def. 4.7 clause 2's {@code reif(C, g_C)} — which the enclosing operators then read like any
         * other materialized operand.
         */
        public static Step path(CircuitRewriter.PathQuery path, String label) {
            // Deliberately undeclared, hence a barrier: the fixpoint is not one CONSTRUCT, it feeds each
            // round's reach gates back for the next one to match, and it drives that loop on the caller's
            // connection. Scheduling it alongside anything else would need the loop itself to be
            // concurrency-safe, which it is not.
            return new Step(null, true, label, path, Collections.emptySet(), Collections.emptySet(),
                    false);
        }

        public String query() { return query; }
        public boolean feedback() { return feedback; }
        public String label() { return label; }
        /** Non-null when this step is a closure atom's iterative subplan. */
        public CircuitRewriter.PathQuery path() { return path; }
        /** Whether {@link #reads()}/{@link #writes()} are complete enough to schedule on. */
        public boolean dependenciesDeclared() { return dependenciesDeclared; }
        /** The private message relations this step's WHERE matches. */
        public Set<String> reads() { return reads; }
        /** The private message relations this step's CONSTRUCT publishes. */
        public Set<String> writes() { return writes; }
    }

    private final List<Step> steps;
    private final ConstructionMode requestedMode;
    private final ConstructionMode effectiveMode;
    private final String fallbackReason;
    private final List<String> strategyFragments;

    public CircuitConstructionPlan(List<Step> steps, ConstructionMode requestedMode,
                                   ConstructionMode effectiveMode, String fallbackReason) {
        this(steps, requestedMode, effectiveMode, fallbackReason,
                Collections.<String>emptyList());
    }

    public CircuitConstructionPlan(List<Step> steps, ConstructionMode requestedMode,
                                   ConstructionMode effectiveMode, String fallbackReason,
                                   List<String> strategyFragments) {
        this.steps = Collections.unmodifiableList(new ArrayList<>(steps));
        this.requestedMode = requestedMode;
        this.effectiveMode = effectiveMode;
        this.fallbackReason = fallbackReason;
        this.strategyFragments = Collections.unmodifiableList(
                new ArrayList<>(strategyFragments));
    }

    public List<Step> steps() { return steps; }
    public ConstructionMode requestedMode() { return requestedMode; }
    public ConstructionMode effectiveMode() { return effectiveMode; }
    public String fallbackReason() { return fallbackReason; }
    /** Ordered physical strategies selected for the query's BGP and operator fragments. */
    public List<String> strategyFragments() { return strategyFragments; }

    public boolean requiresFeedback() {
        for (Step step : steps) if (step.feedback()) return true;
        return false;
    }

    /** Whether this outer plan contains an iterative property-path operand. */
    public boolean containsPathStep() {
        for (Step step : steps) if (step.path() != null) return true;
        return false;
    }

    /** Compatibility view for callers which only need the emitted query text. */
    public List<String> queries() {
        List<String> out = new ArrayList<>(steps.size());
        for (Step step : steps) if (step.query() != null) out.add(step.query());
        return Collections.unmodifiableList(out);
    }
}
