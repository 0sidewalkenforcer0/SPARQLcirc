package npcs.circuit;

import java.io.File;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

import org.eclipse.rdf4j.model.Model;
import org.eclipse.rdf4j.model.Statement;
import org.eclipse.rdf4j.model.impl.LinkedHashModel;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.rio.RDFFormat;
import org.eclipse.rdf4j.sail.memory.MemoryStore;

import npcs.rewrite.Reification;
import npcs.Utf8Text;

/**
 * Property-path state-isolation harness — the REAL same-endpoint sequential test.
 *
 * Runs the SECOND path query two ways on the same reified data:
 *   (a) on a SHARED connection, AFTER the first query has run and fed its reach/base gates back
 *       (NOT cleaned) — exactly the persistent-endpoint scenario the reviewer flagged;
 *   (b) alone on a FRESH store.
 * If the per-path fingerprint isolates queries, the second query's circuit is byte-identical either way.
 * Pre-fix (or if the fingerprint omitted plus/star), running <code>:p*</code> first would leave
 * zero-length (u,u) reach gates that <code>:p+</code> then wrongly composes, so (a) != (b).
 *
 * <pre>java -cp npcs-rewrite.jar npcs.circuit.PathIsoSeq &lt;data.ttl&gt; &lt;first.sparql&gt; &lt;second.sparql&gt;</pre>
 * Exit 0 = isolated (circuits identical), 1 = contaminated.
 */
public final class PathIsoSeq {

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            System.err.println("Usage: PathIsoSeq <data.ttl> <firstQuery> <secondQuery>");
            System.exit(2);
            return;
        }
        File data = new File(args[0]);
        String qFirst  = Utf8Text.read(Paths.get(args[1]));
        String qSecond = Utf8Text.read(Paths.get(args[2]));
        CircuitRewriter rw = new CircuitRewriter(Reification.STANDARD);

        List<String> shared   = onSharedStore(rw, data, qFirst, qSecond);   // second AFTER first, one connection
        List<String> isolated = onFreshStore(rw, data, qSecond);            // second alone

        boolean ok = shared.equals(isolated);
        System.out.println("second-after-first=" + shared.size() + " triples, second-alone=" + isolated.size()
            + " triples  ->  " + (ok ? "IDENTICAL" : "DIFFER"));
        System.out.println(ok
            ? "OK: a prior path query left the second query's circuit UNCHANGED (no contamination)"
            : "FAIL: the second query's circuit CHANGED after a prior path query -> state contamination");
        if (!ok) {
            List<String> extra = new ArrayList<>(shared);   extra.removeAll(isolated);
            List<String> miss  = new ArrayList<>(isolated); miss.removeAll(shared);
            int n = 0; for (String t : extra) { System.out.println("  +only-when-shared: " + t); if (++n >= 6) break; }
            n = 0;     for (String t : miss)  { System.out.println("  -only-when-alone:  " + t); if (++n >= 6) break; }
        }
        System.exit(ok ? 0 : 1);
    }

    private static List<String> onSharedStore(CircuitRewriter rw, File data, String qFirst, String qSecond)
            throws Exception {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            con.add(data, "urn:base:", RDFFormat.TURTLE);
            CircuitRun.buildPathCircuit(con, rw.pathQuery(qFirst), new LinkedHashModel());   // feeds back, NOT cleaned
            Model second = new LinkedHashModel();
            CircuitRun.buildPathCircuit(con, rw.pathQuery(qSecond), second);
            return sortedTriples(second);
        } finally {
            repo.shutDown();
        }
    }

    private static List<String> onFreshStore(CircuitRewriter rw, File data, String q) throws Exception {
        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            con.add(data, "urn:base:", RDFFormat.TURTLE);
            Model m = new LinkedHashModel();
            CircuitRun.buildPathCircuit(con, rw.pathQuery(q), m);
            return sortedTriples(m);
        } finally {
            repo.shutDown();
        }
    }

    private static List<String> sortedTriples(Model m) {
        List<String> out = new ArrayList<>();
        for (Statement st : m) out.add(st.getSubject() + " " + st.getPredicate() + " " + st.getObject());
        java.util.Collections.sort(out);
        return out;
    }
}
