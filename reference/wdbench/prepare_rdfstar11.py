#!/usr/bin/env python3
"""Extract WDBench direct triples and create RDF-star 1.1 occurrences."""
from __future__ import annotations

import argparse
import bz2
import gzip
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, Optional, Sequence, TextIO, Tuple
from urllib.parse import quote


SCHEMA = "wdbench-rdfstar11-dataset-v1"
OCCURRENCE_OF = "http://example.org/occurrenceOf"
DEFAULT_BNODE_BASE = "urn:wdbench:skolem:bnode:"
DIRECT_PROPERTY = re.compile(
    r"^<http://www\.wikidata\.org/prop/direct/P[1-9][0-9]*>$"
)
NTRIPLES_LINE = re.compile(
    r"^\s*(?P<subject><[^>]*>|_:[^\s]+)\s+"
    r"(?P<predicate><[^>]*>)\s+"
    r"(?P<object>.+)\s+\.\s*$"
)


class PreparationError(RuntimeError):
    """The input dump or requested output layout is invalid."""


def parse_ntriple(line: str, line_number: int) -> Optional[Tuple[str, str, str]]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = NTRIPLES_LINE.match(line)
    if match is None:
        raise PreparationError("invalid N-Triples input at line %d" % line_number)
    return (
        match.group("subject"),
        match.group("predicate"),
        match.group("object").rstrip(),
    )


def skolemize_blank_node(term: str, bnode_base: str) -> Tuple[str, bool]:
    """Replace a serialized blank-node term with a deterministic scoped IRI."""
    if not term.startswith("_:"):
        return term, False
    label = term[2:]
    if not label:
        raise PreparationError("blank-node label is empty")
    return "<%s%s>" % (bnode_base, quote(label, safe="")), True


def _input_stream(path: str) -> Tuple[TextIO, bool]:
    if path == "-":
        return sys.stdin, False
    source = Path(path)
    if not source.is_file():
        raise PreparationError("input dump not found: %s" % source)
    lowered = source.name.lower()
    if lowered.endswith(".bz2"):
        return bz2.open(source, mode="rt", encoding="utf-8", newline=""), True
    if lowered.endswith(".gz"):
        return gzip.open(source, mode="rt", encoding="utf-8", newline=""), True
    return source.open(encoding="utf-8", newline=""), True


def _open_partial(target: Path) -> Tuple[TextIO, Path]:
    if target.exists():
        raise PreparationError("refusing to overwrite %s" % target)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    if partial.exists():
        raise PreparationError("partial output already exists: %s" % partial)
    return partial.open("x", encoding="utf-8", newline="\n"), partial


def _finalize(handle: TextIO, partial: Path, target: Path) -> None:
    handle.flush()
    os.fsync(handle.fileno())
    handle.close()
    os.replace(partial, target)


def _atomic_json(path: Path, value: Any) -> None:
    handle, partial = _open_partial(path)
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        _finalize(handle, partial, path)
    except BaseException:
        if not handle.closed:
            handle.close()
        raise


