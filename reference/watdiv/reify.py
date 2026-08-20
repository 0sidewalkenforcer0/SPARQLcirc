"""Convert N-Triples into SPARQLcirc's mixed asserted-plus-token layout.

The default output keeps every input triple in the default graph and adds its
token encoding.  ``--pure`` reproduces the historical reification-only files.
"""

import argparse
from pathlib import Path
from typing import Iterator, Optional, Sequence, Tuple


RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
OCC = "http://example.org/occurrenceOf"

STANDARD = "standard"
RDF_STAR = "rdf-star"
NAMED_GRAPH = "named-graph"


def _input_triples(lines: Iterator[str]) -> Iterator[Tuple[str, str, str]]:
    """Yield N-Triples terms while retaining literals containing whitespace."""
    for line in lines:
        stripped = line.strip()
        if not stripped.endswith("."):
            continue
        parts = stripped[:-1].strip().split(None, 2)
        if len(parts) == 3:
            yield parts[0], parts[1], parts[2]


def reify_file(input_path: Path, output_path: Path, scheme: str = STANDARD,
               pure: bool = False) -> Tuple[int, int]:
    """Write the selected layout and return logical and physical triple counts."""
    if scheme not in (STANDARD, RDF_STAR, NAMED_GRAPH):
        raise ValueError("unsupported reification scheme: %s" % scheme)

    logical_count = 0
    physical_count = 0
    with input_path.open(encoding="utf-8") as source, output_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as target:
        for subject, predicate, obj in _input_triples(source):
            token = "<urn:t:%d>" % logical_count
            if not pure:
                target.write("%s %s %s .\n" % (subject, predicate, obj))
                physical_count += 1

            if scheme == RDF_STAR:
                target.write(
                    "<< %s %s %s >> <%s> %s .\n"
                    % (subject, predicate, obj, OCC, token)
                )
                physical_count += 1
            elif scheme == NAMED_GRAPH:
                target.write("%s %s %s %s .\n" % (subject, predicate, obj, token))
                physical_count += 1
            else:
                target.write("%s <%ssubject> %s .\n" % (token, RS, subject))
                target.write("%s <%spredicate> %s .\n" % (token, RS, predicate))
                target.write("%s <%sobject> %s .\n" % (token, RS, obj))
                physical_count += 3
            logical_count += 1

    return logical_count, physical_count


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path, help="plain N-Triples input")
    parser.add_argument("output", type=Path, help="mixed output file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--star", action="store_true",
        help="use RDF-star occurrence statements",
    )
    mode.add_argument(
        "--namedgraph", action="store_true",
        help="use one token-named graph per input triple (N-Quads output)",
    )
    parser.add_argument(
        "--pure", action="store_true",
        help="omit asserted triples and reproduce the historical reification-only layout",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    scheme = RDF_STAR if args.star else NAMED_GRAPH if args.namedgraph else STANDARD
    logical_count, physical_count = reify_file(
        args.input, args.output, scheme=scheme, pure=args.pure
    )
    layout = "pure" if args.pure else "mixed"
    print(
        "reified %d triples -> %s (%d physical statements, %s %s layout)"
        % (logical_count, args.output, physical_count, layout, scheme)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
