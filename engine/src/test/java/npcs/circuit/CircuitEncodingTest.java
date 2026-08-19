package npcs.circuit;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import java.util.List;

import org.junit.Test;

import npcs.rewrite.Reification;

public class CircuitEncodingTest {

    private static final String BGP =
            "SELECT ?x WHERE { ?x <urn:p> ?y . ?y <urn:q> <urn:z> . }";

    @Test
    public void planEmitsTheNativeEncodingAtTheEndpoint() {
        CircuitConstructionPlan plan = new CircuitRewriter(
                Reification.STANDARD, ConstructionMode.FACTORED, "encoding-workspace")
                .constructionPlan(BGP);
        String text = String.join("\n", plan.queries());

        assertTrue(text.contains("SUBSTR(SHA256("));
        assertTrue(text.contains("c:answerRoot \"vars:78\""));
        assertTrue(text.contains("<urn:circuit:bind:78> ?x"));
        assertFalse(text.contains("<urn:circuit:unbound:78>"));
        assertFalse(text.contains("IF(!BOUND(?x), true, 1 / 0)"));
        assertFalse(text.contains("IF(BOUND(?x), \"bind:\", \"unbound:\")"));
        assertFalse(text.contains("c:answer ?"));
        assertFalse(text.contains("c:binding ?"));
        assertFalse(text.contains(" a c:Times"));
    }

    @Test
    public void minusKeepsTheExplicitEmptyPlusAnchor() {
        String query = "SELECT ?x WHERE { ?x <urn:p> ?y MINUS { ?x <urn:q> ?z } }";
        String text = String.join("\n", new CircuitRewriter(
                Reification.STANDARD, ConstructionMode.FLAT, "encoding-minus")
                .constructionPlan(query).queries());

        assertTrue(text.contains(" a c:Plus ."));
        assertTrue(text.contains(" c:subtrahend "));
        assertFalse(text.contains(" a c:Minus"));
    }

    @Test
    public void pathRetainsOnlyTheTypesNeededDuringTheFixpoint() {
        CircuitRewriter.PathQuery path = new CircuitRewriter(
                Reification.STANDARD, ConstructionMode.FLAT, "encoding-path")
                .pathQuery("SELECT ?y WHERE { <urn:a> <urn:p>+ ?y }");
        assertNotNull(path);

        List<String> initial = path.init();
        String base = initial.get(0);
        assertTrue(base.contains("SUBSTR(SHA256("));
        assertTrue(base.contains(" a c:Plus ; c:rlvl \"base\""));
        assertFalse(base.contains(" a c:Times"));

        String answer = path.projectAnswers(1).get(0);
        assertTrue(answer.contains("c:answerRoot \"vars:79\""));
        assertTrue(answer.contains("<urn:circuit:bind:79>"));
        assertFalse(answer.contains("c:answer ?"));
    }
}
