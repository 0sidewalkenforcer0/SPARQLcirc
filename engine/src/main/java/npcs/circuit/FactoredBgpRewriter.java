package npcs.circuit;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import org.eclipse.rdf4j.query.algebra.StatementPattern;
import org.eclipse.rdf4j.query.algebra.Var;

import npcs.rewrite.Reification;
import npcs.rewrite.Terms;

/**
 * Engine-native factored construction for a pure BGP.
 *
 * <p>The planner implements the same min-scope variable-elimination policy as
 * {@code reference/factor.py}. Base relations, binary joins and marginalization
 * messages are materialized by ordinary SPARQL 1.1 CONSTRUCT queries. Private
 * {@code urn:sc:*} message triples are fed back between passes by
 * {@link CircuitRun}; the returned event circuit itself remains ordinary RDF
 * and is never used as mutable engine state.</p>
 */
final class FactoredBgpRewriter {

    static final String META_NS = "urn:sc:";
    private static final String MESSAGE = META_NS + "message";
    private static final String GATE = META_NS + "gate";
    private static final String VALUE = META_NS + "value:";
    private static final String PRE = "PREFIX c: <urn:circuit:>\n";

    private final Reification scheme;
    private final String gp;
    private final String workspaceHash;
    private final String queryFingerprint;
    private final List<CircuitConstructionPlan.Step> steps = new ArrayList<>();
    private int stepNumber;

    private FactoredBgpRewriter(Reification scheme, String generatedPrefix,
                               String workspaceId, String queryFingerprint) {
        this.scheme = scheme;
        this.gp = generatedPrefix;
        this.workspaceHash = sha256hex(workspaceId);
        this.queryFingerprint = queryFingerprint;
    }

    static CircuitConstructionPlan build(Reification scheme, String generatedPrefix,
                                         String workspaceId, List<StatementPattern> inputPatterns,
                                         List<String> outputVariables) {
        if (inputPatterns.isEmpty()) {
            throw new UnsupportedOperationException("Factored construction requires a non-empty BGP.");
        }

        List<PatternEntry> patterns = new ArrayList<>();
        for (int i = 0; i < inputPatterns.size(); i++) {
            StatementPattern pattern = inputPatterns.get(i);
            patterns.add(new PatternEntry(pattern, patternKey(pattern), i));
        }
        // BGP conjunction is commutative. Canonical physical order makes the
        // elimination plan stable under harmless parser/algebra re-association.
        patterns.sort(Comparator.comparing((PatternEntry p) -> p.key)
                .thenComparingInt(p -> p.originalPosition));

        StringBuilder semantic = new StringBuilder("FACTORED-BGP");
        for (PatternEntry pattern : patterns) semantic.append(part(pattern.key));
        semantic.append("|OUTPUT");
        for (String output : outputVariables) semantic.append(part(output));
        String fp = sha256hex(semantic.toString());

        FactoredBgpRewriter planner = new FactoredBgpRewriter(
                scheme, generatedPrefix, workspaceId, fp);
        return planner.plan(patterns, outputVariables);
    }

