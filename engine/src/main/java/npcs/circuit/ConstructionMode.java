package npcs.circuit;

import java.util.Locale;

/** Physical construction strategy for non-path provenance circuits. */
public enum ConstructionMode {
    /**
     * Variable elimination with structural fallbacks, one-pass anchored base
     * construction, FILTER placement, direct single-pattern marginals, and
     * compatibility-key grouping for pure-BGP MINUS operands.
     */
    FACTORISED,

    /** One flat product per complete derivation. Retained for ablations and read-only endpoints. */
    FLAT;

    public static ConstructionMode fromCli(String value) {
        if (value == null) return FACTORISED;
        switch (value.toLowerCase(Locale.ROOT)) {
            case "factorised": return FACTORISED;
            case "flat": return FLAT;
            default:
                throw new IllegalArgumentException(
                    "Unknown construction mode '" + value
                    + "' (expected factorised or flat).");
        }
    }

    /** Whether this mode uses the multi-pass variable-elimination planner. */
    public boolean usesVariableElimination() {
        return this == FACTORISED;
    }

    /** Whether this mode enables the new semijoin and compatibility-key optimizations. */
    public boolean isOptimized() {
        return this == FACTORISED;
    }

    public String cliName() {
        return name().toLowerCase(Locale.ROOT);
    }
}
