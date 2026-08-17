"""R9.2 — algebra-preserving reification-only rewriter (the "R" control).

Takes a SPARQL SELECT and rewrites every triple pattern `s p o` into the reification scheme's statement
lookup, **preserving the SPARQL algebra** (Join / OPTIONAL / UNION / MINUS / projection). This is NOT a
textual regex hack: we parse the query with rdflib's SPARQL parser into its algebra, then re-serialize.
The result is a plain SELECT over the reified graph with **no provenance** (no token output, GROUP_CONCAT,
SHA256, gate IRI, or CONSTRUCT) — the "reification only" alternative in the B/R/N/C decomposition, i.e.
the control that isolates the reification cost (R-B) from the provenance cost (N-R or C-R).

Standard reification: `s p o`  ->  `?__tN rdf:subject s . ?__tN rdf:predicate p . ?__tN rdf:object o .`
RDF-star reification: `s p o`  ->  `<< s p o >> occurrenceOf ?__tN .`
By construction R returns the SAME answer multiset as the base query B (one statement node per base edge).

  python3 reify_query.py <query.rq>                         # Standard (default)
  python3 reify_query.py --scheme SPARQL_Star <query.rq>    # RDF-star
  python3 reify_query.py --selftest                         # focused self-tests
"""
import argparse
import sys
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.term import Variable, URIRef, Literal, BNode

RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"          # Standard reification predicates
OCCURRENCE_OF = "http://example.org/occurrenceOf"
STANDARD = "Standard"
SPARQL_STAR = "SPARQL_Star"


def normalize_scheme(value):
    """Return the Java CLI spelling for a supported reification scheme."""
    aliases = {
        "standard": STANDARD,
        "sparql_star": SPARQL_STAR,
        "sparql-star": SPARQL_STAR,
        "rdfstar": SPARQL_STAR,
        "rdf-star": SPARQL_STAR,
    }
    normalized = aliases.get(str(value).strip().lower())
    if normalized is None:
        raise ValueError(
            f"unsupported reification scheme {value!r}; "
            f"expected {STANDARD} or {SPARQL_STAR}"
        )
    return normalized

def term(t):
    if isinstance(t, Variable): return "?" + str(t)
    if isinstance(t, (URIRef, BNode, Literal)): return t.n3()
    raise ValueError(f"unhandled term {type(t).__name__}: {t!r}")

class Reifier:
    def __init__(self, scheme=STANDARD, variables=()):
        self.scheme = normalize_scheme(scheme)
        self.n = 0
        self.variables = {str(variable) for variable in variables}

    def fresh_statement_variable(self):
        while True:
            self.n += 1
            name = f"__t{self.n}"
            if name not in self.variables:
                self.variables.add(name)
                return "?" + name

    def bgp(self, triples, ind):
        out = []
        for s, p, o in triples:
            t = self.fresh_statement_variable()
            if self.scheme == SPARQL_STAR:
                out.append(
                    f"{ind}<< {term(s)} {term(p)} {term(o)} >> "
                    f"<{OCCURRENCE_OF}> {t} ."
                )
            else:
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

def reify(query_text, scheme=STANDARD):
    alg = translateQuery(parseQuery(query_text)).algebra          # SelectQuery -> Project -> ...
    if alg.name != "SelectQuery":
        raise NotImplementedError("only SELECT supported")
    proj = alg["p"]
    # find the Project node (skip Slice/Distinct wrappers) for the projection vars
    pv = alg.get("PV") or proj.get("PV")
    body_root = proj
    while getattr(body_root, "name", "") in ("Project", "Distinct", "Reduced", "Slice", "ToMultiSet"):
        body_root = body_root["p"]
    r = Reifier(scheme, alg.get("_vars") or ())
    body = r.walk(body_root, "  ")
    proj_str = " ".join("?" + str(v) for v in pv) if pv else "*"
    distinct = "DISTINCT " if _has(proj, "Distinct") else ""
    modifiers = []
    slice_node = _find(proj, "Slice")
    if slice_node is not None:
        length = slice_node.get("length")
        start = slice_node.get("start")
        if length is not None:
            modifiers.append(f"LIMIT {length}")
        if start:
            modifiers.append(f"OFFSET {start}")
    suffix = "" if not modifiers else "\n" + "\n".join(modifiers)
    return f"SELECT {distinct}{proj_str} WHERE {{\n{body}\n}}{suffix}\n"


def _find(node, name):
    seen = set()
    def go(n):
        if id(n) in seen: return None
        seen.add(id(n)); nm = getattr(n, "name", None)
        if nm == name: return n
        for key in getattr(n, "keys", lambda: [])():
            value = n[key]
            if hasattr(value, "name"):
                found = go(value)
                if found is not None: return found
        return None
    return go(node)

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
        rq = reify(q, STANDARD)
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
        star = reify(q, SPARQL_STAR)
        star_checks = {
            "quoted": "<< " in star and f"<{OCCURRENCE_OF}>" in star,
            "not-standard": f"<{RS}subject>" not in star,
            "optional": ("OPTIONAL" in star) == ("optional" == name),
            "union": ("UNION" in star) == ("union" == name),
            "minus": ("MINUS" in star) == ("minus" == name),
            "no-prov": not any(
                key in star for key in ("GROUP_CONCAT", "SHA256", "urn:g:", "CONSTRUCT")
            ),
        }
        star_bad = [key for key, value in star_checks.items() if not value]
        print(
            f"  [{name + '-star':9}] "
            f"{'OK' if not star_bad else 'FAIL ' + ','.join(star_bad)}"
        )
        ok &= not star_bad
    print("SELFTEST", "OK" if ok else "FAILED"); return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scheme",
        default=STANDARD,
        help=f"reification scheme: {STANDARD} (default) or {SPARQL_STAR}",
    )
    parser.add_argument("--selftest", action="store_true", help="run focused self-tests")
    parser.add_argument("query", nargs="?", help="SPARQL SELECT query file")
    args = parser.parse_args(argv)

    if args.selftest:
        if args.query:
            parser.error("query is not accepted with --selftest")
        return 0 if _selftest() else 1
    if not args.query:
        parser.error("a query file is required")
    try:
        with open(args.query, encoding="utf-8") as handle:
            rewritten = reify(handle.read(), args.scheme)
    except ValueError as ex:
        parser.error(str(ex))
    print(rewritten, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