def prepare(
    input_path: str,
    direct_output: Path,
    occurrence_output: Path,
    metadata_output: Path,
    source_url: Optional[str],
    token_base: str,
    max_direct_triples: Optional[int],
    bnode_base: str = DEFAULT_BNODE_BASE,
) -> Dict[str, Any]:
    if max_direct_triples is not None and max_direct_triples < 1:
        raise PreparationError("max-direct-triples must be positive")
    if not token_base or any(character.isspace() for character in token_base):
        raise PreparationError("token base must be a non-empty IRI prefix without spaces")
    if not bnode_base or any(character.isspace() for character in bnode_base):
        raise PreparationError("bnode base must be a non-empty IRI prefix without spaces")
    if bnode_base == token_base:
        raise PreparationError("blank-node and occurrence IRI prefixes must be different")
    targets = {
        direct_output.resolve(),
        occurrence_output.resolve(),
        metadata_output.resolve(),
    }
    if len(targets) != 3:
        raise PreparationError("the three output paths must be different")

    direct_handle, direct_partial = _open_partial(direct_output)
    occurrence_handle, occurrence_partial = _open_partial(occurrence_output)
    input_handle: Optional[TextIO] = None
    close_input = False
    started = time.perf_counter()
    input_lines = 0
    direct_source_lines = 0
    direct_triples = 0
    duplicate_direct_source_lines = 0
    source_blank_node_term_occurrences = 0
    skolemized_blank_node_term_occurrences = 0
    current_source_subject: Optional[str] = None
    current_subject_facts = set()
    try:
        input_handle, close_input = _input_stream(input_path)
        for line_number, line in enumerate(input_handle, 1):
            input_lines = line_number
            parsed = parse_ntriple(line, line_number)
            if parsed is None:
                continue
            subject, predicate, obj = parsed
            if subject != current_source_subject:
                current_source_subject = subject
                current_subject_facts.clear()
            if DIRECT_PROPERTY.fullmatch(predicate) is None:
                continue
            subject, subject_was_blank = skolemize_blank_node(subject, bnode_base)
            obj, object_was_blank = skolemize_blank_node(obj, bnode_base)
            direct_source_lines += 1
            source_blank_node_term_occurrences += int(subject_was_blank)
            source_blank_node_term_occurrences += int(object_was_blank)
            fact = "%s %s %s ." % (subject, predicate, obj)
            if fact in current_subject_facts:
                duplicate_direct_source_lines += 1
                continue
            current_subject_facts.add(fact)
            skolemized_blank_node_term_occurrences += int(subject_was_blank)
            skolemized_blank_node_term_occurrences += int(object_was_blank)
            direct_triples += 1
            direct_handle.write(fact + "\n")
            token = "%s%d" % (token_base, direct_triples)
            occurrence_handle.write(
                "<< %s %s %s >> <%s> <%s> .\n"
                % (subject, predicate, obj, OCCURRENCE_OF, token)
            )
            if max_direct_triples is not None and direct_triples >= max_direct_triples:
                break
        _finalize(direct_handle, direct_partial, direct_output)
        _finalize(occurrence_handle, occurrence_partial, occurrence_output)
    except BaseException:
        if not direct_handle.closed:
            direct_handle.close()
        if not occurrence_handle.closed:
            occurrence_handle.close()
        raise
    finally:
        if input_handle is not None and close_input:
            input_handle.close()

    metadata: Dict[str, Any] = {
        "schema": SCHEMA,
        "status": "complete",
        "source_path": input_path,
        "source_url": source_url,
        "input_lines_read": input_lines,
        "direct_source_lines": direct_source_lines,
        "direct_triples": direct_triples,
        "duplicate_direct_source_lines": duplicate_direct_source_lines,
        "occurrence_statements": direct_triples,
        "direct_output": str(direct_output),
        "direct_output_bytes": direct_output.stat().st_size,
        "occurrence_output": str(occurrence_output),
        "occurrence_output_bytes": occurrence_output.stat().st_size,
        "predicate_filter": "http://www.wikidata.org/prop/direct/P[1-9][0-9]*",
        "rdf_star_profile": "pre-RDF-1.2 quoted triples",
        "rdf_12_reifies_used": False,
        "occurrence_predicate": OCCURRENCE_OF,
        "ground": True,
        "blank_node_terms": 0,
        "source_blank_node_term_occurrences": source_blank_node_term_occurrences,
        "skolemized_blank_node_term_occurrences": (
            skolemized_blank_node_term_occurrences
        ),
        "blank_node_policy": "deterministic label-preserving skolemization",
        "bnode_base": bnode_base,
        "duplicate_policy": "one occurrence per unique RDF fact",
        "deduplication_scope": "exact triples within each contiguous source-subject block",
        "deduplication_validation": "loaded base statement count must equal direct_triples",
        "token_base": token_base,
        "max_direct_triples": max_direct_triples,
        "preparation_wall_ms": round(
            (time.perf_counter() - started) * 1000.0, 6
        ),
        "store_layouts": {
            "base": [direct_output.name],
            "mixed": [direct_output.name, occurrence_output.name],
        },
    }
    _atomic_json(metadata_output, metadata)
    return metadata


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        required=True,
        help="Wikidata truthy .nt, .nt.gz, .nt.bz2, or - for standard input",
    )
    parser.add_argument("--direct-out", required=True, type=Path)
    parser.add_argument("--occurrences-out", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--source-url")
    parser.add_argument(
        "--token-base", default="urn:wdbench:statement:", help="occurrence IRI prefix"
    )
    parser.add_argument(
        "--bnode-base",
        default=DEFAULT_BNODE_BASE,
        help="IRI prefix for deterministic source blank-node skolemization",
    )
    parser.add_argument("--max-direct-triples", type=int)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = prepare(
            args.input,
            args.direct_out.resolve(),
            args.occurrences_out.resolve(),
            args.metadata.resolve(),
            args.source_url,
            args.token_base,
            args.max_direct_triples,
            args.bnode_base,
        )
    except (OSError, UnicodeError, PreparationError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
