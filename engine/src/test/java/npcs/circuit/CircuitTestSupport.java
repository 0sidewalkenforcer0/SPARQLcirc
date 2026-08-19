package npcs.circuit;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
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
import org.eclipse.rdf4j.rio.ntriples.NTriplesUtil;

/** Test-side reader for both the native circuit representation and legacy fixtures. */
final class CircuitTestSupport {

    private static final ValueFactory VF = SimpleValueFactory.getInstance();
    private static final String C = "urn:circuit:";
    private static final String BIND_PREFIX = C + "bind:";
    private static final String UNBOUND_PREFIX = C + "unbound:";
    private static final IRI TIMES = VF.createIRI(C, "Times");
    private static final IRI PLUS = VF.createIRI(C, "Plus");
    private static final IRI MINUS = VF.createIRI(C, "Minus");
    private static final IRI IN = VF.createIRI(C, "in");
    private static final IRI FEEDS = VF.createIRI(C, "feeds");
    private static final IRI MINUEND = VF.createIRI(C, "minuend");
    private static final IRI SUBTRAHEND = VF.createIRI(C, "subtrahend");
    private static final IRI ANSWER = VF.createIRI(C, "answer");
    private static final IRI ANSWER_ROOT = VF.createIRI(C, "answerRoot");
    private static final IRI BINDING = VF.createIRI(C, "binding");
    private static final IRI VAR = VF.createIRI(C, "var");
    private static final IRI VAL = VF.createIRI(C, "val");

    private CircuitTestSupport() {}

    static Set<Resource> answerRoots(Model model) {
        Set<Resource> roots = new LinkedHashSet<>(model.filter(null, ANSWER_ROOT, null).subjects());
        roots.addAll(model.filter(null, ANSWER, null).subjects());
        return roots;
    }

    static Set<Resource> gatesOfType(Model model, String type) {
        Set<Resource> gates = new LinkedHashSet<>();
        IRI explicitType = VF.createIRI(C, type);
        gates.addAll(model.filter(null, RDF.TYPE, explicitType).subjects());
        if ("Times".equals(type)) {
            gates.addAll(model.filter(null, IN, null).subjects());
        } else if ("Minus".equals(type)) {
            gates.addAll(model.filter(null, MINUEND, null).subjects());
            gates.addAll(model.filter(null, SUBTRAHEND, null).subjects());
        } else if ("Plus".equals(type)) {
            gates.addAll(answerRoots(model));
            for (Value target : model.filter(null, FEEDS, null).objects()) {
                if (target instanceof Resource) gates.add((Resource) target);
            }
        }
        return gates;
    }

    static Map<String, Value> bindingValues(Model model, Resource root) {
        Map<String, Value> bindings = new LinkedHashMap<>();
        for (Value node : model.filter(root, BINDING, null).objects()) {
            if (!(node instanceof Resource)) continue;
            Set<Value> names = model.filter((Resource) node, VAR, null).objects();
            if (names.isEmpty()) continue;
            Value value = model.filter((Resource) node, VAL, null).objects().stream()
                    .findFirst().orElse(null);
            bindings.put(names.iterator().next().stringValue(), value);
        }
        for (Statement statement : model.filter(root, null, null)) {
            String predicate = statement.getPredicate().stringValue();
            if (predicate.startsWith(BIND_PREFIX)) {
                bindings.put(decodeHex(predicate.substring(BIND_PREFIX.length())), statement.getObject());
            } else if (predicate.startsWith(UNBOUND_PREFIX)) {
                bindings.put(decodeHex(predicate.substring(UNBOUND_PREFIX.length())), null);
            }
        }
        return bindings;
    }

    static Map<String, String> bindingStrings(Model model, Resource root) {
        Map<String, String> bindings = new LinkedHashMap<>();
        for (Map.Entry<String, Value> entry : bindingValues(model, root).entrySet()) {
            bindings.put(entry.getKey(), entry.getValue() == null ? null
                    : NTriplesUtil.toNTriplesString(entry.getValue()));
        }
        return bindings;
    }

    static Map<String, Value> allBindingValues(Model model) {
        Map<String, Value> bindings = new LinkedHashMap<>();
        for (Resource root : answerRoots(model)) bindings.putAll(bindingValues(model, root));
        return bindings;
    }

    static Set<String> leaves(Model model) {
        Set<Resource> gates = new LinkedHashSet<>();
        gates.addAll(gatesOfType(model, "Times"));
        gates.addAll(gatesOfType(model, "Plus"));
        gates.addAll(gatesOfType(model, "Minus"));
        Set<String> leaves = new LinkedHashSet<>();
        for (Value child : model.filter(null, IN, null).objects()) {
            if (child instanceof Resource && !gates.contains(child)) leaves.add(child.stringValue());
        }
        return leaves;
    }

    static boolean evaluate(Model model, Resource node, Set<String> world,
                            Map<Resource, Boolean> memo) {
        Boolean known = memo.get(node);
        if (known != null) return known;
        memo.put(node, false);
        boolean value;
        if (isGate(model, node, "Times")) {
            value = true;
            for (Value child : model.filter(node, IN, null).objects()) {
                value &= evaluate(model, (Resource) child, world, memo);
            }
        } else if (isGate(model, node, "Minus")) {
            Value positive = model.filter(node, MINUEND, null).objects().iterator().next();
            Value negative = model.filter(node, SUBTRAHEND, null).objects().iterator().next();
            value = evaluate(model, (Resource) positive, world, memo)
                    && !evaluate(model, (Resource) negative, world, memo);
        } else if (isGate(model, node, "Plus")) {
            value = false;
            for (Resource child : model.filter(null, FEEDS, node).subjects()) {
                value |= evaluate(model, child, world, memo);
            }
        } else {
            value = world.contains(node.stringValue());
        }
        memo.put(node, value);
        return value;
    }

    private static boolean isGate(Model model, Resource node, String type) {
        if ("Times".equals(type)) {
            return model.contains(node, RDF.TYPE, TIMES) || model.contains(node, IN, null);
        }
        if ("Minus".equals(type)) {
            return model.contains(node, RDF.TYPE, MINUS)
                    || model.contains(node, MINUEND, null)
                    || model.contains(node, SUBTRAHEND, null);
        }
        return model.contains(node, RDF.TYPE, PLUS)
                || model.contains(node, ANSWER_ROOT, null)
                || model.contains(null, FEEDS, node);
    }

    private static String decodeHex(String hex) {
        if ((hex.length() & 1) != 0) throw new IllegalArgumentException("odd UTF-8 hex: " + hex);
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(hex.length() / 2);
        for (int i = 0; i < hex.length(); i += 2) {
            int high = Character.digit(hex.charAt(i), 16);
            int low = Character.digit(hex.charAt(i + 1), 16);
            if (high < 0 || low < 0) throw new IllegalArgumentException("invalid UTF-8 hex: " + hex);
            bytes.write((high << 4) | low);
        }
        return new String(bytes.toByteArray(), StandardCharsets.UTF_8);
    }
}
