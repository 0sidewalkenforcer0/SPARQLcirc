"""Add TPC-H per-row hybrid-inline RDF-star 1.1 lookups to a SELECT query.

The supported input is the single-BGP shape used by the TPC-H non-aggregate
templates: triple patterns, FILTERs, and output-only BINDs in one WHERE scope.
Each distinct triple-pattern subject denotes one relational row and receives
exactly one row-marker lookup.  The original query text, including FILTER and
BIND expressions, is otherwise retained verbatim.
"""

import argparse
from pathlib import Path
import re
from typing import Any, List, Optional, Sequence

try:
    from rdflib.plugins.sparql.algebra import translateQuery
    from rdflib.plugins.sparql.parser import parseQuery
    from rdflib.term import BNode, Literal, URIRef, Variable
    RDFLIB_AVAILABLE = True
except ImportError:  # The core reference suite is dependency-free; experiment setup is not.
    translateQuery = parseQuery = None
    BNode = Literal = URIRef = Variable = ()
    RDFLIB_AVAILABLE = False


RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
OCCURRENCE_OF = "http://example.org/occurrenceOf"


def _term(value: Any) -> str:
    if isinstance(value, Variable):
        return "?" + str(value)
    if isinstance(value, (URIRef, BNode, Literal)):
        return value.n3()
    raise ValueError("unsupported row subject: %r" % (value,))


def _single_bgp_subjects(query_text: str) -> List[Any]:
    if not RDFLIB_AVAILABLE:
        raise RuntimeError(
            "TPC-H query preparation requires rdflib; install reference/requirements-optional.txt"
        )
    algebra = translateQuery(parseQuery(query_text)).algebra
    seen_objects = set()
    bgps = []

    def visit(value: Any) -> None:
        identity = id(value)
        if identity in seen_objects:
            return
        if hasattr(value, "name"):
            seen_objects.add(identity)
            if value.name == "BGP":
                bgps.append(value)
            for key in value.keys():
                visit(value[key])
        elif isinstance(value, (list, tuple)):
            seen_objects.add(identity)
            for item in value:
                visit(item)

    visit(algebra)
    if len(bgps) != 1:
        raise ValueError(
            "row-inline rewriting requires exactly one BGP; found %d" % len(bgps)
        )

    subjects = []
    seen_terms = set()
    for subject, _predicate, _obj in bgps[0]["triples"]:
        rendered = _term(subject)
        if rendered not in seen_terms:
            seen_terms.add(rendered)
            subjects.append(subject)
    if not subjects:
        raise ValueError("row-inline rewriting requires a non-empty BGP")
    return subjects


def _fresh_name(used: set, stem: str, index: int) -> str:
    suffix = index
    while True:
        candidate = "%s%d" % (stem, suffix)
        if candidate not in used:
            used.add(candidate)
            return candidate
        suffix += 1


def inline_rows(query_text: str) -> str:
    """Return a single-BGP SELECT with one old RDF-star lookup per row subject."""
    if "occurrenceOf" in query_text:
        raise ValueError("query already contains an occurrence lookup")
    if "rdf:reifies" in query_text or "http://www.w3.org/1999/02/22-rdf-syntax-ns#reifies" in query_text:
        raise ValueError("RDF 1.2 rdf:reifies is not permitted in the RDF-star 1.1 workload")

    subjects = _single_bgp_subjects(query_text)
    where = re.search(r"\bWHERE\s*\{", query_text, flags=re.IGNORECASE)
    if where is None:
        raise ValueError("SELECT query has no explicit WHERE group")

    used = set(re.findall(r"\?([A-Za-z_][A-Za-z0-9_]*)", query_text))
    rows = []
    for index, subject in enumerate(subjects, 1):
        type_name = _fresh_name(used, "__tpch_row_type_", index)
        token_name = _fresh_name(used, "__tpch_row_token_", index)
        row = _term(subject)
        rows.append("  %s <%s> ?%s ." % (row, RDF_TYPE, type_name))
        rows.append(
            "  << %s <%s> ?%s >> <%s> ?%s ."
            % (row, RDF_TYPE, type_name, OCCURRENCE_OF, token_name)
        )

    insertion = "\n" + "\n".join(rows)
    rewritten = query_text[: where.end()] + insertion + query_text[where.end() :]
    if not rewritten.endswith("\n"):
        rewritten += "\n"
    return rewritten


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", type=Path, help="single-BGP TPC-H SELECT query")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    print(inline_rows(args.query.read_text(encoding="utf-8")), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
