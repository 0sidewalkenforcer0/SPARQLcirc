package npcs.rewrite;

import org.eclipse.rdf4j.model.BNode;
import org.eclipse.rdf4j.model.IRI;
import org.eclipse.rdf4j.model.Literal;
import org.eclipse.rdf4j.model.Value;
import org.eclipse.rdf4j.model.base.CoreDatatype;
import org.eclipse.rdf4j.model.vocabulary.XSD;
import org.eclipse.rdf4j.query.algebra.Var;

/** Renders RDF4J {@link Var}/{@link Value} nodes back to SPARQL surface syntax. */
public final class Terms {

    private Terms() {}

    /** Render a statement-pattern position (subject/predicate/object). */
    public static String render(Var var) {
        if (var == null) {
            return "[]";
        }
        if (var.hasValue()) {
            return value(var.getValue());
        }
        return "?" + var.getName();
    }

    /** Render a concrete RDF value. Prefixes are always expanded (as the original NPCS does). */
    public static String value(Value v) {
        if (v instanceof IRI) {
            return "<" + v.stringValue() + ">";
        }
        if (v instanceof BNode) {
            return "_:" + ((BNode) v).getID();
        }
        if (v instanceof Literal) {
            Literal lit = (Literal) v;
            String lex = "\"" + escape(lit.getLabel()) + "\"";
            if (lit.getLanguage().isPresent()) {
                return lex + "@" + lit.getLanguage().get();
            }
            IRI dt = lit.getDatatype();
            if (dt != null && !dt.equals(XSD.STRING) && lit.getCoreDatatype() != CoreDatatype.XSD.STRING) {
                return lex + "^^<" + dt.stringValue() + ">";
            }
            return lex;
        }
        return v.stringValue();
    }

    private static String escape(String s) {
        StringBuilder sb = new StringBuilder(s.length() + 8);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '\\': sb.append("\\\\"); break;
                case '"':  sb.append("\\\""); break;
                case '\n': sb.append("\\n");  break;
                case '\r': sb.append("\\r");  break;
                case '\t': sb.append("\\t");  break;
                default:   sb.append(c);
            }
        }
        return sb.toString();
    }
}
