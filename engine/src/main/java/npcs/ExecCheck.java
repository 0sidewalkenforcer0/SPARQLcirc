package npcs;

import org.eclipse.rdf4j.model.IRI;
import org.eclipse.rdf4j.model.ValueFactory;
import org.eclipse.rdf4j.query.BindingSet;
import org.eclipse.rdf4j.query.TupleQueryResult;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.sail.memory.MemoryStore;

import npcs.rewrite.NpcsRewriter;
import npcs.rewrite.Reification;

/**
 * Execution-equivalence check for the rewriting.
 *
 * Loads a tiny RDF-reified dataset into an in-memory store, runs the rewritten
 * queries, and prints each answer with its computed provenance polynomial. The
 * provenance strings can be checked by hand against the expected spm-semiring
 * expressions (⊗ product, ⊕ sum, ⊖ monus over the statement identifiers).
 */
public final class ExecCheck {

    static final String EX = "http://ex/";

    public static void main(String[] args) {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            ValueFactory vf = con.getValueFactory();
            IRI A = vf.createIRI(EX, "A"), B = vf.createIRI(EX, "B"), C = vf.createIRI(EX, "C");
            IRI W = vf.createIRI(EX, "W");
            IRI subscribes = vf.createIRI(EX, "subscribes");
            IRI likes = vf.createIRI(EX, "likes");
            IRI caption = vf.createIRI(EX, "caption");

            // Reify base triples under RDF standard reification; the statement
            // node IRI (t1..t4) is the provenance token bound to ?fprov.
            reify(con, "t1", A, subscribes, W);
            reify(con, "t2", A, likes, B);
            reify(con, "t3", A, likes, C);
            reify(con, "t4", B, caption, vf.createLiteral("cap"));
            //  (C intentionally has no caption)

            String bgp =
                "PREFIX ex: <" + EX + "> " +
                "SELECT ?v0 ?v2 WHERE { ?v0 ex:subscribes ex:W . ?v0 ex:likes ?v2 . }";
            String opt =
                "PREFIX ex: <" + EX + "> " +
                "SELECT ?v0 ?v2 ?v3 WHERE { " +
                "  ?v0 ex:subscribes ex:W . ?v0 ex:likes ?v2 . " +
                "  OPTIONAL { ?v2 ex:caption ?v3 . } }";

            run(con, "BGP  (expected: A,B -> ⊗(t1,t2) ; A,C -> ⊗(t1,t3))", bgp);
            run(con, "OPTIONAL (A,B,cap -> ⊗(t1,t2,t4) ; A,C -> ⊗(t1,t3) via monus w/ empty subtrahend)", opt);
        }
        repo.shutDown();
    }

    private static void reify(RepositoryConnection con, String id, IRI s, IRI p, org.eclipse.rdf4j.model.Value o) {
        ValueFactory vf = con.getValueFactory();
        IRI st = vf.createIRI(EX, id);
        IRI RS = vf.createIRI("http://www.w3.org/1999/02/22-rdf-syntax-ns#subject");
        IRI RP = vf.createIRI("http://www.w3.org/1999/02/22-rdf-syntax-ns#predicate");
        IRI RO = vf.createIRI("http://www.w3.org/1999/02/22-rdf-syntax-ns#object");
        con.add(st, RS, s);
        con.add(st, RP, p);
        con.add(st, RO, o);
    }

    private static void run(RepositoryConnection con, String label, String baseQuery) {
        String rq = new NpcsRewriter(Reification.STANDARD).rewrite(baseQuery);
        System.out.println("\n==================== " + label + " ====================");
        try (TupleQueryResult res = con.prepareTupleQuery(rq).evaluate()) {
            while (res.hasNext()) {
                BindingSet bs = res.next();
                StringBuilder row = new StringBuilder();
                for (String n : bs.getBindingNames()) {
                    if (n.equals("finalprovennacevariable")) continue;
                    row.append(n).append("=").append(shorten(bs.getValue(n))).append("  ");
                }
                row.append("| prov=").append(bs.getValue("finalprovennacevariable").stringValue());
                System.out.println("  " + row);
            }
        }
    }

    private static String shorten(org.eclipse.rdf4j.model.Value v) {
        if (v == null) return "∅";
        String s = v.stringValue();
        return s.startsWith(EX) ? s.substring(EX.length()) : s;
    }
}
