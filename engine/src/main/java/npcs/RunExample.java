package npcs;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

import org.eclipse.rdf4j.model.Value;
import org.eclipse.rdf4j.query.BindingSet;
import org.eclipse.rdf4j.query.TupleQueryResult;
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.rio.RDFFormat;
import org.eclipse.rdf4j.sail.memory.MemoryStore;

import npcs.rewrite.NpcsRewriter;
import npcs.rewrite.Reification;

/**
 * Load a reified example dataset, rewrite a query with NPCS, execute it against
 * an in-memory store, and print each answer with its provenance polynomial.
 *
 * <pre>
 *   java -cp target/npcs-rewrite.jar npcs.RunExample Standard    examples/data/example.standard.ttl examples/queries/monotonic/and.sparql
 *   java -cp target/npcs-rewrite.jar npcs.RunExample SPARQL_Star examples/data/example.star.ttls   examples/queries/nonmonotonic/optional.sparql
 * </pre>
 */
public final class RunExample {

    private static final String EX = "http://example.org/paper#";

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            System.err.println("Usage: RunExample <Standard|SPARQL_Star> <dataFile> <queryFile>");
            System.exit(2);
            return;
        }
        Reification scheme = Reification.fromName(args[0]);
        File dataFile = new File(args[1]);
        String query = new String(Files.readAllBytes(Paths.get(args[2])), StandardCharsets.UTF_8);

        RDFFormat fmt = args[1].endsWith(".ttls") ? RDFFormat.TURTLESTAR : RDFFormat.TURTLE;
        NpcsRewriter rewriter = new NpcsRewriter(scheme);
        String rewritten = rewriter.rewrite(query);
        String provenanceBinding = rewriter.provenanceOutputVariable();

        Repository repo = new SailRepository(new MemoryStore());
        try (RepositoryConnection con = repo.getConnection()) {
            con.add(dataFile, EX, fmt);

            System.out.println("# scheme=" + args[0] + "  data=" + dataFile.getName()
                    + "  query=" + new File(args[2]).getName());
            System.out.println("# --- rewritten query ---");
            System.out.println(rewritten);
            System.out.println("# --- results (answer  |  provenance) ---");
            try (TupleQueryResult res = con.prepareTupleQuery(rewritten).evaluate()) {
                if (!res.hasNext()) {
                    System.out.println("  (no answers)");
                }
                while (res.hasNext()) {
                    BindingSet bs = res.next();
                    StringBuilder row = new StringBuilder();
                    for (String n : bs.getBindingNames()) {
                        if (n.equals(provenanceBinding)) {
                            continue;
                        }
                        row.append(n).append("=").append(shorten(bs.getValue(n))).append("  ");
                    }
                    Value prov = bs.getValue(provenanceBinding);
                    row.append("|  ").append(prov == null ? "∅" : shorten(prov));
                    System.out.println("  " + row);
                }
            }
        }
        repo.shutDown();
    }

    private static String shorten(Value v) {
        if (v == null) {
            return "∅";
        }
        return v.stringValue().replace(EX, "");
    }
}
