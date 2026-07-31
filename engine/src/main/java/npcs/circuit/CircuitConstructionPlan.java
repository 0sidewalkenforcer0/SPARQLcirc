package npcs.circuit;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

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

        public Step(String query, boolean feedback, String label) {
            this(query, feedback, label, null);
        }

        private Step(String query, boolean feedback, String label, CircuitRewriter.PathQuery path) {
            this.query = query;
            this.feedback = feedback;
            this.label = label;
            this.path = path;
        }

        /**
         * A closure atom as a plan step. Unlike every other step this is not one CONSTRUCT: the
         * level-indexed fixpoint runs as many rounds as the data demands, so the step carries the
         * plan and {@link CircuitRun} drives it. It ends by publishing the atom's rows —
         * Def. 4.7 clause 2's {@code reif(C, g_C)} — which the enclosing operators then read like any
         * other materialized operand.
         */
        public static Step path(CircuitRewriter.PathQuery path, String label) {
            return new Step(null, true, label, path);
        }

        public String query() { return query; }
        public boolean feedback() { return feedback; }
        public String label() { return label; }
        /** Non-null when this step is a closure atom's iterative subplan. */
        public CircuitRewriter.PathQuery path() { return path; }
    }

    private final List<Step> steps;
    private final ConstructionMode requestedMode;
    private final ConstructionMode effectiveMode;
    private final String fallbackReason;

    public CircuitConstructionPlan(List<Step> steps, ConstructionMode requestedMode,
                                   ConstructionMode effectiveMode, String fallbackReason) {
        this.steps = Collections.unmodifiableList(new ArrayList<>(steps));
        this.requestedMode = requestedMode;
        this.effectiveMode = effectiveMode;
        this.fallbackReason = fallbackReason;
    }

    public List<Step> steps() { return steps; }
    public ConstructionMode requestedMode() { return requestedMode; }
    public ConstructionMode effectiveMode() { return effectiveMode; }
    public String fallbackReason() { return fallbackReason; }

    public boolean requiresFeedback() {
        for (Step step : steps) if (step.feedback()) return true;
        return false;
    }

    /** Compatibility view for callers which only need the emitted query text. */
    public List<String> queries() {
        List<String> out = new ArrayList<>(steps.size());
        for (Step step : steps) if (step.query() != null) out.add(step.query());
        return Collections.unmodifiableList(out);
    }
}