    private CircuitConstructionPlan plan(List<PatternEntry> patterns, List<String> outputVariables) {
        List<Relation> relations = new ArrayList<>();
        for (int i = 0; i < patterns.size(); i++) {
            PatternEntry entry = patterns.get(i);
            Relation relation = relation("base-" + i, patternVariables(entry.pattern));
            steps.add(new CircuitConstructionPlan.Step(
                    baseQuery(entry.pattern, relation, "BASE@" + queryFingerprint + "@" + i),
                    true, "base[" + i + "]"));
            relations.add(relation);
        }

        Set<String> outputs = new HashSet<>(outputVariables);
        while (true) {
            List<String> candidates = new ArrayList<>();
            for (Relation relation : relations) {
                for (String variable : relation.variables) {
                    if (!outputs.contains(variable) && !candidates.contains(variable)) {
                        candidates.add(variable);
                    }
                }
            }
            if (candidates.isEmpty()) break;
            Collections.sort(candidates);

            String eliminate = null;
            int bestCost = Integer.MAX_VALUE;
            for (String candidate : candidates) {
                LinkedHashSet<String> scope = new LinkedHashSet<>();
                for (Relation relation : relations) {
                    if (relation.variables.contains(candidate)) scope.addAll(relation.variables);
                }
                int cost = scope.size();
                if (cost < bestCost) {
                    bestCost = cost;
                    eliminate = candidate;
                }
            }

            List<Relation> involved = new ArrayList<>();
            List<Relation> rest = new ArrayList<>();
            for (Relation relation : relations) {
                (relation.variables.contains(eliminate) ? involved : rest).add(relation);
            }
            Relation joined = involved.get(0);
            for (int i = 1; i < involved.size(); i++) joined = join(joined, involved.get(i));
            Relation marginalized = marginalize(joined, eliminate);
            rest.add(marginalized);
            relations = rest;
        }

        Relation result = relations.get(0);
        for (int i = 1; i < relations.size(); i++) result = join(result, relations.get(i));
        steps.add(new CircuitConstructionPlan.Step(
                answerQuery(result, outputVariables), false, "answers"));
        return new CircuitConstructionPlan(steps, ConstructionMode.FACTORED,
                ConstructionMode.FACTORED, null);
    }

    private Relation relation(String hint, List<String> variables) {
        String semanticId = queryFingerprint + "-" + hint + "-" + stepNumber++;
        String messageIri = META_NS + "msg:" + workspaceHash + ":" + semanticId;
        return new Relation(semanticId, messageIri, variables);
    }

    private String baseQuery(StatementPattern pattern, Relation output, String gateTag) {
        String token = qv("f_token");
        String gate = qv("f_gate");
        String row = qv("f_row");
        String tokenName = token.substring(1);
        StringBuilder query = new StringBuilder(PRE);
        query.append("CONSTRUCT {\n")
             .append("  ").append(gate).append(" a c:Plus .\n")
             .append("  ").append(token).append(" c:feeds ").append(gate).append(" .\n")
             .append(rowTemplate(row, output, gate))
             .append("}\nWHERE {\n")
             .append(scheme.reify(pattern, tokenName))
             .append(bindIri(gate, "urn:g:s:", bindingKey(gateTag, output.variables)))
             .append(bindIri(row, META_NS + "row:", bindingKey(output.messageIri, output.variables)))
             .append("}\n");
        return query.toString();
    }

    private Relation join(Relation left, Relation right) {
        List<String> variables = new ArrayList<>(left.variables);
        for (String variable : right.variables) {
            if (!variables.contains(variable)) variables.add(variable);
        }
        Relation output = relation("join", variables);
        String leftRow = qv("f_left_row"), rightRow = qv("f_right_row");
        String leftGate = qv("f_left_gate"), rightGate = qv("f_right_gate");
        String product = qv("f_product"), row = qv("f_row");
        String h0 = qv("f_h0"), h1 = qv("f_h1"), lo = qv("f_lo"), hi = qv("f_hi");

        StringBuilder query = new StringBuilder(PRE);
        query.append("CONSTRUCT {\n")
             .append("  ").append(product).append(" a c:Times ; c:in ").append(leftGate)
             .append(" ; c:in ").append(rightGate).append(" .\n")
             .append(rowTemplate(row, output, product))
             .append("}\nWHERE {\n")
             .append(rowPattern(leftRow, left, leftGate))
             .append(rowPattern(rightRow, right, rightGate))
             .append("  BIND(SHA256(STR(").append(leftGate).append(")) AS ").append(h0).append(")\n")
             .append("  BIND(SHA256(STR(").append(rightGate).append(")) AS ").append(h1).append(")\n")
             .append("  BIND(IF(").append(h0).append(" <= ").append(h1).append(", ")
             .append(h0).append(", ").append(h1).append(") AS ").append(lo).append(")\n")
             .append("  BIND(IF(").append(h0).append(" <= ").append(h1).append(", ")
             .append(h1).append(", ").append(h0).append(") AS ").append(hi).append(")\n")
             .append("  BIND(IRI(CONCAT(\"urn:g:t:\", SHA256(CONCAT(\"T|\", ")
             .append(lo).append(", \"|\", ").append(hi).append(")))) AS ").append(product).append(")\n")
             .append(bindIri(row, META_NS + "row:", bindingKey(output.messageIri, output.variables)))
             .append("}\n");
        steps.add(new CircuitConstructionPlan.Step(query.toString(), true,
                "join(" + left.variables.size() + "," + right.variables.size() + ")"));
        return output;
    }

