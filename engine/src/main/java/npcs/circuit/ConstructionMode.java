package npcs.circuit;

import java.util.Locale;

/** Physical construction strategy for non-path provenance circuits. */
public enum ConstructionMode {
    /** Multi-pass variable elimination for pure BGPs; unsupported operators fall back to {@link #FLAT}. */
    FACTORED,

    /** One flat product per complete derivation. Retained for ablations and read-only endpoints. */
    FLAT;

    public static ConstructionMode fromCli(String value) {
        if (value == null) return FACTORED;
        switch (value.toLowerCase(Locale.ROOT)) {
            case "factored": return FACTORED;
            case "flat": return FLAT;
            default:
                throw new IllegalArgumentException(
                    "Unknown construction mode '" + value + "' (expected factored or flat).");
        }
    }

    public String cliName() {
        return name().toLowerCase(Locale.ROOT);
    }
}
