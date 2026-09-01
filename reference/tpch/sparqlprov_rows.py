"""Build the SPARQLprov n-ary-row provenance form of a TPC-H query.

The formal workload uses the released SPARQLprov templates where they exist.
This generator is reserved for explicitly labelled non-aggregate adaptations,
currently Q4 and Q15.  It mirrors the released templates: a single contributing
row is exposed directly, while a multi-row derivation receives a product IRI
and every projected answer receives a sum IRI.
"""

import re
from typing import List


RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
OCCURRENCE_OF = "http://example.org/occurrenceOf"


def _projection_variables(query_text: str) -> List[str]:
    match = re.search(
        r"\bSELECT\b(?P<select>.*?)\bWHERE\s*\{",
        query_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        raise ValueError("SPARQLprov row rewriting requires SELECT ... WHERE")
    select = match.group("select")
    previous = None
    while previous != select:
        previous = select
        select = re.sub(r"\([^()]*\)", "", select)
    variables = re.findall(r"\?([A-Za-z_][A-Za-z0-9_]*)", select)
    return list(dict.fromkeys(variables))


def _row_subjects(row_inline_query: str) -> List[str]:
    pattern = re.compile(
        r"^\s*<<\s+(?P<row>\?[A-Za-z_][A-Za-z0-9_]*|<[^>]+>)\s+"
        + re.escape("<%s>" % RDF_TYPE)
        + r"\s+\?[A-Za-z_][A-Za-z0-9_]*\s+>>\s+"
        + re.escape("<%s>" % OCCURRENCE_OF),
        flags=re.MULTILINE,
    )
    rows = [match.group("row") for match in pattern.finditer(row_inline_query)]
    if not rows:
        raise ValueError("row-inline query contains no TPC-H row occurrences")
    if len(rows) != len(set(rows)):
        raise ValueError("row-inline query repeats a row subject")
    return rows


def _iri_bind(prefix: str, variables: List[str], target: str) -> str:
    if not variables:
        return '  BIND (URI("%s") AS ?%s)' % (prefix, target)
    pieces = []
    for index, variable in enumerate(variables, 1):
        label = "?s%d=" % index if index == 1 else "&s%d=" % index
        pieces.append(
            '    "%s", ENCODE_FOR_URI(xsd:string(?%s))' % (label, variable)
        )
    return (
        "  BIND (URI(concat(\n"
        '    "%s",\n' % prefix
        + ",\n".join(pieces)
        + "\n  )) AS ?%s)" % target
    )


def rewrite(base_query: str, row_inline_query: str) -> str:
    """Return an explicitly adapted SPARQLprov n-ary-row provenance query."""
    if "?prov_sum" in base_query:
        raise ValueError("query already contains SPARQLprov variables")
    if "occurrenceOf" in base_query or "rdf:reifies" in base_query or "<<(" in base_query:
        raise ValueError("SPARQLprov n-ary-row input must be the plain base query")

    projection = _projection_variables(base_query)
    rows = _row_subjects(row_inline_query)
    where = re.search(r"\bWHERE\s*\{", base_query, flags=re.IGNORECASE)
    if where is None:
        raise ValueError("query has no WHERE group")

    if len(rows) == 1:
        provenance_projection = ["prov_sum", "prov_sum_statement"]
        statements = ["  BIND (%s AS ?prov_sum_statement)" % rows[0]]
        product = []
    else:
        provenance_projection = ["prov_sum", "prov_sum_product"] + [
            "prov_sum_product_%d_statement" % index
            for index in range(1, len(rows) + 1)
        ]
        statements = [
            "  BIND (%s AS ?prov_sum_product_%d_statement)" % (row, index)
            for index, row in enumerate(rows, 1)
        ]
        product = [
            _iri_bind(
                "http://example.org/p_Sum_product/",
                ["prov_sum_product_%d_statement" % index
                 for index in range(1, len(rows) + 1)],
                "prov_sum_product",
            )
        ]

    projection_text = "\n".join("  ?%s" % name for name in provenance_projection)
    header = base_query[:where.start()].rstrip() + "\n" + projection_text + "\n"
    body = base_query[where.start():]
    closing = body.rfind("}")
    if closing < 0:
        raise ValueError("query has no closing WHERE brace")
    additions = statements + product + [
        _iri_bind("http://example.org/p_Sum/", projection, "prov_sum")
    ]
    rewritten = header + body[:closing].rstrip() + "\n" + "\n".join(additions) + "\n" + body[closing:]
    if not rewritten.endswith("\n"):
        rewritten += "\n"
    return rewritten
