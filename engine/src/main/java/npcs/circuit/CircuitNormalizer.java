package npcs.circuit;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.eclipse.rdf4j.model.IRI;
import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.model.Resource;
import org.eclipse.rdf4j.model.Statement;
import org.eclipse.rdf4j.model.Value;
import org.eclipse.rdf4j.model.ValueFactory;
import org.eclipse.rdf4j.model.impl.SimpleValueFactory;
import org.eclipse.rdf4j.model.vocabulary.RDF;

/** Data-dependent final normalization for a generated circuit. */
final class CircuitNormalizer {

    private static final ValueFactory VF = SimpleValueFactory.getInstance();
    private static final String C = "urn:circuit:";
    private static final IRI TIMES = VF.createIRI(C, "Times");
    private static final IRI PLUS = VF.createIRI(C, "Plus");
    private static final IRI MINUS = VF.createIRI(C, "Minus");
    private static final IRI IN = VF.createIRI(C, "in");
    private static final IRI FEEDS = VF.createIRI(C, "feeds");
    private static final IRI MINUEND = VF.createIRI(C, "minuend");
    private static final IRI SUBTRAHEND = VF.createIRI(C, "subtrahend");
    static final IRI ANSWER_ROOT = VF.createIRI(C, "answerRoot");

    private CircuitNormalizer() {}

    static Result normalize(Model circuit) {
        int originalTriples = circuit.size();
        int collapsed = collapseUnaryPlus(circuit);
        int omittedTypes = omitInferableTypes(circuit);
        return new Result(originalTriples, collapsed, omittedTypes);
    }

    /** Collapse a non-answer Plus with exactly one child and no path metadata. */
    private static int collapseUnaryPlus(Model circuit) {
        Set<Resource> pluses = new LinkedHashSet<>();
        Set<Resource> answers = new HashSet<>();
        Map<Resource, Set<Resource>> children = new HashMap<>();
        Map<Resource, Set<Resource>> parents = new HashMap<>();
        for (Statement statement : circuit) {
            if (RDF.TYPE.equals(statement.getPredicate()) && PLUS.equals(statement.getObject())) {
                pluses.add(statement.getSubject());
            } else if (ANSWER_ROOT.equals(statement.getPredicate())) {
                answers.add(statement.getSubject());
            } else if (FEEDS.equals(statement.getPredicate())
                    && statement.getObject() instanceof Resource) {
                Resource child = statement.getSubject();
                Resource parent = (Resource) statement.getObject();
                pluses.add(parent);
                children.computeIfAbsent(parent, key -> new LinkedHashSet<>()).add(child);
                parents.computeIfAbsent(child, key -> new LinkedHashSet<>()).add(parent);
            }
        }
        Set<Resource> annotated = new HashSet<>();
        for (Statement statement : circuit) {
            if (pluses.contains(statement.getSubject())
                    && !RDF.TYPE.equals(statement.getPredicate())
                    && !FEEDS.equals(statement.getPredicate())) {
                annotated.add(statement.getSubject());
            }
        }

        Map<Resource, Resource> replacement = new HashMap<>();
        Deque<Resource> ready = new ArrayDeque<>();
        for (Resource plus : pluses) {
            if (eligible(plus, answers, annotated, children)) ready.addLast(plus);
        }

        while (!ready.isEmpty()) {
            Resource plus = ready.removeFirst();
            if (replacement.containsKey(plus)
                    || !eligible(plus, answers, annotated, children)) continue;
            Resource child = resolve(replacement, children.get(plus).iterator().next());
            if (plus.equals(child)) continue;
            replacement.put(plus, child);
            for (Resource parent : new ArrayList<>(parents.getOrDefault(plus,
                    java.util.Collections.emptySet()))) {
                if (replacement.containsKey(parent)) continue;
                Set<Resource> parentChildren = children.get(parent);
                if (parentChildren == null || !parentChildren.remove(plus)) continue;
                parentChildren.add(child);
                parents.computeIfAbsent(child, key -> new LinkedHashSet<>()).add(parent);
                if (eligible(parent, answers, annotated, children)) ready.addLast(parent);
            }
        }
        if (replacement.isEmpty()) return 0;

        List<Statement> removals = new ArrayList<>();
        List<Statement> additions = new ArrayList<>();
        for (Statement statement : circuit) {
            Resource originalSubject = statement.getSubject();
            Resource subject = resolve(replacement, originalSubject);
            Value originalObject = statement.getObject();
            Value object = originalObject instanceof Resource
                    ? resolve(replacement, (Resource) originalObject) : originalObject;
            if (replacement.containsKey(originalSubject)
                    && RDF.TYPE.equals(statement.getPredicate())) {
                removals.add(statement);
            } else if (!subject.equals(originalSubject) || !object.equals(originalObject)) {
                removals.add(statement);
                if (!FEEDS.equals(statement.getPredicate()) || !subject.equals(object)) {
                    additions.add(statement.getContext() == null
                            ? VF.createStatement(subject, statement.getPredicate(), object)
                            : VF.createStatement(subject, statement.getPredicate(), object,
                                    statement.getContext()));
                }
            }
        }
        circuit.removeAll(removals);
        circuit.addAll(additions);
        return replacement.size();
    }

