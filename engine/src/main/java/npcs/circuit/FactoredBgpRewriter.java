package npcs.circuit;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
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
    private final ConstructionMode constructionMode;
    private final String gp;
    private final String workspaceHash;
    private final String queryFingerprint;
    private final String relationFingerprint;
    private final List<CircuitConstructionPlan.Step> steps = new ArrayList<>();
    private int stepNumber;

    private FactoredBgpRewriter(Reification scheme, ConstructionMode constructionMode,
                               String generatedPrefix, String workspaceId,
                               String queryFingerprint, String relationFingerprint) {
        if (constructionMode != ConstructionMode.FACTORISED) {
            throw new IllegalArgumentException(
                    "Factorised BGP construction requires factorised mode.");
        }
        this.scheme = scheme;
        this.constructionMode = constructionMode;
        this.gp = generatedPrefix;
        this.workspaceHash = sha256hex(workspaceId);
        this.queryFingerprint = queryFingerprint;
        this.relationFingerprint = relationFingerprint;
    }

    /**
     * @param answerTag the query's answer-gate pattern tag (Def. 4.6 θ), supplied by
     *     {@code CircuitRewriter} so a factored branch and a flat branch of the SAME query converge
     *     on one answer ⊕ while two DIFFERENT queries never do. Must be byte-identical to the tag
     *     {@code CircuitRewriter.bgp}/{@code minusRoot} use.
     */
    static CircuitConstructionPlan build(Reification scheme, ConstructionMode constructionMode,
                                         String generatedPrefix,
                                         String workspaceId, List<StatementPattern> inputPatterns,
                                         List<Filters.Condition> filters,
                                         List<String> outputVariables, String answerTag) {
        if (inputPatterns.isEmpty()) {
            throw new UnsupportedOperationException("Factored construction requires a non-empty BGP.");
        }

        List<PatternEntry> patterns = patternEntries(scheme, inputPatterns);
        // BGP conjunction is commutative. Canonical physical order makes the
        // elimination plan stable under harmless parser/algebra re-association.
        patterns.sort(Comparator.comparing((PatternEntry p) -> p.key)
                .thenComparingInt(p -> p.originalPosition));

        StringBuilder semantic = new StringBuilder("FACTORISED-BGP");
        for (PatternEntry pattern : patterns) semantic.append(part(pattern.key));
        for (Filters.Condition filter : filters) {
            semantic.append("|FILTER").append(part(filter.expression()));
        }
        semantic.append("|OUTPUT");
        for (String output : outputVariables) semantic.append(part(output));
        String fp = sha256hex(semantic.toString());

        FactoredBgpRewriter planner = new FactoredBgpRewriter(
                scheme, constructionMode, generatedPrefix, workspaceId, fp, fp);
        return planner.plan(patterns, filters, outputVariables, answerTag);
    }

    /**
     * Factorised pure-BGP construction with ordered deterministic output bindings. Variable
     * elimination retains {@code inputVariables}, which are the projected pattern variables plus
     * the source dependencies of the live bindings. The bindings themselves run only in the final
     * answer sink, where rows that compute the same projected values feed the same answer Plus gate.
     */
    static CircuitConstructionPlan buildWithTerminalBindings(
                                         Reification scheme, ConstructionMode constructionMode,
                                         String generatedPrefix, String workspaceId,
                                         List<StatementPattern> inputPatterns,
                                         List<Filters.Condition> filters,
                                         List<String> inputVariables,
                                         List<CircuitRewriter.ExtensionBinding> bindings,
                                         List<String> outputVariables, String answerTag) {
        if (inputPatterns.isEmpty()) {
            throw new UnsupportedOperationException(
                    "Factorised construction requires a non-empty BGP.");
        }
        if (bindings.isEmpty()) {
            throw new IllegalArgumentException(
                    "Terminal-binding construction requires at least one live BIND.");
        }

        List<PatternEntry> patterns = patternEntries(scheme, inputPatterns);
        patterns.sort(Comparator.comparing((PatternEntry p) -> p.key)
                .thenComparingInt(p -> p.originalPosition));

        StringBuilder semantic = new StringBuilder("FACTORISED-BGP");
        for (PatternEntry pattern : patterns) semantic.append(part(pattern.key));
        for (Filters.Condition filter : filters) {
            semantic.append("|FILTER").append(part(filter.expression()));
        }
        semantic.append("|OUTPUT");
        for (String output : outputVariables) semantic.append(part(output));
        semantic.append("|INPUT");
        for (String input : inputVariables) semantic.append(part(input));
        for (CircuitRewriter.ExtensionBinding binding : bindings) {
            semantic.append("|BIND").append(part(binding.name)).append(part(binding.expression));
        }
        String fp = sha256hex(semantic.toString());

        FactoredBgpRewriter planner = new FactoredBgpRewriter(
                scheme, constructionMode, generatedPrefix, workspaceId, fp, fp);
        return planner.planWithTerminalBindings(
                patterns, filters, inputVariables, bindings, outputVariables, answerTag);
    }

    private CircuitConstructionPlan plan(List<PatternEntry> patterns,
                                         List<Filters.Condition> filters,
                                         List<String> outputVariables,
                                         String answerTag) {
        Relation result = eliminate(patterns, new HashSet<>(outputVariables),
                Collections.<StatementPattern>emptyList(), filters);
        steps.add(new CircuitConstructionPlan.Step(
                answerQuery(result, outputVariables, answerTag), false, "answers",
                relationsOf(result), NO_RELATIONS));
        return new CircuitConstructionPlan(steps, constructionMode,
                constructionMode, null);
    }

    private CircuitConstructionPlan planWithTerminalBindings(
                                         List<PatternEntry> patterns,
                                         List<Filters.Condition> filters,
                                         List<String> inputVariables,
                                         List<CircuitRewriter.ExtensionBinding> bindings,
                                         List<String> outputVariables,
                                         String answerTag) {
        Relation result = eliminate(patterns, new HashSet<>(inputVariables),
                Collections.<StatementPattern>emptyList(), filters);
        steps.add(new CircuitConstructionPlan.Step(
                answerQuery(result, bindings, outputVariables, answerTag), false,
                "answers+bind[" + bindings.size() + "]",
                relationsOf(result), NO_RELATIONS));
        return new CircuitConstructionPlan(steps, constructionMode,
                constructionMode, null);
    }

    /**
     * Factored marginal: min-scope variable elimination down to {@code keepVariables}, then a sink ⊕
     * whose content-addressed IRI is {@code plusPrefix}+SHA256(bindingKey(gateTag, keepVariables)) — the
     * SAME id the flat {@code CircuitRewriter.productPlus} emits, so a ⊖ built over this marginal connects
     * to it unchanged. The reified statement-id leaves are identical to the flat gate's, so the difference
     * semantics (and WMC) are preserved; only the minuend/subtrahend polynomial is factored (shared).
     */
    static CircuitConstructionPlan buildMarginal(Reification scheme,
                                                 ConstructionMode constructionMode,
                                                 String generatedPrefix,
                                                 String workspaceId, List<StatementPattern> inputPatterns,
                                                 List<Filters.Condition> filters,
                                                 List<String> keepVariables, String plusPrefix,
                                                 String gateTag,
                                                 List<StatementPattern> restrictionPatterns) {
        if (inputPatterns.isEmpty()) {
            throw new UnsupportedOperationException("Factored construction requires a non-empty BGP.");
        }
        List<PatternEntry> patterns = patternEntries(scheme, inputPatterns);
        patterns.sort(Comparator.comparing((PatternEntry p) -> p.key)
                .thenComparingInt(p -> p.originalPosition));
        // Fingerprint the internal (base/join/marg) gates by patterns + kept vars + the caller's gate tag,
        // so P1 and P2 marginals of one query never share an intermediate gate id.
        StringBuilder semantic = new StringBuilder("FACTORISED-MARGINAL");
        for (PatternEntry pattern : patterns) semantic.append(part(pattern.key));
        for (Filters.Condition filter : filters) {
            semantic.append("|FILTER").append(part(filter.expression()));
        }
        semantic.append("|KEEP");
        for (String keep : keepVariables) semantic.append(part(keep));
        semantic.append("|TAG").append(part(gateTag));
        String fp = sha256hex(semantic.toString());
        String relationFp = fp;
        if (!restrictionPatterns.isEmpty()) {
            List<String> restrictionKeys = new ArrayList<>();
            for (StatementPattern restriction : restrictionPatterns) {
                restrictionKeys.add(patternKey(restriction));
            }
            Collections.sort(restrictionKeys);
            StringBuilder physical = new StringBuilder(fp).append("|RESTRICT");
            for (String restrictionKey : restrictionKeys) {
                physical.append(part(restrictionKey));
            }
            relationFp = sha256hex(physical.toString());
        }
        FactoredBgpRewriter planner = new FactoredBgpRewriter(
                scheme, constructionMode, generatedPrefix, workspaceId, fp, relationFp);
        return planner.planMarginal(patterns, filters, keepVariables, plusPrefix, gateTag,
                restrictionPatterns);
    }

    private CircuitConstructionPlan planMarginal(List<PatternEntry> patterns,
                                                 List<Filters.Condition> filters,
                                                 List<String> keepVariables,
                                                 String plusPrefix, String gateTag,
                                                 List<StatementPattern> restrictionPatterns) {
        Relation result = eliminate(patterns, new HashSet<>(keepVariables), restrictionPatterns,
                filters);
        steps.add(new CircuitConstructionPlan.Step(
                marginalSink(result, keepVariables, plusPrefix, gateTag), false, "marginal-sink",
                relationsOf(result), NO_RELATIONS));
        return new CircuitConstructionPlan(steps, constructionMode,
                constructionMode, null);
    }

    /**
     * Sink ⊕ keyed by {@code keepVariables}: every factored-result row for a given key-binding feeds one
     * shared Plus gate (so multiple derivations of the same binding are summed, matching the flat gate).
     */
    private String marginalSink(Relation input, List<String> keepVariables, String plusPrefix, String gateTag) {
        String inputRow = qv("f_input_row"), source = qv("f_source"), plus = qv("f_plus");
        StringBuilder query = new StringBuilder(PRE);
        query.append("CONSTRUCT {\n")
             .append("  ").append(source).append(" c:feeds ").append(plus).append(" .\n")
             .append("}\nWHERE {\n")
             .append(rowPattern(inputRow, input, source))
             .append(bindIri(plus, plusPrefix, bindingKey(gateTag, keepVariables)))
             .append("}\n");
        return query.toString();
    }

    private Relation eliminate(List<PatternEntry> patterns, Set<String> outputs,
                               List<StatementPattern> restrictionPatterns,
                               List<Filters.Condition> filterConditions) {
        // Source-restriction pushdown: if any pattern carries a constant subject/object the query is
        // SELECTIVE, so each base relation is restricted to rows that participate in a full match (only
        // then does an interior pattern with no constant -- a chain edge, say -- stop building its ENTIRE
        // unrestricted relation). Factorised construction instead publishes all reduced base
        // relations from one shared full-BGP pass.
        boolean selective = false;
        for (PatternEntry entry : patterns) {
            for (StatementPattern pattern : entry.patterns) {
                if (pattern.getSubjectVar().getValue() != null
                        || pattern.getObjectVar().getValue() != null) {
                    selective = true;
                    break;
                }
            }
            if (selective) break;
        }
        // The relations are created BEFORE any step is emitted, and in pattern order, because
        // relation() advances stepNumber and marginalize() derives its gate tag from the resulting
        // semanticId. Emitting one base step instead of k must not disturb that sequence or every
        // MARG gate IRI would move.
        List<Relation> relations = new ArrayList<>();
        for (int i = 0; i < patterns.size(); i++) {
            relations.add(relation("base-" + i, patternVariables(patterns.get(i).patterns)));
        }
        List<List<Filters.Condition>> baseFilters = assignBaseFilters(patterns, filterConditions);
        List<Filters.Condition> pendingFilters = new ArrayList<>(filterConditions);
        for (List<Filters.Condition> assigned : baseFilters) pendingFilters.removeAll(assigned);

        // A constant subject/object (or a sibling restriction) makes the full-BGP semijoin useful.
        // An unanchored gMark-style chain deliberately takes the other branch: independent predicate
        // scans followed by variable elimination, so no complete W^k path relation is materialized.
        boolean reducedOnePass = (selective || !restrictionPatterns.isEmpty())
                && (patterns.size() > 1 || !restrictionPatterns.isEmpty());
        if (reducedOnePass) {
            // The restriction each base relation needs IS the full BGP, so the k restricted base
            // queries all had the SAME WHERE (the whole BGP reified) and differed only in which token
            // they kept and which columns they published. That is k evaluations of one join. Emit the
            // join once and publish all k relations from it. Identical output triples, one pass.
            steps.add(new CircuitConstructionPlan.Step(baseQueryOnePass(
                    patterns, relations, reducedOnePass
                            ? restrictionPatterns : Collections.<StatementPattern>emptyList(),
                    filterConditions), true,
                    "base[0.." + (patterns.size() - 1) + "] "
                            + (reducedOnePass ? "reduced one pass" : "one pass"),
                    NO_RELATIONS, relationsOf(relations.toArray(new Relation[0]))));
        } else {
            for (int i = 0; i < patterns.size(); i++) {
                List<PatternEntry> semijoinContext = null;
                if (selective) {
                    semijoinContext = patterns;
                }
                steps.add(new CircuitConstructionPlan.Step(
                        baseQuery(patterns.get(i), relations.get(i),
                                  "BASE@" + queryFingerprint + "@" + i,
                                  semijoinContext, i, baseFilters.get(i)),
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
                LinkedHashSet<String> scope = relationScope(
                        involvedForElimination(candidate, relations, pendingFilters));
                int cost = scope.size();
                if (cost < bestCost) {
                    bestCost = cost;
                    elimVar = candidate;
                }
            }

            List<Relation> involved = involvedForElimination(
                    elimVar, relations, pendingFilters);
            List<Relation> rest = new ArrayList<>();
            for (Relation relation : relations) {
                if (!involved.contains(relation)) rest.add(relation);
            }
            Relation joined = involved.get(0);
            for (int i = 1; i < involved.size(); i++) {
                Relation right = involved.get(i);
                List<Filters.Condition> ready = readyFilters(
                        pendingFilters, joined.variables, right.variables);
                joined = join(joined, right, ready);
                pendingFilters.removeAll(ready);
            }
            List<Filters.Condition> ready = readyFilters(
                    pendingFilters, joined.variables, Collections.<String>emptyList());
            Relation marginalized = marginalize(joined, elimVar, ready);
            pendingFilters.removeAll(ready);
            rest.add(marginalized);
            relations = rest;
        }

        Relation result = relations.get(0);
        for (int i = 1; i < relations.size(); i++) {
            Relation right = relations.get(i);
            List<Filters.Condition> ready = readyFilters(
                    pendingFilters, result.variables, right.variables);
            result = join(result, right, ready);
            pendingFilters.removeAll(ready);
        }
        if (!pendingFilters.isEmpty()) {
            for (Filters.Condition condition : pendingFilters) {
                if (!result.variables.containsAll(condition.variables())) {
                    throw new IllegalStateException("FILTER variables were eliminated before evaluation: "
                            + condition.variables());
                }
            }
            result = filter(result, pendingFilters);
        }
        return result;
    }

    /** Assign every condition that one triple pattern can evaluate to one canonical base scan. */
    private static List<List<Filters.Condition>> assignBaseFilters(
            List<PatternEntry> patterns, List<Filters.Condition> conditions) {
        List<List<Filters.Condition>> assigned = new ArrayList<>();
        for (int i = 0; i < patterns.size(); i++) assigned.add(new ArrayList<>());
        for (Filters.Condition condition : conditions) {
            int best = -1;
            int bestScope = Integer.MAX_VALUE;
            for (int i = 0; i < patterns.size(); i++) {
                List<String> variables = patternVariables(patterns.get(i).patterns);
                if (variables.containsAll(condition.variables()) && variables.size() < bestScope) {
                    best = i;
                    bestScope = variables.size();
                }
            }
            if (best >= 0) assigned.get(best).add(condition);
        }
        return assigned;
    }

    /**
     * Relations that must meet before {@code variable} can be eliminated.  FILTER is a Boolean factor:
     * if a pending condition mentions the variable, the bucket is extended along the relation graph
     * until every other condition variable is bound.  A disconnected condition necessarily induces a
     * Cartesian product, so the final fallback adds its target relation directly.
     */
    private static List<Relation> involvedForElimination(
            String variable, List<Relation> relations, List<Filters.Condition> filters) {
        List<Relation> selected = new ArrayList<>();
        for (Relation relation : relations) {
            if (relation.variables.contains(variable)) selected.add(relation);
        }
        LinkedHashSet<String> required = new LinkedHashSet<>();
        for (Filters.Condition filter : filters) {
            if (filter.variables().contains(variable)) required.addAll(filter.variables());
        }
        for (String target : required) {
            if (relationScope(selected).contains(target)) continue;
            addShortestRelationPath(selected, relations, target);
        }
        return selected;
    }

    /** Add a shortest shared-variable path from {@code selected} to a relation binding target. */
    private static void addShortestRelationPath(
            List<Relation> selected, List<Relation> relations, String target) {
        List<Relation> queue = new ArrayList<>(selected);
        Set<Relation> seen = new LinkedHashSet<>(selected);
        Map<Relation, Relation> parent = new LinkedHashMap<>();
        Relation found = null;
        for (int cursor = 0; cursor < queue.size() && found == null; cursor++) {
            Relation current = queue.get(cursor);
            for (Relation candidate : relations) {
                if (seen.contains(candidate) || !sharesVariable(current, candidate)) continue;
                seen.add(candidate);
                parent.put(candidate, current);
                queue.add(candidate);
                if (candidate.variables.contains(target)) {
                    found = candidate;
                    break;
                }
            }
        }
        if (found == null) {
            for (Relation relation : relations) {
                if (!selected.contains(relation) && relation.variables.contains(target)) {
                    selected.add(relation);       // disconnected FILTER: the Cartesian product is real
                    return;
                }
            }
            throw new IllegalStateException("No factor binds FILTER variable ?" + target);
        }
        List<Relation> path = new ArrayList<>();
        for (Relation cursor = found; cursor != null && !selected.contains(cursor);
                cursor = parent.get(cursor)) {
            path.add(cursor);
        }
        Collections.reverse(path);
        for (Relation relation : path) if (!selected.contains(relation)) selected.add(relation);
    }

    private static boolean sharesVariable(Relation left, Relation right) {
        for (String variable : left.variables) {
            if (right.variables.contains(variable)) return true;
        }
        return false;
    }

    private static LinkedHashSet<String> relationScope(List<Relation> relations) {
        LinkedHashSet<String> scope = new LinkedHashSet<>();
        for (Relation relation : relations) scope.addAll(relation.variables);
        return scope;
    }

    /** Conditions that become evaluable after joining the two supplied scopes. */
    private static List<Filters.Condition> readyFilters(List<Filters.Condition> pending,
            List<String> leftVariables, List<String> rightVariables) {
        LinkedHashSet<String> scope = new LinkedHashSet<>(leftVariables);
        scope.addAll(rightVariables);
        List<Filters.Condition> ready = new ArrayList<>();
        for (Filters.Condition condition : pending) {
            if (scope.containsAll(condition.variables())) ready.add(condition);
        }
        return ready;
    }

    /**
     * All k reduced base relations of a BGP from ONE evaluation of the join.
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
    private String baseQueryOnePass(List<PatternEntry> patterns, List<Relation> outputs,
                                    List<StatementPattern> restrictionPatterns,
                                    List<Filters.Condition> filters) {
        StringBuilder template = new StringBuilder();
        StringBuilder where = new StringBuilder();
        StringBuilder binds = new StringBuilder();
        for (int i = 0; i < patterns.size(); i++) {
            Relation output = outputs.get(i);
            String token = qv("f_token" + i), gate = qv("f_gate" + i), row = qv("f_row" + i);
            template.append("  ").append(token).append(" c:feeds ").append(gate).append(" .\n")
                    .append(rowTemplate(row, output, gate));
            where.append(reifyEntry(
                    patterns.get(i), token.substring(1), "f_rowtype" + i));
            binds.append(bindIri(gate, "urn:g:s:",
                            bindingKey("BASE@" + queryFingerprint + "@" + i, output.variables)))
                 .append(bindIri(row, META_NS + "row:",
                            bindingKey(output.messageIri, output.variables)));
        }
        int restrictionIndex = 0;
        for (PatternEntry restriction : patternEntries(scheme, restrictionPatterns)) {
            int index = restrictionIndex++;
            where.append(reifyEntry(restriction,
                    qv("f_restrict" + index).substring(1),
                    "f_restrict_rowtype" + index));
        }
        where.append(Filters.emitConditions(filters));
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
        int relationNumber = stepNumber++;
        String semanticId = queryFingerprint + "-" + hint + "-" + relationNumber;
        String physicalId = relationFingerprint + "-" + hint + "-" + relationNumber;
        String messageIri = META_NS + "msg:" + workspaceHash + ":" + physicalId;
        return new Relation(semanticId, messageIri, variables);
    }

    private String baseQuery(PatternEntry pattern, Relation output, String gateTag,
                             List<PatternEntry> semijoinContext, int selfIndex,
                             List<Filters.Condition> filters) {
        String token = qv("f_token");
        String gate = qv("f_gate");
        String row = qv("f_row");
        String tokenName = token.substring(1);
        StringBuilder query = new StringBuilder(PRE);
        query.append("CONSTRUCT {\n")
             .append("  ").append(token).append(" c:feeds ").append(gate).append(" .\n")
             .append(rowTemplate(row, output, gate))
             .append("}\nWHERE {\n")
             .append(reifyEntry(pattern, tokenName, "f_rowtype"));
        // A semijoin context is appended as an ordinary join. CONSTRUCT set semantics project
        // duplicate completions back to the same base row.
        if (semijoinContext != null && !semijoinContext.isEmpty()) {
            int ctx = 0;
            for (int i = 0; i < semijoinContext.size(); i++) {
                if (i == selfIndex) continue;
                int index = ctx++;
                query.append(reifyEntry(semijoinContext.get(i),
                        qv("f_ctx" + index).substring(1),
                        "f_ctx_rowtype" + index));
            }
        }
        query.append(Filters.emitConditions(filters));
        query.append(bindIri(gate, "urn:g:s:", bindingKey(gateTag, output.variables)))
             .append(bindIri(row, META_NS + "row:", bindingKey(output.messageIri, output.variables)))
             .append("}\n");
        return query.toString();
    }

    /** Match one provenance unit: one triple normally, or one TPC-H row subject. */
    private String reifyEntry(PatternEntry entry, String tokenName, String typeHint) {
        if (!scheme.isRowLevel()) {
            if (entry.patterns.size() != 1) {
                throw new IllegalStateException(
                        "non-row provenance unit contains multiple patterns");
            }
            return scheme.reify(entry.patterns.get(0), tokenName);
        }
        StringBuilder where = new StringBuilder();
        for (StatementPattern pattern : entry.patterns) {
            where.append(scheme.asserted(pattern));
        }
        where.append(scheme.rowOccurrence(entry.patterns.get(0).getSubjectVar(),
                tokenName, qv(typeHint).substring(1)));
        return where.toString();
    }

    private Relation join(Relation left, Relation right, List<Filters.Condition> filters) {
        List<String> variables = new ArrayList<>(left.variables);
        for (String variable : right.variables) {
            if (!variables.contains(variable)) variables.add(variable);
        }
        Relation output = relation("join", variables);
        String leftRow = qv("f_left_row"), rightRow = qv("f_right_row");
        String leftGate = qv("f_left_gate"), rightGate = qv("f_right_gate");
        String product = qv("f_product"), row = qv("f_row");
        String h0 = qv("f_h0"), h1 = qv("f_h1"), pair = qv("f_pair");

        StringBuilder query = new StringBuilder(PRE);
        query.append("CONSTRUCT {\n  ").append(product);
        query.append(" c:in ").append(leftGate)
             .append(" ; c:in ").append(rightGate).append(" .\n")
             .append(rowTemplate(row, output, product))
             .append("}\nWHERE {\n")
             .append(rowPattern(leftRow, left, leftGate))
             .append(rowPattern(rightRow, right, rightGate))
             .append(Filters.emitConditions(filters))
             .append("  BIND(SHA256(STR(").append(leftGate).append(")) AS ").append(h0).append(")\n")
             .append("  BIND(SHA256(STR(").append(rightGate).append(")) AS ").append(h1).append(")\n");
        // Keep the T|lo|hi preimage while evaluating the ordering comparison once.
        query.append("  BIND(IF(").append(h0).append(" <= ").append(h1)
             .append(", CONCAT(").append(h0).append(", \"|\", ").append(h1)
             .append("), CONCAT(").append(h1).append(", \"|\", ").append(h0)
             .append(")) AS ").append(pair).append(")\n")
             .append("  BIND(IRI(CONCAT(\"urn:g:t:\", ")
             .append(CircuitEncoding.hashExpression("CONCAT(\"T|\", " + pair + ")"))
             .append(")) AS ").append(product).append(")\n");
        query.append(bindIri(row, META_NS + "row:", bindingKey(output.messageIri, output.variables)))
             .append("}\n");
        steps.add(new CircuitConstructionPlan.Step(query.toString(), true,
                filterLabel("join(" + left.variables.size() + "," + right.variables.size() + ")",
                        filters),
                relationsOf(left, right), relationsOf(output)));
        return output;
    }

    private Relation marginalize(Relation input, String eliminate,
                                 List<Filters.Condition> filters) {
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
             .append("  ").append(source).append(" c:feeds ").append(sum).append(" .\n")
             .append(rowTemplate(row, output, sum))
             .append("}\nWHERE {\n")
             .append(rowPattern(inputRow, input, source))
             .append(Filters.emitConditions(filters))
             .append(bindIri(sum, "urn:g:s:", bindingKey(gateTag, keep)))
             .append(bindIri(row, META_NS + "row:", bindingKey(output.messageIri, keep)))
             .append("}\n");
        steps.add(new CircuitConstructionPlan.Step(query.toString(), true,
                filterLabel("marginalize(?" + eliminate + ")", filters),
                relationsOf(input), relationsOf(output)));
        return output;
    }

    /** Selection over an already materialized factor: copy its rows and keep every gate unchanged. */
    private Relation filter(Relation input, List<Filters.Condition> filters) {
        Relation output = relation("filter", input.variables);
        String inputRow = qv("f_input_row"), source = qv("f_source"), row = qv("f_row");
        StringBuilder query = new StringBuilder(PRE);
        query.append("CONSTRUCT {\n")
             .append(rowTemplate(row, output, source))
             .append("}\nWHERE {\n")
             .append(rowPattern(inputRow, input, source))
             .append(Filters.emitConditions(filters))
             .append(bindIri(row, META_NS + "row:", bindingKey(output.messageIri, output.variables)))
             .append("}\n");
        steps.add(new CircuitConstructionPlan.Step(query.toString(), true,
                filterLabel("filter", filters), relationsOf(input), relationsOf(output)));
        return output;
    }

    private static String filterLabel(String base, List<Filters.Condition> filters) {
        return filters.isEmpty() ? base : base + "+filter[" + filters.size() + "]";
    }

    private String answerQuery(Relation input, List<String> outputVariables, String answerTag) {
        String inputRow = qv("f_input_row"), source = qv("f_source");
        String answer = qv("ans");
        StringBuilder ctor = new StringBuilder();
        for (String variable : outputVariables) {
            ctor.append("  ").append(answer).append(" <")
                .append(CircuitEncoding.bindingPredicateIri(variable)).append("> ?")
                .append(variable).append(" .\n");
        }

        StringBuilder query = new StringBuilder(PRE);
        query.append("CONSTRUCT {\n")
             .append("  ").append(source).append(" c:feeds ").append(answer).append(" .\n")
             .append(answerTriple(answer, outputVariables))
             .append(ctor)
             .append("}\nWHERE {\n")
             .append(rowPattern(inputRow, input, source))
             .append(bindIri(answer, "urn:g:a:", bindingKey(answerTag, outputVariables)))
             .append("}\n");
        return query.toString();
    }

    private String answerQuery(Relation input,
                               List<CircuitRewriter.ExtensionBinding> bindings,
                               List<String> outputVariables, String answerTag) {
        String inputRow = qv("f_input_row"), source = qv("f_source");
        String answer = qv("ans");
        StringBuilder ctor = new StringBuilder();
        for (String variable : outputVariables) {
            ctor.append("  ").append(answer).append(" <")
                .append(CircuitEncoding.bindingPredicateIri(variable)).append("> ?")
                .append(variable).append(" .\n");
        }

        StringBuilder query = new StringBuilder(PRE);
        query.append("CONSTRUCT {\n")
             .append("  ").append(source).append(" c:feeds ").append(answer).append(" .\n")
             .append(answerTriple(answer, outputVariables))
             .append(ctor)
             .append("}\nWHERE {\n")
             .append(rowPattern(inputRow, input, source))
             .append(CircuitRewriter.emitBindings(bindings))
             .append(bindIri(answer, "urn:g:a:", bindingKey(answerTag, outputVariables)))
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

    /** Must remain byte-for-byte compatible with CircuitRewriter's answer identity. */
    private static String termHash(String label, String term) {
        String enc =
            "IF(!BOUND(" + term + "), \"u\"," +
            " IF(isIRI(" + term + "), CONCAT(\"i\", SHA256(STR(" + term + ")))," +
            " IF(isBlank(" + term + "), CONCAT(\"b\", SHA256(STR(" + term + ")))," +
            " IF(isLiteral(" + term + "), CONCAT(\"l\", SHA256(STR(" + term + "))," +
            " SHA256(STR(DATATYPE(" + term + "))), SHA256(LCASE(LANG(" + term + "))))," +
            " CONCAT(\"t\", SHA256(STR(" + term + ")))))))";
        return "SHA256(CONCAT(\"" + escapeString(label) + "=\", " + enc + "))";
    }

    private String bindIri(String variable, String prefix, String key) {
        return "  BIND(IRI(CONCAT(\"" + prefix + "\", "
                + CircuitEncoding.hashExpression(key) + ")) AS "
                + variable + ")\n";
    }

    private String answerTriple(String answer, List<String> variables) {
        return "  " + answer + " c:answerRoot "
                + CircuitEncoding.answerSchema(variables) + " .\n";
    }

    private String qv(String hint) { return "?" + gp + hint; }

    private static List<String> patternVariables(List<StatementPattern> patterns) {
        List<String> variables = new ArrayList<>();
        for (StatementPattern pattern : patterns) {
            addVariable(variables, pattern.getSubjectVar());
            addVariable(variables, pattern.getPredicateVar());
            addVariable(variables, pattern.getObjectVar());
        }
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

    /** Canonical provenance units: triples normally, row-subject groups for TPC-H. */
    private static List<PatternEntry> patternEntries(
            Reification scheme, List<StatementPattern> inputPatterns) {
        List<PatternEntry> entries = new ArrayList<>();
        if (!scheme.isRowLevel()) {
            for (int i = 0; i < inputPatterns.size(); i++) {
                StatementPattern pattern = inputPatterns.get(i);
                entries.add(new PatternEntry(
                        Collections.singletonList(pattern), patternKey(pattern), i));
            }
            return entries;
        }

        Map<String, List<StatementPattern>> grouped = new LinkedHashMap<>();
        Map<String, Integer> positions = new LinkedHashMap<>();
        for (int i = 0; i < inputPatterns.size(); i++) {
            StatementPattern pattern = inputPatterns.get(i);
            String subjectKey = variableKey(pattern.getSubjectVar());
            grouped.computeIfAbsent(subjectKey, ignored -> new ArrayList<>()).add(pattern);
            positions.putIfAbsent(subjectKey, i);
        }
        for (Map.Entry<String, List<StatementPattern>> group : grouped.entrySet()) {
            List<StatementPattern> patterns = new ArrayList<>(group.getValue());
            patterns.sort(Comparator.comparing(FactoredBgpRewriter::patternKey));
            StringBuilder key = new StringBuilder("ROW").append(part(group.getKey()));
            for (StatementPattern pattern : patterns) key.append(part(patternKey(pattern)));
            entries.add(new PatternEntry(patterns, key.toString(), positions.get(group.getKey())));
        }
        return entries;
    }

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
        final List<StatementPattern> patterns;
        final String key;
        final int originalPosition;

        PatternEntry(List<StatementPattern> patterns, String key, int originalPosition) {
            this.patterns = Collections.unmodifiableList(new ArrayList<>(patterns));
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