    private Relation marginalize(Relation input, String eliminate) {
        List<String> keep = new ArrayList<>();
        for (String variable : input.variables) {
            if (!variable.equals(eliminate)) keep.add(variable);
        }
        Relation output = relation("marg-" + sha256hex(eliminate).substring(0, 12), keep);
        String inputRow = qv("f_input_row"), source = qv("f_source");
        String sum = qv("f_sum"), row = qv("f_row");
        String gateTag = "MARG@" + queryFingerprint + "@" + output.semanticId;

        StringBuilder query = new StringBuilder(PRE);
        query.append("CONSTRUCT {\n")
             .append("  ").append(sum).append(" a c:Plus .\n")
             .append("  ").append(source).append(" c:feeds ").append(sum).append(" .\n")
             .append(rowTemplate(row, output, sum))
             .append("}\nWHERE {\n")
             .append(rowPattern(inputRow, input, source))
             .append(bindIri(sum, "urn:g:s:", bindingKey(gateTag, keep)))
             .append(bindIri(row, META_NS + "row:", bindingKey(output.messageIri, keep)))
             .append("}\n");
        steps.add(new CircuitConstructionPlan.Step(query.toString(), true,
                "marginalize(?" + eliminate + ")"));
        return output;
    }

    private String answerQuery(Relation input, List<String> outputVariables) {
        String inputRow = qv("f_input_row"), source = qv("f_source");
        String answer = qv("ans"), answerKey = qv("anskey");
        StringBuilder ctor = new StringBuilder();
        StringBuilder binds = new StringBuilder();
        for (String variable : outputVariables) {
            String binding = qv("b_" + variable);
            ctor.append("  ").append(answer).append(" c:binding ").append(binding).append(" . ")
                .append(binding).append(" c:var \"").append(escapeString(variable))
                .append("\" ; c:val ?").append(variable).append(" .\n");
            binds.append("  BIND(IRI(CONCAT(STR(").append(answer).append("), \"#")
                 .append(escapeString(variable)).append("\")) AS ").append(binding).append(")\n");
        }

        Set<String> present = new HashSet<>(input.variables);
        StringBuilder query = new StringBuilder(PRE);
        query.append("CONSTRUCT {\n")
             .append("  ").append(source).append(" c:feeds ").append(answer).append(" .\n")
             .append("  ").append(answer).append(" a c:Plus ; c:answer ").append(answerKey).append(" .\n")
             .append(ctor)
             .append("}\nWHERE {\n")
             .append(rowPattern(inputRow, input, source))
             .append("  BIND(").append(answerLabel(outputVariables, present)).append(" AS ")
             .append(answerKey).append(")\n")
             .append(bindIri(answer, "urn:g:a:", bindingKey("A", outputVariables)))
             .append(binds)
             .append("}\n");
        return query.toString();
    }

    private static String rowTemplate(String row, Relation relation, String gate) {
        StringBuilder out = new StringBuilder("  ").append(row)
                .append(" <").append(MESSAGE).append("> <").append(relation.messageIri)
                .append("> ; <").append(GATE).append("> ").append(gate);
        for (String variable : relation.variables) {
            out.append(" ; <").append(valuePredicate(variable)).append("> ?").append(variable);
        }
        return out.append(" .\n").toString();
    }

