package npcs.circuit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.eclipse.rdf4j.model.IRI;
import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.model.ValueFactory;
import org.eclipse.rdf4j.model.impl.LinkedHashModel;
import org.eclipse.rdf4j.model.impl.SimpleValueFactory;
import org.eclipse.rdf4j.model.vocabulary.RDF;
import org.junit.Test;

public class CircuitNormalizerTest {

    private static final ValueFactory VF = SimpleValueFactory.getInstance();
    private static final String C = "urn:circuit:";

    @Test
    public void finalNormalizationPreservesBindingsAndTheEmptyPlusAnchor() {
        IRI token0 = iri("urn:data:t0");
        IRI token1 = iri("urn:data:t1");
        IRI unary = gate("s", '1');
        IRI product = gate("t", '2');
        IRI answer = gate("a", '3');
        IRI emptyPlus = gate("sub", '4');
        String unicodeVariable = "\u53d8\u91cf";
        String answerSchema = "vars:" + utf8Hex("x") + "," + utf8Hex(unicodeVariable);

        Model circuit = new LinkedHashModel();
        circuit.add(token0, iri(C + "feeds"), unary);
        circuit.add(product, RDF.TYPE, iri(C + "Times"));
        circuit.add(product, iri(C + "in"), unary);
        circuit.add(product, iri(C + "in"), token1);
        circuit.add(product, iri(C + "feeds"), answer);
        circuit.add(answer, RDF.TYPE, iri(C + "Plus"));
        circuit.add(answer, CircuitNormalizer.ANSWER_ROOT, VF.createLiteral(answerSchema));
        circuit.add(answer, iri(CircuitEncoding.bindingPredicateIri("x")),
                VF.createLiteral("same lexical form", "en"));
        circuit.add(emptyPlus, RDF.TYPE, iri(C + "Plus"));

        CircuitNormalizer.Result normalized = CircuitNormalizer.normalize(circuit);

        assertEquals(1, normalized.collapsedUnaryPlus);
        assertEquals(2, normalized.omittedTypes);
        assertTrue(circuit.size() < normalized.originalTriples);

        assertFalse(circuit.contains(unary, null, null));
        assertTrue(circuit.contains(product, iri(C + "in"), token0));
        assertTrue(circuit.contains(product, iri(C + "in"), token1));
        assertFalse(circuit.contains(product, RDF.TYPE, null));
        assertFalse(circuit.contains(answer, RDF.TYPE, null));
        assertTrue(circuit.contains(answer, CircuitNormalizer.ANSWER_ROOT,
                VF.createLiteral(answerSchema)));
        assertTrue(circuit.contains(answer,
                iri(CircuitEncoding.bindingPredicateIri("x")),
                VF.createLiteral("same lexical form", "en")));
        assertFalse(circuit.contains(answer,
                iri(CircuitEncoding.bindingPredicateIri(unicodeVariable)), null));

        // An empty Plus is the zero anchor used by MINUS; it is not inferable from an incoming edge.
        assertTrue(circuit.contains(emptyPlus, RDF.TYPE, iri(C + "Plus")));
    }

    @Test
    public void unaryCollapseReachesAReconvergentFixedPointInOneBatch() {
        IRI token = iri("urn:data:t0");
        IRI left = gate("s", '1');
        IRI right = gate("s", '2');
        IRI parent = gate("s", '3');
        IRI answer = gate("a", '4');

        Model circuit = new LinkedHashModel();
        circuit.add(left, RDF.TYPE, iri(C + "Plus"));
        circuit.add(right, RDF.TYPE, iri(C + "Plus"));
        circuit.add(parent, RDF.TYPE, iri(C + "Plus"));
        circuit.add(token, iri(C + "feeds"), left);
        circuit.add(token, iri(C + "feeds"), right);
        circuit.add(left, iri(C + "feeds"), parent);
        circuit.add(right, iri(C + "feeds"), parent);
        circuit.add(parent, iri(C + "feeds"), answer);
        circuit.add(answer, CircuitNormalizer.ANSWER_ROOT, VF.createLiteral("vars:"));

        CircuitNormalizer.Result normalized = CircuitNormalizer.normalize(circuit);

        assertEquals(3, normalized.collapsedUnaryPlus);
        assertTrue(circuit.contains(token, iri(C + "feeds"), answer));
        assertFalse(circuit.contains(left, null, null));
        assertFalse(circuit.contains(right, null, null));
        assertFalse(circuit.contains(parent, null, null));
    }

    private static IRI iri(String value) {
        return VF.createIRI(value);
    }

    private static IRI gate(String kind, char digit) {
        return iri("urn:g:" + kind + ":" + String.valueOf(digit).repeat(32));
    }

    private static String utf8Hex(String value) {
        return CircuitEncoding.variableHex(value);
    }
}
