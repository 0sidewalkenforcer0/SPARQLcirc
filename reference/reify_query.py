"""R9.2 — algebra-preserving reification-only rewriter (the "R" control).

Takes a SPARQL SELECT and rewrites every triple pattern `s p o` into the reification scheme's statement
lookup, **preserving the SPARQL algebra** (Join / OPTIONAL / UNION / MINUS / projection). This is NOT a
textual regex hack: we parse the query with rdflib's SPARQL parser into its algebra, then re-serialize.
The result is a plain SELECT over the reified graph with **no provenance** (no token output, GROUP_CONCAT,
SHA256, gate IRI, or CONSTRUCT) — the "reification only" alternative in the B/R/N/C decomposition, i.e.
the control that isolates the reification cost (R-B) from the provenance cost (N-R or C-R).

Standard reification: `s p o`  ->  `?__tN rdf:subject s . ?__tN rdf:predicate p . ?__tN rdf:object o .`
By construction R returns the SAME answer multiset as the base query B (one statement node per base edge).

  python3 reify_query.py <query.rq>          # print the reified query
  python3 reify_query.py --selftest          # parse + structure-preservation unit tests
"""
import sys
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.term import Variable, URIRef, Literal, BNode

RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"          # Standard reification predicates

def term(t):
    if isinstance(t, Variable): return "?" + str(t)
    if isinstance(t, URIRef):   return "<" + str(t) + ">"
    if isinstance(t, BNode):    return "_:" + str(t)
    if isinstance(t, Literal):
        lex = '"' + str(t).replace("\\", "\\\\").replace('"', '\\"') + '"'
        if t.language: return lex + "@" + t.language
        if t.datatype: return lex + "^^<" + str(t.datatype) + ">"
        return lex
    raise ValueError(f"unhandled term {type(t).__name__}: {t!r}")

class Reifier:
    def __init__(self, scheme="standard"):
        if scheme != "standard":
            raise NotImplementedError("only Standard reification implemented (matches our loaded repos)")
        self.n = 0
    def bgp(self, triples, ind):
        out = []
        for s, p, o in triples:
            self.n += 1; t = f"?__t{self.n}"
            out.append(f"{ind}{t} <{RS}subject> {term(s)} .")
            out.append(f"{ind}{t} <{RS}predicate> {term(p)} .")
            out.append(f"{ind}{t} <{RS}object> {term(o)} .")
        return "\n".join(out)
    def walk(self, n, ind):
        nm = getattr(n, "name", None)
        if nm == "BGP":
            return self.bgp(n["triples"], ind) if n["triples"] else ""
        if nm == "Join":
            a = self.walk(n["p1"], ind); b = self.walk(n["p2"], ind)
            return "\n".join(x for x in (a, b) if x)
        if nm == "LeftJoin":                                   # OPTIONAL
            a = self.walk(n["p1"], ind); b = self.walk(n["p2"], ind + "  ")
            expr = n.get("expr")
            fil = ""
            if expr is not None and getattr(expr, "name", "") != "TrueFilter":
                raise NotImplementedError("OPTIONAL with a FILTER expr is not in the workload; not serialized")
            return f"{a}\n{ind}OPTIONAL {{\n{b}\n{ind}}}"
        if nm == "Union":
            a = self.walk(n["p1"], ind + "  "); b = self.walk(n["p2"], ind + "  ")
            return f"{ind}{{\n{a}\n{ind}}} UNION {{\n{b}\n{ind}}}"
        if nm == "Minus":
            a = self.walk(n["p1"], ind); b = self.walk(n["p2"], ind + "  ")
            return f"{a}\n{ind}MINUS {{\n{b}\n{ind}}}"
        if nm in ("ToMultiSet", "Distinct", "Reduced", "Slice", "Project", "Filter"):
            # projection/modifiers handled at top level; a nested Filter would need expr serialization
            if nm == "Filter":
                raise NotImplementedError("FILTER expr serialization not implemented (workload is filter-free)")
            return self.walk(n["p"], ind)
        raise NotImplementedError(f"algebra node not handled: {nm}")

def reify(query_text, scheme="standard"):
    alg = translateQuery(parseQuery(query_text)).algebra          # SelectQuery -> Project -> ...
    if alg.name != "SelectQuery":
        raise NotImplementedError("only SELECT supported")
    proj = alg["p"]
    # find the Project node (skip Slice/Distinct wrappers) for the projection vars
    pv = alg.get("PV") or proj.get("PV")
    body_root = proj
    while getattr(body_root, "name", "") in ("Project", "Distinct", "Reduced", "Slice", "ToMultiSet"):
        body_root = body_root["p"]
    r = Reifier(scheme)
    body = r.walk(body_root, "  ")
    proj_str = " ".join("?" + str(v) for v in pv) if pv else "*"
    distinct = "DISTINCT " if _has(proj, "Distinct") else ""
    return f"SELECT {distinct}{proj_str} WHERE {{\n{body}\n}}\n"

def _has(node, name):
    seen = set()
    def go(n):
        if id(n) in seen: return False
        seen.add(id(n)); nm = getattr(n, "name", None)
        if nm == name: return True
        for k in (getattr(n, "keys", lambda: [])()):
            v = n[k]
            if hasattr(v, "name") and go(v): return True
        return False
    return go(node)

# ----------------------------- self-test -----------------------------
def _selftest():
    cases = {
        "bgp": "PREFIX x:<http://x/> SELECT ?a ?b WHERE { ?a x:p ?b . ?a x:q <http://x/C> }",
        "optional": "PREFIX x:<http://x/> SELECT ?a ?b ?c WHERE { ?a x:p ?b OPTIONAL { ?b x:r ?c } }",
        "minus": "PREFIX x:<http://x/> SELECT ?a WHERE { ?a x:p ?b MINUS { ?a x:bad ?z } }",
        "union": "PREFIX x:<http://x/> SELECT ?a ?d WHERE { { ?a x:s ?d } UNION { ?a x:t ?d } }",
    }
    ok = True
    for name, q in cases.items():
        rq = reify(q)
        # (1) reified query must itself parse; (2) structure operator must be preserved; (3) no provenance tokens
        try:
            parseQuery(rq)
        except Exception as e:
            print(f"  [{name}] FAIL reified query does not parse: {e}"); ok = False; continue
        checks = {
            "reified": f"<{RS}subject>" in rq and f"<{RS}predicate>" in rq and f"<{RS}object>" in rq,
            "optional": ("OPTIONAL" in rq) == ("optional" == name),
            "union": ("UNION" in rq) == ("union" == name),
            "minus": ("MINUS" in rq) == ("minus" == name),
            "no-prov": not any(k in rq for k in ("GROUP_CONCAT", "SHA256", "urn:g:", "CONSTRUCT")),
        }
        bad = [k for k, v in checks.items() if not v]
        print(f"  [{name:9}] {'OK' if not bad else 'FAIL ' + ','.join(bad)}")
        ok &= not bad
    print("SELFTEST", "OK" if ok else "FAILED"); return ok

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        sys.exit(0 if _selftest() else 1)
    if len(sys.argv) == 2:
        print(reify(open(sys.argv[1]).read()))
    else:
        print(__doc__); sys.exit(2)
