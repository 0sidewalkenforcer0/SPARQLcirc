package npcs.circuit;

import java.nio.charset.StandardCharsets;
import java.util.List;

/** Helpers for the native RDF circuit representation. */
final class CircuitEncoding {

    static final String BIND_PREFIX = "urn:circuit:bind:";

    private CircuitEncoding() {}

    /** Hash a content address and retain 128 bits in its generated IRI. */
    static String hashExpression(String expression) {
        return "SUBSTR(SHA256(" + expression + "), 1, 32)";
    }

    /** Stable predicate suffix for a projected variable in the answer encoding. */
    static String variableHex(String variable) {
        StringBuilder out = new StringBuilder();
        for (byte b : variable.getBytes(StandardCharsets.UTF_8)) {
            out.append(Character.forDigit((b >>> 4) & 0xF, 16));
            out.append(Character.forDigit(b & 0xF, 16));
        }
        return out.toString();
    }

    /** Direct-binding predicate IRI for one projected variable. */
    static String bindingPredicateIri(String variable) {
        return BIND_PREFIX + variableHex(variable);
    }

    /** Projected-variable schema carried by an answer-root triple. */
    static String answerSchema(List<String> variables) {
        StringBuilder out = new StringBuilder("\"vars:");
        for (int i = 0; i < variables.size(); i++) {
            if (i > 0) out.append(',');
            out.append(variableHex(variables.get(i)));
        }
        return out.append('\"').toString();
    }
}
