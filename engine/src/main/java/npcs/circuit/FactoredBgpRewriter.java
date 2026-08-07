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
    // Package-private: CircuitRewriter materializes a COMPOSITE join operand as a row relation in the
    // same private vocabulary, so both readers agree on the shape and CircuitRun's urn:sc: prefix test
    // feeds them back and cleans them up identically.
    static final String MESSAGE = META_NS + "message";
    static final String GATE = META_NS + "gate";
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

    /**
     * @param answerTag the query's answer-gate pattern tag (Def. 4.6 θ), supplied by
     *     {@code CircuitRewriter} so a factored branch and a flat branch of the SAME query converge
     *     on one answer ⊕ while two DIFFERENT queries never do. Must be byte-identical to the tag
     *     {@code CircuitRewriter.bgp}/{@code minusRoot} use.
     */
    static CircuitConstructionPlan build(Reification scheme, String generatedPrefix,
                                         String workspaceId, List<StatementPattern> inputPatterns,
                                         List<String> outputVariables, String answerTag) {
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
        return planner.plan(patterns, outputVariables, answerTag);
    }

    private CircuitConstructionPlan plan(List<PatternEntry> patterns, List<String> outputVariables,
                                         String answerTag) {
        Relation result = eliminate(patterns, new HashSet<>(outputVariables));
        steps.add(new CircuitConstructionPlan.Step(
                answerQuery(result, outputVariables, answerTag), false, "answers",
                relationsOf(result), NO_RELATIONS));
        return new CircuitConstructionPlan(steps, ConstructionMode.FACTORED,
                ConstructionMode.FACTORED, null);
    }

    /**
     * Factored marginal: min-scope variable elimination down to {@code keepVariables}, then a sink ⊕
     * whose content-addressed IRI is {@code plusPrefix}+SHA256(bindingKey(gateTag, keepVariables)) — the
     * SAME id the flat {@code CircuitRewriter.productPlus} emits, so a ⊖ built over this marginal connects
     * to it unchanged. The reified statement-id leaves are identical to the flat gate's, so the difference
     * semantics (and WMC) are preserved; only the minuend/subtrahend polynomial is factored (shared).
     */
    static CircuitConstructionPlan buildMarginal(Reification scheme, String generatedPrefix,
                                                 String workspaceId, List<StatementPattern> inputPatterns,
                                                 List<String> keepVariables, String plusPrefix, String gateTag) {
        if (inputPatterns.isEmpty()) {
            throw new UnsupportedOperationException("Factored construction requires a non-empty BGP.");
        }
        List<PatternEntry> patterns = new ArrayList<>();
        for (int i = 0; i < inputPatterns.size(); i++) {
            patterns.add(new PatternEntry(inputPatterns.get(i), patternKey(inputPatterns.get(i)), i));
        }
        patterns.sort(Comparator.comparing((PatternEntry p) -> p.key)
                .thenComparingInt(p -> p.originalPosition));
        // Fingerprint the internal (base/join/marg) gates by patterns + kept vars + the caller's gate tag,
        // so P1 and P2 marginals of one query never share an intermediate gate id.
        StringBuilder semantic = new StringBuilder("FACTORED-MARGINAL");
        for (PatternEntry pattern : patterns) semantic.append(part(pattern.key));
        semantic.append("|KEEP");
        for (String keep : keepVariables) semantic.append(part(keep));
        semantic.append("|TAG").append(part(gateTag));
        String fp = sha256hex(semantic.toString());
        FactoredBgpRewriter planner = new FactoredBgpRewriter(scheme, generatedPrefix, workspaceId, fp);
        return planner.planMarginal(patterns, keepVariables, plusPrefix, gateTag);
    }

    private CircuitConstructionPlan planMarginal(List<PatternEntry> patterns, List<String> keepVariables,
                                                 String plusPrefix, String gateTag) {
        Relation result = eliminate(patterns, new HashSet<>(keepVariables));
        steps.add(new CircuitConstructionPlan.Step(
                marginalSink(result, keepVariables, plusPrefix, gateTag), false, "marginal-sink",
                relationsOf(result), NO_RELATIONS));
        return new CircuitConstructionPlan(steps, ConstructionMode.FACTORED,
                ConstructionMode.FACTORED, null);
    }

    /**
     * Sink ⊕ keyed by {@code keepVariables}: every factored-result row for a given key-binding feeds one
     * shared Plus gate (so multiple derivations of the same binding are summed, matching the flat gate).
     */
    private String marginalSink(Relation input, List<String> keepVariables, String plusPrefix, String gateTag) {
        String inputRow = qv("f_input_row"), source = qv("f_source"), plus = qv("f_plus");
        StringBuilder query = new StringBuilder(PRE);
        query.append("CONSTRUCT {\n")
             .append("  ").append(plus).append(" a c:Plus .\n")
             .append("  ").append(source).append(" c:feeds ").append(plus).append(" .\n")
             .append("}\nWHERE {\n")
             .append(rowPattern(inputRow, input, source))
             .append(bindIri(plus, plusPrefix, bindingKey(gateTag, keepVariables)))
             .append("}\n");
        return query.toString();
    }

    private Relation eliminate(List<PatternEntry> patterns, Set<String> outputs) {
        // Source-restriction pushdown: if any pattern carries a constant subject/object the query is
        // SELECTIVE, so each base relation is restricted to rows that participate in a full match (only
        // then does an interior pattern with no constant -- a chain edge, say -- stop building its ENTIRE
        // unrestricted relation). Unbound BGPs (all endpoints variable) keep plain base scans; factored's
        // design regime is unchanged.
        boolean selective = false;
        for (PatternEntry entry : patterns) {
            if (entry.pattern.getSubjectVar().getValue() != null
                    || entry.pattern.getObjectVar().getValue() != null) { selective = true; break; }
        }
        // The relations are created BEFORE any step is emitted, and in pattern order, because
        // relation() advances stepNumber and marginalize() derives its gate tag from the resulting
        // semanticId. Emitting one base step instead of k must not disturb that sequence or every
        // MARG gate IRI would move.
        List<Relation> relations = new ArrayList<>();
        for (int i = 0; i < patterns.size(); i++) {
            relations.add(relation("base-" + i, patternVariables(patterns.get(i).pattern)));
        }
        if (selective && patterns.size() > 1 && onePassBaseEnabled()) {
            // The restriction each base relation needs IS the full BGP, so the k restricted base
            // queries all had the SAME WHERE (the whole BGP reified) and differed only in which token
            // they kept and which columns they published. That is k evaluations of one join. Emit the
            // join once and publish all k relations from it. Identical output triples, one pass.
            steps.add(new CircuitConstructionPlan.Step(baseQueryOnePass(patterns, relations), true,
                    "base[0.." + (patterns.size() - 1) + "] one pass",
                    NO_RELATIONS, relationsOf(relations.toArray(new Relation[0]))));
        } else {
            for (int i = 0; i < patterns.size(); i++) {
                steps.add(new CircuitConstructionPlan.Step(
                        baseQuery(patterns.get(i).pattern, relations.get(i),
                                  "BASE@" + queryFingerprint + "@" + i,
                                  selective ? patterns : null, i),
                        true, "base[" + i + "]",
                        NO_RELATIONS, relationsOf(relations.get(i))));
            }
        }

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

            String elimVar = null;
            int bestCost = Integer.MAX_VALUE;
            for (String candidate : candidates) {
                LinkedHashSet<String> scope = new LinkedHashSet<>();
                for (Relation relation : relations) {
                    if (relation.variables.contains(candidate)) scope.addAll(relation.variables);
                }
                int cost = scope.size();
                if (cost < bestCost) {
                    bestCost = cost;
                    elimVar = candidate;
                }
            }

            List<Relation> involved = new ArrayList<>();
            List<Relation> rest = new ArrayList<>();
            for (Relation relation : relations) {
                (relation.variables.contains(elimVar) ? involved : rest).add(relation);
            }
            Relation joined = involved.get(0);
            for (int i = 1; i < involved.size(); i++) joined = join(joined, involved.get(i));
            Relation marginalized = marginalize(joined, elimVar);
            rest.add(marginalized);
            relations = rest;
        }

        Relation result = relations.get(0);
        for (int i = 1; i < relations.size(); i++) result = join(result, relations.get(i));
        return result;
    }

    /**
     * Escape hatch for the one-pass base materialization. It is on by default: the emitted triples are
     * identical either way, so the only observable difference is construction time. The switch exists
     * because the published construction-time tables for the bound (selective) query classes were
     * measured with k separate base passes, and reproducing those numbers should not require checking
     * out an old commit.
     */
    private static boolean onePassBaseEnabled() {
        return !"0".equals(System.getenv("CIRCUIT_ONE_PASS_BASE"))
                && !Boolean.getBoolean("sparqlcirc.perPatternBase");
    }

    /**
     * All k base relations of a SELECTIVE BGP from ONE evaluation of the join.
     *
     * <p>Each restricted base relation is the projection of the full match onto one pattern's variables,
     * paired with that pattern's token — so k of them are k projections of a single relation, and the
     * per-pattern queries were re-running the same join k times to get them. Here the BGP is reified
     * once with one token variable per pattern, and the template publishes every relation's gate, its
     * {@code c:feeds} edge and its row.
     *
     * <p>Byte-for-byte identical to the k restricted queries: a base gate is
     * {@code urn:g:s:}+SHA256(bindingKey("BASE@fp@i", vars(p_i))) and a row is keyed by the relation's
     * message IRI and the same variables, neither of which depends on how many CONSTRUCTs carried them;
     * the WHERE denotes the same match set in both shapes (the per-pattern form named the other tokens
     * {@code f_ctx*} and dropped them, which is what makes them interchangeable).
     *
     * <p>Every reified pattern is emitted before any BIND. The keys are term-type-aware and guard on
     * {@code BOUND}, so a BIND evaluated before the pattern that binds its variables would silently
     * hash "unbound" instead of failing.
     */
    private String baseQueryOnePass(List<PatternEntry> patterns, List<Relation> outputs) {
        StringBuilder template = new StringBuilder();
        StringBuilder where = new StringBuilder();
        StringBuilder binds = new StringBuilder();
        for (int i = 0; i < patterns.size(); i++) {
            Relation output = outputs.get(i);
            String token = qv("f_token" + i), gate = qv("f_gate" + i), row = qv("f_row" + i);
            template.append("  ").append(gate).append(" a c:Plus .\n")
                    .append("  ").append(token).append(" c:feeds ").append(gate).append(" .\n")
                    .append(rowTemplate(row, output, gate));
            where.append(scheme.reify(patterns.get(i).pattern, token.substring(1)));
            binds.append(bindIri(gate, "urn:g:s:",
                            bindingKey("BASE@" + queryFingerprint + "@" + i, output.variables)))
                 .append(bindIri(row, META_NS + "row:",
                            bindingKey(output.messageIri, output.variables)));
        }
        return PRE + "CONSTRUCT {\n" + template + "}\nWHERE {\n" + where + binds + "}\n";
    }

    private static final Set<String> NO_RELATIONS = Collections.emptySet();

    /** The message IRIs of the given relations, as a dependency set for a plan step. */
    private static Set<String> relationsOf(Relation... relations) {
        Set<String> out = new LinkedHashSet<>();
        for (Relation relation : relations) out.add(relation.messageIri);
        return out;
    }

    private Relation relation(String hint, List<String> variables) {
        String semanticId = queryFingerprint + "-" + hint + "-" + stepNumber++;
        String messageIri = META_NS + "msg:" + workspaceHash + ":" + semanticId;
        return new Relation(semanticId, messageIri, variables);
    }

    private String baseQuery(StatementPattern pattern, Relation output, String gateTag,
                             List<PatternEntry> semijoinContext, int selfIndex) {
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
             .append(scheme.reify(pattern, tokenName));
        // Semi-join context (selective queries only): reify the OTHER BGP patterns so this base relation is
        // restricted to rows that participate in a full match. Context patterns share this pattern's join
        // variables (so they filter it) and contribute NO gate -- their tokens are throwaway existentials.
        if (semijoinContext != null) {
            int ctx = 0;
            for (int j = 0; j < semijoinContext.size(); j++) {
                if (j == selfIndex) continue;
                query.append(scheme.reify(semijoinContext.get(j).pattern, qv("f_ctx" + (ctx++)).substring(1)));
            }
        }
        query.append(bindIri(gate, "urn:g:s:", bindingKey(gateTag, output.variables)))
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
                "join(" + left.variables.size() + "," + right.variables.size() + ")",
                relationsOf(left, right), relationsOf(output)));
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
                "marginalize(?" + eliminate + ")", relationsOf(input), relationsOf(output)));
        return output;
    }

    private String answerQuery(Relation input, List<String> outputVariables, String answerTag) {
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
             .append(bindIri(answer, "urn:g:a:", bindingKey(answerTag, outputVariables)))
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

    static String valuePredicate(String variable) {
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