    private static boolean eligible(Resource plus, Set<Resource> answers, Set<Resource> annotated,
                                    Map<Resource, Set<Resource>> children) {
        Set<Resource> inputs = children.get(plus);
        return !answers.contains(plus) && !annotated.contains(plus)
                && inputs != null && inputs.size() == 1 && !inputs.contains(plus);
    }

    private static Resource resolve(Map<Resource, Resource> replacement, Resource resource) {
        Resource current = resource;
        List<Resource> path = new ArrayList<>();
        while (replacement.containsKey(current)) {
            path.add(current);
            current = replacement.get(current);
            if (path.contains(current)) return resource;
        }
        for (Resource step : path) replacement.put(step, current);
        return current;
    }

    /** Keep an explicit type only where the edge vocabulary cannot recover it, notably an empty Plus. */
    private static int omitInferableTypes(Model circuit) {
        Set<Resource> times = subjects(circuit, IN);
        Set<Resource> minus = subjects(circuit, MINUEND);
        minus.addAll(subjects(circuit, SUBTRAHEND));
        Set<Resource> plus = new HashSet<>();
        for (Statement statement : circuit.filter(null, FEEDS, null)) {
            if (statement.getObject() instanceof Resource) plus.add((Resource) statement.getObject());
        }
        for (Statement statement : circuit.filter(null, ANSWER_ROOT, null)) plus.add(statement.getSubject());

        List<Statement> remove = new ArrayList<>();
        for (Statement statement : circuit.filter(null, RDF.TYPE, null)) {
            if ((TIMES.equals(statement.getObject()) && times.contains(statement.getSubject()))
                    || (MINUS.equals(statement.getObject()) && minus.contains(statement.getSubject()))
                    || (PLUS.equals(statement.getObject()) && plus.contains(statement.getSubject()))) {
                remove.add(statement);
            }
        }
        for (Statement statement : remove) circuit.remove(statement);
        return remove.size();
    }

    private static Set<Resource> subjects(Model circuit, IRI predicate) {
        Set<Resource> subjects = new HashSet<>();
        for (Statement statement : circuit.filter(null, predicate, null)) subjects.add(statement.getSubject());
        return subjects;
    }

    static final class Result {
        final int originalTriples;
        final int collapsedUnaryPlus;
        final int omittedTypes;

        Result(int originalTriples, int collapsedUnaryPlus, int omittedTypes) {
            this.originalTriples = originalTriples;
            this.collapsedUnaryPlus = collapsedUnaryPlus;
            this.omittedTypes = omittedTypes;
        }
    }
}
