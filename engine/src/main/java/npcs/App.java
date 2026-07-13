package npcs;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.Arrays;

import npcs.circuit.CircuitRun;
import npcs.rewrite.NpcsRewriter;
import npcs.rewrite.Reification;

/**
 * CLI entry point for both the circuit contribution and the NPCS-compatible
 * string-rewriting baseline:
 * <pre>
 *   java -jar npcs-rewrite.jar circuit [--construction=factored|flat]
 *        &lt;scheme&gt; &lt;data&gt; &lt;query-file&gt; [&lt;endpoint&gt;]
 *   java -jar npcs-rewrite.jar rewrite &lt;scheme&gt; &lt;query|path&gt; &lt;value&gt;
 *
 *   // Backwards-compatible baseline form:
 *   java -jar npcs-rewrite.jar &lt;scheme&gt; &lt;query|path&gt; &lt;value&gt;
 * </pre>
 * where {@code scheme} is Standard or SPARQL_Star, the second argument is the
 * literal "query" or "path", and the third is the query text or a file path.
 *
 * <p>Unlike the original (which prints a byte array), this prints the rewritten
 * query as plain UTF-8 text on stdout.
 */
public final class App {

    public static void main(String[] args) throws Exception {
        if (args.length > 0 && "circuit".equals(args[0])) {
            CircuitRun.main(Arrays.copyOfRange(args, 1, args.length));
            return;
        }
        if (args.length > 0 && "rewrite".equals(args[0])) {
            args = Arrays.copyOfRange(args, 1, args.length);
        }
        if (args.length != 3) {
            System.err.println("Usage:");
            System.err.println("  java -jar npcs-rewrite.jar circuit [--construction=factored|flat] "
                    + "<Standard|SPARQL_Star> <dataFile> <queryFile> [sparqlEndpointURL]");
            System.err.println("  java -jar npcs-rewrite.jar rewrite <Standard|SPARQL_Star> <query|path> <text-or-file>");
            System.err.println("  java -jar npcs-rewrite.jar <Standard|SPARQL_Star> <query|path> <text-or-file>  # legacy baseline form");
            System.exit(2);
            return;
        }
        String schemeName = args[0];
        String mode = args[1];
        String third = args[2];

        // Self-validation mode: parse the given query/file and report if it is
        // well-formed SPARQL (used to check that rewritten output re-parses).
        if ("parsecheck".equals(schemeName)) {
            String q = "path".equals(mode)
                    ? new String(Files.readAllBytes(Paths.get(third)), StandardCharsets.UTF_8)
                    : third;
            try {
                new org.eclipse.rdf4j.query.parser.sparql.SPARQLParser().parseQuery(q, null);
                System.out.println("PARSE_OK");
            } catch (Exception e) {
                System.out.println("PARSE_FAIL: " + e.getMessage());
                System.exit(1);
            }
            return;
        }

        String query;
        if ("path".equals(mode)) {
            query = new String(Files.readAllBytes(Paths.get(third)), StandardCharsets.UTF_8);
        } else if ("query".equals(mode)) {
            query = third;
        } else {
            System.err.println("Second argument must be 'query' or 'path'.");
            System.exit(2);
            return;
        }

        Reification scheme = Reification.fromName(schemeName);
        String rewritten = new NpcsRewriter(scheme).rewrite(query);
        System.out.println(rewritten);
    }
}
