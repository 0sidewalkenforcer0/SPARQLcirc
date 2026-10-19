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

    /**
     * Wrap every {@code SHA256(...)} call of a generated query in {@code LCASE(...)}. SPARQL 1.1
     * recommends lower-case hex digits for its hash functions but does not require them, so the
     * plan lower-cases every digest and a gate receives the same name on every engine. The rewrite
     * is idempotent, and string literals are copied unchanged.
     */
    static String lowercaseDigests(String sparql) {
        if (sparql == null || sparql.indexOf("SHA256(") < 0) return sparql;
        return wrapDigests(sparql);
    }

    private static String wrapDigests(String s) {
        StringBuilder out = new StringBuilder(s.length() + 64);
        int i = 0;
        while (i < s.length()) {
            char c = s.charAt(i);
            if (c == '"' || c == '\'') {
                int j = endOfString(s, i);
                out.append(s, i, j);
                i = j;
            } else if (s.startsWith("SHA256(", i) && !identifierCharBefore(s, i)) {
                int open = i + "SHA256".length();
                int close = matchingParen(s, open);
                if (close < 0) {
                    out.append(s, i, s.length());
                    break;
                }
                String inner = wrapDigests(s.substring(open + 1, close));
                if (endsWithLcaseOpen(out)) {
                    out.append("SHA256(").append(inner).append(')');
                } else {
                    out.append("LCASE(SHA256(").append(inner).append("))");
                }
                i = close + 1;
            } else {
                out.append(c);
                i++;
            }
        }
        return out.toString();
    }

    private static int endOfString(String s, int start) {
        char quote = s.charAt(start);
        int j = start + 1;
        while (j < s.length()) {
            char c = s.charAt(j);
            if (c == '\\') {
                j += 2;
            } else if (c == quote) {
                return j + 1;
            } else {
                j++;
            }
        }
        return s.length();
    }

    private static int matchingParen(String s, int open) {
        int depth = 0;
        int j = open;
        while (j < s.length()) {
            char c = s.charAt(j);
            if (c == '"' || c == '\'') {
                j = endOfString(s, j);
                continue;
            }
            if (c == '(') depth++;
            if (c == ')') {
                depth--;
                if (depth == 0) return j;
            }
            j++;
        }
        return -1;
    }

    private static boolean identifierCharBefore(String s, int i) {
        if (i == 0) return false;
        char c = s.charAt(i - 1);
        return Character.isLetterOrDigit(c) || c == '_';
    }

    private static boolean endsWithLcaseOpen(StringBuilder out) {
        int k = out.length() - 1;
        while (k >= 0 && Character.isWhitespace(out.charAt(k))) k--;
        return k >= 5 && out.substring(k - 5, k + 1).equals("LCASE(");
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
