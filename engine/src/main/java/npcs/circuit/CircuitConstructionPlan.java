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

        public Step(String query, boolean feedback, String label) {
            this.query = query;
            this.feedback = feedback;
            this.label = label;
        }

        public String query() { return query; }
        public boolean feedback() { return feedback; }
        public String label() { return label; }
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
        for (Step step : steps) out.add(step.query());
        return Collections.unmodifiableList(out);
    }
}
