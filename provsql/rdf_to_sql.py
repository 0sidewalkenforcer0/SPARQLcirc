"""E7 helper: map our reified RDF KG into the ProvSQL relational form.

- load(path): parse a reified graph (`<t> rdf:subject/predicate/object` per token,
  Turtle or N-Triples) and return the underlying (s, p, o) triples.
- inserts(rows): emit `INSERT INTO triples(s,p,o) VALUES (...)` for schema.sql.
- bgp_to_sql(patterns, proj): translate a SPARQL BGP (self-join of `triples`) to SQL;
  ProvSQL then tracks the ⊗/⊕ provenance circuit. UNION/OPTIONAL/MINUS templates are
  in README.md.

Generation only — running the SQL needs PostgreSQL + ProvSQL (see README.md)."""
import sys, os
import rdflib

RDF = rdflib.RDF

def _short(term):
    """Local name of an IRI (after # or last / or urn: segment), else the literal."""
    s = str(term)
    for sep in ("#", "/", ":"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    return s

def load(path):
    fmt = "nt" if path.endswith((".nt", ".ntriples")) else "turtle"
    g = rdflib.Graph().parse(path, format=fmt)
    rows = []
    for tok in set(g.subjects(RDF.subject, None)):
        s, p, o = g.value(tok, RDF.subject), g.value(tok, RDF.predicate), g.value(tok, RDF.object)
        if s is not None and p is not None and o is not None:
            rows.append((_short(tok), _short(s), _short(p), _short(o)))
    return sorted(rows)

def inserts(rows):
    out = ["-- one row per base triple; add_provenance('triples') makes each a token"]
    for _tok, s, p, o in rows:
        out.append(f"INSERT INTO triples(s,p,o) VALUES ('{s}','{p}','{o}');")
    return "\n".join(out)

def bgp_to_sql(patterns, proj):
    """patterns: list of (s,p,o); a term starting with '?' is a variable, else a
    constant. proj: list of projected variables (e.g. ['?z'])."""
    cols = {0: "s", 1: "p", 2: "o"}
    where, first = [], {}                      # var -> "alias.col" of its first occurrence
    for i, pat in enumerate(patterns):
        for c, term in enumerate(pat):
            ref = f"t{i}.{cols[c]}"
            if term.startswith("?"):
                if term in first:
                    where.append(f"{ref} = {first[term]}")
                else:
                    first[term] = ref
            else:
                where.append(f"{ref} = '{term}'")
    sel = ", ".join(f"{first[v]} AS {v[1:]}" for v in proj)
    frm = ", ".join(f"triples t{i}" for i in range(len(patterns)))
    grp = ", ".join(first[v] for v in proj)
    return (f"SELECT {sel}\nFROM   {frm}\nWHERE  " + " AND ".join(where)
            + f"\nGROUP BY {grp};")

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "../engine/examples/gallery/gallery.ttl"
    rows = load(path)
    print(f"-- {len(rows)} triples from {os.path.basename(path)}\n")
    print(inserts(rows))
    print("\n-- example BGP translation: 2-hop join  ?x :knows ?y . ?y :knows ?z  (project ?z)")
    print(bgp_to_sql([("?x", "knows", "?y"), ("?y", "knows", "?z")], ["?z"]))