    private static String rowPattern(String row, Relation relation, String gate) {
        return rowTemplate(row, relation, gate);
    }

    private static String valuePredicate(String variable) {
        return VALUE + sha256hex(variable);
    }

    private static String bindingKey(String tag, List<String> variables) {
        StringBuilder out = new StringBuilder("CONCAT(\"").append(escapeString(tag)).append("\"");
        for (String variable : variables) {
            out.append(", ").append(termHash(variable, "?" + variable));
        }
        return out.append(")").toString();
    }

    private static String answerLabel(List<String> variables, Set<String> present) {
        StringBuilder out = new StringBuilder("CONCAT(\"A\"");
        for (String variable : variables) {
            out.append(", \"|").append(escapeString(variable)).append("=\", ");
            out.append(present.contains(variable)
                    ? "IF(BOUND(?" + variable + "), STR(?" + variable + "), \"NULL\")"
                    : "\"NULL\"");
        }
        return out.append(")").toString();
    }

    /** Must remain byte-for-byte compatible with CircuitRewriter's answer identity. */
    private static String termHash(String label, String term) {
        String enc =
            "IF(!BOUND(" + term + "), \"u\"," +
            " IF(isIRI(" + term + "), CONCAT(\"i\", SHA256(STR(" + term + ")))," +
            " IF(isBlank(" + term + "), CONCAT(\"b\", SHA256(STR(" + term + ")))," +
            " IF(isLiteral(" + term + "), CONCAT(\"l\", SHA256(STR(" + term + "))," +
            " SHA256(STR(DATATYPE(" + term + "))), SHA256(LCASE(LANG(" + term + "))))," +
            " \"x\"))))";
        return "SHA256(CONCAT(\"" + escapeString(label) + "=\", " + enc + "))";
    }

    private static String bindIri(String variable, String prefix, String key) {
        return "  BIND(IRI(CONCAT(\"" + prefix + "\", SHA256(" + key + "))) AS "
                + variable + ")\n";
    }

    private String qv(String hint) { return "?" + gp + hint; }

    private static List<String> patternVariables(StatementPattern pattern) {
        List<String> variables = new ArrayList<>();
        addVariable(variables, pattern.getSubjectVar());
        addVariable(variables, pattern.getPredicateVar());
        addVariable(variables, pattern.getObjectVar());
        return variables;
    }

    private static void addVariable(List<String> variables, Var variable) {
        if (variable != null && !variable.hasValue() && !variables.contains(variable.getName())) {
            variables.add(variable.getName());
        }
    }

    private static String patternKey(StatementPattern pattern) {
        return "SP" + part(variableKey(pattern.getSubjectVar()))
                + part(variableKey(pattern.getPredicateVar()))
                + part(variableKey(pattern.getObjectVar()));
    }

    private static String variableKey(Var variable) {
        if (variable == null) return "N";
        if (variable.hasValue()) return "C" + part(Terms.value(variable.getValue()));
        return (variable.isAnonymous() ? "X" : "V") + part(variable.getName());
    }

    private static String part(String value) { return value.length() + ":" + value; }

    private static String escapeString(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", "\\n").replace("\r", "\\r");
    }

    private static String sha256hex(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder out = new StringBuilder(64);
            for (byte b : digest) {
                out.append(Character.forDigit((b >> 4) & 0xf, 16));
                out.append(Character.forDigit(b & 0xf, 16));
            }
            return out.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    private static final class PatternEntry {
        final StatementPattern pattern;
        final String key;
        final int originalPosition;

        PatternEntry(StatementPattern pattern, String key, int originalPosition) {
            this.pattern = pattern;
            this.key = key;
            this.originalPosition = originalPosition;
        }
    }

    private static final class Relation {
        final String semanticId;
        final String messageIri;
        final List<String> variables;

        Relation(String semanticId, String messageIri, List<String> variables) {
            this.semanticId = semanticId;
            this.messageIri = messageIri;
            this.variables = Collections.unmodifiableList(new ArrayList<>(variables));
        }
    }
}
