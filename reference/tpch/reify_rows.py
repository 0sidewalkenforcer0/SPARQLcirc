"""Build the mixed RDF-star 1.1 layout used by the TPC-H experiments.

TPC-H provenance is per relational row.  The SPARQLprov-compatible direct
mapping represents each row by one ``row rdf:type Table`` triple, so this tool
keeps the complete asserted RDF graph and adds one occurrence statement for
that type triple.  The row IRI itself is the provenance token::

    <row> rdf:type <Table> .
    << <row> rdf:type <Table> >> occurrenceOf <row> .

The quoted-triple syntax is the pre-standard RDF-star/SPARQL-star syntax used
by the project's RDF 1.1-era engine matrix.  It deliberately does not emit RDF
1.2 ``rdf:reifies`` or triple terms.
"""

import argparse
import os
from pathlib import Path
from typing import Iterator, Optional, Sequence, Tuple


RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
OCCURRENCE_OF = "http://example.org/occurrenceOf"


def _input_triples(lines: Iterator[str]) -> Iterator[Tuple[str, str, str, str]]:
    """Yield source line and its three N-Triples terms.

    ``tbl_to_rdf.py`` emits one ground N-Triples statement per line.  Splitting
    at most twice retains literal lexical forms containing whitespace.
    """
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.endswith("."):
            raise ValueError("line %d is not an N-Triples statement" % line_number)
        terms = stripped[:-1].strip().split(None, 2)
        if len(terms) != 3:
            raise ValueError("line %d does not contain three RDF terms" % line_number)
        if not (terms[0].startswith("<") and terms[0].endswith(">")):
            raise ValueError("line %d is not a ground N-Triples statement" % line_number)
        if not (terms[1].startswith("<") and terms[1].endswith(">")):
            raise ValueError("line %d is not a ground N-Triples statement" % line_number)
        yield stripped, terms[0], terms[1], terms[2]


def reify_rows(input_path: Path, output_path: Path) -> Tuple[int, int, int]:
    """Write mixed row-level RDF-star and return base, row, and output counts."""
    partial = output_path.with_name(output_path.name + ".partial")
    if output_path.exists() or partial.exists():
        raise ValueError("refusing to overwrite reification output: %s" % output_path)
    base_count = 0
    row_count = 0
    seen_rows = set()
    rdf_type = "<%s>" % RDF_TYPE

    with input_path.open(encoding="utf-8") as source, partial.open(
        "x", encoding="utf-8", newline="\n"
    ) as target:
        for source_line, subject, predicate, obj in _input_triples(source):
            target.write(source_line + "\n")
            base_count += 1
            if predicate != rdf_type:
                continue
            if not subject.startswith("<") or not subject.endswith(">"):
                raise ValueError("row subject must be an IRI: %s" % subject)
            if subject in seen_rows:
                raise ValueError("row has more than one rdf:type statement: %s" % subject)
            seen_rows.add(subject)
            target.write(
                "<< %s %s %s >> <%s> %s .\n"
                % (subject, predicate, obj, OCCURRENCE_OF, subject)
            )
            row_count += 1

    if not base_count:
        raise ValueError("input contains no RDF statements")
    if not row_count:
        raise ValueError("input contains no row rdf:type statements")
    os.replace(partial, output_path)
    return base_count, row_count, base_count + row_count


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path, help="SPARQLprov-compatible base N-Triples")
    parser.add_argument("output", type=Path, help="mixed RDF-star 1.1 output")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    base_count, row_count, output_count = reify_rows(args.input, args.output)
    print(
        "copied %d asserted triples and reified %d rows -> %s "
        "(%d physical statements, RDF-star 1.1 per-row mixed layout)"
        % (base_count, row_count, args.output, output_count)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
